from __future__ import annotations

import io
import random
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


Color = tuple[int, int, int]
ColorA = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    @property
    def size(self) -> tuple[int, int]:
        return (self.w, self.h)

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    def inset(self, x: int, y: int | None = None) -> Rect:
        y_amount = x if y is None else y
        return Rect(self.x + x, self.y + y_amount, self.w - x * 2, self.h - y_amount * 2)

    def offset(self, x: int, y: int) -> Rect:
        return Rect(self.x + x, self.y + y, self.w, self.h)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def vertical_gradient(size: tuple[int, int], top: ColorA, bottom: ColorA) -> Image.Image:
    width, height = size
    gradient = Image.new("RGBA", size, top)
    draw = ImageDraw.Draw(gradient)
    denominator = max(1, height - 1)
    for y in range(height):
        ratio = y / denominator
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(4))
        draw.line((0, y, width, y), fill=color)
    return gradient


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    scale = 3
    mask = Image.new("L", (size[0] * scale, size[1] * scale), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0] * scale, size[1] * scale), radius=radius * scale, fill=255)
    return mask.resize(size, Image.Resampling.LANCZOS)


def paste_rounded(base: Image.Image, layer: Image.Image, rect: Rect, radius: int) -> None:
    layer = layer.resize(rect.size, Image.Resampling.LANCZOS).convert("RGBA")
    mask = rounded_mask(rect.size, radius)
    base.paste(layer, (rect.x, rect.y), mask)


def draw_soft_shadow(
    base: Image.Image,
    rect: Rect,
    radius: int,
    *,
    offset: tuple[int, int] = (0, 12),
    blur: int = 24,
    color: ColorA = (0, 0, 0, 140),
    spread: int = 0,
) -> None:
    margin = blur * 2 + spread + 4
    shadow_size = (rect.w + spread * 2 + margin * 2, rect.h + spread * 2 + margin * 2)
    shadow = Image.new("RGBA", shadow_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(shadow)
    shadow_rect = Rect(margin, margin, rect.w + spread * 2, rect.h + spread * 2)
    draw.rounded_rectangle(shadow_rect.box, radius=radius + spread, fill=color)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))

    dest_x = rect.x + offset[0] - spread - margin
    dest_y = rect.y + offset[1] - spread - margin
    src_left = max(0, -dest_x)
    src_top = max(0, -dest_y)
    src_right = min(shadow.width, base.width - dest_x)
    src_bottom = min(shadow.height, base.height - dest_y)
    if src_right <= src_left or src_bottom <= src_top:
        return
    cropped = shadow.crop((src_left, src_top, src_right, src_bottom))
    base.alpha_composite(cropped, (dest_x + src_left, dest_y + src_top))


def draw_bevel_border(
    base: Image.Image,
    rect: Rect,
    radius: int,
    *,
    highlight: ColorA,
    shadow: ColorA,
    outline: ColorA,
    width: int = 3,
) -> None:
    draw = ImageDraw.Draw(base)
    for i in range(width):
        box = (rect.x + i, rect.y + i, rect.right - i, rect.bottom - i)
        draw.rounded_rectangle(box, radius=max(0, radius - i), outline=outline, width=1)

    x0, y0, x1, y1 = rect.box
    r = radius
    draw.line((x0 + r, y0 + 2, x1 - r, y0 + 2), fill=highlight, width=2)
    draw.line((x0 + 2, y0 + r, x0 + 2, y1 - r), fill=highlight, width=2)
    draw.arc((x0 + 2, y0 + 2, x0 + r * 2, y0 + r * 2), 180, 270, fill=highlight, width=2)
    draw.line((x0 + r, y1 - 2, x1 - r, y1 - 2), fill=shadow, width=2)
    draw.line((x1 - 2, y0 + r, x1 - 2, y1 - r), fill=shadow, width=2)
    draw.arc((x1 - r * 2, y1 - r * 2, x1 - 2, y1 - 2), 0, 90, fill=shadow, width=2)


