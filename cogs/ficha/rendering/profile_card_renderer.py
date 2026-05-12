from future import annotations

import io
import math
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError

from .drawing import (
ColorA,
Rect,
add_noise_overlay,
circular_crop,
clamp,
clamp_lines_with_ellipsis,
create_avatar_placeholder,
create_badge_placeholder,
draw_soft_shadow,
draw_text_shadow,
fit_font_to_width,
load_rgba_from_bytes,
paste_centered,
paste_rounded,
text_height,
text_width,
truncate_text,
wrap_text_pixels,
)
from .fonts import FontManager
from .profile_card_types import ProfileRenderData

REMOVED_CONTENT = "[Conteúdo removido]"

try:
RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
RESAMPLE_LANCZOS = Image.LANCZOS

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
    if not isinstance(color, (tuple, list)):
        return fallback

    if len(color) == 3:
        return (
            self._channel(color[0], fallback[0]),
            self._channel(color[1], fallback[1]),
            self._channel(color[2], fallback[2]),
            255,
        )

    if len(color) >= 4:
        return (
            self._channel(color[0], fallback[0]),
            self._channel(color[1], fallback[1]),
            self._channel(color[2], fallback[2]),
            self._channel(color[3], fallback[3]),
        )

    return fallback

def _rgb(self, color: Any, fallback: tuple[int, int, int] = (120, 220, 220)) -> tuple[int, int, int]:
    rgba = self._rgba(color, (*fallback, 255))
    return rgba[:3]

def _safe_load_external_image(self, image_bytes: bytes | bytearray | memoryview | None) -> Image.Image | None:
    if not image_bytes:
        return None

    try:
        with Image.open(io.BytesIO(bytes(image_bytes))) as img:
            img.load()
            return img.convert("RGBA").copy()
    except (OSError, ValueError, UnidentifiedImageError):
        return None

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

    bold = weight in {"bold", "display", "semibold", "black"}
    fallback_paths = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ) if bold else (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    )

    for path in fallback_paths:
        try:
            return ImageFont.truetype(path, size=safe_size)
        except OSError:
            continue

    return ImageFont.load_default()

def _measure_width(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    value = str(text or "")

    try:
        return int(math.ceil(float(draw.textlength(value, font=font))))
    except Exception:
        pass

    try:
        box = draw.textbbox((0, 0), value, font=font)
        return max(0, box[2] - box[0])
    except Exception:
        return text_width(draw, value, font)

def _measure_height(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    value = str(text or "Ag")

    try:
        box = draw.textbbox((0, 0), value, font=font)
        return max(1, box[3] - box[1])
    except Exception:
        return max(1, text_height(draw, value, font))

def _truncate_to_width(
    self,
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

    if self._measure_width(draw, text, font) <= limit:
        return text

    if self._measure_width(draw, ellipsis, font) > limit:
        return ""

    lo = 0
    hi = len(text)
    best = ellipsis

    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid].rstrip() + ellipsis

        if self._measure_width(draw, candidate, font) <= limit:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1

    return best

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
    safe_max = max(1, int(max_width))
    safe_start = max(1, int(start_size))
    safe_min = max(1, min(int(min_size), safe_start))

    for size in range(safe_start, safe_min - 1, -1):
        font = self._font(size, weight)
        if self._measure_width(draw, text, font) <= safe_max:
            return font

    return self._font(safe_min, weight)

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
    try:
        converted = img.convert("RGBA")
        tiny = converted.resize((1, 1), resample=RESAMPLE_LANCZOS)
        pixel = tiny.getpixel((0, 0))
    except Exception:
        return (120, 220, 220)
    finally:
        try:
            converted.close()
        except Exception:
            pass

    try:
        tiny.close()
    except Exception:
        pass

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

    glow_draw.ellipse(
        (-220, -260, 650, 590),
        fill=self._rgba((*accent, 34)),
    )
    glow_draw.ellipse(
        (width - 620, height - 520, width + 260, height + 250),
        fill=self._rgba((0, 255, 255, 20)),
    )
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

    draw.ellipse(
        border_rect.box,
        fill=self._rgba((*self._accent, 255)),
    )

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

    draw.ellipse(
        avatar_rect.box,
        outline=self._rgba((255, 255, 255, 65)),
        width=2,
    )

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
        draw.text(
            (x, y),
            label_text,
            font=label_font,
            fill=self._rgba(self.theme.text_muted),
        )

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

    topics = self._clean_topics(profile.ask_me_about)
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
        rect.w - 56 - 20,
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
    required = max(0, int(profile.xp_required))
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

    try:
        for i in range(filled_width):
            t = i / max(1, rect.w)
            r = int(start_color[0] + (end_color[0] - start_color[0]) * t)
            g = int(start_color[1] + (end_color[1] - start_color[1]) * t)
            b = int(start_color[2] + (end_color[2] - start_color[2]) * t)
            a = int(start_color[3] + (end_color[3] - start_color[3]) * t)

            fill_draw.line(
                [(i, 0), (i, rect.h)],
                fill=self._rgba((r, g, b, a)),
            )

        mask = Image.new("L", (rect.w, rect.h), 0)
        try:
            big_scale = 3
            big_mask = Image.new("L", (rect.w * big_scale, rect.h * big_scale), 0)
            big_draw = ImageDraw.Draw(big_mask)
            big_draw.rounded_rectangle(
                (0, 0, filled_width * big_scale, rect.h * big_scale),
                radius=radius * big_scale,
                fill=255,
            )
            aa_mask = big_mask.resize((rect.w, rect.h), RESAMPLE_LANCZOS)
            mask.paste(aa_mask, (0, 0))
        finally:
            try:
                big_mask.close()
            except Exception:
                pass
            try:
                aa_mask.close()
            except Exception:
                pass

        fill_layer.putalpha(mask)
        self._safe_paste(canvas, fill_layer, (rect.x, rect.y), fill_layer)
    finally:
        try:
            mask.close()
        except Exception:
            pass
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