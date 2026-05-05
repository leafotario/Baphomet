from __future__ import annotations

import io
import json
import logging
import math
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from .asset_repository import TierAssetRepository
from .assets import TierTemplateAssetStore
from .models import TierTemplate, TierTemplateItem, TierTemplateVersion, TemplateItemType


LOGGER = logging.getLogger(__name__)


class TierTemplatePreviewRenderer:
    def __init__(
        self,
        *,
        asset_repository: TierAssetRepository,
        asset_store: TierTemplateAssetStore,
    ) -> None:
        self.asset_repository = asset_repository
        self.asset_store = asset_store

    async def render_preview(
        self,
        *,
        template: TierTemplate,
        version: TierTemplateVersion,
        items: list[TierTemplateItem],
    ) -> io.BytesIO:
        asset_bytes: dict[str, bytes] = {}
        for item in items[:48]:
            if not item.asset_id:
                continue
            asset = await self.asset_repository.get_asset(item.asset_id)
            if asset is None:
                LOGGER.warning(
                    "asset_missing surface=template_preview template_item_id=%s asset_id=%s reason=db_row_missing",
                    item.id,
                    item.asset_id,
                )
                continue
            try:
                asset_bytes[item.id] = await self.asset_store.load_asset_bytes(asset)
            except OSError:
                LOGGER.exception(
                    "asset_missing surface=template_preview template_item_id=%s asset_id=%s reason=file_unavailable",
                    item.id,
                    asset.id,
                )
                continue

        return await self._to_thread(template=template, version=version, items=items, asset_bytes=asset_bytes)

    async def _to_thread(
        self,
        *,
        template: TierTemplate,
        version: TierTemplateVersion,
        items: list[TierTemplateItem],
        asset_bytes: dict[str, bytes],
    ) -> io.BytesIO:
        import asyncio

        return await asyncio.to_thread(
            self._render_sync,
            template,
            version,
            items,
            asset_bytes,
        )

    def _render_sync(
        self,
        template: TierTemplate,
        version: TierTemplateVersion,
        items: list[TierTemplateItem],
        asset_bytes: dict[str, bytes],
    ) -> io.BytesIO:
        visible_items = items[:48]
        columns = 6
        card_w = 136
        card_h = 156
        gap = 14
        header_h = 138
        tier_h = 52
        rows = max(1, math.ceil(len(visible_items) / columns))
        width = 980
        height = header_h + tier_h + 54 + rows * (card_h + gap) + 36

        image = Image.new("RGB", (width, height), (30, 32, 38))
        draw = ImageDraw.Draw(image)
        title_font = self._font(34)
        text_font = self._font(20)
        small_font = self._font(16)
        tiny_font = self._font(13)

        draw.rectangle((0, 0, width, header_h), fill=(42, 45, 54))
        draw.text((36, 30), template.name[:80], font=title_font, fill=(245, 246, 250))
        subtitle = f"Template preview • v{version.version_number} • {len(items)} itens"
        draw.text((38, 78), subtitle, font=text_font, fill=(194, 199, 210))
        if template.description:
            draw.text((38, 106), template.description[:110], font=small_font, fill=(156, 163, 176))

        tiers = self._tiers(version.default_tiers_json)
        tier_x = 36
        tier_y = header_h + 22
        tier_gap = 8
        tier_w = max(74, min(136, (width - 72 - tier_gap * max(0, len(tiers) - 1)) // max(1, len(tiers))))
        for tier in tiers:
            color = self._hex_to_rgb(str(tier.get("color") or "#5865f2"))
            draw.rounded_rectangle((tier_x, tier_y, tier_x + tier_w, tier_y + 42), radius=6, fill=color)
            label = str(tier.get("label") or tier.get("id") or "?")[:10]
            self._center_text(draw, label, (tier_x, tier_y, tier_x + tier_w, tier_y + 42), text_font, (20, 22, 28))
            tier_x += tier_w + tier_gap

        start_x = 36
        start_y = header_h + tier_h + 54
        for index, item in enumerate(visible_items):
            col = index % columns
            row = index // columns
            x = start_x + col * (card_w + gap)
            y = start_y + row * (card_h + gap)
            self._draw_item_card(draw, image, item, asset_bytes.get(item.id), index, (x, y, card_w, card_h), small_font, tiny_font)

        if len(items) > len(visible_items):
            draw.text(
                (36, height - 28),
                f"+{len(items) - len(visible_items)} itens fora deste preview",
                font=tiny_font,
                fill=(156, 163, 176),
            )

        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        output.seek(0)
        return output

    def _draw_item_card(
        self,
        draw: ImageDraw.ImageDraw,
        canvas: Image.Image,
        item: TierTemplateItem,
        image_bytes: bytes | None,
        index: int,
        box: tuple[int, int, int, int],
        small_font: ImageFont.ImageFont,
        tiny_font: ImageFont.ImageFont,
    ) -> None:
        x, y, w, h = box
        caption = self._safe_text(item.render_caption) if item.has_visible_caption else None
        if item.item_type == TemplateItemType.IMAGE:
            if image_bytes is None:
                self._draw_missing_image_card(draw, box, tiny_font)
                return

            footer_height = 32 if caption else 0
            image_box = (x, y, x + w, y + h - footer_height)
            draw.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=(47, 51, 62), outline=(72, 78, 94), width=1)
            try:
                with Image.open(io.BytesIO(image_bytes)) as raw:
                    thumb = self._cover_image(raw, (image_box[2] - image_box[0], image_box[3] - image_box[1]))
                    self._paste_rounded(canvas, thumb, image_box, radius=8 if footer_height == 0 else 6)
            except (UnidentifiedImageError, OSError, ValueError):
                LOGGER.exception("render_failed surface=template_preview_item template_item_id=%s reason=invalid_asset", item.id)
                self._draw_missing_image_card(draw, box, tiny_font)
                return

            if caption:
                footer_box = (x, y + h - footer_height, x + w, y + h)
                draw.rectangle(footer_box, fill=(30, 32, 42))
                self._draw_single_line(draw, caption, (footer_box[0] + 8, footer_box[1] + 5, footer_box[2] - 8, footer_box[3] - 5), tiny_font, (236, 239, 244))
            return

        draw.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=(47, 51, 62), outline=(72, 78, 94), width=1)
        self._draw_wrapped_text(
            draw,
            item.render_caption,
            (x + 12, y + 12, x + w - 12, y + h - 12),
            small_font,
            (236, 239, 244),
            max_lines=5,
        )

    def _tiers(self, raw_json: str) -> list[dict[str, Any]]:
        try:
            tiers = json.loads(raw_json)
        except json.JSONDecodeError:
            return []
        if not isinstance(tiers, list):
            return []
        return [tier for tier in tiers if isinstance(tier, dict)][:10]

    def _font(self, size: int) -> ImageFont.ImageFont:
        for path in (
            "assets/fonts/DejaVuSans-Bold.ttf" if size >= 30 else "assets/fonts/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if size >= 30 else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _center_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        box: tuple[int, int, int, int],
        font: ImageFont.ImageFont,
        fill: tuple[int, int, int],
    ) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        x = box[0] + ((box[2] - box[0]) - (bbox[2] - bbox[0])) // 2
        y = box[1] + ((box[3] - box[1]) - (bbox[3] - bbox[1])) // 2
        draw.text((x, y), text, font=font, fill=fill)

    def _draw_wrapped_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str | None,
        box: tuple[int, int, int, int],
        font: ImageFont.ImageFont,
        fill: tuple[int, int, int],
        *,
        max_lines: int,
    ) -> None:
        value = self._safe_text(text)
        if value is None:
            return
        words = value.split()
        lines: list[str] = []
        current = ""
        max_width = box[2] - box[0]
        overflow = False
        for word in words:
            candidate = f"{current} {word}".strip()
            if self._text_width(draw, candidate, font) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                    current = ""
                    if len(lines) >= max_lines:
                        overflow = True
                        break
                if self._text_width(draw, word, font) <= max_width:
                    current = word
                else:
                    lines.append(self._truncate_to_width(draw, word, font, max_width))
                    current = ""
            if len(lines) >= max_lines:
                overflow = True
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        elif current:
            overflow = True
        if overflow and lines:
            lines[-1] = self._truncate_to_width(draw, f"{lines[-1].rstrip('.')}...", font, max_width)
        line_height = 16
        total_h = len(lines) * line_height
        y = box[1] + max(0, ((box[3] - box[1]) - total_h) // 2)
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            x = box[0] + max(0, (max_width - (bbox[2] - bbox[0])) // 2)
            draw.text((x, y), line, font=font, fill=fill)
            y += line_height

    def _draw_missing_image_card(
        self,
        draw: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        font: ImageFont.ImageFont,
    ) -> None:
        draw.rounded_rectangle(box, radius=8, fill=(48, 53, 65), outline=(92, 74, 74), width=1)
        self._draw_wrapped_text(draw, "Imagem indisponível", (box[0] + 8, box[1] + 8, box[2] - 8, box[3] - 8), font, (218, 224, 235), max_lines=2)

    def _cover_image(self, source: Image.Image, size: tuple[int, int]) -> Image.Image:
        image = source.convert("RGBA")
        return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))

    def _paste_rounded(
        self,
        canvas: Image.Image,
        source: Image.Image,
        box: tuple[int, int, int, int],
        *,
        radius: int,
    ) -> None:
        width = box[2] - box[0]
        height = box[3] - box[1]
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        layer.paste(source, (0, 0), source if source.mode == "RGBA" else None)
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width, height), radius=radius, fill=255)
        alpha = ImageChops.multiply(layer.getchannel("A"), mask)
        canvas.paste(layer, (box[0], box[1]), alpha)

    def _draw_single_line(
        self,
        draw: ImageDraw.ImageDraw,
        text: str | None,
        box: tuple[int, int, int, int],
        font: ImageFont.ImageFont,
        fill: tuple[int, int, int],
    ) -> None:
        value = self._safe_text(text)
        if value is None:
            return
        value = self._truncate_to_width(draw, value, font, box[2] - box[0])
        self._center_text(draw, value, box, font, fill)

    def _truncate_to_width(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
    ) -> str:
        if self._text_width(draw, text, font) <= max_width:
            return text
        suffix = "..."
        value = text
        while value and self._text_width(draw, f"{value}{suffix}", font) > max_width:
            value = value[:-1].rstrip()
        return f"{value}{suffix}" if value else suffix

    def _text_width(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]

    def _safe_text(self, value: object | None) -> str | None:
        if not isinstance(value, str):
            return None
        text = " ".join(value.split()).strip()
        if not text or text.casefold() in {"none", "null"}:
            return None
        return text

    def _hex_to_rgb(self, value: str) -> tuple[int, int, int]:
        cleaned = value.strip().lstrip("#")
        if len(cleaned) != 6:
            return (88, 101, 242)
        try:
            return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))
        except ValueError:
            return (88, 101, 242)