def draw_inner_highlight(base: Image.Image, rect: Rect, radius: int, color: ColorA = (255, 255, 255, 28)) -> None:
    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    top = Rect(rect.x + 10, rect.y + 8, rect.w - 20, max(10, rect.h // 5))
    draw.rounded_rectangle(top.box, radius=max(4, radius // 2), fill=color)
    glow = glow.filter(ImageFilter.GaussianBlur(10))
    base.alpha_composite(glow)


def add_noise_overlay(base: Image.Image, *, opacity: int = 22, seed: int = 13, scale: int = 5) -> None:
    small = (max(1, base.width // scale), max(1, base.height // scale))
    rng = random.Random(seed)
    noise = Image.new("L", small)
    noise.putdata([rng.randrange(256) for _ in range(small[0] * small[1])])
    noise = noise.filter(ImageFilter.GaussianBlur(0.8)).resize(base.size, Image.Resampling.BICUBIC)
    alpha = noise.point(lambda p: int(abs(p - 128) * opacity / 128))
    overlay = Image.new("RGBA", base.size, (255, 255, 255, 0))
    overlay.putalpha(alpha)
    base.alpha_composite(overlay)


def load_rgba_from_bytes(image_bytes: bytes | None) -> Image.Image | None:
    if not image_bytes:
        return None
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            return image.convert("RGBA")
    except Exception:
        return None


def circular_crop(image: Image.Image, size: int) -> Image.Image:
    fitted = ImageOps.fit(image.convert("RGBA"), (size, size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    mask = rounded_mask((size, size), size // 2)
    output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    output.paste(fitted, (0, 0), mask)
    return output


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
    canvas = vertical_gradient((size, size), fill_top, fill_bottom)
    add_noise_overlay(canvas, opacity=8, seed=71, scale=3)
    mask = rounded_mask((size, size), size // 2)
    canvas.putalpha(mask)

    draw = ImageDraw.Draw(canvas)
    draw.ellipse((size * 0.16, size * 0.16, size * 0.84, size * 0.84), outline=accent, width=max(2, size // 70))
    draw.arc((size * 0.24, size * 0.24, size * 0.76, size * 0.76), 205, 335, fill=accent, width=max(2, size // 90))
    text = initials[:2].upper() or "?"
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((size - (bbox[2] - bbox[0])) // 2, (size - (bbox[3] - bbox[1])) // 2 - bbox[1]),
        text,
        font=font,
        fill=text_fill,
        stroke_width=max(1, size // 120),
        stroke_fill=(0, 0, 0, 120),
    )
    return canvas


def create_badge_placeholder(
    size: tuple[int, int],
    *,
    fill_top: ColorA,
    fill_bottom: ColorA,
    accent: ColorA,
    line: ColorA,
) -> Image.Image:
    canvas = vertical_gradient(size, fill_top, fill_bottom)
    add_noise_overlay(canvas, opacity=8, seed=91, scale=3)
    draw = ImageDraw.Draw(canvas)
    w, h = size
    cx = w // 2
    shield = (
        (cx, int(h * 0.1)),
        (int(w * 0.76), int(h * 0.28)),
        (int(w * 0.69), int(h * 0.68)),
        (cx, int(h * 0.9)),
        (int(w * 0.31), int(h * 0.68)),
        (int(w * 0.24), int(h * 0.28)),
    )
    draw.polygon(shield, outline=line, fill=(accent[0], accent[1], accent[2], 82))
    draw.line((cx, int(h * 0.22), cx, int(h * 0.74)), fill=line, width=4)
    draw.arc((int(w * 0.28), int(h * 0.2), int(w * 0.72), int(h * 0.72)), 200, 340, fill=line, width=4)
    draw.ellipse((cx - 10, int(h * 0.49) - 10, cx + 10, int(h * 0.49) + 10), fill=accent)
    return canvas


def fit_image_in_rect(image: Image.Image, rect: Rect) -> Image.Image:
    ratio = min(rect.w / max(1, image.width), rect.h / max(1, image.height))
    width = max(1, int(image.width * ratio))
    height = max(1, int(image.height * ratio))
    return image.convert("RGBA").resize((width, height), Image.Resampling.LANCZOS)


def paste_centered(base: Image.Image, image: Image.Image, rect: Rect) -> None:
    fitted = fit_image_in_rect(image, rect)
    x = rect.x + (rect.w - fitted.width) // 2
    y = rect.y + (rect.h - fitted.height) // 2
    base.paste(fitted, (x, y), fitted)


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def text_height(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def draw_text_shadow(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: ColorA,
    shadow: ColorA = (0, 0, 0, 145),
    offset: tuple[int, int] = (0, 2),
    stroke_width: int = 0,
    stroke_fill: ColorA = (0, 0, 0, 0),
) -> None:
    x, y = xy
    if shadow[3] > 0:
        draw.text((x + offset[0], y + offset[1]), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)


def truncate_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    if text_width(draw, text, font) <= max_width:
        return text
    ellipsis = "..."
    if text_width(draw, ellipsis, font) > max_width:
        return ""

    low = 0
    high = len(text)
    best = ""
    while low <= high:
        mid = (low + high) // 2
        candidate = text[:mid].rstrip() + ellipsis
        if text_width(draw, candidate, font) <= max_width:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


def fit_font_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    *,
    font_loader: Callable[[int], ImageFont.ImageFont],
    start_size: int,
    min_size: int,
) -> ImageFont.ImageFont:
    for size in range(start_size, min_size - 1, -1):
        font = font_loader(size)
        if text_width(draw, text, font) <= max_width:
            return font
    return font_loader(min_size)


def wrap_text_pixels(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    paragraphs = text.splitlines() or [""]
    for paragraph in paragraphs:
        if not paragraph:
            lines.append("")
            continue

        current = ""
        for word in paragraph.split(" "):
            if not word:
                continue
            candidate = word if not current else f"{current} {word}"
            if text_width(draw, candidate, font) <= max_width:
                current = candidate
                continue

            if current:
                lines.append(current)
                current = ""

            if text_width(draw, word, font) <= max_width:
                current = word
            else:
                fragments = _split_long_word(draw, word, font, max_width)
                lines.extend(fragments[:-1])
                current = fragments[-1] if fragments else ""

        if current:
            lines.append(current)
    return lines


def clamp_lines_with_ellipsis(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
) -> list[str]:
    if len(lines) <= max_lines:
        return lines
    if max_lines <= 0:
        return []
    clipped = lines[:max_lines]
    clipped[-1] = truncate_text(draw, clipped[-1].rstrip() + "...", font, max_width)
    return clipped


def _split_long_word(
    draw: ImageDraw.ImageDraw,
    word: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    fragments: list[str] = []
    remaining = word
    while remaining:
        low = 1
        high = len(remaining)
        best = 1
        while low <= high:
            mid = (low + high) // 2
            if text_width(draw, remaining[:mid], font) <= max_width:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        fragments.append(remaining[:best])
        remaining = remaining[best:]
    return fragments


def draw_xp_bar(
    base: Image.Image,
    rect: Rect,
    *,
    ratio: float,
    track_fill: ColorA,
    fill_start: ColorA,
    fill_end: ColorA,
    outline: ColorA,
    highlight: ColorA,
) -> None:
    draw = ImageDraw.Draw(base)
    radius = rect.h // 2
    draw.rounded_rectangle(rect.box, radius=radius, fill=track_fill, outline=outline, width=2)

    ratio = clamp(ratio, 0.0, 1.0)
    fill_width = int(rect.w * ratio)
    if fill_width > 0:
        fill_rect = Rect(rect.x, rect.y, min(fill_width, rect.w), rect.h)
        gradient = vertical_gradient((fill_rect.w, fill_rect.h), fill_start, fill_end)
        gradient = gradient.rotate(90, expand=True).resize(fill_rect.size, Image.Resampling.BICUBIC)
        mask = rounded_mask(fill_rect.size, radius)
        base.paste(gradient, (fill_rect.x, fill_rect.y), mask)

    shine = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shine_draw = ImageDraw.Draw(shine)
    shine_draw.rounded_rectangle(
        (rect.x + 4, rect.y + 4, rect.right - 4, rect.y + rect.h // 2),
        radius=max(1, radius // 2),
        fill=highlight,
    )
    base.alpha_composite(shine)


def draw_chip(
    base: Image.Image,
    rect: Rect,
    *,
    label: str,
    font: ImageFont.ImageFont,
    fill: ColorA,
    outline: ColorA,
    highlight: ColorA,
    text_fill: ColorA,
) -> None:
    draw_soft_shadow(base, rect, rect.h // 2, offset=(0, 4), blur=9, color=(0, 0, 0, 90))
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle(rect.box, radius=rect.h // 2, fill=fill, outline=outline, width=1)
    draw.line((rect.x + 14, rect.y + 3, rect.right - 14, rect.y + 3), fill=highlight, width=1)
    label_width = text_width(draw, label, font)
    label_height = text_height(draw, label, font)
    draw_text_shadow(
        draw,
        (rect.x + (rect.w - label_width) // 2, rect.y + (rect.h - label_height) // 2 - 2),
        label,
        font=font,
        fill=text_fill,
        shadow=(0, 0, 0, 90),
        offset=(0, 1),
    )
