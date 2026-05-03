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
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None


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
    Motor de renderização Premium/AAA Skeuomórfico para Tier Lists.
    Design UI/UX Avançado com Efeitos 3D, Iluminação, Sombras e Layout Dinâmico.
    """

    # ── Canvas e Layout ─────────────────────────────────────────
    OUTER_PADDING = 50
    TITLE_HEIGHT = 140
    FOOTER_HEIGHT = 80

    # ── Tiers ───────────────────────────────────────────────────
    TIER_LABEL_WIDTH = 180
    TIER_GAP = 20
    ROW_PADDING_X = 25
    ROW_PADDING_Y = 25

    # ── Itens ───────────────────────────────────────────────────
    ITEM_SIZE = 160          
    TEXT_ITEM_HEIGHT = 80    
    ITEM_GAP = 15
    ITEM_RADIUS = 20         

    # ── Cores Modernizadas (Vibrantes) ──────────────────────────
    TEXT_COLOR = (255, 255, 255)
    SHADOW_COLOR = (0, 0, 0, 180)
    CARD_BG = (45, 48, 56)
    CARD_BORDER = (80, 85, 100)
    
    TIER_COLORS = [
        (255, 60, 80),    # S  - Neon Red
        (255, 145, 0),    # A  - Vibrant Orange
        (250, 215, 30),   # B  - Bright Yellow
        (46, 213, 115),   # C  - Toxic Green
        (30, 144, 255),   # D  - Electric Blue
        (156, 95, 255),   # E  - Neon Purple
        (255, 71, 156),   # F  - Hot Pink
        (0, 210, 211),    # G  - Cyan
        (1, 235, 180),    # H  - Mint
        (200, 150, 255),  # I  - Lavender
    ]

    def __init__(self, font_path: str | None = None) -> None:
        self.font_path = font_path

    # ════════════════════════════════════════════════════════════
    #  MÉTODOS AUXILIARES: FONTES E CORES
    # ════════════════════════════════════════════════════════════

    def _font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Carrega fontes Premium externas ou fallback seguro."""
        candidates = []
        if self.font_path:
            candidates.append(self.font_path)
            
        # Tentativas de fontes premium locais/padrão (Linux/Windows)
        candidates.extend([
            "assets/fonts/Impact.ttf",
            "assets/fonts/Poppins-Black.ttf",
            "assets/fonts/Montserrat-ExtraBold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "arialbd.ttf"
        ])
        
        for p in candidates:
            try:
                if pathlib.Path(p).exists() or p == "arialbd.ttf":
                    return ImageFont.truetype(p, size)
            except Exception:
                continue
                
        # Fallback extremo caso nenhuma fonte exista
        print("[AVISO] Nenhuma fonte premium encontrada. Usando fallback padrão do Pillow.")
        return ImageFont.load_default()

    def _darken_color(self, color: tuple, factor: float = 0.7) -> tuple:
        return tuple(int(c * factor) for c in color[:3])

    def _lighten_color(self, color: tuple, factor: float = 1.3) -> tuple:
        return tuple(min(255, int(c * factor)) for c in color[:3])

    # ════════════════════════════════════════════════════════════
    #  RENDERIZAÇÃO: SKEUOMORFISMO E 3D
    # ════════════════════════════════════════════════════════════

    def _draw_skeuomorphic_background(self, w: int, h: int) -> Image.Image:
        """
        1. O Canvas e o Fundo (Background Skeuomórfico)
        Cria um gradiente radial escuro simulando iluminação central
        e uma borda metálica luxuosa ao redor do canvas.
        """
        bg = Image.new("RGBA", (w, h), (15, 15, 18, 255))
        draw = ImageDraw.Draw(bg)
        
        # Gradiente Radial
        cx, cy = w // 2, h // 2
        max_dist = math.hypot(cx, cy)
        center_color = (40, 42, 50)
        edge_color = (5, 5, 8)
        
        # Otimização: desenhar faixas horizontais simulando gradiente ou 
        # criar um radial suave. Radial real pixel-a-pixel é caro. 
        # Simulação via anéis ou overlay linear:
        grad = Image.new("RGBA", (w, h), (0,0,0,0))
        g_draw = ImageDraw.Draw(grad)
        for y in range(h):
            ratio = abs(y - cy) / cy
            r = int(center_color[0] * (1 - ratio) + edge_color[0] * ratio)
            g = int(center_color[1] * (1 - ratio) + edge_color[1] * ratio)
            b = int(center_color[2] * (1 - ratio) + edge_color[2] * ratio)
            g_draw.line([(0, y), (w, y)], fill=(r, g, b, 255))
            
        bg = Image.alpha_composite(bg, grad)
        
        # Borda de Quadro (Moldura metálica)
        b_draw = ImageDraw.Draw(bg)
        border_box = (10, 10, w - 10, h - 10)
        b_draw.rounded_rectangle(border_box, radius=20, outline=(85, 85, 95, 255), width=3)
        b_draw.rounded_rectangle((12, 12, w - 12, h - 12), radius=18, outline=(20, 20, 25, 255), width=1)
        
        return bg

    def _draw_3d_tier_label(self, draw: ImageDraw.ImageDraw, box: tuple, text: str, font: ImageFont.ImageFont, base_color: tuple) -> None:
        """
        2. Labels das Tiers (Efeito 3D e Brilho)
        Desenha o bloco esquerdo da Tier com chanfro e iluminação acrílica.
        """
        x1, y1, x2, y2 = box
        dark_color = self._darken_color(base_color, 0.6)
        light_color = self._lighten_color(base_color, 1.4)
        
        # Fundo Base (Sombra Inferior)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=15, fill=dark_color)
        draw.rectangle((x2 - 15, y1, x2, y2), fill=dark_color) # Cortar arredondamento direito
        
        # Chanfro Interno (Bevel)
        draw.rounded_rectangle((x1 + 4, y1 + 4, x2, y2 - 6), radius=12, fill=base_color)
        draw.rectangle((x2 - 12, y1 + 4, x2, y2 - 6), fill=base_color)
        
        # Inner Glow (Luz Superior)
        draw.line([(x1 + 10, y1 + 5), (x2, y1 + 5)], fill=light_color, width=2)
        
        # Texto Centralizado com Drop Shadow absoluto
        bb = draw.textbbox((0, 0), text, font=font)
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        
        tx = x1 + (x2 - x1 - tw) // 2 - bb[0]
        ty = y1 + (y2 - y1 - th) // 2 - bb[1]
        
        self._draw_text_with_shadow(draw, text, (int(tx), int(ty)), font, (255, 255, 255))

    def _draw_text_with_shadow(self, draw: ImageDraw.ImageDraw, text: str, pos: tuple, font: ImageFont.ImageFont, fill: tuple) -> None:
        """
        3. Tipografia Premium (Sombra e Contorno)
        """
        x, y = pos
        # Contorno / Stroke pesado para contraste
        stroke_color = (10, 10, 15)
        for dx, dy in [(-2,0), (2,0), (0,-2), (0,2), (-1,-1), (1,1), (-1,1), (1,-1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=stroke_color)
            
        # Sombra principal (+4, +4)
        draw.text((x + 4, y + 4), text, font=font, fill=self.SHADOW_COLOR)
        
        # Texto Final
        draw.text(pos, text, font=font, fill=fill)

    # ════════════════════════════════════════════════════════════
    #  CARDS E ITENS
    # ════════════════════════════════════════════════════════════

    def _draw_image_card(self, base: Image.Image, draw: ImageDraw.ImageDraw, item: "TierItem", box: tuple, font: ImageFont.ImageFont) -> None:
        """
        4. Os Cards dos Itens (As imagens baixadas)
        Mascara a imagem, adiciona borda de "Trading Card" e aplica Drop Shadow brutal.
        """
        x1, y1, x2, y2 = box
        sz = self.ITEM_SIZE

        try:
            # Proteção Absoluta de ponteiro e canais (conforme corrigido anteriormente)
            bio = io.BytesIO(item.image_bytes)
            bio.seek(0)
            raw = Image.open(bio).convert("RGBA")
            fitted = ImageOps.fit(raw, (sz, sz), method=Image.Resampling.LANCZOS)
        except Exception as e:
            print(f"[ERRO] Fallback visual para card {item.name}: {e}")
            self._draw_text_card(base, draw, item, box, font)
            return

        # Máscara de cantos arredondados Anti-Aliased
        ms = sz * 3
        mask_hq = Image.new("L", (ms, ms), 0)
        ImageDraw.Draw(mask_hq).rounded_rectangle((0, 0, ms, ms), radius=self.ITEM_RADIUS * 3, fill=255)
        mask = mask_hq.resize((sz, sz), Image.Resampling.LANCZOS)

        # 4. Simulação de Drop Shadow no Item
        # Desenhamos uma base preta na coordenada X+6, Y+6 usando a mesma máscara
        shadow_card = Image.new("RGBA", (sz, sz), (0, 0, 0, 150))
        shadow_card.putalpha(mask) # Transparência nativa + cantos redondos
        base.paste(shadow_card, (x1 + 6, y1 + 6), mask=shadow_card)

        # Montagem do Card Original
        card = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        card.paste(fitted, (0, 0), mask=fitted) # Respeita Alpha nativo
        
        # Borda Branca/Prateada ("Trading Card")
        c_draw = ImageDraw.Draw(card)
        c_draw.rounded_rectangle((0, 0, sz - 1, sz - 1), radius=self.ITEM_RADIUS, outline=(240, 240, 240, 255), width=2)
        
        # Multiplicamos o Alpha final com a máscara arredondada
        card.putalpha(ImageChops.multiply(card.getchannel('A'), mask))
        
        # Colagem Final no Z-Index superior ao da sombra
        base.paste(card, (x1, y1), mask=card)

        # Legenda do Item com sobreposição escura suave
        cap_h = 35
        overlay = Image.new("RGBA", (sz, cap_h), (0, 0, 0, 200))
        cap_y = y1 + sz - cap_h
        base.paste(overlay, (x1, cap_y), mask=overlay)
        
        # Usa Draw da Base para renderizar o texto
        cap_font = self._font(16, bold=True)
        bb = draw.textbbox((0, 0), item.name, font=cap_font)
        cx = x1 + (sz - (bb[2] - bb[0])) // 2 - bb[0]
        cy = cap_y + (cap_h - (bb[3] - bb[1])) // 2 - bb[1]
        self._draw_text_with_shadow(draw, item.name, (int(cx), int(cy)), cap_font, self.TEXT_COLOR)

    def _draw_text_card(self, base: Image.Image, draw: ImageDraw.ImageDraw, item: "TierItem", box: tuple, font: ImageFont.ImageFont) -> None:
        """Fallback ou Item exclusivo de texto."""
        x1, y1, x2, y2 = box
        
        # Sombra Projetada
        s_box = (x1 + 6, y1 + 6, x2 + 6, y2 + 6)
        draw.rounded_rectangle(s_box, radius=self.ITEM_RADIUS, fill=(0, 0, 0, 150))
        
        # Card
        draw.rounded_rectangle(box, radius=self.ITEM_RADIUS, fill=self.CARD_BG + (255,), outline=self.CARD_BORDER + (255,), width=2)
        
        # Quebra de texto simplificada
        words = item.name.split()
        lines, cur = [], ""
        for w in words:
            cand = w if not cur else f"{cur} {w}"
            if draw.textbbox((0, 0), cand, font=font)[2] <= (x2 - x1 - 20):
                cur = cand
            else:
                if cur: lines.append(cur)
                cur = w
        if cur: lines.append(cur)
        lines = lines[:2]
        
        lh = draw.textbbox((0, 0), "Ag", font=font)[3] - draw.textbbox((0, 0), "Ag", font=font)[1]
        total_h = len(lines) * lh + max(0, len(lines) - 1) * 3
        cy = y1 + ((y2 - y1) - total_h) // 2
        
        for line in lines:
            bb = draw.textbbox((0, 0), line, font=font)
            lx = x1 + (x2 - x1 - bb[2]) // 2
            self._draw_text_with_shadow(draw, line, (int(lx), int(cy - bb[1])), font, self.TEXT_COLOR)
            cy += lh + 3

    # ════════════════════════════════════════════════════════════
    #  MOTOR PRINCIPAL E LAYOUT MATEMÁTICO
    # ════════════════════════════════════════════════════════════

    def calculate_tierlist_dimensions(self, tiers_dict: dict, min_width: int = 800, max_width: int = 1920) -> dict:
        """
        5. Matemática de Layout Dinâmico e "Z-Index"
        Calcula o height perfeito antes de alocar a imagem em memória.
        """
        max_items = max((len(items) for items in tiers_dict.values()), default=1)
        max_items = max(1, max_items)

        ideal_width = (
            self.OUTER_PADDING * 2
            + self.TIER_LABEL_WIDTH
            + self.ROW_PADDING_X * 2
            + (max_items * self.ITEM_SIZE)
            + ((max_items - 1) * self.ITEM_GAP)
        )
        final_width = max(min_width, min(max_width, ideal_width))

        items_area_w = final_width - (self.OUTER_PADDING * 2) - self.TIER_LABEL_WIDTH - (self.ROW_PADDING_X * 2)
        per_line = max(1, math.floor((items_area_w + self.ITEM_GAP) / (self.ITEM_SIZE + self.ITEM_GAP)))

        row_layouts = []
        accumulated_h = 0

        for idx, (tier_name, items) in enumerate(tiers_dict.items()):
            color = self.TIER_COLORS[idx % len(self.TIER_COLORS)]
            lines_data = []
            
            if not items:
                lines_data.append(self.TEXT_ITEM_HEIGHT)
            else:
                for i in range(0, len(items), per_line):
                    chunk = items[i : i + per_line]
                    # Se há alguma imagem na linha, a altura é ITEM_SIZE, senão TEXT_ITEM_HEIGHT
                    line_h = self.ITEM_SIZE if any(it.image_bytes for it in chunk) else self.TEXT_ITEM_HEIGHT
                    lines_data.append(line_h)

            row_h = self.ROW_PADDING_Y * 2 + sum(lines_data) + (len(lines_data) - 1) * self.ITEM_GAP
            row_layouts.append({
                "tier": tier_name,
                "color": color,
                "items": items,
                "per_line": per_line,
                "line_heights": lines_data,
                "row_height": row_h,
            })
            accumulated_h += row_h + self.TIER_GAP

        padding_y_extra = self.OUTER_PADDING
        final_height = padding_y_extra + self.TITLE_HEIGHT + accumulated_h + self.FOOTER_HEIGHT + padding_y_extra

        return {
            "canvas_w": final_width,
            "canvas_h": final_height,
            "row_layouts": row_layouts,
            "padding_y_extra": padding_y_extra,
        }

    def generate_tierlist_image(self, title: str, tiers_dict: dict, *, creator_name: str = "", guild_icon_bytes: bytes | None = None) -> io.BytesIO:
        """Fluxo Principal: Ordena o Z-Index do esqueleto da imagem."""
        title_font = self._font(65, bold=True)
        tier_font = self._font(55, bold=True)
        item_font = self._font(24, bold=True)
        footer_font = self._font(22, bold=False)

        # Pré-Cálculo
        layout = self.calculate_tierlist_dimensions(tiers_dict)
        cw, ch = layout["canvas_w"], layout["canvas_h"]
        
        # 1º Fundo global com textura
        image = self._draw_skeuomorphic_background(cw, ch)
        draw = ImageDraw.Draw(image)

        # ── TÍTULO PREMIUM ──
        ty = layout["padding_y_extra"]
        bb = draw.textbbox((0, 0), title, font=title_font)
        tx = (cw - (bb[2] - bb[0])) // 2 - bb[0]
        self._draw_text_with_shadow(draw, title, (int(tx), int(ty)), title_font, self.TEXT_COLOR)

        # ── RENDERIZAÇÃO DAS TIERS (Z-Index) ──
        y = ty + self.TITLE_HEIGHT + 20

        for row in layout["row_layouts"]:
            rh = row["row_height"]
            color = row["color"]
            rx1, ry1 = self.OUTER_PADDING, y
            rx2, ry2 = cw - self.OUTER_PADDING, y + rh

            # 3º Sombras de fundo e Fundo da Fileira
            draw.rounded_rectangle((rx1 + 8, ry1 + 8, rx2 + 8, ry2 + 8), radius=22, fill=(0,0,0, 100))
            draw.rounded_rectangle((rx1, ry1, rx2, ry2), radius=20, fill=(35, 38, 45, 255))
            
            # Divisória escura do conteúdo da fileira (profundidade)
            draw.rounded_rectangle((rx1 + self.TIER_LABEL_WIDTH + 10, ry1 + 10, rx2 - 10, ry2 - 10), radius=15, fill=(28, 30, 36, 255))

            # 4º As caixas das Tiers (labels) com efeito 3D
            lx1, ly1 = rx1, ry1
            lx2, ly2 = rx1 + self.TIER_LABEL_WIDTH, ry2
            self._draw_3d_tier_label(draw, (lx1, ly1, lx2, ly2), row["tier"], tier_font, color)

            # 5º e 6º Itens e Sombras de Cards
            ix_start = lx2 + self.ROW_PADDING_X
            iy_start = ry1 + self.ROW_PADDING_Y
            
            if not row["items"]:
                self._draw_text_with_shadow(draw, "Área Vazia", (ix_start + 20, iy_start), item_font, (120, 120, 130))
            else:
                for ii, item in enumerate(row["items"]):
                    r_idx = ii // row["per_line"]
                    c_idx = ii % row["per_line"]

                    offset_y = sum(row["line_heights"][:r_idx]) + r_idx * self.ITEM_GAP
                    item_h = self.ITEM_SIZE if item.image_bytes else self.TEXT_ITEM_HEIGHT

                    cx1 = ix_start + c_idx * (self.ITEM_SIZE + self.ITEM_GAP)
                    cy1 = iy_start + offset_y
                    cx2 = cx1 + self.ITEM_SIZE
                    cy2 = cy1 + item_h

                    if item.image_bytes:
                        self._draw_image_card(image, draw, item, (cx1, cy1, cx2, cy2), item_font)
                    else:
                        self._draw_text_card(image, draw, item, (cx1, cy1, cx2, cy2), item_font)

            y += rh + self.TIER_GAP

        # 6. Marca D'água / Rodapé Estilizado
        fw = "Gerado por Baphomet • Motor Skeuomórfico"
        f_bb = draw.textbbox((0,0), fw, font=footer_font)
        fx = (cw - (f_bb[2] - f_bb[0])) // 2 - f_bb[0]
        fy = ch - self.FOOTER_HEIGHT - 10
        # Texto com 50% de opacidade (Simulado através da cor base do fundo)
        self._draw_text_with_shadow(draw, fw, (int(fx), int(fy)), footer_font, (150, 150, 160))

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

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
        # Prevenção Absoluta de Timeouts: Avisa ao Discord que o processamento será longo
        # OBRIGATÓRIO DEFER ANTES DE TUDO
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
        
        image_bytes = None

        # Hierarquia Estrita de Prioridade
        # Prioridade 1: Usa a URL informada
        if clean_url:
            if not self.cog.looks_like_url(clean_url):
                clean_url = None
        # Prioridade 2: Sem URL, mas com ID informado. Tenta baixar avatar via Discord API
        elif user_id_str:
            try:
                user_id = int(user_id_str)
                user = await interaction.client.fetch_user(user_id)
                clean_url = user.display_avatar.replace(format='png', size=256).url
            except (ValueError, discord.NotFound, discord.HTTPException):
                clean_url = None
        # Prioridade 3: Sem URL ou ID, mas com Pesquisa Web solicitada. Busca a imagem oficial da Wikipedia!
        elif web_search_str:
            print(f"[DEBUG] Iniciando busca na Wikipedia pelo termo: '{web_search_str}'")
            
            async with aiohttp.ClientSession() as http:
                fetched_bytes = await self.cog.fetch_wikipedia_image(http, web_search_str)
                if fetched_bytes:
                    image_bytes = fetched_bytes
                    clean_url = "(Pesquisa Wikipedia Automática)"  # Fallback estético para o log interno
                else:
                    print(f"[ERRO] Wikipedia retornou falha total para '{web_search_str}'. Aplicando fallback para texto.")

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


class EditTitleModal(discord.ui.Modal):
    def __init__(self, current_title: str, view_instance: "TierListControlView") -> None:
        super().__init__(title="Editar Nome da Tier List", timeout=5 * 60)
        self.view_instance = view_instance
        self.old_title = current_title
        
        self.new_title = discord.ui.TextInput(
            label="Novo Título",
            style=discord.TextStyle.short,
            placeholder="Digite o novo título da Tier List...",
            default=current_title,  # Injeta o título atual para UX
            required=True,
            min_length=1,
            max_length=100,
        )
        self.add_item(self.new_title)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Passo 1: Prevenção de Timeout
        await interaction.response.defer()

        # Passo 2: Atualização de Estado
        session = self.view_instance.cog.sessions.get(self.view_instance.owner_id)
        if not session:
            await interaction.followup.send("❌ Essa sessão expirou.", ephemeral=True)
            return

        new_val = str(self.new_title.value).strip()
        session.title = new_val

        # Passo 3 e 4: Regeração Visual e Edição da Mensagem
        try:
            # Tenta atualizar o painel principal (isso recria o Embed com o novo título)
            await self.view_instance.cog.refresh_panel(session)
            await interaction.followup.send("✅ Título atualizado com sucesso!", ephemeral=True)
        except Exception as e:
            # Preservação Absoluta de Dados (Blindagem) em caso de falha visual
            session.title = self.old_title
            print(f"[ERRO] Falha ao atualizar título no painel: {e}")
            await interaction.followup.send("❌ Houve um erro ao atualizar o painel. O título foi revertido.", ephemeral=True)


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

    async def fetch_wikipedia_image(self, http: aiohttp.ClientSession, query: str) -> bytes | None:
        """
        Pesquisa o artigo na Wikipedia e extrai a imagem principal (thumbnail), garantindo alta estabilidade e sem Rate Limits.
        """
        # Header Obrigatório pela Wikimedia Foundation
        headers = {
            "User-Agent": "BaphometTierList/1.0",
            "Accept": "application/json"
        }
        
        base_url = "https://pt.wikipedia.org/w/api.php"
        timeout = aiohttp.ClientTimeout(total=5)

        try:
            print(f"[DEBUG] Buscando artigo na Wikipedia para: '{query}'")
            # Passo 1: Resolução do Título
            search_params = {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json"
            }
            
            async with http.get(base_url, params=search_params, headers=headers, timeout=timeout) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                
            search_results = data.get("query", {}).get("search", [])
            if not search_results:
                print(f"[ERRO] Wikipedia: Nenhum artigo encontrado para '{query}'.")
                return None
                
            titulo_artigo = search_results[0].get("title")
            if not titulo_artigo:
                return None
                
            print(f"[DEBUG] Artigo encontrado: '{titulo_artigo}'. Buscando thumbnail...")

            # Passo 2: Extração da Thumbnail
            image_params = {
                "action": "query",
                "prop": "pageimages",
                "titles": titulo_artigo,
                "pithumbsize": 500,
                "format": "json"
            }
            
            async with http.get(base_url, params=image_params, headers=headers, timeout=timeout) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                
            pages = data.get("query", {}).get("pages", {})
            if not pages:
                return None
                
            # Extrai o primeiro resultado do dicionário de páginas
            page_info = next(iter(pages.values()), {})
            image_url = page_info.get("thumbnail", {}).get("source")
            
            if not image_url:
                print(f"[ERRO] Wikipedia: O artigo '{titulo_artigo}' não possui imagem principal.")
                return None
                
            # Filtro de Extensão Restrita
            if image_url.lower().endswith(".svg") or "ambox" in image_url.lower():
                print(f"[ERRO] Wikipedia: Imagem rejeitada (SVG / Ícone de Sistema) -> {image_url}")
                return None

            print(f"[DEBUG] Fazendo download da imagem oficial: {image_url}")
            
            # Passo 3: Download da Imagem e Sanitização no Pillow
            async with http.get(image_url, headers=headers, timeout=timeout) as resp:
                if resp.status != 200:
                    return None
                    
                image_data = await resp.read()
                
                try:
                    # Tenta abrir para validar se os bytes são realmente decodificáveis
                    img_test = Image.open(io.BytesIO(bytes(image_data)))
                    # Força o RGBA como pedido e garante que a imagem sobrevive à conversão
                    # (A conversão real para o fluxo principal ocorre no TierListRenderer._draw_image_card)
                    img_test.convert('RGBA')
                except Exception as e:
                    print(f"[ERRO] Pillow recusou o arquivo da Wikipedia | Motivo: {e}")
                    return None
                    
                print(f"[DEBUG] Sucesso! Imagem da Wikipedia de {len(image_data)} bytes pronta.")
                return bytes(image_data)

        except Exception as e:
            print(f"[ERRO] Falha silenciosa no motor da Wikipedia: {e}")
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
        Baixa uma imagem sem derrubar o bot, operando com resiliência total.

        Regras de Negócio e Segurança:
        - Spoofing de User-Agent real para bypass de firewalls básicos (Erro 403).
        - Suporte a redirecionamentos (allow_redirects=True) para novos CDNs (ex: Discord).
        - Strict timeout individual de 5 segundos para não prender a queue de processamento.
        - Verificação rigorosa do 'Content-Type' -> Deve começar com 'image/'.
        - Captura de ClientError e TimeoutError isolada (Safe fallback para texto se falhar).
        """

        if not self.looks_like_url(url):
            return None

        # 1. Bypass de Firewalls Básicos (Cloudflare / CDN Blocks)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        }

        # 4. Gestão Rigorosa de Timeouts (5s limite rígido)
        timeout = aiohttp.ClientTimeout(total=5)

        try:
            # 2. allow_redirects=True garante o fetch seguro em CDN do Discord e afins
            async with http.get(url, headers=headers, allow_redirects=True, timeout=timeout) as response:
                if response.status != 200:
                    return None

                # 3. Validação de Content-Type (Evita abrir HTML como imagem no Pillow)
                content_type = response.headers.get("Content-Type", "").lower()
                if "image/" not in content_type:
                    return None

                data = bytearray()
                
                # Fetch iterativo seguro para proteção de memória
                async for chunk in response.content.iter_chunked(64 * 1024):
                    data.extend(chunk)
                    if len(data) > getattr(self, 'MAX_IMAGE_BYTES', 5 * 1024 * 1024):  # Fallback seguro para 5MB
                        return None

                if not data:
                    return None

                # Retorno seguro em bytes brutos, que será instanciado em io.BytesIO() no Pillow
                return bytes(data)

        # Tratamento cirúrgico de quebras de rede (Timeout e Falha de HTTP)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None
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