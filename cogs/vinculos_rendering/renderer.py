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
    draw_soft_shadow,
    load_rgba_from_bytes,
    rounded_mask,
    vertical_gradient,
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

        panel = Rect(92, 90, 1416, 720)
        self._draw_panel(canvas, panel, accent)

        draw = ImageDraw.Draw(canvas)
        self._draw_title(draw, width, panel.y + 54, accent)
        self._draw_connector(canvas, accent)
        self._draw_participant(canvas, participant_a, Rect(220, 245, 470, 430), accent)
        self._draw_participant(canvas, participant_b, Rect(910, 245, 470, 430), accent)
        self._draw_center_mark(canvas, (width // 2, 410), accent)

        output = io.BytesIO()
        canvas.convert("RGBA").save(output, format="PNG")
        output.seek(0)
        return output

    def _draw_background(self, width: int, height: int, accent: Color) -> Image.Image:
        canvas = vertical_gradient((width, height), (13, 13, 15, 255), (25, 23, 27, 255))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, width, 18), fill=(*accent, 140))
        draw.rectangle((0, height - 18, width, height), fill=(5, 5, 6, 255))
        draw.rectangle((0, height - 24, width, height - 18), fill=(*accent, 90))
        return canvas

    def _draw_panel(self, canvas: Image.Image, panel: Rect, accent: Color) -> None:
        draw_soft_shadow(
            canvas,
            panel,
            36,
            offset=(0, 16),
            blur=18,
            spread=0,
            color=(0, 0, 0, 130),
        )
        panel_fill = Image.new("RGBA", panel.size, (42, 41, 44, 255))
        canvas.paste(panel_fill, (panel.x, panel.y), rounded_mask(panel.size, 36))

        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(panel.box, radius=36, outline=(4, 4, 5, 255), width=10)
        draw.rounded_rectangle(
            (panel.x + 18, panel.y + 18, panel.right - 18, panel.bottom - 18),
            radius=24,
            outline=(*accent, 120),
            width=2,
        )

    def _draw_title(self, draw: ImageDraw.ImageDraw, width: int, y: int, accent: Color) -> None:
        title = "VINCULO SELADO"
        font = self._font(42, "display")
        bbox = self._text_bbox(draw, title, font)
        x = (width - self._box_width(bbox)) // 2
        draw.text(
            (x, y),
            title,
            font=font,
            fill=(238, 233, 224, 255),
        )
        line_y = y + self._box_height(bbox) + 24
        draw.line((width // 2 - 190, line_y, width // 2 + 190, line_y), fill=(*accent, 175), width=4)

    def _draw_connector(self, canvas: Image.Image, accent: Color) -> None:
        width, _ = canvas.size
        center = (width // 2, 410)
        draw = ImageDraw.Draw(canvas)
        line_y = center[1]
        draw.line((610, line_y, 730, line_y), fill=(18, 17, 20, 255), width=10)
        draw.line((870, line_y, 990, line_y), fill=(18, 17, 20, 255), width=10)
        draw.line((610, line_y, 730, line_y), fill=(*accent, 190), width=3)
        draw.line((870, line_y, 990, line_y), fill=(*accent, 190), width=3)

    def _draw_center_mark(self, canvas: Image.Image, center: tuple[int, int], accent: Color) -> None:
        cx, cy = center
        draw = ImageDraw.Draw(canvas)
        draw.ellipse((cx - 70, cy - 70, cx + 70, cy + 70), fill=(28, 26, 31, 255), outline=(8, 8, 10, 255), width=8)
        draw.ellipse((cx - 52, cy - 52, cx + 52, cy + 52), outline=(*accent, 210), width=4)
        draw.line((cx, cy - 32, cx, cy + 32), fill=(238, 233, 224, 235), width=7)
        draw.line((cx - 32, cy, cx + 32, cy), fill=(238, 233, 224, 235), width=7)
        draw.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=(*accent, 255))

    def _draw_participant(
        self,
        canvas: Image.Image,
        participant: VinculoParticipantRenderData,
        area: Rect,
        accent: Color,
    ) -> None:
        draw = ImageDraw.Draw(canvas)
        avatar_size = 260
        avatar_x = area.x + (area.w - avatar_size) // 2
        avatar_y = area.y
        avatar_rect = Rect(avatar_x, avatar_y, avatar_size, avatar_size)
        ring_rect = Rect(avatar_x - 13, avatar_y - 13, avatar_size + 26, avatar_size + 26)

        draw_soft_shadow(canvas, ring_rect, ring_rect.w // 2, offset=(0, 8), blur=10, color=(0, 0, 0, 105))
        draw.ellipse(ring_rect.box, fill=(21, 20, 23, 255), outline=(*accent, 215), width=5)

        avatar = self._avatar_image(participant, avatar_size, accent)
        canvas.alpha_composite(avatar, (avatar_rect.x, avatar_rect.y))

        name_box = Rect(area.x, avatar_y + avatar_size + 48, area.w, 84)
        name, font = self._fit_text(draw, participant.display_name, name_box.w, start_size=40, min_size=25, weight="bold")
        bbox = self._text_bbox(draw, name, font)
        text_x = name_box.x + (name_box.w - self._box_width(bbox)) // 2
        text_y = name_box.y + (name_box.h - self._box_height(bbox)) // 2 - bbox[1]
        draw.text(
            (text_x, text_y),
            name,
            font=font,
            fill=(238, 233, 224, 255),
        )

    def _avatar_image(self, participant: VinculoParticipantRenderData, size: int, accent: Color) -> Image.Image:
        source = load_rgba_from_bytes(participant.avatar_bytes)
        if source is None:
            return self._avatar_placeholder(size, participant.fallback_initials, accent)
        return circular_crop(source, size)

    def _avatar_placeholder(self, size: int, initials: str, accent: Color) -> Image.Image:
        canvas = Image.new("RGBA", (size, size), (31, 29, 35, 255))
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
            fill=(238, 233, 224, 255),
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
