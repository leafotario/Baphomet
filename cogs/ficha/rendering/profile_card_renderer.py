from __future__ import annotations

import asyncio
import io
import json
import math
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError


REMOVED_CONTENT = "[Conteúdo removido]"

try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE_LANCZOS = Image.LANCZOS


ColorA = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.right, self.bottom)

    @property
    def size(self) -> tuple[int, int]:
        return (self.w, self.h)

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    def inset(self, dx: int, dy: int) -> Rect:
        return Rect(self.x + dx, self.y + dy, max(1, self.w - dx * 2), max(1, self.h - dy * 2))


@dataclass(frozen=True, slots=True)
class ProfileRenderData:
    user_id: int
    username: str
    display_name: str
    pronouns: str | None = None
    rank_text: str | None = None
    ask_me_about: list[str] | None = None
    basic_info: str | None = None
    badge_name: str | None = None
    avatar_bytes: bytes | None = None
    badge_image_bytes: bytes | None = None
    bonds_count: int = 0
    bonds_multiplier: float = 1.0
    level: int = 0
    xp_current: int = 0
    xp_required: int = 1
    xp_total: int = 0
    xp_percent: float = 0.0


class FontManager:
    def __init__(
        self,
        *,
        regular_path: str = "assets/fonts/Poppins-Regular.ttf",
        bold_path: str = "assets/fonts/Poppins-Bold.ttf",
        display_path: str = "assets/fonts/Poppins-Bold.ttf",
    ) -> None:
        self.regular_path = regular_path
        self.bold_path = bold_path
        self.display_path = display_path
        self._cache: dict[tuple[int, str], ImageFont.ImageFont] = {}

    def font(self, size: int, weight: str = "regular") -> ImageFont.ImageFont:
        safe_size = max(1, int(size))
        normalized = str(weight or "regular").casefold()
        key = (safe_size, normalized)

        cached = self._cache.get(key)
        if cached is not None:
            return cached

        if normalized in {"bold", "semibold", "black"}:
            preferred = self.bold_path
            fallbacks = (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            )
        elif normalized in {"display", "title"}:
            preferred = self.display_path
            fallbacks = (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            )
        else:
            preferred = self.regular_path
            fallbacks = (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            )

        for path in (preferred, *fallbacks):
            try:
                font = ImageFont.truetype(path, size=safe_size)
                self._cache[key] = font
                return font
            except OSError:
                continue

        font = ImageFont.load_default()
        self._cache[key] = font
        return font


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _safe_rgba(color: Any, fallback: ColorA = (0, 0, 0, 255)) -> ColorA:
    if not isinstance(color, (tuple, list)):
        return fallback

    def ch(v: Any, fb: int) -> int:
        try:
            return max(0, min(255, int(round(float(v)))))
        except (TypeError, ValueError, OverflowError):
            return fb

    if len(color) == 3:
        return (ch(color[0], fallback[0]), ch(color[1], fallback[1]), ch(color[2], fallback[2]), 255)

    if len(color) >= 4:
        return (
            ch(color[0], fallback[0]),
            ch(color[1], fallback[1]),
            ch(color[2], fallback[2]),
            ch(color[3], fallback[3]),
        )

    return fallback


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    value = str(text or "")

    try:
        return int(math.ceil(float(draw.textlength(value, font=font))))
    except Exception:
        pass

    try:
        box = draw.textbbox((0, 0), value, font=font)
        return max(0, box[2] - box[0])
    except Exception:
        return 0


def text_height(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    value = str(text or "Ag")

    try:
        box = draw.textbbox((0, 0), value, font=font)
        return max(1, box[3] - box[1])
    except Exception:
        return 1


def truncate_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.ImageFont,
    max_width: int,
    *,
    ellipsis: str = "...",
) -> str:
    text = str(value or "")
    limit = max(0, int(max_width))

    if limit <= 0:
        return ""

    if text_width(draw, text, font) <= limit:
        return text

    if text_width(draw, ellipsis, font) > limit:
        return ""

    lo = 0
    hi = len(text)
    best = ellipsis

    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid].rstrip() + ellipsis

        if text_width(draw, candidate, font) <= limit:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1

    return best


def fit_font_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    *,
    font_loader: Any,
    start_size: int,
    min_size: int,
) -> ImageFont.ImageFont:
    safe_start = max(1, int(start_size))
    safe_min = max(1, min(int(min_size), safe_start))
    safe_max = max(1, int(max_width))

    for size in range(safe_start, safe_min - 1, -1):
        font = font_loader(size)
        if text_width(draw, text, font) <= safe_max:
            return font

    return font_loader(safe_min)


def draw_text_shadow(
    draw: ImageDraw.ImageDraw,
    pos: tuple[int, int],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: ColorA,
    shadow: ColorA = (0, 0, 0, 160),
    offset: tuple[int, int] = (2, 2),
    stroke_width: int = 0,
    stroke_fill: ColorA = (0, 0, 0, 0),
) -> None:
    x, y = pos
    ox, oy = offset
    safe_text = str(text or "")

    draw.text(
        (x + ox, y + oy),
        safe_text,
        font=font,
        fill=_safe_rgba(shadow),
        stroke_width=max(0, int(stroke_width)),
        stroke_fill=_safe_rgba(stroke_fill, (0, 0, 0, 0)),
    )
    draw.text(
        (x, y),
        safe_text,
        font=font,
        fill=_safe_rgba(fill),
        stroke_width=max(0, int(stroke_width)),
        stroke_fill=_safe_rgba(stroke_fill, (0, 0, 0, 0)),
    )


def draw_soft_shadow(
    canvas: Image.Image,
    rect: Rect,
    radius: int,
    *,
    offset: tuple[int, int] = (0, 8),
    blur: int = 18,
    color: ColorA = (0, 0, 0, 120),
    spread: int = 0,
) -> None:
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")

    box = (
        rect.x + offset[0] - spread,
        rect.y + offset[1] - spread,
        rect.right + offset[0] + spread,
        rect.bottom + offset[1] + spread,
    )

    draw.rounded_rectangle(box, radius=max(1, radius + spread), fill=_safe_rgba(color))
    blurred = layer.filter(ImageFilter.GaussianBlur(max(0, int(blur))))

    try:
        canvas.alpha_composite(blurred)
    finally:
        layer.close()
        blurred.close()


def _rounded_mask(size: tuple[int, int], radius: int, *, scale: int = 3) -> Image.Image:
    width, height = max(1, size[0]), max(1, size[1])
    scale = max(2, int(scale))

    big = Image.new("L", (width * scale, height * scale), 0)
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1),
        radius=max(1, radius * scale),
        fill=255,
    )

    mask = big.resize((width, height), RESAMPLE_LANCZOS)
    big.close()
    return mask


def paste_rounded(canvas: Image.Image, image: Image.Image, rect: Rect, radius: int) -> None:
    src = image.convert("RGBA") if image.mode != "RGBA" else image
    if src.size != rect.size:
        src = src.resize(rect.size, RESAMPLE_LANCZOS)

    mask = _rounded_mask(rect.size, radius, scale=3)

    try:
        canvas.paste(src, (rect.x, rect.y), mask)
    finally:
        mask.close()
        if src is not image:
            src.close()


def paste_centered(canvas: Image.Image, image: Image.Image, rect: Rect) -> None:
    src = image.convert("RGBA") if image.mode != "RGBA" else image.copy()
    src.thumbnail((rect.w, rect.h), RESAMPLE_LANCZOS)

    x = rect.x + (rect.w - src.width) // 2
    y = rect.y + (rect.h - src.height) // 2

    try:
        canvas.paste(src, (x, y), src)
    finally:
        src.close()


def add_noise_overlay(canvas: Image.Image, *, opacity: int = 1, seed: int = 0, scale: int = 2) -> None:
    if opacity <= 0:
        return

    noise = Image.effect_noise(canvas.size, 18)
    alpha = noise.convert("L").point(lambda p: max(0, min(255, int(opacity))))
    layer = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
    layer.putalpha(alpha)

    try:
        canvas.alpha_composite(layer)
    finally:
        noise.close()
        alpha.close()
        layer.close()


