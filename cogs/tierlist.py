from __future__ import annotations

import asyncio
import io
import math
import pathlib
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import OrderedDict as OrderedDictType
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

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
    Motor visual Sleek Modern Minimalist para Tier Lists do Baphomet.

    Esta classe trata exclusivamente do canvas Pillow: layout, mascaras,
    sombras suaves e desenho final. A ingestao de imagens por URL, Discord ID
    ou Wikipedia continua fora daqui e chega como `TierItem.image_bytes`.
    """

    CANVAS_BG = (17, 17, 25, 255)       # #111119
    ROW_BG = (29, 29, 37, 255)          # #1D1D25
    ITEM_BG = (48, 55, 70, 255)         # #303746
    ITEM_OUTLINE = (74, 82, 111, 255)   # #4A526F
    TEXT = (246, 247, 250, 255)
    TEXT_DARK = (17, 17, 25, 255)
    TEXT_MUTED = (174, 183, 196, 255)
    FOOTER_LINE = (61, 62, 70, 255)

    # Cores extraidas do mock anexado.
    TIER_COLORS = [
        (255, 67, 88),
        (255, 164, 5),
        (236, 208, 101),
        (115, 229, 146),
        (107, 155, 242),
        (153, 126, 246),
        (235, 111, 184),
        (86, 190, 205),
    ]

    CANVAS_WIDTH = 800
    GRID_X = 40
    GRID_RIGHT = 760
    TITLE_TOP = 28
    ROWS_TOP = 160
    ROW_HEIGHT_BASE = 111
    ROW_GAP = 11
    ROW_RADIUS = 18
    ROW_PADDING_TOP = 21
    ROW_PADDING_BOTTOM = 20
    LABEL_WIDTH = 162
    LABEL_TO_ITEMS_GAP = 18
    MAX_ITEMS_PER_ROW = 8
    ITEM_WIDTH = 160
    ITEM_HEIGHT = 70
    ITEM_RADIUS = 12
    ITEM_GAP_X = 12
    ITEM_GAP_Y = 12
    ITEM_THUMB_SIZE = 48
    TEXT_ITEM_PADDING_X = 48
    TEXT_ITEM_PADDING_Y = 18
    TEXT_ITEM_LINE_GAP = 4
    IMAGE_ITEM_SIZE = 160
    IMAGE_ITEM_RADIUS = 18
    IMAGE_CAPTION_HEIGHT = 46
    FOOTER_TOP_GAP = 50
    FOOTER_AVATAR_TOP_GAP = 18
    FOOTER_AVATAR_SIZE = 63
    FOOTER_TEXT_X = 129
    FOOTER_BOTTOM = 30

    def __init__(self, font_path: str | None = None) -> None:
        self.font_path = font_path
        self._font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
        self._mask_cache: dict[tuple, Image.Image] = {}
        self._warned_font_fallback = False

    # ============================================================
    #  FONTES E MEDIDAS DE TEXTO
    # ============================================================

    def _font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """
        Carrega uma fonte sem serifa moderna via ImageFont.truetype().

        O cache evita reabrir arquivos TTF em cada card. Se as fontes do projeto
        nao existirem no host, a renderizacao continua com fallback do Pillow e
        emite um aviso unico no terminal.
        """
        cache_key = (size, bold)
        cached = self._font_cache.get(cache_key)
        if cached is not None:
            return cached

        repo_root = pathlib.Path(__file__).resolve().parents[1]
        font_dir = repo_root / "assets" / "fonts"
        candidates: list[str] = []

        if self.font_path:
            candidates.append(self.font_path)

        if bold:
            candidates.extend([
                str(font_dir / "Poppins-Bold.ttf"),
                str(font_dir / "Montserrat-Black.ttf"),
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "arialbd.ttf",
            ])
        else:
            candidates.extend([
                str(font_dir / "Poppins-Regular.ttf"),
                str(font_dir / "Poppins-Bold.ttf"),
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "arial.ttf",
            ])

        for candidate in candidates:
            try:
                font = ImageFont.truetype(candidate, size)
                self._font_cache[cache_key] = font
                return font
            except Exception:
                continue

        if not self._warned_font_fallback:
            print("[AVISO] Fonte premium nao encontrada. Usando fallback padrao do Pillow.")
            self._warned_font_fallback = True

        font = ImageFont.load_default()
        self._font_cache[cache_key] = font
        return font

    def _text_bbox(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ) -> tuple[int, int, int, int]:
        return draw.textbbox((0, 0), text, font=font)

    def _text_size(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ) -> tuple[int, int]:
        left, top, right, bottom = self._text_bbox(draw, text, font)
        return right - left, bottom - top

    def _center_text_position(
        self,
        draw: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ) -> tuple[int, int]:
        """Centraliza texto compensando bearings reais medidos por textbbox()."""
        x1, y1, x2, y2 = box
        left, top, right, bottom = self._text_bbox(draw, text, font)
        text_w = right - left
        text_h = bottom - top
        x = x1 + ((x2 - x1) - text_w) / 2 - left
        y = y1 + ((y2 - y1) - text_h) / 2 - top
        return int(x), int(y)

    def _fit_font(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        *,
        start_size: int,
        max_width: int,
        min_size: int,
        bold: bool,
    ) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Diminui a fonte ate o texto caber na largura disponivel."""
        for size in range(start_size, min_size - 1, -1):
            font = self._font(size, bold=bold)
            if self._text_size(draw, text, font)[0] <= max_width:
                return font
        return self._font(min_size, bold=bold)

    def _clip_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
    ) -> str:
        """Corta textos longos com reticencias ASCII sem quebrar o chip."""
        value = re.sub(r"\s+", " ", (text or "Item").strip()) or "Item"
        if self._text_size(draw, value, font)[0] <= max_width:
            return value

        suffix = "..."
        while value and self._text_size(draw, value + suffix, font)[0] > max_width:
            value = value[:-1]
        return value + suffix if value else suffix

    def _draw_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        pos: tuple[int, int],
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        fill: tuple[int, int, int, int],
        *,
        shadow_alpha: int = 80,
    ) -> None:
        """Texto flat com uma sombra minima para legibilidade em dark mode."""
        x, y = pos
        if shadow_alpha > 0:
            draw.text((x, y + 2), text, font=font, fill=(0, 0, 0, shadow_alpha))
        draw.text((x, y), text, font=font, fill=fill)

    def _create_background(self, width: int, height: int) -> Image.Image:
        """Cria o fundo solido do mock 1:1, sem gradientes ou textura."""
        return Image.new("RGBA", (width, height), self.CANVAS_BG)

    # ============================================================
    #  MASCARAS PILL-SHAPED E SOMBRAS DIFUSAS
    # ============================================================

    def _pill_mask(self, size: tuple[int, int]) -> Image.Image:
        """
        Cria mascara de pilula perfeita.

        Regra geometrica: radius = height / 2. A mascara e desenhada em 3x e
        reduzida com LANCZOS; isso deixa as laterais semicirculares sem serrilha.
        """
        width, height = size
        key = ("pill", width, height)
        cached = self._mask_cache.get(key)
        if cached is not None:
            return cached.copy()

        scale = 3
        hi_w, hi_h = width * scale, height * scale
        radius = hi_h // 2
        mask_hi = Image.new("L", (hi_w, hi_h), 0)
        ImageDraw.Draw(mask_hi).rounded_rectangle(
            (0, 0, hi_w - 1, hi_h - 1),
            radius=radius,
            fill=255,
        )
        mask = mask_hi.resize((width, height), Image.Resampling.LANCZOS)
        self._mask_cache[key] = mask
        return mask.copy()

    def _circle_mask(self, diameter: int) -> Image.Image:
        """Mascara circular perfeita para avatares/imagens dentro dos chips."""
        key = ("circle", diameter, diameter)
        cached = self._mask_cache.get(key)
        if cached is not None:
            return cached.copy()

        scale = 3
        hi = diameter * scale
        mask_hi = Image.new("L", (hi, hi), 0)
        ImageDraw.Draw(mask_hi).ellipse((0, 0, hi - 1, hi - 1), fill=255)
        mask = mask_hi.resize((diameter, diameter), Image.Resampling.LANCZOS)
        self._mask_cache[key] = mask
        return mask.copy()

    def _rounded_mask(self, size: tuple[int, int], radius: int) -> Image.Image:
        """Mascara anti-aliased para cards arredondados do item."""
        width, height = size
        key = ("round", width, height, radius)
        cached = self._mask_cache.get(key)
        if cached is not None:
            return cached.copy()

        scale = 3
        hi_w, hi_h = width * scale, height * scale
        mask_hi = Image.new("L", (hi_w, hi_h), 0)
        ImageDraw.Draw(mask_hi).rounded_rectangle(
            (0, 0, hi_w - 1, hi_h - 1),
            radius=radius * scale,
            fill=255,
        )
        mask = mask_hi.resize((width, height), Image.Resampling.LANCZOS)
        self._mask_cache[key] = mask
        return mask.copy()

    def _left_round_mask(self, size: tuple[int, int], radius: int) -> Image.Image:
        """Mascara com apenas os cantos esquerdos arredondados, igual ao label."""
        width, height = size
        key = ("left_round", width, height, radius)
        cached = self._mask_cache.get(key)
        if cached is not None:
            return cached.copy()

        scale = 3
        hi_w, hi_h = width * scale, height * scale
        hi_r = radius * scale
        mask_hi = Image.new("L", (hi_w, hi_h), 0)
        draw = ImageDraw.Draw(mask_hi)
        draw.rectangle((hi_r, 0, hi_w, hi_h), fill=255)
        draw.rectangle((0, hi_r, hi_r, hi_h - hi_r), fill=255)
        draw.pieslice((0, 0, hi_r * 2, hi_r * 2), 180, 270, fill=255)
        draw.pieslice((0, hi_h - hi_r * 2, hi_r * 2, hi_h), 90, 180, fill=255)
        mask = mask_hi.resize((width, height), Image.Resampling.LANCZOS)
        self._mask_cache[key] = mask
        return mask.copy()

    def _draw_soft_shadow(
        self,
        base: Image.Image,
        box: tuple[int, int, int, int],
        *,
        radius: int,
        offset: tuple[int, int] = (0, 10),
        blur: int = 28,
        opacity: int = 54,
    ) -> None:
        """
        Sombra moderna: alta dispersao, baixa opacidade.

        A profundidade do layout vem daqui. A sombra e renderizada em layer
        propria, borrada com GaussianBlur e composta antes do elemento principal.
        """
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1
        local_w = width + blur * 2 + abs(offset[0])
        local_h = height + blur * 2 + abs(offset[1])
        layer = Image.new("RGBA", (local_w, local_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")
        sx = blur + max(0, -offset[0])
        sy = blur + max(0, -offset[1])
        draw.rounded_rectangle(
            (sx, sy, sx + width, sy + height),
            radius=radius,
            fill=(0, 0, 0, opacity),
        )
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
        base.alpha_composite(
            layer,
            (x1 + offset[0] - blur - max(0, -offset[0]), y1 + offset[1] - blur - max(0, -offset[1])),
        )

    def _draw_item_shadow(
        self,
        base: Image.Image,
        box: tuple[int, int, int, int],
    ) -> None:
        """
        Sombra especifica dos itens, com offset Y positivo e blur 5.

        O item permanece visualmente elevado sobre o trilho, mas a sombra fica
        contida e organica: uma pilula preta translúcida, deslocada para baixo,
        borrada por GaussianBlur(radius=5) antes da colagem do chip real.
        """
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1
        blur = 5
        offset_y = 8
        pad = blur * 3
        layer = Image.new("RGBA", (width + pad * 2, height + pad * 2 + offset_y), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(layer, "RGBA")
        shadow_draw.rounded_rectangle(
            (pad, pad + offset_y, pad + width, pad + offset_y + height),
            radius=height // 2,
            fill=(0, 0, 0, 72),
        )
        layer = layer.filter(ImageFilter.GaussianBlur(radius=blur))
        base.alpha_composite(layer, (x1 - pad, y1 - pad))

    def _paste_pill(
        self,
        base: Image.Image,
        box: tuple[int, int, int, int],
        fill: tuple[int, int, int, int],
        *,
        outline: tuple[int, int, int, int] | None = None,
        outline_width: int = 1,
    ) -> None:
        """Desenha uma pilula flat usando mascara alpha de radius = height / 2."""
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1
        layer = Image.new("RGBA", (width, height), fill)
        mask = self._pill_mask((width, height))
        layer.putalpha(mask)

        if outline is not None and outline_width > 0:
            draw = ImageDraw.Draw(layer, "RGBA")
            draw.rounded_rectangle(
                (outline_width // 2, outline_width // 2, width - 1 - outline_width // 2, height - 1 - outline_width // 2),
                radius=height // 2,
                outline=outline,
                width=outline_width,
            )

        base.paste(layer, (x1, y1), mask=layer)

    def _paste_rounded_rect(
        self,
        base: Image.Image,
        box: tuple[int, int, int, int],
        fill: tuple[int, int, int, int],
        *,
        radius: int,
        outline: tuple[int, int, int, int] | None = None,
        outline_width: int = 1,
    ) -> None:
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1
        mask = self._rounded_mask((width, height), radius)
        layer = Image.new("RGBA", (width, height), fill)
        layer.putalpha(mask)

        if outline is not None and outline_width > 0:
            draw = ImageDraw.Draw(layer, "RGBA")
            draw.rounded_rectangle(
                (0, 0, width - 1, height - 1),
                radius=radius,
                outline=outline,
                width=outline_width,
            )

        base.paste(layer, (x1, y1), mask=layer)

    def _paste_left_round_rect(
        self,
        base: Image.Image,
        box: tuple[int, int, int, int],
        fill: tuple[int, int, int, int],
        *,
        radius: int,
    ) -> None:
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1
        mask = self._left_round_mask((width, height), radius)
        layer = Image.new("RGBA", (width, height), fill)
        layer.putalpha(mask)
        base.paste(layer, (x1, y1), mask=layer)

    # ============================================================
    #  IMAGENS E CHIPS DE ITENS
    # ============================================================

    def _safe_open_image(self, image_bytes: bytes | None, item_name: str) -> Image.Image:
        """
        Abre bytes em RGBA com tolerancia a formatos e ponteiros internos.

        BytesIO.seek(0), raw.seek(0) e convert("RGBA") preservam o conserto de
        fotos parcialmente lidas, GIF/WebP no frame errado e imagens sem alpha.
        """
        if not image_bytes:
            raise ValueError("item sem bytes de imagem")

        buffer = io.BytesIO(image_bytes)
        buffer.seek(0)
        try:
            with Image.open(buffer) as raw:
                try:
                    raw.seek(0)
                except Exception:
                    pass
                return raw.convert("RGBA")
        except Exception as exc:
            raise ValueError(f"imagem corrompida/ilegivel para {item_name}: {exc}") from exc

    def _item_has_image(self, item: "TierItem") -> bool:
        """A imagem altera o formato visual do item e entra no dry-run."""
        return bool(getattr(item, "image_bytes", None))

    def _item_display_name(self, item: "TierItem") -> str:
        """Nome opcional: retorna string vazia quando o item nao tiver legenda."""
        return re.sub(r"\s+", " ", str(getattr(item, "name", "") or "").strip())

    def _wrap_text_lines(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
    ) -> list[str]:
        """
        Quebra texto sem reduzir fonte e sem cortar conteudo.

        O algoritmo tenta preservar palavras; se uma palavra isolada for maior
        que a largura util, ela e quebrada em caracteres para permanecer dentro
        do card e do trilho.
        """
        words = text.split()
        if not words:
            return []

        lines: list[str] = []
        current = ""

        for word in words:
            candidate = f"{current} {word}".strip()
            if self._text_size(draw, candidate, font)[0] <= max_width:
                current = candidate
                continue

            if current:
                lines.append(current)
                current = ""

            if self._text_size(draw, word, font)[0] <= max_width:
                current = word
                continue

            chunk = ""
            for char in word:
                candidate = chunk + char
                if chunk and self._text_size(draw, candidate, font)[0] > max_width:
                    lines.append(chunk)
                    chunk = char
                else:
                    chunk = candidate
            current = chunk

        if current:
            lines.append(current)

        return lines

    def _measure_text_item(
        self,
        draw: ImageDraw.ImageDraw,
        item: "TierItem",
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        *,
        max_width: int,
    ) -> dict:
        """
        Mede um item textual antes do desenho.

        A largura cresce ate o texto caber inteiro. Quando o texto ultrapassa a
        largura util restante do trilho, ele quebra linhas e aumenta a altura
        do card, sem reduzir fonte e sem reticencias.
        """
        display_name = self._item_display_name(item)
        if not display_name:
            return {"width": 0, "height": 0, "lines": []}

        text_max_w = max(24, max_width - self.TEXT_ITEM_PADDING_X * 2)
        lines = self._wrap_text_lines(draw, display_name, font, text_max_w)
        line_widths = [self._text_size(draw, line, font)[0] for line in lines] or [0]
        line_heights = [self._text_size(draw, line, font)[1] for line in lines] or [0]

        text_w = max(line_widths)
        text_h = sum(line_heights) + max(0, len(lines) - 1) * self.TEXT_ITEM_LINE_GAP

        width = min(max_width, max(self.ITEM_WIDTH, text_w + self.TEXT_ITEM_PADDING_X * 2))
        height = max(self.ITEM_HEIGHT, text_h + self.TEXT_ITEM_PADDING_Y * 2)

        return {"width": int(width), "height": int(height), "lines": lines}

    def _draw_image_inside_chip(
        self,
        chip: Image.Image,
        item: "TierItem",
        *,
        tier_color: tuple[int, int, int],
    ) -> bool:
        """Recorta imagem em card arredondado e cola dentro do item."""
        image_x = 11
        image_y = (self.ITEM_HEIGHT - self.ITEM_THUMB_SIZE) // 2

        try:
            raw = self._safe_open_image(item.image_bytes, item.name)
            fitted = ImageOps.fit(
                raw,
                (self.ITEM_THUMB_SIZE, self.ITEM_THUMB_SIZE),
                method=Image.Resampling.LANCZOS,
            )
        except Exception as exc:
            print(f"[ERRO] Fallback visual para item '{item.name}': {exc}")
            return False

        mask = self._rounded_mask((self.ITEM_THUMB_SIZE, self.ITEM_THUMB_SIZE), 9)
        fitted.putalpha(mask)
        chip.paste(fitted, (image_x, image_y), mask=fitted)
        return True

    def _draw_image_square_item(
        self,
        base: Image.Image,
        item: "TierItem",
        box: tuple[int, int, int, int],
        *,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ) -> None:
        """Desenha item com imagem como card quadrado, com legenda opcional."""
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1
        mask = self._rounded_mask((width, height), self.IMAGE_ITEM_RADIUS)

        try:
            raw = self._safe_open_image(item.image_bytes, self._item_display_name(item) or "item com imagem")
            card = ImageOps.fit(raw, (width, height), method=Image.Resampling.LANCZOS)
            card = card.convert("RGBA")
        except Exception as exc:
            print(f"[ERRO] Fallback visual para item '{self._item_display_name(item)}': {exc}")
            card = Image.new("RGBA", (width, height), self.ITEM_BG)

        card.putalpha(mask)
        draw = ImageDraw.Draw(card, "RGBA")
        draw.rounded_rectangle(
            (0, 0, width - 1, height - 1),
            radius=self.IMAGE_ITEM_RADIUS,
            outline=self.ITEM_OUTLINE,
            width=1,
        )

        caption = self._item_display_name(item)
        if caption:
            caption_box = (0, height - self.IMAGE_CAPTION_HEIGHT, width, height)
            draw.rectangle(caption_box, fill=(8, 13, 15, 178))
            caption_font = self._fit_font(
                draw,
                caption,
                start_size=getattr(font, "size", 22),
                max_width=width - 18,
                min_size=12,
                bold=True,
            )
            clipped = self._clip_text(draw, caption, caption_font, width - 18)
            tx, ty = self._center_text_position(draw, caption_box, clipped, caption_font)
            self._draw_text(draw, clipped, (tx, ty), caption_font, self.TEXT, shadow_alpha=120)

        base.paste(card, (x1, y1), mask=card)

    def _draw_item_chip(
        self,
        base: Image.Image,
        item: "TierItem",
        box: tuple[int, int, int, int],
        *,
        tier_color: tuple[int, int, int],
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ) -> None:
        """Desenha o card de item 160x70 do mock, com borda azulada sutil."""
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1

        if self._item_has_image(item):
            self._draw_image_square_item(base, item, box, font=font)
            return

        display_name = self._item_display_name(item)
        if not display_name:
            return

        chip = Image.new("RGBA", (width, height), self.ITEM_BG)
        chip.putalpha(self._rounded_mask((width, height), self.ITEM_RADIUS))
        draw = ImageDraw.Draw(chip, "RGBA")
        draw.rounded_rectangle(
            (0, 0, width - 1, height - 1),
            radius=self.ITEM_RADIUS,
            outline=self.ITEM_OUTLINE,
            width=1,
        )

        lines = self._wrap_text_lines(
            draw,
            display_name,
            font,
            max(24, width - self.TEXT_ITEM_PADDING_X * 2),
        )
        line_sizes = [self._text_size(draw, line, font) for line in lines]
        text_block_h = sum(line_h for _, line_h in line_sizes) + max(0, len(lines) - 1) * self.TEXT_ITEM_LINE_GAP
        line_y = (height - text_block_h) // 2

        for line, (line_w, line_h) in zip(lines, line_sizes):
            tx = (width - line_w) // 2
            self._draw_text(draw, line, (tx, line_y), font, self.TEXT, shadow_alpha=0)
            line_y += line_h + self.TEXT_ITEM_LINE_GAP

        base.paste(chip, (x1, y1), mask=chip)

    # ============================================================
    #  LINHAS, LABELS E RODAPE
    # ============================================================

    def _draw_row_container(
        self,
        base: Image.Image,
        box: tuple[int, int, int, int],
    ) -> None:
        """Desenha o trilho escuro com raio fixo igual ao mock."""
        self._paste_rounded_rect(base, box, self.ROW_BG, radius=self.ROW_RADIUS)

    def _draw_tier_label(
        self,
        base: Image.Image,
        row_box: tuple[int, int, int, int],
        tier_name: str,
        tier_color: tuple[int, int, int],
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ) -> None:
        """Label colorido com cantos esquerdos arredondados e direita reta."""
        row_x1, row_y1, _, row_y2 = row_box
        label_box = (row_x1, row_y1, row_x1 + self.LABEL_WIDTH, row_y2)
        self._paste_left_round_rect(base, label_box, (*tier_color, 255), radius=self.ROW_RADIUS)

        draw = ImageDraw.Draw(base, "RGBA")
        label_text = re.sub(r"\s+", "", (tier_name or "?").strip())[:3] or "?"
        label_font = font
        while self._text_size(draw, label_text, label_font)[0] > self.LABEL_WIDTH - 24 and getattr(label_font, "size", 18) > 18:
            label_font = self._font(getattr(label_font, "size", 42) - 2, bold=True)

        tx, ty = self._center_text_position(draw, label_box, label_text, label_font)
        self._draw_text(draw, label_text, (tx, ty), label_font, self.TEXT_DARK, shadow_alpha=0)

    def _draw_empty_state(
        self,
        base: Image.Image,
        box: tuple[int, int, int, int],
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ) -> None:
        """Tier vazia permanece visualmente limpa, sem texto placeholder."""
        return

    def _resolve_author_name(self, author: object | None, creator_name: str) -> str:
        """Extrai exatamente o nome de usuario da interacao quando disponivel."""
        raw_name = ""
        if author is not None:
            raw_name = str(getattr(author, "name", "") or "")
        if not raw_name:
            raw_name = str(creator_name or "usuario")
        raw_name = raw_name.strip().lstrip("@") or "usuario"
        return re.sub(r"\s+", "_", raw_name)[:40]

    def _draw_footer(
        self,
        base: Image.Image,
        *,
        usuario_autor: str,
        avatar_bytes: bytes | None,
    ) -> None:
        """Desenha o footer do mock: linha, avatar circular e assinatura."""
        draw = ImageDraw.Draw(base, "RGBA")
        data_atual = datetime.now().strftime("%d/%m/%Y %H:%M")
        footer_text = f"Gerado por Baphomet  •  Criado por @{usuario_autor}  •  {data_atual}"
        line_y = base.height - self.FOOTER_BOTTOM - self.FOOTER_AVATAR_SIZE - self.FOOTER_AVATAR_TOP_GAP
        avatar_y = line_y + self.FOOTER_AVATAR_TOP_GAP
        avatar_box = (self.GRID_X, avatar_y, self.GRID_X + self.FOOTER_AVATAR_SIZE, avatar_y + self.FOOTER_AVATAR_SIZE)

        draw.line((self.GRID_X, line_y, self.GRID_RIGHT, line_y), fill=self.FOOTER_LINE, width=2)
        self._draw_footer_avatar(base, avatar_box, avatar_bytes=avatar_bytes, usuario_autor=usuario_autor)

        footer_box = (
            self.FOOTER_TEXT_X,
            avatar_y,
            self.GRID_RIGHT,
            avatar_y + self.FOOTER_AVATAR_SIZE,
        )
        footer_font = self._fit_font(
            draw,
            footer_text,
            start_size=18,
            max_width=footer_box[2] - footer_box[0],
            min_size=12,
            bold=True,
        )
        _, ty = self._center_text_position(draw, footer_box, footer_text, footer_font)
        tx = footer_box[0]
        self._draw_text(draw, footer_text, (tx, ty), footer_font, self.TEXT_MUTED, shadow_alpha=25)

    def _draw_footer_avatar(
        self,
        base: Image.Image,
        box: tuple[int, int, int, int],
        *,
        avatar_bytes: bytes | None,
        usuario_autor: str,
    ) -> None:
        """Usa bytes ja recebidos pela chamada ou gera fallback local, sem rede."""
        x1, y1, x2, y2 = box
        size = x2 - x1
        mask = self._circle_mask(size)

        if avatar_bytes:
            try:
                raw = self._safe_open_image(avatar_bytes, "avatar do autor")
                avatar = ImageOps.fit(raw, (size, size), method=Image.Resampling.LANCZOS)
                avatar.putalpha(mask)
                base.paste(avatar, (x1, y1), mask=avatar)
                return
            except Exception:
                pass

        layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        avatar_draw = ImageDraw.Draw(layer, "RGBA")
        avatar_draw.ellipse((0, 0, size - 1, size - 1), fill=(70, 76, 92, 255))
        avatar_draw.ellipse((4, 4, size - 5, size - 5), fill=(106, 115, 137, 255))
        initial = (usuario_autor[:1] or "B").upper()
        font = self._font(26, bold=True)
        tx, ty = self._center_text_position(avatar_draw, (0, 0, size, size), initial, font)
        self._draw_text(avatar_draw, initial, (tx, ty), font, self.TEXT, shadow_alpha=0)
        layer.putalpha(mask)
        base.paste(layer, (x1, y1), mask=layer)

    # ============================================================
    #  LAYOUT RESPONSIVO E RENDERIZACAO FINAL
    # ============================================================

    def calculate_tierlist_dimensions(
        self,
        tiers_dict: dict,
        min_width: int = 800,
        max_width: int = 800,
    ) -> dict:
        """
        Calcula a matriz visual antes de instanciar Image.new().

        1. Mantem largura 800px, igual ao mock anexado.
        2. Calcula a capacidade real da matriz de itens:
           floor((largura_util + gap_x) / (item_w + gap_x)).
        3. Limita a capacidade por MAX_ITEMS_PER_ROW, mantendo controle do wrap.
        4. Expande cada tier por line_count = ceil(total_itens / itens_por_linha).
        5. Acumula Y sequencialmente: row_y_atual += row_height + row_gap.
        """
        canvas_w = max(min_width, min(max_width, self.CANVAS_WIDTH))
        items_x = self.GRID_X + self.LABEL_WIDTH + self.LABEL_TO_ITEMS_GAP
        items_right = self.GRID_RIGHT - 22
        items_area_w = max(1, items_right - items_x)
        scratch = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        scratch_draw = ImageDraw.Draw(scratch, "RGBA")
        item_font = self._font(22, bold=True)

        row_layouts: list[dict] = []
        current_y = self.ROWS_TOP
        for index, (tier_name, items) in enumerate(tiers_dict.items()):
            item_layouts: list[dict] = []
            line_heights: list[int] = []
            current_line = 0
            cursor_x = 0

            for item in items:
                if self._item_has_image(item):
                    item_w = self.IMAGE_ITEM_SIZE
                    item_h = self.IMAGE_ITEM_SIZE
                else:
                    measurement = self._measure_text_item(
                        scratch_draw,
                        item,
                        item_font,
                        max_width=items_area_w,
                    )
                    item_w = measurement["width"]
                    item_h = measurement["height"]

                if item_w <= 0 or item_h <= 0:
                    continue

                # Packing horizontal responsivo:
                # se o proximo item nao couber na linha atual, inicia outra
                # linha. A largura do texto nao e comprimida nem cortada.
                projected_x = cursor_x + (self.ITEM_GAP_X if cursor_x else 0) + item_w
                if cursor_x and projected_x > items_area_w:
                    current_line += 1
                    cursor_x = 0

                item_x_offset = cursor_x
                if len(line_heights) <= current_line:
                    line_heights.append(item_h)
                else:
                    line_heights[current_line] = max(line_heights[current_line], item_h)

                item_layouts.append(
                    {
                        "item": item,
                        "line": current_line,
                        "x_offset": item_x_offset,
                        "width": item_w,
                        "height": item_h,
                    }
                )
                cursor_x += item_w + self.ITEM_GAP_X

            if not line_heights:
                line_heights = [self.ITEM_HEIGHT]

            line_count = len(line_heights)
            line_offsets: list[int] = []
            accumulated_y = 0
            for line_height in line_heights:
                line_offsets.append(accumulated_y)
                accumulated_y += line_height + self.ITEM_GAP_Y

            dynamic_items_h = sum(line_heights) + max(0, line_count - 1) * self.ITEM_GAP_Y

            # A tier agora responde diretamente a altura do conteudo:
            # - texto puro fica na altura base do layout;
            # - imagens quadradas removem o padding vertical artificial;
            # - multiplas linhas somam apenas conteudo + gaps reais.
            row_h = max(self.ROW_HEIGHT_BASE, dynamic_items_h)
            content_y_offset = (row_h - dynamic_items_h) // 2

            row_box = (self.GRID_X, current_y, self.GRID_RIGHT, current_y + row_h)
            row_layouts.append({
                "tier": tier_name,
                "items": items,
                "item_layouts": item_layouts,
                "color": self.TIER_COLORS[index % len(self.TIER_COLORS)],
                "line_count": line_count,
                "line_heights": line_heights,
                "line_offsets": line_offsets,
                "content_y_offset": content_y_offset,
                "row_height": row_h,
                "row_y": current_y,
                "row_box": row_box,
            })
            current_y += row_h + self.ROW_GAP

        rows_end = current_y - self.ROW_GAP if row_layouts else self.ROWS_TOP
        footer_line_y = rows_end + self.FOOTER_TOP_GAP
        canvas_h = footer_line_y + self.FOOTER_AVATAR_TOP_GAP + self.FOOTER_AVATAR_SIZE + self.FOOTER_BOTTOM

        return {
            "canvas_w": int(canvas_w),
            "canvas_h": int(canvas_h),
            "row_layouts": row_layouts,
            "items_x": items_x,
            "items_right": items_right,
        }

    def generate_tierlist_image(
        self,
        title: str,
        tiers_dict: dict,
        *,
        author: object | None = None,
        creator_name: str = "",
        guild_icon_bytes: bytes | None = None,
    ) -> io.BytesIO:
        """
        Gera a imagem final em PNG.

        `guild_icon_bytes` e usado apenas como imagem ja pronta para o avatar
        circular do footer. Nenhum download e feito aqui.
        """
        usuario_autor = self._resolve_author_name(author, creator_name)
        layout = self.calculate_tierlist_dimensions(tiers_dict)
        canvas_w = layout["canvas_w"]
        canvas_h = layout["canvas_h"]

        image = self._create_background(canvas_w, canvas_h)
        draw = ImageDraw.Draw(image, "RGBA")

        title_text = re.sub(r"\s+", " ", (title or "Tier List").strip()) or "Tier List"
        title_box = (
            self.GRID_X,
            self.TITLE_TOP,
            self.GRID_RIGHT,
            self.ROWS_TOP - 58,
        )
        title_font = self._fit_font(
            draw,
            title_text,
            start_size=50,
            max_width=title_box[2] - title_box[0],
            min_size=28,
            bold=True,
        )
        tx, ty = self._center_text_position(draw, title_box, title_text, title_font)
        self._draw_text(draw, title_text, (tx, ty), title_font, self.TEXT, shadow_alpha=0)

        tier_font = self._font(48, bold=True)
        item_font = self._font(22, bold=True)
        empty_font = self._font(21, bold=True)

        for row in layout["row_layouts"]:
            row_box = row["row_box"]
            self._draw_row_container(image, row_box)
            self._draw_tier_label(image, row_box, row["tier"], row["color"], tier_font)

            items_x = layout["items_x"]
            items_y = row_box[1] + row["content_y_offset"]

            if not row["item_layouts"]:
                self._draw_empty_state(
                    image,
                    (items_x, row_box[1], row_box[2], row_box[3]),
                    empty_font,
                )
            else:
                for item_layout in row["item_layouts"]:
                    item = item_layout["item"]
                    line = item_layout["line"]
                    item_x = items_x + item_layout["x_offset"]
                    item_w = item_layout["width"]
                    item_h = item_layout["height"]
                    line_h = row["line_heights"][line]
                    item_y = items_y + row["line_offsets"][line] + (line_h - item_h) // 2
                    self._draw_item_chip(
                        image,
                        item,
                        (item_x, item_y, item_x + item_w, item_y + item_h),
                        tier_color=row["color"],
                        font=item_font,
                    )

        self._draw_footer(image, usuario_autor=usuario_autor, avatar_bytes=guild_icon_bytes)

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
            placeholder="Opcional: Pizza, Minecraft, Billie...",
            min_length=0,
            max_length=25,
            required=False,
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

        if not clean_item and not clean_url and not image_bytes:
            await interaction.followup.send(
                "⚠️ Informe um nome ou alguma fonte de imagem para adicionar o item.",
                ephemeral=True,
            )
            return

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

        item_label = clean_item or "item com imagem"
        await interaction.followup.send(
            f"📌 Escolha em qual tier colocar **{discord.utils.escape_markdown(item_label)}**:{extra}",
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

        image_badge = " com imagem" if (self.item.image_url or self.item.image_bytes) else ""
        item_label = self.item.name or "item com imagem"

        await interaction.response.edit_message(
            content=(
                f"✅ **{discord.utils.escape_markdown(item_label)}**{image_badge} "
                f"foi adicionado em **{selected_tier}**."
            ),
            view=None,
        )


class EditTitleModal(discord.ui.Modal, title="Editar Nome da Tier List"):
    novo_titulo = discord.ui.TextInput(
        label="Novo Título",
        style=discord.TextStyle.short,
        required=True,
        min_length=1,
        max_length=100,
    )

    def __init__(self, current_title: str, view_instance: "TierListControlView") -> None:
        super().__init__()
        self.view_instance = view_instance
        self.novo_titulo.default = current_title

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Passo 1: Prevenção de Timeout
        await interaction.response.defer()

        # Passo 2: Atualização de Estado
        session = self.view_instance.cog.sessions.get(self.view_instance.owner_id)
        if not session:
            await interaction.followup.send("❌ Sessão expirada.", ephemeral=True)
            return

        old_title = session.title
        try:
            # Altera a variável na memória da sessão atual
            session.title = self.novo_titulo.value.strip()

            # Passo 3: Regeração Visual
            # Recria o Embed do painel que exibe o novo título na parte superior
            novo_embed = self.view_instance.cog.build_panel_embed(session)

            # Passo 4: Edição da Mensagem
            if session.panel_message:
                await session.panel_message.edit(embed=novo_embed, view=self.view_instance)
                
            await interaction.followup.send("✅ Título atualizado com sucesso!", ephemeral=True)
        except Exception as e:
            # Reverte em caso de falha visual para proteger os dados da sessão
            session.title = old_title
            await interaction.followup.send(f"❌ Falha ao atualizar o título: {e}", ephemeral=True)


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


class ItemSelectionSelect(discord.ui.Select):
    """Select paginado que transforma a escolha do usuario em abertura de modal."""

    def __init__(
        self,
        view_instance: "ItemSelectionView",
        page_refs: list[dict],
    ) -> None:
        self.view_instance = view_instance
        self.page_refs = page_refs

        options: list[discord.SelectOption] = []
        for local_index, ref in enumerate(page_refs):
            item: TierItem = ref["item"]
            display_name = view_instance.display_item_name(item)
            options.append(
                discord.SelectOption(
                    label=display_name[:100],
                    value=str(local_index),
                    description=f"Tier {ref['tier']} • posição {ref['index'] + 1}"[:100],
                    emoji="🖼️" if item.image_url or item.image_bytes else "📝",
                )
            )

        super().__init__(
            placeholder="Selecione o item que deseja editar",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_index = int(self.values[0])
        ref = self.page_refs[selected_index]

        # Select -> Modal:
        # O usuario nunca digita o nome defeituoso para localizar o item.
        # Ele escolhe uma referencia exata de tier + indice, e o modal abre
        # com os dados atuais ja preenchidos.
        await interaction.response.send_modal(
            EditItemModal(
                main_view=self.view_instance.main_view,
                tier_name=ref["tier"],
                item_index=ref["index"],
                item=ref["item"],
            )
        )


class ItemSelectionView(discord.ui.View):
    """View efemera que lista todos os itens da sessao em paginas de ate 25."""

    PAGE_SIZE = 25

    def __init__(
        self,
        main_view: "TierListControlView",
        *,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=120)
        self.main_view = main_view
        self.cog = main_view.cog
        self.owner_id = main_view.owner_id
        self.page = page
        self.item_refs = self.collect_item_refs()
        self.total_pages = max(1, math.ceil(len(self.item_refs) / self.PAGE_SIZE))
        self.page = max(0, min(self.page, self.total_pages - 1))

        page_start = self.page * self.PAGE_SIZE
        page_end = page_start + self.PAGE_SIZE
        page_refs = self.item_refs[page_start:page_end]

        if page_refs:
            self.add_item(ItemSelectionSelect(self, page_refs))

        self.previous_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.total_pages - 1

    def collect_item_refs(self) -> list[dict]:
        session = self.cog.sessions.get(self.owner_id)
        if not session:
            return []

        refs: list[dict] = []
        for tier in session.tiers:
            for index, item in enumerate(session.items.get(tier, [])):
                refs.append(
                    {
                        "tier": tier,
                        "index": index,
                        "item": item,
                    }
                )
        return refs

    def display_item_name(self, item: TierItem) -> str:
        name = re.sub(r"\s+", " ", (item.name or "").strip())
        if name:
            return name
        if item.image_url or item.image_bytes:
            return "item com imagem"
        return "item sem nome"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Só quem criou a tier list pode usar esse menu.",
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

    @discord.ui.button(label="Anterior", emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            content=f"✏️ Selecione o item para editar. Página {self.page}/{self.total_pages}",
            view=ItemSelectionView(self.main_view, page=self.page - 1),
        )

    @discord.ui.button(label="Próxima", emoji="➡️", style=discord.ButtonStyle.secondary)
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            content=f"✏️ Selecione o item para editar. Página {self.page + 2}/{self.total_pages}",
            view=ItemSelectionView(self.main_view, page=self.page + 1),
        )


class EditItemModal(discord.ui.Modal):
    """Modal preenchido com os dados atuais do item selecionado."""

    def __init__(
        self,
        *,
        main_view: "TierListControlView",
        tier_name: str,
        item_index: int,
        item: TierItem,
    ) -> None:
        super().__init__(title="Editar Item")
        self.main_view = main_view
        self.tier_name = tier_name
        self.item_index = item_index
        self.item = item

        self.item_name = discord.ui.TextInput(
            label="Nome do item",
            placeholder="Opcional",
            default=item.name or "",
            min_length=0,
            max_length=25,
            required=False,
        )
        default_url = item.image_url if item.image_url and main_view.cog.looks_like_url(item.image_url) else ""
        self.image_url = discord.ui.TextInput(
            label="URL da imagem",
            placeholder="Opcional: deixe vazio para remover/trocar por texto",
            default=default_url,
            min_length=0,
            max_length=500,
            required=False,
        )
        self.target_tier = discord.ui.TextInput(
            label="Tier",
            placeholder="Exemplo: S, A, B...",
            default=tier_name,
            min_length=1,
            max_length=20,
            required=True,
        )

        self.add_item(self.item_name)
        self.add_item(self.image_url)
        self.add_item(self.target_tier)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Defer obrigatório: mutação + possível hidratação + Pillow podem passar
        # do tempo limite de resposta inicial do Discord.
        await interaction.response.defer(ephemeral=True)

        session = self.main_view.cog.sessions.get(self.main_view.owner_id)
        if not session:
            await interaction.followup.send("❌ Sessão expirada.", ephemeral=True)
            return

        old_tier = self.tier_name
        new_tier = self.main_view.cog.clean_text(str(self.target_tier.value), max_length=20)
        new_name = self.main_view.cog.clean_text(str(self.item_name.value), max_length=25)
        new_url = str(self.image_url.value).strip() or None

        if new_tier not in session.items:
            await interaction.followup.send("❌ Essa tier não existe na sessão atual.", ephemeral=True)
            return

        if new_url and not self.main_view.cog.looks_like_url(new_url):
            await interaction.followup.send("❌ A URL informada não parece válida.", ephemeral=True)
            return

        if not new_name and not new_url and not self.item.image_bytes:
            await interaction.followup.send(
                "⚠️ O item precisa ter um nome ou uma imagem.",
                ephemeral=True,
            )
            return

        try:
            current_item = session.items[old_tier][self.item_index]
        except (KeyError, IndexError):
            await interaction.followup.send(
                "❌ Não encontrei esse item na sessão atual. Abra o menu de edição novamente.",
                ephemeral=True,
            )
            return

        if current_item is not self.item:
            await interaction.followup.send(
                "❌ A lista mudou desde que você abriu o menu. Abra o menu de edição novamente.",
                ephemeral=True,
            )
            return

        # Snapshot reversivel: se refresh/render falhar, o estado volta ao ponto
        # anterior e a sessão não fica parcialmente corrompida.
        old_name = current_item.name
        old_url = current_item.image_url
        old_bytes = current_item.image_bytes
        old_valid_url = old_url if old_url and self.main_view.cog.looks_like_url(old_url) else None
        moved_item: TierItem | None = None

        try:
            current_item.name = new_name
            current_item.image_url = new_url
            if new_url != old_valid_url:
                current_item.image_bytes = None

            if new_tier != old_tier:
                moved_item = session.items[old_tier].pop(self.item_index)
                session.items[new_tier].append(moved_item)

            await self.main_view.refresh_after_item_edit(interaction, session)

            await interaction.followup.send("✅ Item editado com sucesso.", ephemeral=True)

        except Exception:
            # Rollback completo: restaura campos e recoloca o item na tier/indice
            # original caso ele tenha sido movido antes da falha.
            rollback_item = moved_item or current_item
            if moved_item is not None:
                try:
                    session.items[new_tier].remove(moved_item)
                except (KeyError, ValueError):
                    pass
                session.items[old_tier].insert(self.item_index, moved_item)

            rollback_item.name = old_name
            rollback_item.image_url = old_url
            rollback_item.image_bytes = old_bytes

            await interaction.followup.send("❌ Falha ao editar o item.", ephemeral=True)


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

    async def refresh_after_item_edit(
        self,
        interaction: discord.Interaction,
        session: TierListSession,
    ) -> None:
        """
        Atualiza a mensagem principal depois da mutacao do item.

        A fonte da verdade continua sendo `session.items`. Para a imagem, criamos
        um snapshot descartavel e hidratado; assim bytes temporarios de download
        nao poluem a sessao em memoria.
        """
        if not session.panel_message:
            return

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

        hydrated_snapshot = await self.cog.hydrate_tier_images(tiers_snapshot)

        guild_icon_bytes = None
        if interaction.guild and interaction.guild.icon:
            try:
                guild_icon_bytes = await interaction.guild.icon.replace(format="png", size=128).read()
            except (discord.HTTPException, ValueError, TypeError):
                guild_icon_bytes = None

        image_buffer = await asyncio.to_thread(
            self.cog.renderer.generate_tierlist_image,
            session.title,
            hydrated_snapshot,
            author=interaction.user,
            guild_icon_bytes=guild_icon_bytes,
        )

        preview_file = discord.File(image_buffer, filename="tierlist_preview.png")
        await session.panel_message.edit(
            embed=self.cog.build_panel_embed(session),
            attachments=[preview_file],
            view=self,
        )

    @discord.ui.button(
        label="Editar Título",
        emoji="✏️",
        style=discord.ButtonStyle.secondary,
    )
    async def edit_title(
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

        # Abre o modal passando o título atual para carregar como default
        await interaction.response.send_modal(
            EditTitleModal(
                current_title=session.title,
                view_instance=self,
            )
        )

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
        label="Editar Item",
        emoji="✏️",
        style=discord.ButtonStyle.secondary,
    )
    async def edit_item(
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
                "⚠️ Não há itens para editar.",
                ephemeral=True,
            )
            return

        selection_view = ItemSelectionView(self)
        await interaction.response.send_message(
            f"✏️ Selecione o item para editar. Página {selection_view.page + 1}/{selection_view.total_pages}",
            view=selection_view,
            ephemeral=True,
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

            guild_icon_bytes = None
            if interaction.guild and interaction.guild.icon:
                try:
                    guild_icon_bytes = await interaction.guild.icon.replace(format="png", size=128).read()
                except (discord.HTTPException, ValueError, TypeError):
                    guild_icon_bytes = None

            # Pillow fora do event loop.
            image_buffer = await asyncio.to_thread(
                self.cog.renderer.generate_tierlist_image,
                title_snapshot,
                hydrated_snapshot,
                author=interaction.user,
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
        Pesquisa uma imagem principal na Wikipedia com fallback silencioso.

        Contrato inviolavel:
        - retorna bytes PNG normalizados em RGBA quando a imagem for segura;
        - retorna None para qualquer falha de busca, rede, JSON, SVG ou Pillow;
        - nunca levanta excecao para o fluxo principal do Discord.
        """
        headers = {
            "User-Agent": "TierListBot/2.0 (Bot Privado de Discord; Contato via Discord)",
            "Accept": "application/json",
        }

        base_url = "https://pt.wikipedia.org/w/api.php"
        timeout = aiohttp.ClientTimeout(total=4, connect=2)
        clean_query = (query or "").strip()

        def reject_wiki_asset(url: str) -> bool:
            """
            Bloqueia imagens que o Pillow nao decodifica ou que quase sempre sao
            artefatos internos da Wikimedia, nao imagem real do item pesquisado.
            """
            normalized = (url or "").strip().lower()
            parsed_path = urlparse(normalized).path
            return (
                not normalized
                or normalized.endswith(".svg")
                or parsed_path.endswith(".svg")
                or ".svg.png" in normalized
                or "ambox" in normalized
                or "wikimedia-button" in normalized
                or "disambig" in normalized
                or "question_book" in normalized
            )

        if not clean_query:
            print("[WIKI API] Busca vazia recebida; fallback para texto.")
            return None

        try:
            print(f"[WIKI API] Buscando artigo para: {clean_query}")

            # Passo 1: busca textual estrita. A Wikipedia pode retornar uma
            # pagina de redirecionamento como primeiro resultado; por isso esta
            # etapa apenas descobre o melhor titulo candidato.
            search_params = {
                "action": "query",
                "list": "search",
                "srsearch": clean_query,
                "format": "json",
            }

            async with http.get(base_url, params=search_params, headers=headers, timeout=timeout) as response:
                if response.status != 200:
                    print(f"[WIKI API] Erro ao buscar '{clean_query}': HTTP {response.status}")
                    return None
                search_data = await response.json(content_type=None)

            search_results = search_data.get("query", {}).get("search", [])
            if not search_results:
                print(f"[WIKI API] Erro ao processar '{clean_query}': busca sem resultados.")
                return None

            resolved_title = search_results[0].get("title")
            if not resolved_title:
                print(f"[WIKI API] Erro ao processar '{clean_query}': resultado sem title.")
                return None

            # Passo 2: extracao da imagem com redirects=1. A estrutura de pages
            # usa IDs numericos imprevisiveis; nunca acessamos uma chave fixa.
            image_params = {
                "action": "query",
                "prop": "pageimages",
                "titles": resolved_title,
                "pithumbsize": 500,
                "redirects": 1,
                "format": "json",
            }

            async with http.get(base_url, params=image_params, headers=headers, timeout=timeout) as response:
                if response.status != 200:
                    print(f"[WIKI API] Erro ao buscar thumbnail de '{resolved_title}': HTTP {response.status}")
                    return None
                image_data_json = await response.json(content_type=None)

            pages = image_data_json.get("query", {}).get("pages", {})
            if not pages:
                print(f"[WIKI API] Erro ao processar '{resolved_title}': sem dicionario pages.")
                return None

            page_ids = list(pages.keys())
            if not page_ids:
                print(f"[WIKI API] Erro ao processar '{resolved_title}': pages sem IDs.")
                return None

            first_page_id = page_ids[0]
            page_info = pages.get(first_page_id, {})
            if not isinstance(page_info, dict):
                print(f"[WIKI API] Erro ao processar '{resolved_title}': page_info invalido.")
                return None

            image_url = page_info.get("thumbnail", {}).get("source")
            if not image_url:
                print(f"[WIKI API] Erro ao processar '{resolved_title}': sem chave thumbnail.source.")
                return None

            # Passo 3: bloqueio preventivo de SVG e assets internos da Wiki.
            if reject_wiki_asset(image_url):
                print(f"[WIKI API] Imagem rejeitada para '{resolved_title}': asset SVG/sistema -> {image_url}")
                return None

            # Passo 4: download da imagem. Status nao-200 nunca e lido como
            # imagem; HTML de erro 404/503 nao deve chegar ao Pillow.
            async with http.get(image_url, headers=headers, timeout=timeout) as response:
                if response.status != 200:
                    print(f"[WIKI API] Erro ao baixar imagem de '{resolved_title}': HTTP {response.status}")
                    return None
                image_bytes = await response.read()

            if not image_bytes:
                print(f"[WIKI API] Erro ao processar '{resolved_title}': imagem vazia.")
                return None

            if len(image_bytes) > self.MAX_IMAGE_BYTES:
                print(f"[WIKI API] Erro ao processar '{resolved_title}': imagem maior que {self.MAX_IMAGE_BYTES} bytes.")
                return None

            # Passo 5: rito de passagem do byte. O seek(0) antes do Image.open
            # evita ponteiro perdido; convert('RGBA') normaliza paleta, alpha e
            # perfis estranhos antes do renderer receber os bytes.
            try:
                buffer = io.BytesIO(image_bytes)
                buffer.seek(0)
                with Image.open(buffer) as raw_image:
                    try:
                        raw_image.seek(0)
                    except Exception:
                        pass
                    purified_image = raw_image.convert("RGBA")

                output = io.BytesIO()
                purified_image.save(output, format="PNG")
                output.seek(0)
                normalized_bytes = output.getvalue()
            except Exception as exc:
                print(f"[WIKI API] Pillow recusou '{resolved_title}': {exc}")
                return None

            print(f"[WIKI API] Sucesso para '{clean_query}' -> '{resolved_title}' ({len(normalized_bytes)} bytes PNG RGBA).")
            return normalized_bytes

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            print(f"[WIKI API] Erro de rede/timeout ao processar '{clean_query}': {exc}")
            return None
        except Exception as exc:
            print(f"[WIKI API] Erro inesperado ao processar '{clean_query}': {exc}")
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
                has_image = bool(item.image_url or item.image_bytes)
                icon = "🖼️" if has_image else "📝"
                item_label = item.name or "item com imagem"
                preview_parts.append(f"{icon} {item_label}")

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
