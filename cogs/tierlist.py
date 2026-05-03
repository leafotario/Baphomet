from __future__ import annotations

import asyncio
import inspect
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
from PIL import (
    Image,
    ImageDraw,
    ImageFilter,
    ImageFont,
    ImageOps,
    UnidentifiedImageError,
)

try:
    from duckduckgo_search import AsyncDDGS
except ImportError:
    AsyncDDGS = None


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
    OUTER_PADDING = 40
    TITLE_HEIGHT = 120
    FOOTER_HEIGHT = 120

    # ── Tiers ───────────────────────────────────────────────────
    TIER_LABEL_WIDTH = 160
    TIER_GAP = 12
    ROW_PADDING_X = 20
    ROW_PADDING_Y = 20

    # ── Items ───────────────────────────────────────────────────
    ITEM_SIZE = 160
    TEXT_ITEM_HEIGHT = 70
    ITEM_GAP = 20
    ITEM_RADIUS = 15

    # ── Cores ───────────────────────────────────────────────────
    BG_TOP = (20, 20, 28)
    BG_BOTTOM = (18, 18, 24)
    ROW_BG = (30, 30, 36)
    CARD_BG = (47, 53, 66)
    CARD_BORDER = (69, 69, 90)
    TEXT_COLOR = (255, 255, 255)
    MUTED_COLOR = (164, 176, 190)
    DIVIDER_COLOR = (58, 58, 64)
    SHADOW_COLOR = (0, 0, 0, 80)

    TIER_COLORS = [
        (255, 71, 87),
        (255, 165, 2),
        (236, 204, 104),
        (123, 237, 159),
        (112, 161, 255),
        (155, 93, 229),
        (241, 91, 181),
        (0, 187, 249),
        (0, 245, 212),
        (199, 125, 255),
    ]

    def __init__(self, font_path: str | None = None) -> None:
        self.font_path = font_path

    def calculate_tierlist_dimensions(
        self,
        tiers_dict: OrderedDictType[str, list[TierItem]],
        min_width: int = 800,
        max_width: int = 1920,
    ) -> dict:
        """
        Motor Flexbox de análise matemática pré-renderização.
        Calcula dimensões fluidas baseadas no número de itens.
        """

        max_items_in_a_tier = 0

        for items in tiers_dict.values():
            if len(items) > max_items_in_a_tier:
                max_items_in_a_tier = len(items)

        if max_items_in_a_tier == 0:
            max_items_in_a_tier = 1

        ideal_width = (
            self.OUTER_PADDING * 2
            + self.TIER_LABEL_WIDTH
            + self.ROW_PADDING_X * 2
            + (max_items_in_a_tier * self.ITEM_SIZE)
            + ((max_items_in_a_tier - 1) * self.ITEM_GAP)
        )

        final_width = max(min_width, min(max_width, ideal_width))

        items_area_w = (
            final_width
            - self.OUTER_PADDING * 2
            - self.TIER_LABEL_WIDTH
            - self.ROW_PADDING_X * 2
        )

        per_line = max(
            1,
            math.floor((items_area_w + self.ITEM_GAP) / (self.ITEM_SIZE + self.ITEM_GAP)),
        )

        row_layouts = []

        for idx, (tier_name, items) in enumerate(tiers_dict.items()):
            n = len(items)
            lines = max(1, math.ceil(n / per_line)) if n > 0 else 1

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

            row_layouts.append(
                {
                    "tier": tier_name,
                    "items": items,
                    "color": self.TIER_COLORS[idx % len(self.TIER_COLORS)],
                    "row_height": max(96, content_h),
                    "per_line": per_line,
                    "line_heights": line_h,
                }
            )

        rows_h = sum(r["row_height"] for r in row_layouts)
        gaps_h = max(0, len(row_layouts) - 1) * self.TIER_GAP

        raw_canvas_h = (
            self.TITLE_HEIGHT
            + self.OUTER_PADDING
            + rows_h
            + gaps_h
            + self.OUTER_PADDING
            + self.FOOTER_HEIGHT
        )

        min_aspect_ratio = 9 / 16
        min_allowed_height = int(final_width * min_aspect_ratio)

        padding_y_extra = 0

        if raw_canvas_h < min_allowed_height:
            padding_y_extra = (min_allowed_height - raw_canvas_h) // 2

        final_canvas_h = raw_canvas_h + (padding_y_extra * 2)

        return {
            "canvas_w": final_width,
            "canvas_h": final_canvas_h,
            "padding_y_extra": padding_y_extra,
            "row_layouts": row_layouts,
        }

    def generate_tierlist_image(
        self,
        title: str,
        tiers_dict: OrderedDictType[str, list[TierItem]],
        *,
        creator_name: str = "",
        guild_icon_bytes: bytes | None = None,
    ) -> io.BytesIO:

        title_font = self._font(48, bold=True)
        tier_font = self._font(46, bold=True)
        item_font = self._font(22, bold=True)
        empty_font = self._font(20)

        layout = self.calculate_tierlist_dimensions(
            tiers_dict,
            max_width=1920,
            min_width=800,
        )

        canvas_w = layout["canvas_w"]
        canvas_h = layout["canvas_h"]
        row_layouts = layout["row_layouts"]
        padding_y_extra = layout["padding_y_extra"]

        image = Image.new("RGBA", (canvas_w, canvas_h), self.BG_TOP + (255,))
        grad = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        grad_draw = ImageDraw.Draw(grad)

        for y_px in range(canvas_h):
            ratio = y_px / max(1, canvas_h - 1)
            r = int(self.BG_TOP[0] + (self.BG_BOTTOM[0] - self.BG_TOP[0]) * ratio)
            g = int(self.BG_TOP[1] + (self.BG_BOTTOM[1] - self.BG_TOP[1]) * ratio)
            b = int(self.BG_TOP[2] + (self.BG_BOTTOM[2] - self.BG_TOP[2]) * ratio)
            grad_draw.line([(0, y_px), (canvas_w, y_px)], fill=(r, g, b, 255))

        image = Image.alpha_composite(image, grad)
        draw = ImageDraw.Draw(image)

        title_y_start = padding_y_extra

        self._draw_centered(
            draw,
            (
                self.OUTER_PADDING,
                title_y_start,
                canvas_w - self.OUTER_PADDING,
                title_y_start + self.TITLE_HEIGHT,
            ),
            title,
            title_font,
            self.TEXT_COLOR,
        )

        y = title_y_start + self.TITLE_HEIGHT + self.OUTER_PADDING

        for row in row_layouts:
            rh = row["row_height"]
            color = row["color"]

            rx1 = self.OUTER_PADDING
            ry1 = y
            rx2 = canvas_w - self.OUTER_PADDING
            ry2 = y + rh

            draw.rounded_rectangle((rx1, ry1, rx2, ry2), radius=18, fill=self.ROW_BG)

            lx1, ly1 = rx1, ry1
            lx2, ly2 = rx1 + self.TIER_LABEL_WIDTH, ry2

            shadow = Image.new("RGBA", (self.TIER_LABEL_WIDTH + 8, rh + 8), (0, 0, 0, 0))
            s_draw = ImageDraw.Draw(shadow)
            s_draw.rounded_rectangle(
                (0, 0, self.TIER_LABEL_WIDTH + 7, rh + 7),
                radius=18,
                fill=self.SHADOW_COLOR,
            )
            shadow = shadow.filter(ImageFilter.GaussianBlur(radius=6))
            image.paste(shadow, (lx1 - 2, ly1 - 2), shadow)

            draw.rounded_rectangle((lx1, ly1, lx2, ly2), radius=18, fill=color + (255,))
            draw.rectangle((lx2 - 18, ly1, lx2, ly2), fill=color + (255,))

            self._draw_centered(
                draw,
                (lx1, ly1, lx2, ly2),
                row["tier"],
                tier_font,
                (17, 17, 25),
            )

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

                    offset_y = sum(line_heights[:item_row]) + item_row * self.ITEM_GAP
                    item_h = self.ITEM_SIZE if item.image_bytes else self.TEXT_ITEM_HEIGHT

                    cx1 = ix_start + item_col * (self.ITEM_SIZE + self.ITEM_GAP)
                    cy1 = iy_start + offset_y
                    cx2 = cx1 + self.ITEM_SIZE
                    cy2 = cy1 + item_h

                    if item.image_bytes:
                        self._draw_image_card(
                            image,
                            draw,
                            item,
                            (cx1, cy1, cx2, cy2),
                            item_font,
                        )
                    else:
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

        footer_y = canvas_h - padding_y_extra - self.FOOTER_HEIGHT

        draw.line(
            [
                (self.OUTER_PADDING, footer_y + 10),
                (canvas_w - self.OUTER_PADDING, footer_y + 10),
            ],
            fill=self.DIVIDER_COLOR + (255,),
            width=2,
        )

        icon_size = 64
        icon_x = self.OUTER_PADDING
        icon_y = footer_y + (self.FOOTER_HEIGHT - icon_size) // 2

        if guild_icon_bytes:
            try:
                icon_img = Image.open(io.BytesIO(guild_icon_bytes)).convert("RGBA")
                icon_img = ImageOps.fit(
                    icon_img,
                    (icon_size, icon_size),
                    method=Image.Resampling.LANCZOS,
                )

                mask = Image.new("L", (icon_size * 3, icon_size * 3), 0)
                ImageDraw.Draw(mask).ellipse(
                    (0, 0, icon_size * 3, icon_size * 3),
                    fill=255,
                )
                mask = mask.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
                image.paste(icon_img, (icon_x, icon_y), mask)
            except Exception:
                pass

        from datetime import datetime

        date_str = datetime.now().strftime("%d/%m/%Y %H:%M")

        parts = ["Gerado por Baphomet"]

        if creator_name:
            parts.append(f"Criado por @{creator_name}")

        parts.append(date_str)

        footer_text = "  •  ".join(parts)

        f_size = 20
        footer_font = self._font(f_size)
        ft_bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
        ft_w = ft_bbox[2] - ft_bbox[0]

        max_text_width = (canvas_w - self.OUTER_PADDING) - (icon_x + icon_size + 20)

        while ft_w > max_text_width and f_size > 10:
            f_size -= 1
            footer_font = self._font(f_size)
            ft_bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
            ft_w = ft_bbox[2] - ft_bbox[0]

        ft_h = ft_bbox[3] - ft_bbox[1]

        ft_x = canvas_w - self.OUTER_PADDING - ft_w
        ft_y = icon_y + (icon_size - ft_h) // 2

        draw.text((ft_x, ft_y), footer_text, font=footer_font, fill=self.MUTED_COLOR + (255,))

        final = image.convert("RGB")
        buf = io.BytesIO()
        final.save(buf, format="PNG", optimize=True)
        buf.seek(0)

        return buf

    def _draw_image_card(
        self,
        base: Image.Image,
        draw: ImageDraw.ImageDraw,
        item: TierItem,
        box: tuple[int, int, int, int],
        font: ImageFont.ImageFont,
    ) -> None:
        """Card com imagem: crop quadrado, cantos arredondados e legenda."""

        x1, y1, x2, y2 = box
        sz = self.ITEM_SIZE

        try:
            raw = Image.open(io.BytesIO(item.image_bytes or b"")).convert("RGBA")
            fitted = ImageOps.fit(raw, (sz, sz), method=Image.Resampling.LANCZOS)
        except Exception:
            draw.rounded_rectangle(
                box,
                radius=self.ITEM_RADIUS,
                fill=self.CARD_BG + (255,),
                outline=self.CARD_BORDER + (255,),
                width=2,
            )

            self._draw_centered_wrap(
                draw,
                (x1 + 10, y1 + 6, x2 - 10, y2 - 6),
                item.name,
                font,
                self.TEXT_COLOR,
            )

            return

        ms = sz * 3

        mask = Image.new("L", (ms, ms), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, ms, ms),
            radius=self.ITEM_RADIUS * 3,
            fill=255,
        )
        mask = mask.resize((sz, sz), Image.Resampling.LANCZOS)

        card = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        card.paste(fitted, (0, 0))
        card.putalpha(mask)

        base.paste(card, (x1, y1), card)

        cap_h = 32
        overlay = Image.new("RGBA", (sz, cap_h), (0, 0, 0, 175))
        cap_y = y1 + sz - cap_h

        base.paste(overlay, (x1, cap_y), overlay)

        cap_font = self._font(15, bold=True)

        self._draw_centered(
            draw,
            (x1 + 4, cap_y, x2 - 4, cap_y + cap_h),
            item.name,
            cap_font,
            self.TEXT_COLOR,
        )

    def _font(
        self,
        size: int,
        bold: bool = False,
    ) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:

        candidates: list[str] = []

        if self.font_path:
            candidates.append(self.font_path)

        assets = pathlib.Path("assets/fonts")

        if assets.exists():
            candidates.extend(str(p) for p in assets.glob("*.ttf"))

        if bold:
            candidates.extend(
                [
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "C:/Windows/Fonts/arialbd.ttf",
                ]
            )
        else:
            candidates.extend(
                [
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "C:/Windows/Fonts/arial.ttf",
                ]
            )

        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size=size)
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
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]

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
        """Centraliza texto com word-wrap de no máximo 2 linhas."""

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
                widest = max(
                    (
                        draw.textbbox((0, 0), line, font=cur)[2]
                        - draw.textbbox((0, 0), line, font=cur)[0]
                        for line in lines
                    ),
                    default=0,
                )

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

    def _wrap(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_w: int,
        max_lines: int,
    ) -> list[str]:

        words = text.split()

        if not words:
            return [""]

        lines: list[str] = []
        cur = ""

        for word in words:
            cand = word if not cur else f"{cur} {word}"

            cand_box = draw.textbbox((0, 0), cand, font=font)
            cand_w = cand_box[2] - cand_box[0]

            if cand_w <= max_w:
                cur = cand
            else:
                if cur:
                    lines.append(cur)

                cur = word

            if len(lines) >= max_lines:
                break

        if cur and len(lines) < max_lines:
            lines.append(cur)

        joined = " ".join(lines)

        if joined != " ".join(words) and lines:
            last = lines[-1]

            while last and draw.textbbox((0, 0), last + "…", font=font)[2] > max_w:
                last = last[:-1]

            lines[-1] = last + "…"

        return lines[:max_lines]

    def _lh(
        self,
        draw: ImageDraw.ImageDraw,
        font: ImageFont.ImageFont,
    ) -> int:

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

        for tier in parsed:
            if tier in old_items:
                new_items[tier].extend(old_items[tier])

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

        self.user_id_input = discord.ui.TextInput(
            label="Foto de Usuário (ID)",
            placeholder="ID do usuário: 123456789012345678",
            min_length=0,
            max_length=25,
            required=False,
        )

        self.web_search_input = discord.ui.TextInput(
            label="Pesquisa na Web (Termo)",
            placeholder="Exemplo: Maçã, Goku, Logo do Python",
            min_length=0,
            max_length=50,
            required=False,
        )

        self.add_item(self.item_name)
        self.add_item(self.image_url)
        self.add_item(self.user_id_input)
        self.add_item(self.web_search_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        session = self.cog.sessions.get(self.owner_id)

        if session is None:
            await interaction.followup.send(
                "❌ Essa sessão expirou ou foi cancelada.",
                ephemeral=True,
            )
            return

        if not session.tiers:
            await interaction.followup.send(
                "⚠️ Configure as tiers antes de adicionar itens.",
                ephemeral=True,
            )
            return

        total_items = sum(len(items) for items in session.items.values())

        if total_items >= self.cog.MAX_ITEMS_PER_SESSION:
            await interaction.followup.send(
                f"⚠️ Limite de **{self.cog.MAX_ITEMS_PER_SESSION} itens** atingido nessa tier list.",
                ephemeral=True,
            )
            return

        clean_item = self.cog.clean_text(str(self.item_name.value), max_length=25)
        clean_url = str(self.image_url.value).strip() or None
        user_id_str = str(self.user_id_input.value).strip()
        web_search_str = str(self.web_search_input.value).strip()

        image_bytes: bytes | None = None

        # Prioridade 1: URL informada manualmente.
        if clean_url:
            if not self.cog.looks_like_url(clean_url):
                print(f"URL manual ignorada: URL inválida ({clean_url})")
                clean_url = None

        # Prioridade 2: Avatar de usuário por ID.
        elif user_id_str:
            try:
                user_id = int(user_id_str)
                user = await interaction.client.fetch_user(user_id)
                clean_url = user.display_avatar.replace(format="png", size=256).url
            except (ValueError, discord.NotFound, discord.HTTPException) as exc:
                print(f"Avatar por ID falhou: {type(exc).__name__}: {exc}")
                clean_url = None

        # Prioridade 3: Pesquisa web automática.
        elif web_search_str:
            fetched_bytes = await self.cog.fetch_image_from_web(web_search_str)

            if fetched_bytes:
                image_bytes = fetched_bytes
                clean_url = None

        item = TierItem(
            name=clean_item,
            image_url=clean_url,
            image_bytes=image_bytes,
        )

        view = ItemTierSelectView(
            cog=self.cog,
            owner_id=self.owner_id,
            item=item,
            tiers=session.tiers,
        )

        extra = (
            "\n🖼️ Imagem detectada com sucesso. Se a renderização falhar, o bot usará card de texto."
            if (clean_url or image_bytes)
            else ""
        )

        await interaction.followup.send(
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

        if selected_tier not in session.tiers or selected_tier not in session.items:
            await interaction.response.edit_message(
                content="❌ Essa tier não existe mais na sessão atual. Configure novamente.",
                view=None,
            )
            return

        session.items[selected_tier].append(self.item)

        await self.cog.refresh_panel(session)

        image_badge = " com imagem" if (self.item.image_url or self.item.image_bytes) else ""

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

        await interaction.response.defer(thinking=True)

        title_snapshot = session.title

        tiers_snapshot: OrderedDictType[str, list[TierItem]] = OrderedDict(
            (
                tier,
                [
                    TierItem(
                        name=item.name,
                        image_url=item.image_url,
                        image_bytes=item.image_bytes,
                    )
                    for item in session.items.get(tier, [])
                ],
            )
            for tier in session.tiers
        )

        try:
            hydrated_snapshot = await self.cog.hydrate_tier_images(tiers_snapshot)

            creator_name = interaction.user.display_name

            guild_icon_bytes = None

            if interaction.guild and interaction.guild.icon:
                try:
                    guild_icon_bytes = await interaction.guild.icon.read()
                except Exception:
                    pass

            image_buffer = await asyncio.to_thread(
                self.cog.renderer.generate_tierlist_image,
                title_snapshot,
                hydrated_snapshot,
                creator_name=creator_name,
                guild_icon_bytes=guild_icon_bytes,
            )

        except Exception as exc:
            print(f"Renderização final falhou: {type(exc).__name__}: {exc}")

            await interaction.followup.send(
                "❌ Não consegui gerar a imagem final dessa vez.",
                ephemeral=True,
            )
            return

        file = discord.File(
            image_buffer,
            filename="tierlist.png",
        )

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
    IMAGE_DOWNLOAD_TIMEOUT = 3
    IMAGE_DOWNLOAD_CONCURRENCY = 8

    WEB_SEARCH_MAX_RESULTS = 5
    WEB_SEARCH_TIMEOUT = 8
    WEB_DOWNLOAD_TOTAL_BUDGET = 15

    IMAGE_REQUEST_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/png,image/jpeg,image/*,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
        "Referer": "https://duckduckgo.com/",
    }

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.sessions: dict[int, TierListSession] = {}
        self.renderer = TierListRenderer()

    async def fetch_image_from_web(self, query: str) -> bytes | None:
        """
        Pesquisa imagem via DuckDuckGo e baixa a primeira candidata realmente válida.

        Blindagens:
        - AsyncDDGS isolado em try/except.
        - max_results baixo para reduzir rate-limit.
        - timeout na busca.
        - headers robustos simulando navegador real.
        - ssl=False para sites com SSL quebrado.
        - allow_redirects=True com max_redirects limitado.
        - timeout de 3s por link.
        - orçamento total de 15s para os 5 downloads.
        - valida Content-Type.
        - rejeita base64, data URI, svg, HTML e URLs não HTTP.
        - valida no Pillow e converte para RGBA.
        - retorna None em qualquer falha para cair no card de texto.
        """

        clean_query = self.clean_text(str(query), max_length=80)

        if not clean_query:
            print("Busca web falhou: termo vazio.")
            return None

        if AsyncDDGS is None:
            print("Busca web falhou: biblioteca duckduckgo_search.AsyncDDGS não encontrada.")
            return None

        results: list[dict] = []

        async def run_ddg_search() -> list[dict]:
            collected: list[dict] = []

            async with AsyncDDGS() as ddgs:
                maybe_results = ddgs.images(
                    clean_query,
                    safesearch="on",
                    max_results=self.WEB_SEARCH_MAX_RESULTS,
                )

                if inspect.isawaitable(maybe_results):
                    maybe_results = await maybe_results

                if hasattr(maybe_results, "__aiter__"):
                    async for item in maybe_results:
                        if isinstance(item, dict):
                            collected.append(item)

                        if len(collected) >= self.WEB_SEARCH_MAX_RESULTS:
                            break
                else:
                    for item in list(maybe_results or [])[: self.WEB_SEARCH_MAX_RESULTS]:
                        if isinstance(item, dict):
                            collected.append(item)

            return collected

        try:
            results = await asyncio.wait_for(
                run_ddg_search(),
                timeout=self.WEB_SEARCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            print(f"Busca DDG falhou para '{clean_query}': Timeout")
            return None
        except Exception as exc:
            print(f"Busca DDG falhou para '{clean_query}': {type(exc).__name__}: {exc}")
            return None

        if not results:
            print(f"Busca DDG falhou para '{clean_query}': nenhum resultado.")
            return None

        connector = aiohttp.TCPConnector(
            ssl=False,
            limit=4,
            limit_per_host=2,
            enable_cleanup_closed=True,
        )

        session_timeout = aiohttp.ClientTimeout(total=self.WEB_DOWNLOAD_TOTAL_BUDGET)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.WEB_DOWNLOAD_TOTAL_BUDGET

        try:
            async with aiohttp.ClientSession(
                headers=self.IMAGE_REQUEST_HEADERS,
                connector=connector,
                timeout=session_timeout,
                raise_for_status=False,
            ) as session:

                for index, img_info in enumerate(results[: self.WEB_SEARCH_MAX_RESULTS], start=1):
                    remaining = deadline - loop.time()

                    if remaining <= 0:
                        print(f"Link {index} falhou: orçamento total de 15s esgotado.")
                        break

                    url = (
                        img_info.get("image")
                        or img_info.get("thumbnail")
                        or img_info.get("url")
                        or ""
                    )

                    data = await self._download_and_purify_image(
                        session=session,
                        url=str(url),
                        label=f"Link {index}",
                        timeout_seconds=min(self.IMAGE_DOWNLOAD_TIMEOUT, remaining),
                    )

                    if data:
                        return data

        except Exception as exc:
            print(f"Busca web '{clean_query}' falhou na sessão aiohttp: {type(exc).__name__}: {exc}")
            return None

        print(f"Busca web '{clean_query}' falhou: nenhum link válido. Fallback para texto.")
        return None

    async def fetch_image_safely(
        self,
        http: aiohttp.ClientSession,
        url: str,
    ) -> bytes | None:
        """
        Baixa uma imagem direta por URL sem derrubar o bot.

        Mesmo sendo usada para URLs manuais, reaproveita a mesma blindagem:
        - URL HTTP/HTTPS obrigatória.
        - headers robustos.
        - timeout curto.
        - ssl=False.
        - valida Content-Type.
        - purifica via Pillow em RGBA.
        """

        try:
            return await self._download_and_purify_image(
                session=http,
                url=url,
                label="URL direta",
                timeout_seconds=self.IMAGE_DOWNLOAD_TIMEOUT,
            )
        except Exception as exc:
            print(f"URL direta falhou: erro inesperado {type(exc).__name__}: {exc}")
            return None

    async def _download_and_purify_image(
        self,
        *,
        session: aiohttp.ClientSession,
        url: str,
        label: str,
        timeout_seconds: float,
    ) -> bytes | None:
        """
        Baixa bytes e só devolve se forem uma imagem real validada pelo Pillow.
        Qualquer falha retorna None.
        """

        clean_url = str(url or "").strip()

        if not clean_url:
            print(f"{label} falhou: URL vazia.")
            return None

        if not clean_url.startswith("http"):
            print(f"{label} falhou: URL não HTTP.")
            return None

        lowered_url = clean_url.lower()

        if lowered_url.startswith("data:"):
            print(f"{label} falhou: URL base64/data URI ignorada.")
            return None

        if ".svg" in lowered_url.split("?")[0]:
            print(f"{label} falhou: SVG ignorado.")
            return None

        timeout = aiohttp.ClientTimeout(total=max(0.1, timeout_seconds))

        try:
            async with session.get(
                clean_url,
                allow_redirects=True,
                max_redirects=5,
                timeout=timeout,
                ssl=False,
            ) as response:

                if response.status != 200:
                    reason = response.reason or "HTTP error"
                    print(f"{label} falhou: {response.status} {reason}")
                    return None

                content_type = response.headers.get("Content-Type", "").lower().strip()

                if "image/" not in content_type:
                    print(f"{label} falhou: Content-Type inválido ({content_type or 'ausente'}).")
                    return None

                if "svg" in content_type:
                    print(f"{label} falhou: SVG ignorado ({content_type}).")
                    return None

                content_length = response.headers.get("Content-Length")

                if content_length:
                    try:
                        if int(content_length) > self.MAX_IMAGE_BYTES:
                            print(f"{label} falhou: imagem maior que {self.MAX_IMAGE_BYTES} bytes.")
                            return None
                    except ValueError:
                        pass

                data = bytearray()

                async for chunk in response.content.iter_chunked(64 * 1024):
                    data.extend(chunk)

                    if len(data) > self.MAX_IMAGE_BYTES:
                        print(f"{label} falhou: imagem excedeu {self.MAX_IMAGE_BYTES} bytes.")
                        return None

                if not data:
                    print(f"{label} falhou: resposta vazia.")
                    return None

                purified = await asyncio.to_thread(
                    self._purify_image_bytes,
                    bytes(data),
                    label,
                )

                return purified

        except asyncio.TimeoutError:
            print(f"{label} falhou: Timeout.")
            return None
        except aiohttp.TooManyRedirects:
            print(f"{label} falhou: redirecionamentos demais.")
            return None
        except aiohttp.ClientConnectorCertificateError:
            print(f"{label} falhou: erro de certificado SSL.")
            return None
        except aiohttp.ClientSSLError:
            print(f"{label} falhou: erro SSL.")
            return None
        except aiohttp.ClientResponseError as exc:
            print(f"{label} falhou: resposta HTTP inválida ({exc.status}).")
            return None
        except aiohttp.ClientError as exc:
            print(f"{label} falhou: erro de rede ({type(exc).__name__}).")
            return None
        except Exception as exc:
            print(f"{label} falhou: erro inesperado ({type(exc).__name__}: {exc}).")
            return None

    def _purify_image_bytes(
        self,
        raw_bytes: bytes,
        label: str,
    ) -> bytes | None:
        """
        Valida e purifica imagem usando Pillow.

        Saída sempre em PNG RGBA:
        - elimina metadados problemáticos;
        - normaliza paleta/transparência;
        - evita que o renderer receba bytes corrompidos;
        - usa EXIF transpose para corrigir rotação.
        """

        try:
            with Image.open(io.BytesIO(raw_bytes)) as probe:
                probe.verify()

            with Image.open(io.BytesIO(raw_bytes)) as img:
                img = ImageOps.exif_transpose(img)
                img = img.convert("RGBA")

                if img.width <= 0 or img.height <= 0:
                    print(f"{label} falhou: imagem com dimensões inválidas.")
                    return None

                if img.width * img.height > Image.MAX_IMAGE_PIXELS:
                    print(f"{label} falhou: imagem grande demais em pixels.")
                    return None

                # Reduz imagens gigantes antes de salvar em PNG, evitando bytes finais enormes.
                img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)

                output = io.BytesIO()
                img.save(output, format="PNG", optimize=True)
                purified = output.getvalue()

                if not purified:
                    print(f"{label} falhou: Pillow gerou saída vazia.")
                    return None

                if len(purified) > self.MAX_IMAGE_BYTES:
                    print(f"{label} falhou: PNG purificado ficou maior que {self.MAX_IMAGE_BYTES} bytes.")
                    return None

                return purified

        except UnidentifiedImageError:
            print(f"{label} falhou: Pillow não reconheceu o formato.")
            return None
        except OSError as exc:
            print(f"{label} falhou: imagem corrompida ({exc}).")
            return None
        except ValueError as exc:
            print(f"{label} falhou: imagem inválida ({exc}).")
            return None
        except Exception as exc:
            print(f"{label} falhou: erro no Pillow ({type(exc).__name__}: {exc}).")
            return None

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

    async def hydrate_tier_images(
        self,
        tiers_snapshot: OrderedDictType[str, list[TierItem]],
    ) -> OrderedDictType[str, list[TierItem]]:
        """
        Baixa todas as imagens antes do Pillow.

        Se qualquer URL falhar, o item segue com image_bytes=None
        e o renderer automaticamente usa card de texto.
        """

        timeout = aiohttp.ClientTimeout(total=self.IMAGE_DOWNLOAD_TIMEOUT)

        connector = aiohttp.TCPConnector(
            ssl=False,
            limit=self.IMAGE_DOWNLOAD_CONCURRENCY,
            limit_per_host=4,
            enable_cleanup_closed=True,
        )

        semaphore = asyncio.Semaphore(self.IMAGE_DOWNLOAD_CONCURRENCY)

        async with aiohttp.ClientSession(
            headers=self.IMAGE_REQUEST_HEADERS,
            timeout=timeout,
            connector=connector,
            raise_for_status=False,
        ) as http:

            async def hydrate_one(item: TierItem) -> None:
                if item.image_bytes:
                    return

                if not item.image_url:
                    return

                if not str(item.image_url).startswith("http"):
                    return

                async with semaphore:
                    item.image_bytes = await self.fetch_image_safely(http, item.image_url)

            tasks: list[asyncio.Task[None]] = []

            for items in tiers_snapshot.values():
                for item in items:
                    if item.image_url and not item.image_bytes:
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
            if item.image_url or item.image_bytes
        )

        embed = discord.Embed(
            title="🧩 Painel De Criação De Tier List",
            description=(
                f"**Título:** {discord.utils.escape_markdown(session.title)}\n"
                f"**Tiers:** {len(session.tiers)}\n"
                f"**Itens:** {total_items}/{self.MAX_ITEMS_PER_SESSION}\n"
                f"**Itens Com Imagem:** {image_items}\n\n"
                "Use os botões abaixo para configurar, adicionar itens e gerar a imagem final."
            ),
            color=discord.Color.from_rgb(155, 93, 229),
        )

        for tier in session.tiers:
            items = session.items.get(tier, [])

            preview_parts: list[str] = []

            for item in items[:8]:
                icon = "🖼️" if (item.image_url or item.image_bytes) else "📝"
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