from __future__ import annotations

import asyncio
import io
import logging
import random
import unicodedata
from dataclasses import dataclass
from threading import Lock

import discord
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from cogs.ficha.rendering.drawing import (
    Rect,
    add_noise_overlay,
    circular_crop,
    create_avatar_placeholder,
    draw_soft_shadow,
    load_rgba_from_bytes,
    rounded_mask,
    vertical_gradient,
)
from cogs.ficha.rendering.fonts import FontManager


LOGGER = logging.getLogger("baphomet.vinculos.renderer")

VINCULO_CARD_FILENAME = "vinculo_selado.png"

Color = tuple[int, int, int]
ColorA = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class VinculoParticipantRenderData:
    display_name: str
    avatar_bytes: bytes | None
    fallback_initials: str


class VinculoCardRenderer:
    """Renderiza o card publico de vinculo sem acoplar Pillow ao Cog."""

    def __init__(self, *, fonts: FontManager | None = None) -> None:
        self.fonts = fonts or FontManager()
        self._font_lock = Lock()

    async def render(
        self,
        *,
        participant_a: discord.Member | discord.User | None,
        participant_b: discord.Member | discord.User | None,
        accent: Color,
        fallback_name_a: str = "Usuario 1",
        fallback_name_b: str = "Usuario 2",
    ) -> io.BytesIO:
        avatar_a, avatar_b = await asyncio.gather(
            self._read_avatar_bytes(participant_a),
            self._read_avatar_bytes(participant_b),
        )
        data_a = VinculoParticipantRenderData(
            display_name=self._display_name(participant_a, fallback_name_a),
            avatar_bytes=avatar_a,
            fallback_initials=self._initials(self._display_name(participant_a, fallback_name_a)),
        )
        data_b = VinculoParticipantRenderData(
            display_name=self._display_name(participant_b, fallback_name_b),
            avatar_bytes=avatar_b,
            fallback_initials=self._initials(self._display_name(participant_b, fallback_name_b)),
        )
        return await asyncio.to_thread(self._render_sync, data_a, data_b, accent)

    async def _read_avatar_bytes(self, participant: discord.Member | discord.User | None) -> bytes | None:
        if participant is None:
            return None
        asset = getattr(participant, "display_avatar", None)
        if asset is None:
            return None
        try:
            return await asset.replace(format="png", size=512).read()
        except (discord.HTTPException, TypeError, ValueError, OSError):
            LOGGER.warning("falha ao ler avatar para card de vinculo user_id=%s", getattr(participant, "id", None))
            return None

    def _render_sync(
        self,
        participant_a: VinculoParticipantRenderData,
        participant_b: VinculoParticipantRenderData,
        accent: Color,
    ) -> io.BytesIO:
        width, height = 1600, 900
        accent = self._normalize_accent(accent)
        canvas = self._draw_background(width, height, accent)

        panel = Rect(92, 90, 1416, 720)
        self._draw_panel(canvas, panel, accent)

        draw = ImageDraw.Draw(canvas)
        self._draw_title(draw, width, panel.y + 52, accent)
        self._draw_connector(canvas, accent)
        self._draw_participant(canvas, participant_a, Rect(205, 220, 510, 500), accent)
        self._draw_participant(canvas, participant_b, Rect(885, 220, 510, 500), accent)
        self._draw_sigil(canvas, (width // 2, 445), accent)

        output = io.BytesIO()
        canvas.convert("RGBA").save(output, format="PNG")
        output.seek(0)
        return output

    def _draw_background(self, width: int, height: int, accent: Color) -> Image.Image:
        canvas = vertical_gradient((width, height), (8, 8, 11, 255), (31, 28, 36, 255))
        add_noise_overlay(canvas, opacity=34, seed=317, scale=3)

        texture = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(texture)
        rng = random.Random(923)
        for _ in range(340):
            x = rng.randint(-120, width + 120)
            y = rng.randint(-80, height + 80)
            length = rng.randint(60, 260)
            shade = rng.choice((210, 225, 245, 20))
            alpha = rng.randint(8, 34)
            draw.line(
                (x, y, x + length, y + rng.randint(-28, 28)),
                fill=(shade, shade, shade, alpha),
                width=rng.randint(1, 4),
            )
        texture = texture.filter(ImageFilter.GaussianBlur(0.55))
        canvas.alpha_composite(texture)

        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.ellipse((width // 2 - 470, 110, width // 2 + 470, 760), fill=(*accent, 34))
        glow = glow.filter(ImageFilter.GaussianBlur(90))
        canvas.alpha_composite(glow)

        vignette = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        vignette_draw = ImageDraw.Draw(vignette)
        for index in range(110):
            alpha = max(0, int(180 * (1 - index / 110) ** 2))
            vignette_draw.rectangle(
                (index, index, width - index - 1, height - index - 1),
                outline=(0, 0, 0, alpha),
                width=2,
            )
        canvas.alpha_composite(vignette)
        return canvas

    def _draw_panel(self, canvas: Image.Image, panel: Rect, accent: Color) -> None:
        draw_soft_shadow(
            canvas,
            panel,
            64,
            offset=(0, 22),
            blur=34,
            spread=8,
            color=(0, 0, 0, 190),
        )
        panel_fill = vertical_gradient(panel.size, (44, 44, 46, 244), (24, 23, 28, 244))
        add_noise_overlay(panel_fill, opacity=16, seed=911, scale=4)
        canvas.paste(panel_fill, (panel.x, panel.y), rounded_mask(panel.size, 64))

        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(panel.box, radius=64, outline=(0, 0, 0, 255), width=18)
        draw.rounded_rectangle(
            (panel.x + 18, panel.y + 18, panel.right - 18, panel.bottom - 18),
            radius=48,
            outline=(*accent, 120),
            width=3,
        )
        draw.rounded_rectangle(
            (panel.x + 30, panel.y + 30, panel.right - 30, panel.bottom - 30),
            radius=38,
            outline=(230, 220, 210, 34),
            width=2,
        )

    def _draw_title(self, draw: ImageDraw.ImageDraw, width: int, y: int, accent: Color) -> None:
        title = "VINCULO SELADO"
        font = self._font(48, "display")
        bbox = self._text_bbox(draw, title, font)
        x = (width - self._box_width(bbox)) // 2
        draw.text(
            (x, y),
            title,
            font=font,
            fill=(245, 238, 226, 235),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 210),
        )
        line_y = y + self._box_height(bbox) + 28
        draw.line((width // 2 - 260, line_y, width // 2 + 260, line_y), fill=(*accent, 145), width=3)
        draw.line((width // 2 - 130, line_y + 9, width // 2 + 130, line_y + 9), fill=(245, 238, 226, 72), width=2)

    def _draw_connector(self, canvas: Image.Image, accent: Color) -> None:
        width, height = canvas.size
        center = (width // 2, 445)
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        left_start = (560, center[1])
        right_end = (1040, center[1])
        glow_draw.line((left_start, center), fill=(*accent, 135), width=18)
        glow_draw.line((center, right_end), fill=(*accent, 135), width=18)
        glow = glow.filter(ImageFilter.GaussianBlur(13))
        canvas.alpha_composite(glow)

        draw = ImageDraw.Draw(canvas)
        draw.line((left_start, center), fill=(8, 6, 9, 245), width=12)
        draw.line((center, right_end), fill=(8, 6, 9, 245), width=12)
        draw.line((left_start, center), fill=(*accent, 210), width=4)
        draw.line((center, right_end), fill=(*accent, 210), width=4)
        for x in (610, 990):
            draw.ellipse((x - 11, center[1] - 11, x + 11, center[1] + 11), fill=(*accent, 215))
            draw.ellipse((x - 5, center[1] - 5, x + 5, center[1] + 5), fill=(245, 238, 226, 210))

    def _draw_sigil(self, canvas: Image.Image, center: tuple[int, int], accent: Color) -> None:
        cx, cy = center
        glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.ellipse((cx - 142, cy - 142, cx + 142, cy + 142), fill=(*accent, 72))
        glow = glow.filter(ImageFilter.GaussianBlur(24))
        canvas.alpha_composite(glow)

        draw = ImageDraw.Draw(canvas)
        draw.ellipse((cx - 92, cy - 92, cx + 92, cy + 92), outline=(3, 3, 5, 245), width=12)
        draw.ellipse((cx - 82, cy - 82, cx + 82, cy + 82), outline=(*accent, 225), width=4)
        draw.ellipse((cx - 57, cy - 57, cx + 57, cy + 57), outline=(245, 238, 226, 82), width=2)
        draw.polygon(
            ((cx, cy - 74), (cx + 58, cy), (cx, cy + 74), (cx - 58, cy)),
            outline=(*accent, 190),
        )
        draw.line((cx, cy - 58, cx, cy + 58), fill=(245, 238, 226, 230), width=8)
        draw.line((cx - 58, cy, cx + 58, cy), fill=(245, 238, 226, 230), width=8)
        draw.line((cx - 38, cy - 38, cx + 38, cy + 38), fill=(*accent, 170), width=3)
        draw.line((cx + 38, cy - 38, cx - 38, cy + 38), fill=(*accent, 170), width=3)
        draw.ellipse((cx - 13, cy - 13, cx + 13, cy + 13), fill=(245, 238, 226, 245))

    def _draw_participant(
        self,
        canvas: Image.Image,
        participant: VinculoParticipantRenderData,
        area: Rect,
        accent: Color,
    ) -> None:
        draw = ImageDraw.Draw(canvas)
        avatar_size = 292
        avatar_x = area.x + (area.w - avatar_size) // 2
        avatar_y = area.y
        avatar_rect = Rect(avatar_x, avatar_y, avatar_size, avatar_size)
        ring_rect = Rect(avatar_x - 17, avatar_y - 17, avatar_size + 34, avatar_size + 34)

        draw_soft_shadow(canvas, ring_rect, ring_rect.w // 2, offset=(0, 18), blur=22, color=(0, 0, 0, 190))
        ring = Image.new("RGBA", ring_rect.size, (0, 0, 0, 0))
        ring_draw = ImageDraw.Draw(ring)
        ring_draw.ellipse((0, 0, ring_rect.w - 1, ring_rect.h - 1), fill=(8, 7, 10, 255))
        ring_draw.ellipse((9, 9, ring_rect.w - 10, ring_rect.h - 10), outline=(*accent, 230), width=7)
        ring_draw.ellipse((23, 23, ring_rect.w - 24, ring_rect.h - 24), outline=(245, 238, 226, 64), width=2)
        canvas.alpha_composite(ring, (ring_rect.x, ring_rect.y))

        avatar = self._avatar_image(participant, avatar_size, accent)
        canvas.alpha_composite(avatar, (avatar_rect.x, avatar_rect.y))

        name_box = Rect(area.x, avatar_y + avatar_size + 54, area.w, 96)
        name, font = self._fit_text(draw, participant.display_name, name_box.w, start_size=44, min_size=26, weight="bold")
        bbox = self._text_bbox(draw, name, font)
        text_x = name_box.x + (name_box.w - self._box_width(bbox)) // 2
        text_y = name_box.y + (name_box.h - self._box_height(bbox)) // 2 - bbox[1]
        draw.text(
            (text_x + 3, text_y + 4),
            name,
            font=font,
            fill=(0, 0, 0, 185),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 120),
        )
        draw.text(
            (text_x, text_y),
            name,
            font=font,
            fill=(248, 244, 236, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 210),
        )

    def _avatar_image(self, participant: VinculoParticipantRenderData, size: int, accent: Color) -> Image.Image:
        source = load_rgba_from_bytes(participant.avatar_bytes)
        if source is None:
            return create_avatar_placeholder(
                size,
                initials=participant.fallback_initials,
                font=self._font(90, "display"),
                fill_top=(38, 34, 47, 255),
                fill_bottom=(14, 13, 18, 255),
                accent=(*accent, 210),
                text_fill=(248, 244, 236, 255),
            )
        return circular_crop(source, size)

    def _fit_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        max_width: int,
        *,
        start_size: int,
        min_size: int,
        weight: str,
    ) -> tuple[str, ImageFont.ImageFont]:
        clean = self._clean_name(text)
        for size in range(start_size, min_size - 1, -2):
            font = self._font(size, weight)
            fitted = self._truncate_to_width(draw, clean, font, max_width)
            if self._text_width(draw, fitted, font) <= max_width:
                return fitted, font
        font = self._font(min_size, weight)
        return self._truncate_to_width(draw, clean, font, max_width), font

    def _truncate_to_width(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
        if self._text_width(draw, text, font) <= max_width:
            return text
        suffix = "..."
        left, right = 0, len(text)
        best = suffix
        while left <= right:
            mid = (left + right) // 2
            candidate = text[:mid].rstrip()
            if not candidate:
                right = mid - 1
                continue
            candidate = f"{candidate}{suffix}"
            if self._text_width(draw, candidate, font) <= max_width:
                best = candidate
                left = mid + 1
            else:
                right = mid - 1
        if self._text_width(draw, best, font) > max_width:
            return text[: max(1, len(text) // 2)].rstrip()
        return best

    def _font(self, size: int, weight: str = "regular") -> ImageFont.ImageFont:
        try:
            with self._font_lock:
                return self.fonts.font(size, weight)
        except Exception:
            try:
                return ImageFont.load_default(size=size)
            except TypeError:
                return ImageFont.load_default()

    @staticmethod
    def _display_name(participant: discord.Member | discord.User | None, fallback: str) -> str:
        if participant is None:
            return fallback
        value = getattr(participant, "display_name", None) or getattr(participant, "global_name", None)
        return str(value or getattr(participant, "name", None) or fallback)

    @staticmethod
    def _clean_name(value: str) -> str:
        text = "".join(char for char in str(value) if not unicodedata.category(char).startswith("C"))
        text = " ".join(text.split())
        return text or "Alma sem nome"

    @classmethod
    def _initials(cls, value: str) -> str:
        clean = cls._clean_name(value)
        pieces = ["".join(char for char in part if char.isalnum()) for part in clean.split()]
        letters = [piece[0] for piece in pieces if piece]
        if len(letters) >= 2:
            return "".join(letters[:2])
        if letters:
            return letters[0]
        return "?"

    @staticmethod
    def _normalize_accent(accent: Color) -> Color:
        if len(accent) != 3:
            return (132, 48, 79)
        return tuple(max(0, min(255, int(channel))) for channel in accent)  # type: ignore[return-value]

    @staticmethod
    def _text_bbox(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
    ) -> tuple[int, int, int, int]:
        try:
            return draw.textbbox((0, 0), text, font=font)
        except UnicodeError:
            safe = text.encode("utf-8", "ignore").decode("utf-8", "ignore")
            return draw.textbbox((0, 0), safe, font=font)

    @classmethod
    def _text_width(cls, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> float:
        bbox = cls._text_bbox(draw, text, font)
        return float(cls._box_width(bbox))

    @staticmethod
    def _box_width(box: tuple[int, int, int, int]) -> int:
        return box[2] - box[0]

    @staticmethod
    def _box_height(box: tuple[int, int, int, int]) -> int:
        return box[3] - box[1]
