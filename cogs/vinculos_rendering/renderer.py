from __future__ import annotations

import asyncio
import io
import logging
import unicodedata
from dataclasses import dataclass
from threading import Lock

import discord
from PIL import Image, ImageDraw, ImageFont

from cogs.ficha.rendering.drawing import (
    Rect,
    circular_crop,
    load_rgba_from_bytes,
    rounded_mask,
)
from cogs.ficha.rendering.fonts import FontManager


LOGGER = logging.getLogger("baphomet.vinculos.renderer")

VINCULO_CARD_FILENAME = "vinculo_selado.png"

Color = tuple[int, int, int]


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

        panel = Rect(70, 70, 1460, 760)
        self._draw_panel(canvas, panel, accent)

        draw = ImageDraw.Draw(canvas)
        self._draw_title(draw, width, panel.y + 46, accent)
        self._draw_connector(canvas, accent)
        self._draw_participant(canvas, participant_a, Rect(135, 250, 560, 520), accent)
        self._draw_participant(canvas, participant_b, Rect(905, 250, 560, 520), accent)
        self._draw_center_mark(canvas, (width // 2, 430), accent)

        output = io.BytesIO()
        canvas.convert("RGBA").save(output, format="PNG")
        output.seek(0)
        return output

    def _draw_background(self, width: int, height: int, accent: Color) -> Image.Image:
        canvas = Image.new("RGBA", (width, height), (18, 18, 18, 255))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, width, 14), fill=(*accent, 180))
        draw.rectangle((0, height - 14, width, height), fill=(*accent, 180))
        return canvas

    def _draw_panel(self, canvas: Image.Image, panel: Rect, accent: Color) -> None:
        panel_fill = Image.new("RGBA", panel.size, (38, 38, 38, 255))
        canvas.paste(panel_fill, (panel.x, panel.y), rounded_mask(panel.size, 24))

        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(panel.box, radius=24, outline=(3, 3, 3, 255), width=8)
        draw.rounded_rectangle(
            (panel.x + 16, panel.y + 16, panel.right - 16, panel.bottom - 16),
            radius=18,
            outline=(*accent, 170),
            width=3,
        )

    def _draw_title(self, draw: ImageDraw.ImageDraw, width: int, y: int, accent: Color) -> None:
        title = "VINCULO SELADO"
        font = self._font(58, "display")
        bbox = self._text_bbox(draw, title, font)
        x = (width - self._box_width(bbox)) // 2
        draw.text(
            (x, y),
            title,
            font=font,
            fill=(238, 238, 238, 255),
        )
        line_y = y + self._box_height(bbox) + 22
        draw.line((width // 2 - 260, line_y, width // 2 + 260, line_y), fill=(*accent, 210), width=6)

    def _draw_connector(self, canvas: Image.Image, accent: Color) -> None:
        width, _ = canvas.size
        center = (width // 2, 430)
        draw = ImageDraw.Draw(canvas)
        line_y = center[1]
        draw.line((600, line_y, 712, line_y), fill=(*accent, 215), width=6)
        draw.line((888, line_y, 1000, line_y), fill=(*accent, 215), width=6)

    def _draw_center_mark(self, canvas: Image.Image, center: tuple[int, int], accent: Color) -> None:
        cx, cy = center
        draw = ImageDraw.Draw(canvas)
        draw.ellipse((cx - 78, cy - 78, cx + 78, cy + 78), fill=(24, 24, 24, 255), outline=(*accent, 230), width=8)
        draw.line((cx, cy - 42, cx, cy + 42), fill=(238, 238, 238, 245), width=10)
        draw.line((cx - 42, cy, cx + 42, cy), fill=(238, 238, 238, 245), width=10)

    def _draw_participant(
        self,
        canvas: Image.Image,
        participant: VinculoParticipantRenderData,
        area: Rect,
        accent: Color,
    ) -> None:
        draw = ImageDraw.Draw(canvas)
        avatar_size = 340
        avatar_x = area.x + (area.w - avatar_size) // 2
        avatar_y = area.y
        avatar_rect = Rect(avatar_x, avatar_y, avatar_size, avatar_size)
        ring_rect = Rect(avatar_x - 14, avatar_y - 14, avatar_size + 28, avatar_size + 28)

        draw.ellipse(ring_rect.box, fill=(21, 21, 21, 255), outline=(*accent, 230), width=7)

        avatar = self._avatar_image(participant, avatar_size, accent)
        canvas.alpha_composite(avatar, (avatar_rect.x, avatar_rect.y))

        name_box = Rect(area.x, avatar_y + avatar_size + 42, area.w, 118)
        name, font = self._fit_text(draw, participant.display_name, name_box.w, start_size=58, min_size=30, weight="bold")
        bbox = self._text_bbox(draw, name, font)
        text_x = name_box.x + (name_box.w - self._box_width(bbox)) // 2
        text_y = name_box.y + (name_box.h - self._box_height(bbox)) // 2 - bbox[1]
        draw.text(
            (text_x, text_y),
            name,
            font=font,
            fill=(238, 238, 238, 255),
        )

    def _avatar_image(self, participant: VinculoParticipantRenderData, size: int, accent: Color) -> Image.Image:
        source = load_rgba_from_bytes(participant.avatar_bytes)
        if source is None:
            return self._avatar_placeholder(size, participant.fallback_initials, accent)
        return circular_crop(self._to_grayscale(source), size)

    def _avatar_placeholder(self, size: int, initials: str, accent: Color) -> Image.Image:
        canvas = Image.new("RGBA", (size, size), (31, 31, 31, 255))
        mask = rounded_mask((size, size), size // 2)
        canvas.putalpha(mask)

        draw = ImageDraw.Draw(canvas)
        draw.ellipse((size * 0.17, size * 0.17, size * 0.83, size * 0.83), outline=(*accent, 150), width=max(3, size // 35))
        text = initials[:2].upper() or "?"
        font = self._font(max(38, size // 3), "display")
        bbox = self._text_bbox(draw, text, font)
        draw.text(
            ((size - self._box_width(bbox)) // 2, (size - self._box_height(bbox)) // 2 - bbox[1]),
            text,
            font=font,
            fill=(238, 238, 238, 255),
        )
        return canvas

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
            return (140, 140, 140)
        red, green, blue = (max(0, min(255, int(channel))) for channel in accent)
        gray = int(red * 0.299 + green * 0.587 + blue * 0.114)
        gray = max(105, min(170, gray))
        return (gray, gray, gray)

    @staticmethod
    def _to_grayscale(image: Image.Image) -> Image.Image:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        gray = rgba.convert("L")
        return Image.merge("RGBA", (gray, gray, gray, alpha))

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
