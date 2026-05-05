from __future__ import annotations

import io
import json
import math
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from .asset_repository import TierAssetRepository
from .assets import TierTemplateAssetStore
from .models import TierTemplate, TierTemplateItem, TierTemplateVersion, TemplateItemType


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
                continue
            try:
                asset_bytes[item.id] = await self.asset_store.load_asset_bytes(asset)
            except OSError:
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
        draw.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=(47, 51, 62), outline=(72, 78, 94), width=1)
        media_box = (x + 8, y + 8, x + w - 8, y + 110)
        if item.item_type == TemplateItemType.IMAGE and image_bytes:
            try:
                with Image.open(io.BytesIO(image_bytes)) as raw:
                    raw = raw.convert("RGBA")
                    thumb = ImageOps.fit(raw, (media_box[2] - media_box[0], media_box[3] - media_box[1]), method=Image.Resampling.LANCZOS)
                    canvas.paste(thumb, (media_box[0], media_box[1]), thumb if thumb.mode == "RGBA" else None)
            except (UnidentifiedImageError, OSError, ValueError):
                draw.rectangle(media_box, fill=(67, 73, 89))
                self._center_text(draw, "imagem", media_box, tiny_font, (200, 205, 214))
        else:
            draw.rectangle(media_box, fill=(67, 73, 89))
            label = item.render_caption or item.internal_title or f"Item {index + 1}"
            self._draw_wrapped_text(draw, label, media_box, small_font, (236, 239, 244), max_lines=3)

        caption = item.render_caption
        if caption:
            self._draw_wrapped_text(draw, caption, (x + 9, y + 118, x + w - 9, y + h - 8), tiny_font, (236, 239, 244), max_lines=2)
        elif item.item_type == TemplateItemType.IMAGE:
            draw.text((x + 9, y + 126), "sem legenda", font=tiny_font, fill=(138, 145, 160))

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
        text: str,
        box: tuple[int, int, int, int],
        font: ImageFont.ImageFont,
        fill: tuple[int, int, int],
        *,
        max_lines: int,
    ) -> None:
        words = str(text).split()
        lines: list[str] = []
        current = ""
        max_width = box[2] - box[0]
        for word in words:
            candidate = f"{current} {word}".strip()
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        if len(lines) == max_lines and len(" ".join(words)) > len(" ".join(lines)):
            lines[-1] = lines[-1][: max(1, len(lines[-1]) - 1)].rstrip() + "…"
        line_height = 16
        total_h = len(lines) * line_height
        y = box[1] + max(0, ((box[3] - box[1]) - total_h) // 2)
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            x = box[0] + max(0, (max_width - (bbox[2] - bbox[0])) // 2)
            draw.text((x, y), line, font=font, fill=fill)
            y += line_height

    def _hex_to_rgb(self, value: str) -> tuple[int, int, int]:
        cleaned = value.strip().lstrip("#")
        if len(cleaned) != 6:
            return (88, 101, 242)
        try:
            return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))
        except ValueError:
            return (88, 101, 242)
