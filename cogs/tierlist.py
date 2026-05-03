from __future__ import annotations

import asyncio
import io
import math
import pathlib
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import OrderedDict as OrderedDictType
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


# Evita que imagens absurdamente gigantes tentem explodir a memória do bot.
Image.MAX_IMAGE_PIXELS = 25_000_000


# ============================================================
# MODELOS DE DADOS
# ============================================================

@dataclass
class TierItem:
    """
    Representa um item da Tier List.

    name:
        Nome/legenda do item.

    image_url:
        URL opcional enviada pelo usuário.

    image_bytes:
        Bytes baixados da imagem.
        Esse campo é preenchido apenas na hora de gerar a imagem final.
        Se for None, o renderer usa fallback de texto.
    """

    name: str
    image_url: str | None = None
    image_bytes: bytes | None = None


@dataclass
class TierListSession:
    """
    Estado temporário de uma Tier List em criação.
    Fica em RAM e é apagado ao gerar, cancelar ou expirar.
    """

    owner_id: int
    title: str
    tiers: list[str] = field(default_factory=lambda: ["S", "A", "B", "C", "D"])
    items: OrderedDictType[str, list[TierItem]] = field(
        default_factory=lambda: OrderedDict(
            {
                "S": [],
                "A": [],
                "B": [],
                "C": [],
                "D": [],
            }
        )
    )
    panel_message: discord.Message | None = None


# ============================================================
# PARTE 1 — PILLOW RENDERER
# ============================================================