def load_rgba_from_bytes(image_bytes: bytes | bytearray | memoryview | None) -> Image.Image | None:
    if not image_bytes:
        return None

    try:
        with Image.open(io.BytesIO(bytes(image_bytes))) as image:
            image.load()
            return image.convert("RGBA").copy()
    except (OSError, ValueError, UnidentifiedImageError):
        return None


def circular_crop(source: Image.Image, size: int) -> Image.Image:
    safe_size = max(1, int(size))
    image = ImageOps.fit(source.convert("RGBA"), (safe_size, safe_size), method=RESAMPLE_LANCZOS)

    mask_big = Image.new("L", (safe_size * 3, safe_size * 3), 0)
    draw = ImageDraw.Draw(mask_big)
    draw.ellipse((0, 0, safe_size * 3 - 1, safe_size * 3 - 1), fill=255)
    mask = mask_big.resize((safe_size, safe_size), RESAMPLE_LANCZOS)

    output = Image.new("RGBA", (safe_size, safe_size), (0, 0, 0, 0))

    try:
        output.paste(image, (0, 0), mask)
        return output
    finally:
        image.close()
        mask_big.close()
        mask.close()


def create_avatar_placeholder(
    size: int,
    *,
    initials: str,
    font: ImageFont.ImageFont,
    fill_top: ColorA,
    fill_bottom: ColorA,
    accent: ColorA,
    text_fill: ColorA,
) -> Image.Image:
    image = Image.new("RGBA", (size, size), _safe_rgba(fill_bottom))
    draw = ImageDraw.Draw(image, "RGBA")

    for y in range(size):
        t = y / max(1, size - 1)
        top = _safe_rgba(fill_top)
        bottom = _safe_rgba(fill_bottom)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(4))
        draw.line((0, y, size, y), fill=color)

    draw.ellipse((size // 5, size // 5, size - size // 5, size - size // 5), outline=_safe_rgba(accent), width=max(2, size // 35))

    text = str(initials or "?").upper()[:2]
    tw = text_width(draw, text, font)
    th = text_height(draw, text, font)
    draw_text_shadow(
        draw,
        ((size - tw) // 2, (size - th) // 2 - 3),
        text,
        font=font,
        fill=_safe_rgba(text_fill),
        shadow=(0, 0, 0, 170),
        offset=(2, 2),
    )

    return circular_crop(image, size)


def create_badge_placeholder(
    size: tuple[int, int],
    *,
    fill_top: ColorA,
    fill_bottom: ColorA,
    accent: ColorA,
    line: ColorA,
) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size, _safe_rgba(fill_bottom))
    draw = ImageDraw.Draw(image, "RGBA")

    for y in range(height):
        t = y / max(1, height - 1)
        top = _safe_rgba(fill_top)
        bottom = _safe_rgba(fill_bottom)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(4))
        draw.line((0, y, width, y), fill=color)

    cx = width // 2
    cy = height // 2

    draw.ellipse((cx - 36, cy - 36, cx + 36, cy + 36), outline=_safe_rgba(line), width=3)
    draw.line((cx, cy - 44, cx, cy + 44), fill=_safe_rgba(accent), width=4)
    draw.line((cx - 42, cy, cx + 42, cy), fill=_safe_rgba(line), width=2)
    draw.polygon(
        [(cx, cy - 56), (cx + 16, cy - 30), (cx, cy - 18), (cx - 16, cy - 30)],
        fill=_safe_rgba(accent),
    )

    return image


def wrap_text_pixels(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = str(text or "").replace("\n", " ").split()

    if not words:
        return [""]

    lines: list[str] = []
    current = ""

    for word in words:
        candidate = word if not current else f"{current} {word}"

        if text_width(draw, candidate, font) <= max_width:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = word
        else:
            lines.append(truncate_text(draw, word, font, max_width))
            current = ""

    if current:
        lines.append(current)

    return lines or [""]


def clamp_lines_with_ellipsis(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
) -> list[str]:
    if max_lines <= 0:
        return []

    if len(lines) <= max_lines:
        return [truncate_text(draw, line, font, max_width) for line in lines]

    clamped = [truncate_text(draw, line, font, max_width) for line in lines[:max_lines]]
    clamped[-1] = truncate_text(draw, clamped[-1].rstrip(". ") + "...", font, max_width)
    return clamped


@dataclass(frozen=True, slots=True)
class ProfileCardTheme:
    background_fallback: ColorA = (42, 39, 48, 255)

    glass_overlay: ColorA = (18, 15, 24, 180)
    outer_card_fill: ColorA = (0, 0, 0, 105)
    inner_card_fill: ColorA = (0, 0, 0, 62)

    panel_fill: ColorA = (0, 0, 0, 82)
    panel_outline: ColorA = (255, 255, 255, 42)

    field_fill: ColorA = (0, 0, 0, 92)
    field_outline: ColorA = (255, 255, 255, 36)

    text: ColorA = (255, 255, 255, 255)
    text_soft: ColorA = (225, 225, 230, 255)
    text_muted: ColorA = (190, 190, 198, 255)

    rank_gold: ColorA = (255, 220, 50, 255)
    cyan: ColorA = (0, 255, 255, 255)

    bar_track: ColorA = (0, 0, 0, 135)
    shadow: ColorA = (0, 0, 0, 170)


@dataclass(frozen=True, slots=True)
class ProfileCardLayout:
    canvas: tuple[int, int] = (1500, 1000)

    outer_card: Rect = Rect(74, 58, 1352, 884)
    inner_card: Rect = Rect(116, 96, 1268, 808)

    avatar_medallion: Rect = Rect(156, 122, 308, 308)
    identity_panel: Rect = Rect(156, 448, 308, 416)
    ask_panel: Rect = Rect(488, 122, 512, 220)
    basic_panel: Rect = Rect(488, 366, 512, 282)
    badge_panel: Rect = Rect(1024, 122, 306, 290)
    bonds_panel: Rect = Rect(1024, 438, 306, 210)
    xp_panel: Rect = Rect(488, 674, 842, 190)

    outer_radius: int = 40
    inner_radius: int = 26
    panel_radius: int = 22
    section_pad: int = 28


class ProfileCardRenderer:
    """Renderer Pillow puro para gerar a ficha PNG em 3:2."""

    def __init__(
        self,
        *,
        layout: ProfileCardLayout | None = None,
        theme: ProfileCardTheme | None = None,
        fonts: FontManager | None = None,
    ) -> None:
        self.layout = layout or ProfileCardLayout()
        self.theme = theme or ProfileCardTheme()
        self.fonts = fonts or FontManager()
        self._accent: tuple[int, int, int] = (120, 220, 220)

    def render(self, profile: ProfileRenderData) -> bytes:
        """Renderiza a ficha completa e retorna bytes PNG prontos para Discord."""

        self._accent = self._extract_accent(profile)

        canvas = self._create_glass_background(profile).convert("RGBA")
        self._draw_main_frame(canvas)
        self._draw_avatar(canvas, profile)
        self._draw_identity(canvas, profile)
        self._draw_ask_me_about(canvas, profile)
        self._draw_basic_info(canvas, profile)
        self._draw_badge(canvas, profile)
        self._draw_bonds(canvas, profile)
        self._draw_xp_progress(canvas, profile)
        self._apply_finishing(canvas)

        output = io.BytesIO()

        try:
            canvas.convert("RGBA").save(output, format="PNG")
            return output.getvalue()
        finally:
            output.close()
            canvas.close()

    @staticmethod
    def _channel(value: Any, fallback: int = 0) -> int:
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError, OverflowError):
            number = fallback

        return max(0, min(255, number))

    def _rgba(self, color: Any, fallback: ColorA = (0, 0, 0, 255)) -> ColorA:
        return _safe_rgba(color, fallback)

    def _rgb(self, color: Any, fallback: tuple[int, int, int] = (120, 220, 220)) -> tuple[int, int, int]:
        rgba = self._rgba(color, (*fallback, 255))
        return rgba[:3]

    def _safe_load_external_image(self, image_bytes: bytes | bytearray | memoryview | None) -> Image.Image | None:
        return load_rgba_from_bytes(image_bytes)

    def _safe_paste(
        self,
        base: Image.Image,
        image: Image.Image,
        position: tuple[int, int],
        mask: Image.Image | None = None,
    ) -> None:
        src = image.convert("RGBA") if image.mode != "RGBA" else image

        if mask is not None:
            alpha_mask = mask.convert("L") if mask.mode != "L" else mask

            try:
                base.paste(src, position, alpha_mask)
            finally:
                if alpha_mask is not mask:
                    alpha_mask.close()
                if src is not image:
                    src.close()

            return

        try:
            base.paste(src, position, src)
        finally:
            if src is not image:
                src.close()

    def _font(self, size: int, weight: str = "regular") -> ImageFont.ImageFont:
        safe_size = max(1, int(size))

        try:
            font = self.fonts.font(safe_size, weight)
            if font is not None:
                return font
        except Exception:
            pass

        return FontManager().font(safe_size, weight)

    def _measure_width(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
        return text_width(draw, text, font)

    def _measure_height(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
        return text_height(draw, text, font)

    def _truncate_to_width(
        self,
        draw: ImageDraw.ImageDraw,
        value: str,
        font: ImageFont.ImageFont,
        max_width: int,
        *,
        ellipsis: str = "...",
    ) -> str:
        return truncate_text(draw, value, font, max_width, ellipsis=ellipsis)

    def _fit_font_to_width_safe(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        max_width: int,
        *,
        weight: str = "regular",
        start_size: int = 20,
        min_size: int = 10,
    ) -> ImageFont.ImageFont:
        return fit_font_to_width(
            draw,
            text,
            max_width,
            font_loader=lambda size: self._font(size, weight),
            start_size=start_size,
            min_size=min_size,
        )

    def _create_antialiased_circle_mask(self, size: int, *, scale: int = 3) -> Image.Image:
        safe_size = max(1, int(size))
        safe_scale = max(2, int(scale))
        big_size = safe_size * safe_scale

        large = Image.new("L", (big_size, big_size), 0)
        large_draw = ImageDraw.Draw(large)
        large_draw.ellipse((0, 0, big_size - 1, big_size - 1), fill=255)

        mask = large.resize((safe_size, safe_size), RESAMPLE_LANCZOS)
        large.close()
        return mask

    def _create_circular_image(self, source: Image.Image | None, size: int) -> Image.Image:
        safe_size = max(1, int(size))

        if source is None:
            image = Image.new("RGBA", (safe_size, safe_size), (0, 0, 0, 0))
        else:
            image = ImageOps.fit(source.convert("RGBA"), (safe_size, safe_size), method=RESAMPLE_LANCZOS)

        mask = self._create_antialiased_circle_mask(safe_size, scale=3)
        output = Image.new("RGBA", (safe_size, safe_size), (0, 0, 0, 0))

        try:
            output.paste(image, (0, 0), mask)
            return output
        finally:
            mask.close()
            image.close()

    def _safe_placeholder(self, size: tuple[int, int], color: ColorA = (0, 0, 0, 0)) -> Image.Image:
        width = max(1, int(size[0]))
        height = max(1, int(size[1]))
        return Image.new("RGBA", (width, height), self._rgba(color, (0, 0, 0, 0)))

    def _extract_accent(self, profile: ProfileRenderData) -> tuple[int, int, int]:
        source = self._safe_load_external_image(profile.avatar_bytes)

        if source is None:
            return (120, 220, 220)

        try:
            return self._get_dominant_color(source)
        finally:
            source.close()

    def _get_dominant_color(self, img: Image.Image) -> tuple[int, int, int]:
        converted = None
        tiny = None

        try:
            converted = img.convert("RGBA")
            tiny = converted.resize((1, 1), resample=RESAMPLE_LANCZOS)
            pixel = tiny.getpixel((0, 0))
        except Exception:
            return (120, 220, 220)
        finally:
            if converted is not None:
                converted.close()
            if tiny is not None:
                tiny.close()

        if isinstance(pixel, tuple) and len(pixel) >= 3:
            r, g, b = self._rgb(pixel[:3])

            if r + g + b < 90:
                return (120, 220, 220)

            return (r, g, b)

        return (120, 220, 220)

    def _create_glass_background(self, profile: ProfileRenderData) -> Image.Image:
        width, height = self.layout.canvas

        source = self._safe_load_external_image(profile.avatar_bytes)

        if source is None:
            canvas = Image.new("RGBA", self.layout.canvas, self._rgba(self.theme.background_fallback))
        else:
            try:
                canvas = ImageOps.fit(source, self.layout.canvas, method=RESAMPLE_LANCZOS).convert("RGBA")
                canvas = canvas.filter(ImageFilter.GaussianBlur(radius=45))
            except Exception:
                canvas = Image.new("RGBA", self.layout.canvas, self._rgba(self.theme.background_fallback))
            finally:
                source.close()

        overlay = Image.new("RGBA", self.layout.canvas, self._rgba(self.theme.glass_overlay))

        try:
            canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)
        finally:
            overlay.close()

        glow = Image.new("RGBA", self.layout.canvas, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow, "RGBA")
        accent = self._rgb(self._accent)

        glow_draw.ellipse((-220, -260, 650, 590), fill=self._rgba((*accent, 34)))
        glow_draw.ellipse((width - 620, height - 520, width + 260, height + 250), fill=self._rgba((0, 255, 255, 20)))
        glow_draw.ellipse(
            (width // 2 - 420, height // 2 - 260, width // 2 + 420, height // 2 + 260),
            fill=self._rgba((*accent, 12)),
        )

        blurred_glow = glow.filter(ImageFilter.GaussianBlur(radius=65))

        try:
            canvas.alpha_composite(blurred_glow.convert("RGBA"))
        finally:
            glow.close()
            blurred_glow.close()

        vignette = Image.new("RGBA", self.layout.canvas, (0, 0, 0, 0))
        vignette_draw = ImageDraw.Draw(vignette, "RGBA")
        vignette_draw.rectangle((0, 0, width, height), fill=self._rgba((0, 0, 0, 118)))
        vignette_draw.ellipse((-120, -100, width + 120, height + 100), fill=self._rgba((0, 0, 0, 0)))

        blurred_vignette = vignette.filter(ImageFilter.GaussianBlur(radius=90))

        try:
            canvas.alpha_composite(blurred_vignette.convert("RGBA"))
        finally:
            vignette.close()
            blurred_vignette.close()

        return canvas.convert("RGBA")

    def _apply_finishing(self, canvas: Image.Image) -> None:
        add_noise_overlay(canvas, opacity=1, seed=909, scale=2)

    def _draw_main_frame(self, canvas: Image.Image) -> None:
        layout = self.layout
        theme = self.theme
        draw = ImageDraw.Draw(canvas, "RGBA")

        draw_soft_shadow(
            canvas,
            layout.outer_card,
            layout.outer_radius,
            offset=(0, 18),
            blur=38,
            color=self._rgba((0, 0, 0, 175)),
            spread=0,
        )

        outer = Image.new("RGBA", layout.outer_card.size, self._rgba(theme.outer_card_fill))

        try:
            paste_rounded(canvas, outer, layout.outer_card, layout.outer_radius)
        finally:
            outer.close()

        inner = Image.new("RGBA", layout.inner_card.size, self._rgba(theme.inner_card_fill))

        try:
            paste_rounded(canvas, inner, layout.inner_card, layout.inner_radius)
        finally:
            inner.close()

        draw.rounded_rectangle(
            layout.outer_card.box,
            radius=layout.outer_radius,
            outline=self._rgba((255, 255, 255, 52)),
            width=2,
        )

        draw.rounded_rectangle(
            layout.inner_card.box,
            radius=layout.inner_radius,
            outline=self._rgba((255, 255, 255, 26)),
            width=1,
        )

        self._draw_document_header(canvas)

    def _draw_document_header(self, canvas: Image.Image) -> None:
        rect = self.layout.inner_card
        draw = ImageDraw.Draw(canvas, "RGBA")

        font = self._font(13, "bold")
        left = "BAPHOMET ID"
        right = "FICHA DE IDENTIFICAÇÃO"

        self._draw_text(
            draw,
            (rect.x + 30, rect.y + 22),
            left,
            font,
            fill=(220, 220, 225, 135),
            shadow=(0, 0, 0, 95),
            offset=(1, 1),
            max_width=420,
        )

        right = self._truncate_to_width(draw, right, font, 420)
        right_w = self._measure_width(draw, right, font)

        self._draw_text(
            draw,
            (rect.right - 30 - right_w, rect.y + 22),
            right,
            font,
            fill=(220, 220, 225, 135),
            shadow=(0, 0, 0, 95),
            offset=(1, 1),
            max_width=420,
        )

        draw.line(
            (rect.x + 30, rect.y + 49, rect.right - 30, rect.y + 49),
            fill=self._rgba((255, 255, 255, 34)),
            width=1,
        )

    def _draw_panel(self, canvas: Image.Image, rect: Rect, *, radius: int | None = None) -> None:
        radius = radius or self.layout.panel_radius

        draw_soft_shadow(
            canvas,
            rect,
            radius,
            offset=(0, 8),
            blur=18,
            color=self._rgba((0, 0, 0, 105)),
        )

        panel = Image.new("RGBA", rect.size, self._rgba(self.theme.panel_fill))

        try:
            paste_rounded(canvas, panel, rect, radius)
        finally:
            panel.close()

        draw = ImageDraw.Draw(canvas, "RGBA")

        draw.rounded_rectangle(
            rect.box,
            radius=radius,
            outline=self._rgba(self.theme.panel_outline),
            width=1,
        )

        draw.line(
            (rect.x + 24, rect.y + 1, rect.right - 24, rect.y + 1),
            fill=self._rgba((255, 255, 255, 24)),
            width=1,
        )

    def _draw_section_title(self, draw: ImageDraw.ImageDraw, rect: Rect, title: str) -> None:
        font = self._font(26, "display")
        x = rect.x + self.layout.section_pad
        y = rect.y + 22
        max_width = max(1, rect.w - (self.layout.section_pad * 2))

        title = self._truncate_to_width(draw, title, font, max_width)

        self._draw_text(
            draw,
            (x, y),
            title,
            font,
            fill=self.theme.text,
            shadow=(0, 0, 0, 180),
            offset=(2, 2),
            max_width=max_width,
        )

        title_w = self._measure_width(draw, title, font)
        rule_x = x + title_w + 16

        if rule_x < rect.right - 30:
            line_y = y + 17
            draw.line(
                (rule_x, line_y, rect.right - 30, line_y),
                fill=self._rgba((255, 255, 255, 36)),
                width=1,
            )

    def _draw_avatar(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        slot = self.layout.avatar_medallion
        draw = ImageDraw.Draw(canvas, "RGBA")

        avatar_size = 240
        avatar_rect = Rect(
            slot.x + (slot.w - avatar_size) // 2,
            slot.y + (slot.h - avatar_size) // 2,
            avatar_size,
            avatar_size,
        )

        border_size = avatar_size + 18
        border_rect = Rect(
            avatar_rect.x - 9,
            avatar_rect.y - 9,
            border_size,
            border_size,
        )

        draw_soft_shadow(
            canvas,
            border_rect,
            border_rect.w // 2,
            offset=(0, 12),
            blur=28,
            color=self._rgba((0, 0, 0, 170)),
        )

        draw.ellipse(border_rect.box, fill=self._rgba((*self._accent, 255)))

        source = self._safe_load_external_image(profile.avatar_bytes)

        if source is None:
            placeholder = create_avatar_placeholder(
                avatar_size,
                initials=self._initials(profile.display_name or profile.username),
                font=self._font(78, "display"),
                fill_top=(68, 64, 82, 255),
                fill_bottom=(28, 25, 36, 255),
                accent=self._rgba((*self._accent, 255)),
                text_fill=self._rgba(self.theme.text),
            )

            try:
                avatar = self._create_circular_image(placeholder.convert("RGBA"), avatar_size)
            finally:
                placeholder.close()
        else:
            try:
                avatar = self._create_circular_image(source, avatar_size)
            finally:
                source.close()

        try:
            self._safe_paste(canvas, avatar, (avatar_rect.x, avatar_rect.y), avatar)
        finally:
            avatar.close()

        draw.ellipse(avatar_rect.box, outline=self._rgba((255, 255, 255, 65)), width=2)

    def _draw_identity(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.identity_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")

        name = self._field(profile.display_name, profile.username or "Usuário")

        fields = (
            ("Nome", name),
            ("Pronomes", self._field(profile.pronouns)),
            ("ID de usuário", str(profile.user_id)),
            ("Rank", self._field(profile.rank_text, "Sem rank")),
        )

        label_font = self._font(13, "bold")

        x = rect.x + 28
        y = rect.y + 32
        value_width = rect.w - 56

        for label, value in fields:
            label_text = self._truncate_to_width(draw, label.upper(), label_font, value_width)

            draw.text((x, y), label_text, font=label_font, fill=self._rgba(self.theme.text_muted))

            weight = "display" if label == "Nome" else "regular"
            start_size = 30 if label == "Nome" else 22

            value_font = self._fit_font_to_width_safe(
                draw,
                value,
                value_width - 24,
                weight=weight,
                start_size=start_size,
                min_size=15,
            )

            display_value = self._truncate_to_width(draw, value, value_font, value_width - 24)
            value_y = y + 22

            field_rect = Rect(x - 12, value_y - 7, value_width + 24, 40)

            self._draw_field(draw, field_rect)

            self._draw_text(
                draw,
                (x, value_y),
                display_value,
                value_font,
                fill=self.theme.text if label == "Nome" else self.theme.text_soft,
                shadow=(0, 0, 0, 150),
                offset=(2, 2),
                max_width=value_width - 24,
            )

            y += 88

    def _draw_ask_me_about(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.ask_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")

        self._draw_section_title(draw, rect, "Me pergunte sobre")

        topics = self._clean_topics(profile.ask_me_about or [])
        chips_rect = Rect(rect.x + 28, rect.y + 76, rect.w - 56, rect.h - 100)

        self._render_chips(canvas, chips_rect, topics)

    def _draw_basic_info(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.basic_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")

        self._draw_section_title(draw, rect, "Informações básicas")

        text = self._field(profile.basic_info, "Não informado.")
        body_rect = Rect(rect.x + 28, rect.y + 76, rect.w - 56, rect.h - 98)

        self._draw_field(draw, body_rect, radius=16)

        font = self._font(20, "regular")
        line_gap = 8
        line_height = self._measure_height(draw, "Ag", font) + line_gap
        max_lines = max(1, body_rect.h // max(1, line_height))

        text_rect = body_rect.inset(16, 16)

        try:
            lines = wrap_text_pixels(draw, text, font, text_rect.w)
            lines = clamp_lines_with_ellipsis(draw, lines, font, text_rect.w, max_lines)
        except Exception:
            lines = self._wrap_text_safe(draw, text, font, text_rect.w, max_lines)

        y = text_rect.y

        for line in lines:
            if y + line_height > text_rect.bottom + 2:
                break

            safe_line = self._truncate_to_width(draw, line, font, text_rect.w)

            self._draw_text(
                draw,
                (text_rect.x, y),
                safe_line,
                font,
                fill=self.theme.text_soft,
                shadow=(0, 0, 0, 115),
                offset=(2, 2),
                max_width=text_rect.w,
            )

            y += line_height

    def _wrap_text_safe(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
        max_lines: int,
    ) -> list[str]:
        words = str(text or "").replace("\n", " ").split()

        if not words:
            return [""]

        lines: list[str] = []
        current = ""

        for word in words:
            candidate = word if not current else f"{current} {word}"

            if self._measure_width(draw, candidate, font) <= max_width:
                current = candidate
                continue

            if current:
                lines.append(current)
                current = word
            else:
                lines.append(self._truncate_to_width(draw, word, font, max_width))
                current = ""

            if len(lines) >= max_lines:
                break

        if current and len(lines) < max_lines:
            lines.append(current)

        if len(lines) > max_lines:
            lines = lines[:max_lines]

        if len(lines) == max_lines and words:
            lines[-1] = self._truncate_to_width(draw, lines[-1], font, max_width)

        return lines or [""]

    def _draw_badge(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.badge_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")

        self._draw_section_title(draw, rect, "Insígnia")

        slot = Rect(rect.x + 54, rect.y + 80, rect.w - 108, 122)
        self._draw_field(draw, slot, radius=20)

        source = self._safe_load_external_image(profile.badge_image_bytes)
        image_slot = slot.inset(16, 12)

        if source is None:
            badge = create_badge_placeholder(
                (150, 118),
                fill_top=(58, 54, 68, 255),
                fill_bottom=(30, 27, 38, 255),
                accent=self._rgba((*self._accent, 255)),
                line=(255, 255, 255, 65),
            )
        else:
            try:
                badge = source.convert("RGBA").copy()
            except Exception:
                badge = self._safe_placeholder((150, 118), (0, 0, 0, 0))
            finally:
                source.close()

        try:
            badge = badge.convert("RGBA")
            paste_centered(canvas, badge, image_slot)
        finally:
            badge.close()

        label = self._field(profile.badge_name, "Sem insígnia")
        label_font = self._fit_font_to_width_safe(
            draw,
            label,
            rect.w - 76,
            weight="regular",
            start_size=18,
            min_size=13,
        )
        label_rect = Rect(rect.x + 28, rect.y + 224, rect.w - 56, 40)
        label = self._truncate_to_width(draw, label, label_font, label_rect.w - 20)

        self._draw_field(draw, label_rect, radius=15)
        self._draw_centered_text(draw, label_rect, label, label_font, self.theme.text_soft)

    def _draw_bonds(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.bonds_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")

        self._draw_section_title(draw, rect, "Vínculos")

        count = max(0, int(profile.bonds_count))
        count_text = f"{count} vínculo" if count == 1 else f"{count} vínculos"

        count_font = self._fit_font_to_width_safe(
            draw,
            count_text,
            rect.w - 62,
            weight="display",
            start_size=36,
            min_size=20,
        )

        count_text = self._truncate_to_width(draw, count_text, count_font, rect.w - 62)

        self._draw_text(
            draw,
            (rect.x + 30, rect.y + 90),
            count_text,
            count_font,
            fill=self.theme.text,
            shadow=(0, 0, 0, 170),
            offset=(2, 2),
            max_width=rect.w - 62,
        )

        mult = self._format_multiplier(profile.bonds_multiplier)
        badge_rect = Rect(rect.x + 30, rect.y + 158, rect.w - 60, 32)

        self._draw_field(draw, badge_rect, radius=16)

        mult_font = self._fit_font_to_width_safe(
            draw,
            mult,
            badge_rect.w - 18,
            weight="regular",
            start_size=18,
            min_size=14,
        )

        mult = self._truncate_to_width(draw, mult, mult_font, badge_rect.w - 18)
        self._draw_centered_text(draw, badge_rect, mult, mult_font, self.theme.text_soft)

    def _draw_xp_progress(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.xp_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")

        self._draw_section_title(draw, rect, "Progresso de XP")

        level = max(0, int(profile.level))
        level_text = f"Nível {level}"
        level_font = self._font(20, "display")
        level_width = min(150, max(92, self._measure_width(draw, level_text, level_font) + 30))
        level_rect = Rect(rect.right - 30 - level_width, rect.y + 24, level_width, 34)

        self._draw_field(draw, level_rect, radius=17)

        level_text = self._truncate_to_width(draw, level_text, level_font, level_rect.w - 18)

        self._draw_centered_text(
            draw,
            level_rect,
            level_text,
            level_font,
            self.theme.text_soft,
        )

        current = max(0, int(profile.xp_current))
        required = max(1, int(profile.xp_required))
        total = max(0, int(profile.xp_total))
        percent = self._normalize_percent(profile.xp_percent)

        bar_rect = Rect(rect.x + 30, rect.y + 86, rect.w - 60, 35)
        self._draw_xp_bar(canvas, bar_rect, percent / 100)

        xp_label = f"{current:,} / {required:,} XP".replace(",", ".")
        xp_font = self._fit_font_to_width_safe(
            draw,
            xp_label,
            bar_rect.w - 36,
            weight="bold",
            start_size=20,
            min_size=12,
        )
        xp_label = self._truncate_to_width(draw, xp_label, xp_font, bar_rect.w - 36)

        self._draw_centered_text(
            draw,
            bar_rect,
            xp_label,
            xp_font,
            self.theme.text,
            shadow=(0, 0, 0, 255),
        )

        meta_font = self._font(18, "regular")
        total_text = f"XP Total: {total:,}".replace(",", ".")
        percent_text = f"{self._format_percent(percent)} completo"

        total_text = self._truncate_to_width(draw, total_text, meta_font, 320)

        self._draw_text(
            draw,
            (rect.x + 34, rect.y + 140),
            total_text,
            meta_font,
            fill=self.theme.text_muted,
            shadow=(0, 0, 0, 95),
            offset=(1, 1),
            max_width=320,
        )

        percent_text = self._truncate_to_width(draw, percent_text, meta_font, 260)
        percent_w = self._measure_width(draw, percent_text, meta_font)

        self._draw_text(
            draw,
            (rect.right - 34 - percent_w, rect.y + 140),
            percent_text,
            meta_font,
            fill=self.theme.text_muted,
            shadow=(0, 0, 0, 95),
            offset=(1, 1),
            max_width=260,
        )

    def _draw_xp_bar(self, canvas: Image.Image, rect: Rect, ratio: float) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        radius = rect.h // 2

        draw.rounded_rectangle(
            rect.box,
            radius=radius,
            fill=self._rgba(self.theme.bar_track),
            outline=self._rgba((255, 255, 255, 42)),
            width=1,
        )

        ratio = clamp(ratio, 0.0, 1.0)
        filled_width = int(rect.w * ratio)

        if filled_width < rect.h and ratio > 0:
            filled_width = min(rect.h, rect.w)

        if filled_width <= 0:
            return

        fill_layer = Image.new("RGBA", (rect.w, rect.h), (0, 0, 0, 0))
        fill_draw = ImageDraw.Draw(fill_layer)

        start_color = self._rgba((*self._accent, 255))
        end_color = self._rgba(self.theme.cyan)
        mask = None

        try:
            for i in range(filled_width):
                t = i / max(1, rect.w)
                r = int(start_color[0] + (end_color[0] - start_color[0]) * t)
                g = int(start_color[1] + (end_color[1] - start_color[1]) * t)
                b = int(start_color[2] + (end_color[2] - start_color[2]) * t)
                a = int(start_color[3] + (end_color[3] - start_color[3]) * t)

                fill_draw.line([(i, 0), (i, rect.h)], fill=self._rgba((r, g, b, a)))

            big_scale = 3
            big_mask = Image.new("L", (rect.w * big_scale, rect.h * big_scale), 0)
            big_draw = ImageDraw.Draw(big_mask)
            big_draw.rounded_rectangle(
                (0, 0, filled_width * big_scale, rect.h * big_scale),
                radius=radius * big_scale,
                fill=255,
            )

            mask = big_mask.resize((rect.w, rect.h), RESAMPLE_LANCZOS)
            big_mask.close()

            fill_layer.putalpha(mask)
            self._safe_paste(canvas, fill_layer, (rect.x, rect.y), fill_layer)
        finally:
            if mask is not None:
                mask.close()
            fill_layer.close()

    def _render_chips(self, canvas: Image.Image, rect: Rect, labels: list[str]) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        font = self._font(18, "bold")

        x = rect.x
        y = rect.y
        gap = 10
        chip_height = 36
        max_y = rect.bottom - chip_height
        index = 0

        while index < len(labels) and y <= max_y:
            raw_label = labels[index]
            max_label_width = min(272, rect.w - 32)
            label = self._truncate_to_width(draw, raw_label, font, max_label_width)
            chip_width = max(68, self._measure_width(draw, label, font) + 32)

            if x + chip_width > rect.right:
                if y + chip_height + gap > max_y:
                    self._draw_more_chip(
                        canvas,
                        Rect(x, y, rect.right - x, chip_height),
                        len(labels) - index,
                        font,
                    )
                    return

                x = rect.x
                y += chip_height + gap
                continue

            remaining_after = len(labels) - index - 1

            if remaining_after and y + chip_height + gap > max_y:
                more_label = f"+{remaining_after}"
                more_width = max(56, self._measure_width(draw, more_label, font) + 26)

                if x + chip_width + gap + more_width > rect.right:
                    self._draw_more_chip(
                        canvas,
                        Rect(x, y, rect.right - x, chip_height),
                        len(labels) - index,
                        font,
                    )
                    return

            self._draw_topic_chip(canvas, Rect(x, y, chip_width, chip_height), label, font)
            x += chip_width + gap
            index += 1

        hidden = len(labels) - index

        if hidden > 0:
            self._draw_more_chip(canvas, Rect(x, y, rect.right - x, chip_height), hidden, font)

    def _draw_more_chip(
        self,
        canvas: Image.Image,
        slot: Rect,
        hidden: int,
        font: ImageFont.ImageFont,
    ) -> None:
        if hidden <= 0 or slot.w <= 0:
            return

        draw = ImageDraw.Draw(canvas, "RGBA")
        label = f"+{hidden}"
        chip_width = max(56, self._measure_width(draw, label, font) + 26)

        if chip_width > slot.w:
            return

        self._draw_topic_chip(canvas, Rect(slot.x, slot.y, chip_width, slot.h), label, font, muted=True)

    def _draw_topic_chip(
        self,
        canvas: Image.Image,
        rect: Rect,
        label: str,
        font: ImageFont.ImageFont,
        *,
        muted: bool = False,
    ) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")

        fill = (0, 0, 0, 86) if muted else (0, 0, 0, 112)
        outline = (255, 255, 255, 30) if muted else (255, 255, 255, 42)
        text_fill = self.theme.text_muted if muted else self.theme.text_soft

        draw.rounded_rectangle(
            rect.box,
            radius=rect.h // 2,
            fill=self._rgba(fill),
            outline=self._rgba(outline),
            width=1,
        )

        label = self._truncate_to_width(draw, label, font, rect.w - 20)
        label_width = self._measure_width(draw, label, font)
        label_height = self._measure_height(draw, label, font)

        self._draw_text(
            draw,
            (rect.x + (rect.w - label_width) // 2, rect.y + (rect.h - label_height) // 2 - 2),
            label,
            font,
            fill=text_fill,
            shadow=(0, 0, 0, 135),
            offset=(2, 2),
            max_width=rect.w - 20,
        )

    def _draw_field(
        self,
        draw: ImageDraw.ImageDraw,
        rect: Rect,
        *,
        radius: int = 14,
    ) -> None:
        draw.rounded_rectangle(
            rect.box,
            radius=radius,
            fill=self._rgba(self.theme.field_fill),
            outline=self._rgba(self.theme.field_outline),
            width=1,
        )

    def _draw_centered_text(
        self,
        draw: ImageDraw.ImageDraw,
        rect: Rect,
        text: str,
        font: ImageFont.ImageFont,
        fill: ColorA,
        *,
        shadow: ColorA = (0, 0, 0, 145),
        stroke_width: int = 0,
        stroke_fill: ColorA = (0, 0, 0, 0),
    ) -> None:
        safe_text = self._truncate_to_width(draw, text, font, rect.w - 8)
        text_w = self._measure_width(draw, safe_text, font)
        text_h = self._measure_height(draw, safe_text or "Ag", font)

        self._draw_text(
            draw,
            (rect.x + (rect.w - text_w) // 2, rect.y + (rect.h - text_h) // 2 - 2),
            safe_text,
            font,
            fill=fill,
            shadow=shadow,
            offset=(2, 2),
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
            max_width=rect.w - 8,
        )

    def _draw_text(
        self,
        draw: ImageDraw.ImageDraw,
        pos: tuple[int, int],
        text: str,
        font: ImageFont.ImageFont,
        *,
        fill: ColorA,
        shadow: ColorA = (0, 0, 0, 160),
        offset: tuple[int, int] = (2, 2),
        stroke_width: int = 0,
        stroke_fill: ColorA = (0, 0, 0, 0),
        max_width: int | None = None,
    ) -> None:
        safe_text = str(text or "")

        if max_width is not None:
            safe_text = self._truncate_to_width(draw, safe_text, font, max_width)

        try:
            draw_text_shadow(
                draw,
                pos,
                safe_text,
                font=font,
                fill=self._rgba(fill),
                shadow=self._rgba(shadow),
                offset=(int(offset[0]), int(offset[1])),
                stroke_width=max(0, int(stroke_width)),
                stroke_fill=self._rgba(stroke_fill, (0, 0, 0, 0)),
            )
        except Exception:
            x, y = pos
            ox, oy = int(offset[0]), int(offset[1])
            draw.text((x + ox, y + oy), safe_text, font=font, fill=self._rgba(shadow))
            draw.text((x, y), safe_text, font=font, fill=self._rgba(fill))

    def _field(self, value: str | None, fallback: str = "Não informado") -> str:
        if value == REMOVED_CONTENT:
            return value

        if value is None:
            return fallback

        cleaned = str(value).strip()
        return cleaned if cleaned else fallback

    def _clean_topics(self, topics: list[str]) -> list[str]:
        cleaned: list[str] = []

        for topic in topics:
            if topic == REMOVED_CONTENT:
                cleaned.append(topic)
                continue

            normalized = str(topic).strip()

            if normalized:
                cleaned.append(normalized)

        return cleaned or ["Não informado"]

    @staticmethod
    def _initials(name: str) -> str:
        pieces = [piece for piece in str(name or "").replace("_", " ").split(" ") if piece]

        if not pieces:
            return "?"

        if len(pieces) == 1:
            return pieces[0][:2]

        return pieces[0][:1] + pieces[1][:1]

    @staticmethod
    def _format_multiplier(value: float) -> str:
        if not math.isfinite(value):
            value = 1.0

        text = f"{max(0.0, value):.1f}".rstrip("0").rstrip(".")
        return f"{text}x"

    @staticmethod
    def _normalize_percent(value: float) -> float:
        if not math.isfinite(value):
            return 0.0

        percent = value * 100 if 0 <= value <= 1 else value
        return clamp(percent, 0.0, 100.0)

    @staticmethod
    def _format_percent(value: float) -> str:
        if abs(value - round(value)) < 0.05:
            return f"{int(round(value))}%"

        return f"{value:.1f}%"


class ProfileCardCog(commands.Cog):
    """
    Cog autocontido da ficha do Baphomet.

    Ele renderiza a ficha com o ProfileCardRenderer embutido e tenta ler dados
    de bancos SQLite comuns sem quebrar caso tabelas/colunas não existam.
    """

    PROFILE_TABLES = ("profile_cards", "profiles", "user_profiles", "fichas", "ficha_profiles")
    TOPIC_TABLES = ("profile_topics", "user_topics", "ficha_topics", "ask_me_about")
    BOND_TABLES = ("profile_bonds", "bonds", "vinculos", "vínculos", "user_bonds")
    XP_TABLES = ("user_xp", "xp_users", "xp", "levels", "member_xp", "leveling_users", "guild_xp")

    USER_COLUMNS = ("user_id", "member_id", "discord_id", "author_id", "target_id")
    GUILD_COLUMNS = ("guild_id", "server_id", "guild")
    TOPIC_COLUMNS = ("topic", "value", "label", "name", "text", "conteudo", "content")
    STATUS_COLUMNS = ("status", "state", "situacao")

    def __init__(
        self,
        bot: commands.Bot,
        *,
        db_path: str | os.PathLike[str] = "data/profile_cards.sqlite3",
        xp_db_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.bot = bot
        self.db_path = Path(db_path)
        self.xp_db_path = Path(xp_db_path) if xp_db_path else self.db_path
        self.renderer = ProfileCardRenderer()

    @app_commands.command(name="ficha", description="Mostra a ficha de identificação de um membro.")
    @app_commands.guild_only()
    @app_commands.describe(membro="Membro que você quer visualizar. Se vazio, mostra a sua ficha.")
    async def ficha(
        self,
        interaction: discord.Interaction,
        membro: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        target = membro or interaction.user
        guild = interaction.guild

        if guild is None:
            await interaction.followup.send("Esse comando só pode ser usado em servidor.", ephemeral=True)
            return

        avatar_bytes = await self._read_asset(getattr(target, "display_avatar", None))

        profile_payload = await asyncio.to_thread(
            self._load_profile_payload_sync,
            guild.id,
            target.id,
        )

        badge_image_bytes = await self._load_badge_image_bytes(profile_payload)

        profile = self._make_profile_data(
            guild_id=guild.id,
            member=target,
            avatar_bytes=avatar_bytes,
            badge_image_bytes=badge_image_bytes,
            payload=profile_payload,
        )

        try:
            image_bytes = await asyncio.to_thread(self.renderer.render, profile)
        except Exception as exc:
            await interaction.followup.send(
                f"Não consegui renderizar a ficha agora. Erro: `{type(exc).__name__}`",
                ephemeral=True,
            )
            return

        file = discord.File(io.BytesIO(image_bytes), filename=f"ficha_{target.id}.png")
        await interaction.followup.send(file=file)

    async def _read_asset(self, asset: discord.Asset | None) -> bytes | None:
        if asset is None:
            return None

        try:
            return await asset.read()
        except Exception:
            return None

    async def _fetch_image_url(self, url: str | None, *, limit: int = 8 * 1024 * 1024) -> bytes | None:
        if not url:
            return None

        clean_url = str(url).strip()

        if not clean_url.startswith(("http://", "https://")):
            return None

        headers = {
            "User-Agent": "Mozilla/5.0 BaphometBot/1.0",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }

        timeout = aiohttp.ClientTimeout(total=8)

        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(clean_url, allow_redirects=True) as response:
                    if response.status != 200:
                        return None

                    content_type = response.headers.get("Content-Type", "").lower()

                    if "image/" not in content_type or "svg" in content_type:
                        return None

                    data = bytearray()

                    async for chunk in response.content.iter_chunked(64 * 1024):
                        data.extend(chunk)

                        if len(data) > limit:
                            return None

                    return bytes(data) if data else None
        except Exception:
            return None

    async def _load_badge_image_bytes(self, payload: dict[str, Any]) -> bytes | None:
        badge_bytes = payload.get("badge_image_bytes")

        if isinstance(badge_bytes, bytes):
            return badge_bytes

        badge_url = self._pick(payload.get("profile", {}), ("badge_image_url", "badge_url", "insignia_url", "badge_icon_url"))
        return await self._fetch_image_url(badge_url)

    def _make_profile_data(
        self,
        *,
        guild_id: int,
        member: discord.Member | discord.User,
        avatar_bytes: bytes | None,
        badge_image_bytes: bytes | None,
        payload: dict[str, Any],
    ) -> ProfileRenderData:
        profile = payload.get("profile", {})
        xp = payload.get("xp", {})
        topics = payload.get("topics") or []
        bonds_count = int(payload.get("bonds_count") or 0)

        display_name = self._pick(profile, ("display_name", "name", "nome", "apelido", "nickname")) or getattr(member, "display_name", None) or getattr(member, "name", "Usuário")
        username = getattr(member, "name", None) or display_name

        pronouns = self._pick(profile, ("pronouns", "pronomes"))
        basic_info = self._pick(profile, ("basic_info", "bio", "sobre", "description", "descricao", "descrição", "info"))
        badge_name = self._pick(profile, ("badge_name", "insignia", "insígnia", "insignia_name", "badge"))

        level = self._safe_int(self._pick(xp, ("level", "lvl", "nivel", "nível")), 0)
        xp_total = self._safe_int(self._pick(xp, ("xp_total", "total_xp", "xp", "experience", "points")), 0)
        xp_required = self._safe_int(self._pick(xp, ("xp_required", "xp_for_next_level", "required_xp", "next_level_xp")), 0)
        xp_current = self._safe_int(self._pick(xp, ("xp_current", "xp_into_level", "current_xp", "level_xp")), -1)

        if xp_required <= 0:
            xp_required = max(1, level * 200 if level > 0 else 200)

        if xp_current < 0:
            xp_current = xp_total % xp_required if xp_required else 0

        xp_current = max(0, min(xp_current, xp_required))
        xp_percent = xp_current / xp_required if xp_required else 0.0

        rank_text = self._pick(profile, ("rank_text", "rank", "position_text", "posicao", "posição"))

        if not rank_text:
            rank_value = payload.get("rank_position")

            if rank_value is not None:
                rank_text = f"#{rank_value}"
            else:
                rank_text = "Sem rank"

        multiplier = self._safe_float(self._pick(profile, ("bonds_multiplier", "multiplier", "multiplicador")), 0.0)

        if multiplier <= 0:
            multiplier = 1.0 + (bonds_count * 0.1)

        return ProfileRenderData(
            user_id=int(member.id),
            username=str(username),
            display_name=str(display_name),
            pronouns=pronouns,
            rank_text=str(rank_text),
            ask_me_about=topics,
            basic_info=basic_info,
            badge_name=badge_name,
            avatar_bytes=avatar_bytes,
            badge_image_bytes=badge_image_bytes,
            bonds_count=bonds_count,
            bonds_multiplier=multiplier,
            level=level,
            xp_current=xp_current,
            xp_required=xp_required,
            xp_total=xp_total,
            xp_percent=xp_percent,
        )

    def _load_profile_payload_sync(self, guild_id: int, user_id: int) -> dict[str, Any]:
        profile: dict[str, Any] = {}
        topics: list[str] = []
        bonds_count = 0
        xp: dict[str, Any] = {}
        rank_position: int | None = None

        db_paths = [self.db_path]

        if self.xp_db_path != self.db_path:
            db_paths.append(self.xp_db_path)

        for db_path in db_paths:
            if not db_path.exists() or not db_path.is_file():
                continue

            try:
                with sqlite3.connect(db_path) as conn:
                    conn.row_factory = sqlite3.Row

                    if not profile:
                        profile = self._read_profile_row(conn, guild_id, user_id)

                    if not topics:
                        topics = self._read_topics(conn, guild_id, user_id, profile)

                    if bonds_count <= 0:
                        bonds_count = self._read_bonds_count(conn, guild_id, user_id)

                    if not xp:
                        xp = self._read_xp_row(conn, guild_id, user_id)

                    if rank_position is None and xp:
                        rank_position = self._read_rank_position(conn, guild_id, user_id)
            except sqlite3.Error:
                continue

        return {
            "profile": profile,
            "topics": topics or self._parse_topics(self._pick(profile, ("ask_me_about", "topics", "interests", "assuntos"))),
            "bonds_count": bonds_count,
            "xp": xp,
            "rank_position": rank_position,
        }

    def _read_profile_row(self, conn: sqlite3.Connection, guild_id: int, user_id: int) -> dict[str, Any]:
        row = self._fetch_user_row(conn, self.PROFILE_TABLES, guild_id, user_id)
        return row or {}

    def _read_topics(
        self,
        conn: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        profile: dict[str, Any],
    ) -> list[str]:
        inline_topics = self._parse_topics(self._pick(profile, ("ask_me_about", "topics", "interests", "assuntos")))

        if inline_topics:
            return inline_topics

        table = self._find_existing_table(conn, self.TOPIC_TABLES)

        if not table:
            return []

        columns = self._table_columns(conn, table)
        user_col = self._first_existing(columns, self.USER_COLUMNS)
        guild_col = self._first_existing(columns, self.GUILD_COLUMNS)
        topic_col = self._first_existing(columns, self.TOPIC_COLUMNS)

        if not user_col or not topic_col:
            return []

        where = [f"{self._q(user_col)} = ?"]
        args: list[Any] = [str(user_id)]

        if guild_col:
            where.append(f"{self._q(guild_col)} = ?")
            args.append(str(guild_id))

        order_col = self._first_existing(columns, ("position", "ordem", "order_index", "created_at", "id"))

        sql = f"SELECT * FROM {self._q(table)} WHERE {' AND '.join(where)}"

        if order_col:
            sql += f" ORDER BY {self._q(order_col)} ASC"

        try:
            rows = conn.execute(sql, args).fetchall()
        except sqlite3.Error:
            return []

        topics: list[str] = []

        for row in rows:
            value = str(row[topic_col] or "").strip()

            if value:
                topics.append(value)

        return topics

    def _read_bonds_count(self, conn: sqlite3.Connection, guild_id: int, user_id: int) -> int:
        table = self._find_existing_table(conn, self.BOND_TABLES)

        if not table:
            return 0

        columns = self._table_columns(conn, table)

        pair_options = (
            ("user_id", "target_id"),
            ("user_id", "friend_id"),
            ("user1_id", "user2_id"),
            ("user_a_id", "user_b_id"),
            ("member_a_id", "member_b_id"),
            ("requester_id", "receiver_id"),
            ("author_id", "target_id"),
            ("from_user_id", "to_user_id"),
        )

        selected_pair: tuple[str, str] | None = None

        for left, right in pair_options:
            if left in columns and right in columns:
                selected_pair = (left, right)
                break

        if selected_pair is None:
            user_col = self._first_existing(columns, self.USER_COLUMNS)

            if not user_col:
                return 0

            selected_pair = (user_col, user_col)

        left_col, right_col = selected_pair
        guild_col = self._first_existing(columns, self.GUILD_COLUMNS)
        status_col = self._first_existing(columns, self.STATUS_COLUMNS)

        where = [f"({self._q(left_col)} = ? OR {self._q(right_col)} = ?)"]
        args: list[Any] = [str(user_id), str(user_id)]

        if guild_col:
            where.append(f"{self._q(guild_col)} = ?")
            args.append(str(guild_id))

        if status_col:
            where.append(f"LOWER(CAST({self._q(status_col)} AS TEXT)) IN ('accepted', 'ativo', 'ativa', 'aprovado', 'aprovada', 'active')")

        sql = f"SELECT COUNT(*) FROM {self._q(table)} WHERE {' AND '.join(where)}"

        try:
            row = conn.execute(sql, args).fetchone()
            return int(row[0] or 0)
        except sqlite3.Error:
            return 0

    def _read_xp_row(self, conn: sqlite3.Connection, guild_id: int, user_id: int) -> dict[str, Any]:
        row = self._fetch_user_row(conn, self.XP_TABLES, guild_id, user_id)
        return row or {}

    def _read_rank_position(self, conn: sqlite3.Connection, guild_id: int, user_id: int) -> int | None:
        table = self._find_existing_table(conn, self.XP_TABLES)

        if not table:
            return None

        columns = self._table_columns(conn, table)
        user_col = self._first_existing(columns, self.USER_COLUMNS)
        guild_col = self._first_existing(columns, self.GUILD_COLUMNS)
        xp_col = self._first_existing(columns, ("total_xp", "xp_total", "xp", "experience", "points"))

        if not user_col or not xp_col:
            return None

        where = []
        args: list[Any] = []

        if guild_col:
            where.append(f"{self._q(guild_col)} = ?")
            args.append(str(guild_id))

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        sql = f"""
            SELECT {self._q(user_col)} AS user_id
            FROM {self._q(table)}
            {where_sql}
            ORDER BY CAST({self._q(xp_col)} AS INTEGER) DESC
        """

        try:
            rows = conn.execute(sql, args).fetchall()
        except sqlite3.Error:
            return None

        for index, row in enumerate(rows, start=1):
            try:
                if int(row["user_id"]) == int(user_id):
                    return index
            except (TypeError, ValueError):
                continue

        return None

    def _fetch_user_row(
        self,
        conn: sqlite3.Connection,
        table_candidates: Iterable[str],
        guild_id: int,
        user_id: int,
    ) -> dict[str, Any] | None:
        table = self._find_existing_table(conn, table_candidates)

        if not table:
            return None

        columns = self._table_columns(conn, table)
        user_col = self._first_existing(columns, self.USER_COLUMNS)
        guild_col = self._first_existing(columns, self.GUILD_COLUMNS)

        if not user_col:
            return None

        where = [f"{self._q(user_col)} = ?"]
        args: list[Any] = [str(user_id)]

        if guild_col:
            where.append(f"{self._q(guild_col)} = ?")
            args.append(str(guild_id))

        sql = f"SELECT * FROM {self._q(table)} WHERE {' AND '.join(where)} LIMIT 1"

        try:
            row = conn.execute(sql, args).fetchone()
        except sqlite3.Error:
            return None

        if row is None:
            return None

        return dict(row)

    def _find_existing_table(self, conn: sqlite3.Connection, candidates: Iterable[str]) -> str | None:
        try:
            existing = {
                str(row[0]).casefold(): str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
        except sqlite3.Error:
            return None

        for candidate in candidates:
            found = existing.get(str(candidate).casefold())

            if found:
                return found

        return None

    def _table_columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        try:
            rows = conn.execute(f"PRAGMA table_info({self._q(table)})").fetchall()
        except sqlite3.Error:
            return set()

        return {str(row[1]) for row in rows}

    @staticmethod
    def _first_existing(columns: set[str], candidates: Iterable[str]) -> str | None:
        lowered = {col.casefold(): col for col in columns}

        for candidate in candidates:
            found = lowered.get(str(candidate).casefold())

            if found:
                return found

        return None

    @staticmethod
    def _q(identifier: str) -> str:
        return '"' + str(identifier).replace('"', '""') + '"'

    @staticmethod
    def _pick(row: dict[str, Any], keys: Iterable[str]) -> str | None:
        lowered = {str(key).casefold(): key for key in row.keys()}

        for key in keys:
            real_key = lowered.get(str(key).casefold())

            if real_key is None:
                continue

            value = row.get(real_key)

            if value is None:
                continue

            text = str(value).strip()

            if text:
                return text

        return None

    @staticmethod
    def _parse_topics(raw: str | None) -> list[str]:
        if not raw:
            return []

        text = str(raw).strip()

        if not text:
            return []

        try:
            parsed = json.loads(text)

            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]

            if isinstance(parsed, dict):
                values = parsed.get("topics") or parsed.get("ask_me_about") or parsed.get("items")

                if isinstance(values, list):
                    return [str(item).strip() for item in values if str(item).strip()]
        except json.JSONDecodeError:
            pass

        parts: list[str] = []

        for separator in ("\n", ";", "|", ","):
            if separator in text:
                parts = [part.strip() for part in text.split(separator) if part.strip()]
                break

        return parts or [text]

    @staticmethod
    def _safe_int(value: Any, fallback: int = 0) -> int:
        try:
            return int(float(str(value).replace(".", "").replace(",", ".")))
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _safe_float(value: Any, fallback: float = 0.0) -> float:
        try:
            return float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return fallback


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProfileCardCog(bot))