class TierListRenderer:
    """
    Motor de renderização premium para Tier Lists.
    Isolado do Discord — depende apenas do Pillow.
    Design System: Dark Mode sofisticado, 1600px, rodapé com ícone do servidor.
    """

    # ── Canvas ──────────────────────────────────────────────────
    WIDTH = 1600
    OUTER_PADDING = 40
    TITLE_HEIGHT = 120
    FOOTER_HEIGHT = 120

    # ── Tiers ───────────────────────────────────────────────────
    TIER_LABEL_WIDTH = 160
    TIER_GAP = 12
    ROW_PADDING_X = 20
    ROW_PADDING_Y = 20

    # ── Items ───────────────────────────────────────────────────
    ITEM_SIZE = 160          # Quadrado perfeito para imagens
    TEXT_ITEM_HEIGHT = 70    # Cards de texto puro
    ITEM_GAP = 20
    ITEM_RADIUS = 15

    # ── Cores (Design System Dark Mode) ─────────────────────────
    BG_TOP = (20, 20, 28)         # #14141C
    BG_BOTTOM = (18, 18, 24)      # #121218
    ROW_BG = (30, 30, 36)         # #1E1E24
    CARD_BG = (47, 53, 66)        # #2F3542
    CARD_BORDER = (69, 69, 90)    # #45455A
    TEXT_COLOR = (255, 255, 255)
    MUTED_COLOR = (164, 176, 190) # #A4B0BE
    DIVIDER_COLOR = (58, 58, 64)  # #3A3A40
    SHADOW_COLOR = (0, 0, 0, 80)

    TIER_COLORS = [
        (255, 71, 87),    # S  — Carmesim
        (255, 165, 2),    # A  — Laranja
        (236, 204, 104),  # B  — Amarelo
        (123, 237, 159),  # C  — Verde
        (112, 161, 255),  # D  — Azul
        (155, 93, 229),   # E  — Roxo
        (241, 91, 181),   # F  — Rosa
        (0, 187, 249),    # G  — Ciano
        (0, 245, 212),    # H  — Turquesa
        (199, 125, 255),  # I  — Lavanda
    ]

    def __init__(self, font_path: str | None = None) -> None:
        self.font_path = font_path

    # ════════════════════════════════════════════════════════════
    #  MÉTODO PRINCIPAL
    # ════════════════════════════════════════════════════════════

    def generate_tierlist_image(
        self,
        title: str,
        tiers_dict: "OrderedDictType[str, list[TierItem]]",
        *,
        creator_name: str = "",
        guild_icon_bytes: bytes | None = None,
    ) -> io.BytesIO:

        title_font = self._font(48, bold=True)
        tier_font = self._font(46, bold=True)
        item_font = self._font(22, bold=True)
        empty_font = self._font(20)
        footer_font = self._font(20)

        # ── Largura disponível para cards ───────────────────────
        # WIDTH - padding*2 - tier_label - row_padding*2
        items_area_w = (
            self.WIDTH
            - self.OUTER_PADDING * 2
            - self.TIER_LABEL_WIDTH
            - self.ROW_PADDING_X * 2
        )

        # Quantos cards cabem por linha:
        # floor((area + gap) / (item + gap))
        per_line = max(1, math.floor(
            (items_area_w + self.ITEM_GAP) / (self.ITEM_SIZE + self.ITEM_GAP)
        ))

        # ── Pré-cálculo de altura por tier ──────────────────────
        row_layouts: list[dict] = []

        for idx, (tier_name, items) in enumerate(tiers_dict.items()):
            n = len(items)
            lines = max(1, math.ceil(n / per_line))

            # Altura de cada linha (imagem vs texto)
            line_h = [self.TEXT_ITEM_HEIGHT] * lines
            for i, item in enumerate(items):
                row = i // per_line
                h = self.ITEM_SIZE if item.image_bytes else self.TEXT_ITEM_HEIGHT
                line_h[row] = max(line_h[row], h)

            content_h = (
                sum(line_h)
                + max(0, lines - 1) * self.ITEM_GAP
                + self.ROW_PADDING_Y * 2
            )

            row_layouts.append({
                "tier": tier_name,
                "items": items,
                "color": self.TIER_COLORS[idx % len(self.TIER_COLORS)],
                "row_height": max(96, content_h),
                "per_line": per_line,
                "line_heights": line_h,
            })

        # ── Altura total do canvas ──────────────────────────────
        rows_h = sum(r["row_height"] for r in row_layouts)
        gaps_h = max(0, len(row_layouts) - 1) * self.TIER_GAP

        canvas_h = (
            self.TITLE_HEIGHT
            + self.OUTER_PADDING
            + rows_h + gaps_h
            + self.OUTER_PADDING
            + self.FOOTER_HEIGHT
        )

        # ── Fundo com gradiente vertical sutil ──────────────────
        image = Image.new("RGBA", (self.WIDTH, canvas_h), self.BG_TOP + (255,))
        grad = Image.new("RGBA", (self.WIDTH, canvas_h), (0, 0, 0, 0))
        grad_draw = ImageDraw.Draw(grad)
        for y_px in range(canvas_h):
            ratio = y_px / max(1, canvas_h - 1)
            r = int(self.BG_TOP[0] + (self.BG_BOTTOM[0] - self.BG_TOP[0]) * ratio)
            g = int(self.BG_TOP[1] + (self.BG_BOTTOM[1] - self.BG_TOP[1]) * ratio)
            b = int(self.BG_TOP[2] + (self.BG_BOTTOM[2] - self.BG_TOP[2]) * ratio)
            grad_draw.line([(0, y_px), (self.WIDTH, y_px)], fill=(r, g, b, 255))
        image = Image.alpha_composite(image, grad)

        draw = ImageDraw.Draw(image)

        # ── Título ──────────────────────────────────────────────
        self._draw_centered(
            draw,
            (self.OUTER_PADDING, 0, self.WIDTH - self.OUTER_PADDING, self.TITLE_HEIGHT),
            title,
            title_font,
            self.TEXT_COLOR,
        )

        # ── Fileiras de Tiers ───────────────────────────────────
        y = self.TITLE_HEIGHT + self.OUTER_PADDING

        for row in row_layouts:
            rh = row["row_height"]
            color = row["color"]
            rx1 = self.OUTER_PADDING
            ry1 = y
            rx2 = self.WIDTH - self.OUTER_PADDING
            ry2 = y + rh

            # Fundo da fileira
            draw.rounded_rectangle((rx1, ry1, rx2, ry2), radius=18, fill=self.ROW_BG)

            # ── Label da tier (com drop shadow) ─────────────────
            lx1, ly1 = rx1, ry1
            lx2, ly2 = rx1 + self.TIER_LABEL_WIDTH, ry2

            # Sombra projetada (deslocada 4px para baixo-direita)
            shadow = Image.new("RGBA", (self.TIER_LABEL_WIDTH + 8, rh + 8), (0, 0, 0, 0))
            s_draw = ImageDraw.Draw(shadow)
            s_draw.rounded_rectangle((0, 0, self.TIER_LABEL_WIDTH + 7, rh + 7), radius=18, fill=self.SHADOW_COLOR)
            shadow = shadow.filter(ImageFilter.GaussianBlur(radius=6))
            image.paste(shadow, (lx1 - 2, ly1 - 2), shadow)

            # Label principal
            draw.rounded_rectangle((lx1, ly1, lx2, ly2), radius=18, fill=color + (255,))
            # Corte do arredondamento direito
            draw.rectangle((lx2 - 18, ly1, lx2, ly2), fill=color + (255,))

            self._draw_centered(
                draw,
                (lx1, ly1, lx2, ly2),
                row["tier"],
                tier_font,
                (17, 17, 25),
            )

            # ── Items dentro da fileira ─────────────────────────
            ix_start = lx2 + self.ROW_PADDING_X
            iy_start = ry1 + self.ROW_PADDING_Y
            line_heights = row["line_heights"]

            if not row["items"]:
                self._draw_centered(
                    draw,
                    (ix_start, ry1, rx2 - self.ROW_PADDING_X, ry2),
                    "Sem itens ainda",
                    empty_font,
                    self.MUTED_COLOR,
                )
            else:
                for ii, item in enumerate(row["items"]):
                    item_row = ii // row["per_line"]
                    item_col = ii % row["per_line"]

                    # Y acumulado das linhas anteriores
                    offset_y = sum(line_heights[:item_row]) + item_row * self.ITEM_GAP
                    item_h = self.ITEM_SIZE if item.image_bytes else self.TEXT_ITEM_HEIGHT

                    cx1 = ix_start + item_col * (self.ITEM_SIZE + self.ITEM_GAP)
                    cy1 = iy_start + offset_y
                    cx2 = cx1 + self.ITEM_SIZE
                    cy2 = cy1 + item_h

                    if item.image_bytes:
                        self._draw_image_card(image, draw, item, (cx1, cy1, cx2, cy2), item_font)
                    else:
                        # Card de texto: fundo + borda sutil + texto centralizado
                        draw.rounded_rectangle(
                            (cx1, cy1, cx2, cy2),
                            radius=self.ITEM_RADIUS,
                            fill=self.CARD_BG + (255,),
                            outline=self.CARD_BORDER + (255,),
                            width=2,
                        )
                        self._draw_centered_wrap(
                            draw,
                            (cx1 + 10, cy1 + 6, cx2 - 10, cy2 - 6),
                            item.name,
                            item_font,
                            self.TEXT_COLOR,
                        )

            y += rh + self.TIER_GAP

        # ── Rodapé premium ──────────────────────────────────────
        footer_y = canvas_h - self.FOOTER_HEIGHT

        # Linha divisória
        draw.line(
            [(self.OUTER_PADDING, footer_y + 10), (self.WIDTH - self.OUTER_PADDING, footer_y + 10)],
            fill=self.DIVIDER_COLOR + (255,),
            width=2,
        )

        # Ícone do servidor (círculo perfeito, lado esquerdo)
        icon_size = 64
        icon_x = self.OUTER_PADDING + 10
        icon_y = footer_y + (self.FOOTER_HEIGHT - icon_size) // 2 + 5

        if guild_icon_bytes:
            try:
                icon_img = Image.open(io.BytesIO(guild_icon_bytes)).convert("RGBA")
                icon_img = ImageOps.fit(icon_img, (icon_size, icon_size), method=Image.Resampling.LANCZOS)
                # Máscara circular
                mask = Image.new("L", (icon_size * 3, icon_size * 3), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, icon_size * 3, icon_size * 3), fill=255)
                mask = mask.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
                image.paste(icon_img, (icon_x, icon_y), mask)
            except Exception:
                pass

        # Texto do rodapé (lado direito, alinhado verticalmente ao ícone)
        from datetime import datetime
        date_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        parts = ["Gerado por Baphomet"]
        if creator_name:
            parts.append(f"Criado por @{creator_name}")
        parts.append(date_str)
        footer_text = "  •  ".join(parts)

        ft_bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
        ft_w = ft_bbox[2] - ft_bbox[0]
        ft_h = ft_bbox[3] - ft_bbox[1]
        ft_x = self.WIDTH - self.OUTER_PADDING - ft_w - 10
        ft_y = icon_y + (icon_size - ft_h) // 2

        draw.text((ft_x, ft_y), footer_text, font=footer_font, fill=self.MUTED_COLOR + (255,))

        # ── Exportar ────────────────────────────────────────────
        final = image.convert("RGB")
        buf = io.BytesIO()
        final.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf

    # ════════════════════════════════════════════════════════════
    #  FUNÇÕES AUXILIARES
    # ════════════════════════════════════════════════════════════

    def _draw_image_card(
        self,
        base: Image.Image,
        draw: ImageDraw.ImageDraw,
        item: "TierItem",
        box: tuple[int, int, int, int],
        font: ImageFont.ImageFont,
    ) -> None:
        """Card com imagem: crop quadrado, cantos arredondados, legenda."""
        x1, y1, x2, y2 = box
        sz = self.ITEM_SIZE

        try:
            raw = Image.open(io.BytesIO(item.image_bytes)).convert("RGB")
            fitted = ImageOps.fit(raw, (sz, sz), method=Image.Resampling.LANCZOS)
        except Exception:
            # Fallback para card de texto
            draw.rounded_rectangle(box, radius=self.ITEM_RADIUS, fill=self.CARD_BG + (255,), outline=self.CARD_BORDER + (255,), width=2)
            self._draw_centered_wrap(draw, (x1 + 10, y1 + 6, x2 - 10, y2 - 6), item.name, font, self.TEXT_COLOR)
            return

        # Máscara de cantos arredondados (supersampled 3x para anti-aliasing)
        ms = sz * 3
        mask = Image.new("L", (ms, ms), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, ms, ms), radius=self.ITEM_RADIUS * 3, fill=255)
        mask = mask.resize((sz, sz), Image.Resampling.LANCZOS)

        card = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        card.paste(fitted, (0, 0))
        card.putalpha(mask)
        base.paste(card, (x1, y1), card)

        # Legenda escura sobre a base da imagem
        cap_h = 32
        overlay = Image.new("RGBA", (sz, cap_h), (0, 0, 0, 175))
        cap_y = y1 + sz - cap_h
        base.paste(overlay, (x1, cap_y), overlay)

        cap_font = self._font(15, bold=True)
        self._draw_centered(draw, (x1 + 4, cap_y, x2 - 4, cap_y + cap_h), item.name, cap_font, self.TEXT_COLOR)

    def _font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        candidates: list[str] = []
        if self.font_path:
            candidates.append(self.font_path)

        assets = pathlib.Path("assets/fonts")
        if assets.exists():
            candidates.extend(str(p) for p in assets.glob("*.ttf"))

        if bold:
            candidates.extend([
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
            ])
        else:
            candidates.extend([
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "C:/Windows/Fonts/arial.ttf",
            ])

        for c in candidates:
            try:
                return ImageFont.truetype(c, size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _draw_centered(
        self,
        draw: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        text: str,
        font: ImageFont.ImageFont,
        fill: tuple,
    ) -> None:
        """Centraliza texto no centro geométrico exato de uma caixa."""
        x1, y1, x2, y2 = box
        max_w = x2 - x1

        cur = font
        if isinstance(font, ImageFont.FreeTypeFont):
            sz = font.size
            while sz > 10:
                bb = draw.textbbox((0, 0), text, font=cur)
                if (bb[2] - bb[0]) <= max_w:
                    break
                sz -= 2
                cur = self._font(sz, bold=True)

        bb = draw.textbbox((0, 0), text, font=cur)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        tx = x1 + (x2 - x1 - tw) // 2
        ty = y1 + (y2 - y1 - th) // 2 - bb[1]
        draw.text((tx, ty), text, font=cur, fill=fill)

    def _draw_centered_wrap(
        self,
        draw: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        text: str,
        font: ImageFont.ImageFont,
        fill: tuple,
    ) -> None:
        """Centraliza texto com word-wrap (máx 2 linhas) dentro de uma caixa."""
        x1, y1, x2, y2 = box
        max_w = x2 - x1
        max_h = y2 - y1

        cur = font
        lines = [text]

        if isinstance(font, ImageFont.FreeTypeFont):
            sz = font.size
            while sz >= 12:
                cur = self._font(sz, bold=True)
                lines = self._wrap(draw, text, cur, max_w, 2)
                lh = self._lh(draw, cur)
                total_h = len(lines) * lh + max(0, len(lines) - 1) * 3
                widest = max((draw.textbbox((0, 0), l, font=cur)[2] - draw.textbbox((0, 0), l, font=cur)[0] for l in lines), default=0)
                if widest <= max_w and total_h <= max_h:
                    break
                sz -= 2

        lh = self._lh(draw, cur)
        total_h = len(lines) * lh + max(0, len(lines) - 1) * 3
        cy = y1 + (max_h - total_h) // 2

        for line in lines:
            bb = draw.textbbox((0, 0), line, font=cur)
            lw = bb[2] - bb[0]
            lx = x1 + (max_w - lw) // 2
            draw.text((lx, cy - bb[1]), line, font=cur, fill=fill)
            cy += lh + 3

    def _wrap(self, draw, text, font, max_w, max_lines):
        words = text.split()
        if not words:
            return [""]
        lines, cur = [], ""
        for w in words:
            cand = w if not cur else f"{cur} {w}"
            if (draw.textbbox((0, 0), cand, font=font)[2] - draw.textbbox((0, 0), cand, font=font)[0]) <= max_w:
                cur = cand
            else:
                if cur:
                    lines.append(cur)
                cur = w
            if len(lines) >= max_lines:
                break
        if cur and len(lines) < max_lines:
            lines.append(cur)
        joined = " ".join(lines)
        if joined != " ".join(words) and lines:
            last = lines[-1]
            while last and (draw.textbbox((0, 0), last + "…", font=font)[2]) > max_w:
                last = last[:-1]
            lines[-1] = last + "…"
        return lines[:max_lines]

    def _lh(self, draw, font):
        bb = draw.textbbox((0, 0), "Ag", font=font)
        return bb[3] - bb[1]


# ============================================================
# PARTE 2 — DISCORD.PY: MODALS, SELECTS, VIEWS E SESSÃO
# ============================================================

class ConfigureTiersModal(discord.ui.Modal):
    def __init__(self, cog: TierListCog, owner_id: int) -> None:
        super().__init__(title="📝 Configurar Tiers")

        self.cog = cog
        self.owner_id = owner_id

        self.tiers_input = discord.ui.TextInput(
            label="Tiers separadas por vírgula",
            placeholder="Exemplo: S, A, B, C, D",
            default="S, A, B, C, D",
            min_length=1,
            max_length=250,
            required=True,
        )

        self.add_item(self.tiers_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        session = self.cog.sessions.get(self.owner_id)

        if session is None:
            await interaction.response.send_message(
                "❌ Essa sessão expirou ou foi cancelada.",
                ephemeral=True,
            )
            return

        parsed = self.cog.parse_tiers(str(self.tiers_input.value))

        if not parsed:
            await interaction.response.send_message(
                "⚠️ Você precisa informar pelo menos uma tier válida.",
                ephemeral=True,
            )
            return

        if len(parsed) > 25:
            await interaction.response.send_message(
                "⚠️ Use no máximo **25 tiers**, pois esse é o limite do Select Menu do Discord.",
                ephemeral=True,
            )
            return

        old_items = session.items
        new_items: OrderedDictType[str, list[TierItem]] = OrderedDict(
            (tier, []) for tier in parsed
        )

        # Preserva itens das tiers que ainda existem.
        for tier in parsed:
            if tier in old_items:
                new_items[tier].extend(old_items[tier])

        # Se uma tier antiga foi removida, os itens dela vão para a primeira tier nova.
        # Assim o usuário não perde dados silenciosamente.
        removed_items: list[TierItem] = []

        for old_tier, items in old_items.items():
            if old_tier not in new_items:
                removed_items.extend(items)

        if removed_items:
            new_items[parsed[0]].extend(removed_items)

        session.tiers = parsed
        session.items = new_items

        await self.cog.refresh_panel(session)

        await interaction.response.send_message(
            f"✅ Tiers configuradas: **{', '.join(parsed)}**",
            ephemeral=True,
        )


class AddItemModal(discord.ui.Modal):
    def __init__(self, cog: TierListCog, owner_id: int) -> None:
        super().__init__(title="➕ Adicionar Item")

        self.cog = cog
        self.owner_id = owner_id

        self.item_name = discord.ui.TextInput(
            label="Nome do item",
            placeholder="Exemplo: Pizza, Minecraft, Billie...",
            min_length=1,
            max_length=25,
            required=True,
        )

        self.image_url = discord.ui.TextInput(
            label="URL da imagem",
            placeholder="Opcional: https://exemplo.com/imagem.png",
            min_length=0,
            max_length=500,
            required=False,
        )

        self.add_item(self.item_name)
        self.add_item(self.image_url)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        session = self.cog.sessions.get(self.owner_id)

        if session is None:
            await interaction.response.send_message(
                "❌ Essa sessão expirou ou foi cancelada.",
                ephemeral=True,
            )
            return

        if not session.tiers:
            await interaction.response.send_message(
                "⚠️ Configure as tiers antes de adicionar itens.",
                ephemeral=True,
            )
            return

        total_items = sum(len(items) for items in session.items.values())

        if total_items >= self.cog.MAX_ITEMS_PER_SESSION:
            await interaction.response.send_message(
                f"⚠️ Limite de **{self.cog.MAX_ITEMS_PER_SESSION} itens** atingido nessa tier list.",
                ephemeral=True,
            )
            return

        clean_item = self.cog.clean_text(str(self.item_name.value), max_length=25)
        clean_url = str(self.image_url.value).strip() or None

        # Não tenta validar baixando aqui.
        # Só descarta URLs obviamente inválidas.
        # O download real acontece no botão Gerar Imagem.
        if clean_url and not self.cog.looks_like_url(clean_url):
            clean_url = None

        item = TierItem(
            name=clean_item,
            image_url=clean_url,
        )

        view = ItemTierSelectView(
            cog=self.cog,
            owner_id=self.owner_id,
            item=item,
            tiers=session.tiers,
        )

        extra = (
            "\n🖼️ Imagem detectada. Se o link falhar, o bot usa card de texto automaticamente."
            if clean_url
            else ""
        )

        await interaction.response.send_message(
            f"📌 Escolha em qual tier colocar **{discord.utils.escape_markdown(clean_item)}**:{extra}",
            view=view,
            ephemeral=True,
        )


class ItemTierSelect(discord.ui.Select):
    def __init__(
        self,
        cog: TierListCog,
        owner_id: int,
        item: TierItem,
        tiers: list[str],
    ) -> None:
        self.cog = cog
        self.owner_id = owner_id
        self.item = item

        options = [
            discord.SelectOption(
                label=tier[:100],
                value=tier,
                description=f"Colocar em {tier}"[:100],
                emoji="📌",
            )
            for tier in tiers[:25]
        ]

        super().__init__(
            placeholder="Selecione a tier do item",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        session = self.cog.sessions.get(self.owner_id)

        if session is None:
            await interaction.response.edit_message(
                content="❌ Essa sessão expirou ou foi cancelada.",
                view=None,
            )
            return

        selected_tier = self.values[0]

        # Blindagem:
        # O Select foi criado com as tiers da sessão,
        # mas ainda validamos novamente no callback.
        if selected_tier not in session.tiers or selected_tier not in session.items:
            await interaction.response.edit_message(
                content="❌ Essa tier não existe mais na sessão atual. Configure novamente.",
                view=None,
            )
            return

        session.items[selected_tier].append(self.item)

        await self.cog.refresh_panel(session)

        image_badge = " com imagem" if self.item.image_url else ""

        await interaction.response.edit_message(
            content=(
                f"✅ **{discord.utils.escape_markdown(self.item.name)}**{image_badge} "
                f"foi adicionado em **{selected_tier}**."
            ),
            view=None,
        )


class ItemTierSelectView(discord.ui.View):
    def __init__(
        self,
        cog: TierListCog,
        owner_id: int,
        item: TierItem,
        tiers: list[str],
    ) -> None:
        super().__init__(timeout=120)

        self.cog = cog
        self.owner_id = owner_id

        self.add_item(
            ItemTierSelect(
                cog=cog,
                owner_id=owner_id,
                item=item,
                tiers=tiers,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Só quem criou a tier list pode usar esse menu.",
                ephemeral=True,
            )
            return False

        return True


class TierListControlView(discord.ui.View):
    def __init__(self, cog: TierListCog, owner_id: int) -> None:
        super().__init__(timeout=15 * 60)

        self.cog = cog
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Esse painel não é seu, beldade. Crie o seu com `/tierlist criar`.",
                ephemeral=True,
            )
            return False

        if self.owner_id not in self.cog.sessions:
            await interaction.response.send_message(
                "❌ Essa sessão expirou ou foi cancelada.",
                ephemeral=True,
            )
            return False

        return True

    async def on_timeout(self) -> None:
        # Remove a sessão da RAM.
        # Isso evita memory leak caso a pessoa abandone o painel.
        session = self.cog.sessions.pop(self.owner_id, None)

        for child in self.children:
            child.disabled = True

        if session and session.panel_message:
            try:
                embed = discord.Embed(
                    title="⌛ Sessão Expirada",
                    description="A criação dessa tier list ficou inativa por muito tempo e foi encerrada.",
                    color=discord.Color.dark_gray(),
                )

                await session.panel_message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

        self.stop()

    @discord.ui.button(
        label="Configurar Tiers",
        emoji="📝",
        style=discord.ButtonStyle.primary,
    )
    async def configure_tiers(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            ConfigureTiersModal(
                cog=self.cog,
                owner_id=self.owner_id,
            )
        )

    @discord.ui.button(
        label="Adicionar Item",
        emoji="➕",
        style=discord.ButtonStyle.success,
    )
    async def add_item(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        session = self.cog.sessions.get(self.owner_id)

        if session is None:
            await interaction.response.send_message(
                "❌ Essa sessão expirou ou foi cancelada.",
                ephemeral=True,
            )
            return

        if not session.tiers:
            await interaction.response.send_message(
                "⚠️ Configure as tiers antes de adicionar itens.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            AddItemModal(
                cog=self.cog,
                owner_id=self.owner_id,
            )
        )

    @discord.ui.button(
        label="Gerar Imagem",
        emoji="🖼️",
        style=discord.ButtonStyle.secondary,
    )
    async def generate_image(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        session = self.cog.sessions.get(self.owner_id)

        if session is None:
            await interaction.response.send_message(
                "❌ Essa sessão expirou ou foi cancelada.",
                ephemeral=True,
            )
            return

        total_items = sum(len(items) for items in session.items.values())

        if total_items <= 0:
            await interaction.response.send_message(
                "⚠️ Adicione pelo menos **1 item** antes de gerar a imagem.",
                ephemeral=True,
            )
            return

        # Responde imediatamente ao Discord.
        # Isso evita timeout da interação enquanto baixamos imagens e renderizamos.
        await interaction.response.defer(thinking=True)

        title_snapshot = session.title

        # Copia a sessão para evitar alteração enquanto renderiza.
        tiers_snapshot: OrderedDictType[str, list[TierItem]] = OrderedDict(
            (
                tier,
                [
                    TierItem(
                        name=item.name,
                        image_url=item.image_url,
                        image_bytes=None,
                    )
                    for item in session.items.get(tier, [])
                ],
            )
            for tier in session.tiers
        )

        try:
            # Download assíncrono das URLs.
            hydrated_snapshot = await self.cog.hydrate_tier_images(tiers_snapshot)

            # Dados do rodapé premium
            creator_name = interaction.user.display_name
            guild_icon_bytes = None
            if interaction.guild and interaction.guild.icon:
                try:
                    guild_icon_bytes = await interaction.guild.icon.read()
                except Exception:
                    pass

            # Pillow fora do event loop.
            image_buffer = await asyncio.to_thread(
                self.cog.renderer.generate_tierlist_image,
                title_snapshot,
                hydrated_snapshot,
                creator_name=creator_name,
                guild_icon_bytes=guild_icon_bytes,
            )

        except Exception:
            await interaction.followup.send(
                "❌ Não consegui gerar a imagem final dessa vez.",
                ephemeral=True,
            )
            return

        file = discord.File(
            image_buffer,
            filename="tierlist.png",
        )

        # Limpa a sessão da memória.
        self.cog.sessions.pop(self.owner_id, None)

        for child in self.children:
            child.disabled = True

        try:
            if session.panel_message:
                done_embed = discord.Embed(
                    title="✅ Tier List Gerada",
                    description="A imagem final foi criada e a sessão foi encerrada.",
                    color=discord.Color.green(),
                )

                await session.panel_message.edit(embed=done_embed, view=self)
        except discord.HTTPException:
            pass

        self.stop()

        await interaction.followup.send(
            content=f"🖼️ **{discord.utils.escape_markdown(title_snapshot)}**",
            file=file,
        )

    @discord.ui.button(
        label="Cancelar",
        emoji="❌",
        style=discord.ButtonStyle.danger,
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.cog.sessions.pop(self.owner_id, None)

        for child in self.children:
            child.disabled = True

        self.stop()

        await interaction.response.defer(ephemeral=True)

        try:
            await interaction.message.delete()

            await interaction.followup.send(
                "❌ Sessão cancelada e painel removido.",
                ephemeral=True,
            )

        except discord.HTTPException:
            try:
                embed = discord.Embed(
                    title="❌ Sessão Cancelada",
                    description="Os dados temporários foram apagados.",
                    color=discord.Color.red(),
                )

                await interaction.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

            await interaction.followup.send(
                "❌ Sessão cancelada.",
                ephemeral=True,
            )


# ============================================================
# COG PRINCIPAL
# ============================================================

class TierListCog(
    commands.GroupCog,
    group_name="tierlist",
    group_description="Crie Tier Lists Interativas",
):
    MAX_TIERS = 25
    MAX_ITEMS_PER_SESSION = 150

    MAX_IMAGE_BYTES = 5 * 1024 * 1024
    IMAGE_DOWNLOAD_TIMEOUT = 5
    IMAGE_DOWNLOAD_CONCURRENCY = 8

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Estado temporário:
        # user_id -> TierListSession
        self.sessions: dict[int, TierListSession] = {}

        self.renderer = TierListRenderer()

    @app_commands.command(
        name="criar",
        description="Cria uma tier list interativa com texto e imagens por URL.",
    )
    @app_commands.guild_only()
    @app_commands.describe(titulo="Título da tier list")
    async def criar(
        self,
        interaction: discord.Interaction,
        titulo: app_commands.Range[str, 1, 80],
    ) -> None:
        owner_id = interaction.user.id
        clean_title = self.clean_text(str(titulo), max_length=80)

        # Se o usuário já tinha sessão, substitui com segurança.
        old_session = self.sessions.pop(owner_id, None)

        if old_session and old_session.panel_message:
            try:
                old_embed = discord.Embed(
                    title="♻️ Sessão Substituída",
                    description="Você iniciou uma nova tier list, então essa sessão antiga foi encerrada.",
                    color=discord.Color.dark_gray(),
                )

                await old_session.panel_message.edit(embed=old_embed, view=None)
            except discord.HTTPException:
                pass

        session = TierListSession(
            owner_id=owner_id,
            title=clean_title,
        )

        self.sessions[owner_id] = session

        view = TierListControlView(
            cog=self,
            owner_id=owner_id,
        )

        embed = self.build_panel_embed(session)

        await interaction.response.send_message(
            embed=embed,
            view=view,
        )

        session.panel_message = await interaction.original_response()

    async def fetch_image_safely(
        self,
        http: aiohttp.ClientSession,
        url: str,
    ) -> bytes | None:
        """
        Baixa uma imagem sem derrubar o bot.

        Regras:
        - aceita somente http/https;
        - timeout vem do ClientSession;
        - status precisa ser 200;
        - Content-Type precisa começar com image/;
        - limite de 5 MB;
        - qualquer erro retorna None;
        - None vira fallback silencioso para texto.
        """

        if not self.looks_like_url(url):
            return None

        try:
            async with http.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    return None

                content_type = response.headers.get("Content-Type", "").lower()

                if not content_type.startswith("image/"):
                    return None

                data = bytearray()

                async for chunk in response.content.iter_chunked(64 * 1024):
                    data.extend(chunk)

                    if len(data) > self.MAX_IMAGE_BYTES:
                        return None

                if not data:
                    return None

                return bytes(data)

        except Exception:
            return None

    async def hydrate_tier_images(
        self,
        tiers_snapshot: OrderedDictType[str, list[TierItem]],
    ) -> OrderedDictType[str, list[TierItem]]:
        """
        Baixa todas as imagens antes do Pillow.

        Importante:
        - aiohttp roda de forma assíncrona;
        - renderização Pillow roda depois em thread separada;
        - imagem inválida vira None;
        - None é fallback para card de texto.
        """

        timeout = aiohttp.ClientTimeout(total=self.IMAGE_DOWNLOAD_TIMEOUT)
        semaphore = asyncio.Semaphore(self.IMAGE_DOWNLOAD_CONCURRENCY)

        async with aiohttp.ClientSession(timeout=timeout) as http:

            async def hydrate_one(item: TierItem) -> None:
                if not item.image_url:
                    return

                async with semaphore:
                    item.image_bytes = await self.fetch_image_safely(http, item.image_url)

            tasks: list[asyncio.Task[None]] = []

            for items in tiers_snapshot.values():
                for item in items:
                    if item.image_url:
                        tasks.append(asyncio.create_task(hydrate_one(item)))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        return tiers_snapshot

    def parse_tiers(self, raw: str) -> list[str]:
        """
        Entrada:
            S, A, B, C, D

        Saída:
            ["S", "A", "B", "C", "D"]

        Remove vazios, normaliza espaços e evita duplicatas.
        """

        parts = raw.split(",")
        tiers: list[str] = []
        seen: set[str] = set()

        for part in parts:
            clean = self.clean_text(part, max_length=20)

            if not clean:
                continue

            key = clean.casefold()

            if key in seen:
                continue

            seen.add(key)
            tiers.append(clean)

            if len(tiers) >= self.MAX_TIERS:
                break

        return tiers

    def clean_text(
        self,
        text: str,
        *,
        max_length: int,
    ) -> str:
        """
        Limpa texto do usuário:
        - remove quebras de linha;
        - comprime espaços repetidos;
        - corta no limite seguro.
        """

        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_length].strip()

    def looks_like_url(self, url: str) -> bool:
        parsed = urlparse(url)

        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
        )

    def build_panel_embed(self, session: TierListSession) -> discord.Embed:
        total_items = sum(len(items) for items in session.items.values())
        image_items = sum(
            1
            for items in session.items.values()
            for item in items
            if item.image_url
        )

        embed = discord.Embed(
            title="🧩 Painel De Criação De Tier List",
            description=(
                f"**Título:** {discord.utils.escape_markdown(session.title)}\n"
                f"**Tiers:** {len(session.tiers)}\n"
                f"**Itens:** {total_items}/{self.MAX_ITEMS_PER_SESSION}\n"
                f"**Itens Com URL:** {image_items}\n\n"
                "Use os botões abaixo para configurar, adicionar itens e gerar a imagem final."
            ),
            color=discord.Color.from_rgb(155, 93, 229),
        )

        for tier in session.tiers:
            items = session.items.get(tier, [])

            preview_parts: list[str] = []

            for item in items[:8]:
                icon = "🖼️" if item.image_url else "📝"
                preview_parts.append(f"{icon} {item.name}")

            preview = ", ".join(preview_parts)

            if len(items) > 8:
                preview += f" +{len(items) - 8}"

            embed.add_field(
                name=f"📌 {tier}",
                value=preview or "Sem itens ainda",
                inline=False,
            )

        embed.set_footer(
            text="A sessão expira após 15 minutos de inatividade. URLs ruins viram texto automaticamente."
        )

        return embed

    async def refresh_panel(self, session: TierListSession) -> None:
        if not session.panel_message:
            return

        try:
            await session.panel_message.edit(embed=self.build_panel_embed(session))
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TierListCog(bot))