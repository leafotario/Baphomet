from __future__ import annotations

import asyncio
import io
import json
import logging
import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from .asset_repository import TierAssetRepository
from .assets import TierTemplateAssetStore
from .models import TemplateItemType, TierSession, TierSessionItem, TierTemplate, TierTemplateItem, TierTemplateVersion
from .session_repository import TierSessionRepository
from .template_repository import TierTemplateRepository


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionRenderSnapshot:
    template: TierTemplate
    version: TierTemplateVersion
    session: TierSession
    session_items: list[TierSessionItem]
    template_items_by_id: dict[str, TierTemplateItem]
    tiers: list[dict[str, Any]]


@dataclass(frozen=True)
class RenderTier:
    id: str
    label: str
    color: tuple[int, int, int]


@dataclass(frozen=True)
class RenderItem:
    item_type: TemplateItemType
    image_bytes: bytes | None
    render_caption: str | None
    has_visible_caption: bool
    position: int
    debug_id: str


@dataclass(frozen=True)
class RenderSessionPayload:
    template_name: str
    tiers: list[RenderTier]
    items_by_tier: OrderedDict[str, list[RenderItem]]
    unused_count: int
    allocated_count: int
    author_name: str


class TierSessionRenderer:
    def __init__(
        self,
        *,
        template_repository: TierTemplateRepository,
        session_repository: TierSessionRepository,
        asset_repository: TierAssetRepository,
        asset_store: TierTemplateAssetStore,
    ) -> None:
        self.template_repository = template_repository
        self.session_repository = session_repository
        self.asset_repository = asset_repository
        self.asset_store = asset_store

    async def build_snapshot(self, session_id: str) -> SessionRenderSnapshot:
        session = await self.session_repository.get_session(session_id)
        if session is None:
            raise ValueError("Sessão não encontrada.")
        version = await self.template_repository.get_template_version(session.template_version_id)
        if version is None:
            raise ValueError("Versão do template não encontrada.")
        template = await self.template_repository.get_template_by_id(version.template_id)
        if template is None:
            raise ValueError("Template não encontrado.")
        template_items = await self.template_repository.list_template_items(version.id)
        session_items = await self.session_repository.list_session_items(session.id)
        tiers = self._tiers(version.default_tiers_json)
        return SessionRenderSnapshot(
            template=template,
            version=version,
            session=session,
            session_items=session_items,
            template_items_by_id={item.id: item for item in template_items},
            tiers=tiers,
        )

    async def render_session(self, session_id: str, *, author: object | None = None) -> io.BytesIO:
        snapshot = await self.build_snapshot(session_id)
        payload = await self.build_payload(snapshot, author=author)
        return await asyncio.to_thread(self.render_payload, payload)

    async def build_payload(
        self,
        snapshot: SessionRenderSnapshot,
        *,
        author: object | None = None,
    ) -> RenderSessionPayload:
        allocated = [item for item in snapshot.session_items if not item.is_unused and item.current_tier_id]
        asset_bytes = await self._load_asset_bytes(snapshot, allocated)
        render_tiers = self._render_tiers(snapshot.tiers)
        tier_ids = {tier.id for tier in render_tiers}
        items_by_tier: OrderedDict[str, list[RenderItem]] = OrderedDict((tier.id, []) for tier in render_tiers)

        for session_item in sorted(allocated, key=lambda item: (item.current_tier_id or "", item.position, item.created_at)):
            if session_item.current_tier_id not in tier_ids:
                LOGGER.warning(
                    "Session item com tier desconhecida ignorado no render session_id=%s session_item_id=%s tier_id=%s.",
                    snapshot.session.id,
                    session_item.id,
                    session_item.current_tier_id,
                )
                continue
            template_item = snapshot.template_items_by_id.get(session_item.template_item_id)
            if template_item is None:
                LOGGER.warning(
                    "Template item ausente no render session_id=%s session_item_id=%s template_item_id=%s.",
                    snapshot.session.id,
                    session_item.id,
                    session_item.template_item_id,
                )
                continue
            caption = self._safe_text(template_item.render_caption)
            items_by_tier[session_item.current_tier_id].append(
                RenderItem(
                    item_type=template_item.item_type,
                    image_bytes=asset_bytes.get(session_item.id),
                    render_caption=caption,
                    has_visible_caption=bool(template_item.has_visible_caption and caption),
                    position=session_item.position,
                    debug_id=session_item.id,
                )
            )

        return RenderSessionPayload(
            template_name=snapshot.template.name,
            tiers=render_tiers,
            items_by_tier=items_by_tier,
            unused_count=sum(1 for item in snapshot.session_items if item.is_unused),
            allocated_count=sum(1 for item in snapshot.session_items if not item.is_unused),
            author_name=self._author_name(author),
        )

    async def _load_asset_bytes(
        self,
        snapshot: SessionRenderSnapshot,
        session_items: list[TierSessionItem],
    ) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for session_item in session_items:
            template_item = snapshot.template_items_by_id.get(session_item.template_item_id)
            if template_item is None or template_item.item_type != TemplateItemType.IMAGE:
                continue
            if not template_item.asset_id:
                LOGGER.warning(
                    "Item de imagem sem asset_id no render session_id=%s session_item_id=%s template_item_id=%s.",
                    snapshot.session.id,
                    session_item.id,
                    session_item.template_item_id,
                )
                continue
            asset = await self.asset_repository.get_asset(template_item.asset_id)
            if asset is None:
                LOGGER.warning(
                    "Asset ausente no render session_id=%s session_item_id=%s asset_id=%s.",
                    snapshot.session.id,
                    session_item.id,
                    template_item.asset_id,
                )
                continue
            try:
                result[session_item.id] = await self.asset_store.load_asset_bytes(asset)
            except OSError:
                LOGGER.exception(
                    "Falha ao carregar asset local no render session_id=%s session_item_id=%s asset_id=%s.",
                    snapshot.session.id,
                    session_item.id,
                    asset.id,
                )
        return result

    def render_payload(self, payload: RenderSessionPayload) -> io.BytesIO:
        canvas_width = 1200
        margin_x = 40
        header_height = 128
        footer_height = 52
        tier_label_width = 120
        item_size = 120
        item_gap = 8
        tier_padding = 10
        tier_gap = 12
        min_tier_height = item_size + tier_padding * 2
        available_width = canvas_width - margin_x * 2 - tier_label_width - tier_padding * 2
        items_per_row = max(1, (available_width + item_gap) // (item_size + item_gap))

        tier_layouts: list[tuple[RenderTier, int, int]] = []
        for tier in payload.tiers:
            item_count = len(payload.items_by_tier.get(tier.id, []))
            rows = max(1, math.ceil(item_count / items_per_row))
            tier_height = max(min_tier_height, rows * item_size + (rows - 1) * item_gap + tier_padding * 2)
            tier_layouts.append((tier, rows, tier_height))

        if not tier_layouts:
            tier_layouts.append((RenderTier(id="?", label="?", color=(88, 101, 242)), 1, min_tier_height))
            payload.items_by_tier.setdefault("?", [])

        canvas_height = (
            header_height
            + footer_height
            + sum(tier_height for _, _, tier_height in tier_layouts)
            + tier_gap * max(0, len(tier_layouts) - 1)
        )
        image = Image.new("RGB", (canvas_width, canvas_height), (18, 19, 27))
        draw = ImageDraw.Draw(image)

        title_font = self._font(36, bold=True)
        subtitle_font = self._font(19)
        footer_font = self._font(14)
        tier_font = self._font(34, bold=True)
        caption_font = self._font(13)
        text_card_font = self._font(17, bold=True)
        placeholder_font = self._font(14)

        draw.rectangle((0, 0, canvas_width, header_height), fill=(34, 37, 48))
        title = self._safe_text(payload.template_name) or "Tier List"
        self._draw_single_line(draw, title, (margin_x, 28, canvas_width - margin_x, 70), title_font, (245, 246, 250), align="left")
        subtitle = f"{payload.allocated_count} alocados • {payload.unused_count} no inventário"
        draw.text((margin_x, 82), subtitle, font=subtitle_font, fill=(190, 197, 210))

        y = header_height
        for tier, _, tier_height in tier_layouts:
            row_box = (margin_x, y, canvas_width - margin_x, y + tier_height)
            label_box = (margin_x, y, margin_x + tier_label_width, y + tier_height)
            content_box = (margin_x + tier_label_width, y, canvas_width - margin_x, y + tier_height)
            draw.rounded_rectangle(row_box, radius=8, fill=(30, 32, 42))
            draw.rounded_rectangle(label_box, radius=8, fill=tier.color)
            self._center_text(draw, tier.label[:10], label_box, tier_font, (18, 19, 27))

            items = payload.items_by_tier.get(tier.id, [])
            if not items:
                self._center_text(draw, "vazio", content_box, subtitle_font, (126, 135, 152))
            else:
                start_x = margin_x + tier_label_width + tier_padding
                start_y = y + tier_padding
                for item_index, item in enumerate(sorted(items, key=lambda entry: entry.position)):
                    col = item_index % items_per_row
                    row = item_index // items_per_row
                    x = start_x + col * (item_size + item_gap)
                    item_y = start_y + row * (item_size + item_gap)
                    self._draw_render_item(
                        draw,
                        image,
                        item,
                        (x, item_y, x + item_size, item_y + item_size),
                        caption_font=caption_font,
                        text_card_font=text_card_font,
                        placeholder_font=placeholder_font,
                    )
            y += tier_height + tier_gap

        footer_text = f"Sessão de {payload.author_name}" if payload.author_name else "Sessão de tier list"
        self._draw_single_line(draw, footer_text, (margin_x, canvas_height - 38, canvas_width - margin_x, canvas_height - 14), footer_font, (150, 158, 174), align="left")

        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        output.seek(0)
        return output

    def _draw_render_item(
        self,
        draw: ImageDraw.ImageDraw,
        canvas: Image.Image,
        item: RenderItem,
        box: tuple[int, int, int, int],
        *,
        caption_font: ImageFont.ImageFont,
        text_card_font: ImageFont.ImageFont,
        placeholder_font: ImageFont.ImageFont,
    ) -> None:
        if item.item_type == TemplateItemType.TEXT_ONLY:
            self._draw_text_card(draw, item.render_caption, box, text_card_font)
            return

        if item.image_bytes is None:
            self._draw_missing_image_card(draw, box, placeholder_font)
            return

        caption = self._safe_text(item.render_caption) if item.has_visible_caption else None
        footer_height = 30 if caption else 0
        image_box = (box[0], box[1], box[2], box[3] - footer_height)

        draw.rounded_rectangle(box, radius=8, fill=(47, 52, 66), outline=(76, 84, 104), width=1)
        try:
            with Image.open(io.BytesIO(item.image_bytes)) as source:
                fitted = self._cover_image(source, (image_box[2] - image_box[0], image_box[3] - image_box[1]))
                self._paste_rounded(canvas, fitted, image_box, radius=8 if footer_height == 0 else 6)
        except (UnidentifiedImageError, OSError, ValueError):
            LOGGER.exception("Asset local inválido durante render session_item_id=%s.", item.debug_id)
            self._draw_missing_image_card(draw, box, placeholder_font)
            return

        if caption:
            footer_box = (box[0], box[3] - footer_height, box[2], box[3])
            draw.rectangle(footer_box, fill=(30, 32, 42))
            self._draw_single_line(draw, caption, (footer_box[0] + 6, footer_box[1] + 4, footer_box[2] - 6, footer_box[3] - 4), caption_font, (240, 242, 248))

    def _draw_text_card(
        self,
        draw: ImageDraw.ImageDraw,
        caption: str | None,
        box: tuple[int, int, int, int],
        font: ImageFont.ImageFont,
    ) -> None:
        draw.rounded_rectangle(box, radius=8, fill=(47, 52, 66), outline=(76, 84, 104), width=1)
        text = self._safe_text(caption)
        if text is None:
            return
        self._draw_wrapped_center(draw, text, (box[0] + 10, box[1] + 10, box[2] - 10, box[3] - 10), font, (240, 242, 248), max_lines=4)

    def _draw_missing_image_card(
        self,
        draw: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        font: ImageFont.ImageFont,
    ) -> None:
        draw.rounded_rectangle(box, radius=8, fill=(48, 53, 65), outline=(92, 74, 74), width=1)
        self._draw_wrapped_center(draw, "Imagem indisponível", (box[0] + 8, box[1] + 8, box[2] - 8, box[3] - 8), font, (218, 224, 235), max_lines=2)

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

    def _tiers(self, raw_json: str) -> list[dict[str, Any]]:
        try:
            tiers = json.loads(raw_json)
        except json.JSONDecodeError:
            return []
        if not isinstance(tiers, list):
            return []
        return [tier for tier in tiers if isinstance(tier, dict)]

    def _render_tiers(self, tiers: list[dict[str, Any]]) -> list[RenderTier]:
        result: list[RenderTier] = []
        seen: set[str] = set()
        for tier in tiers:
            tier_id = self._safe_tier_text(tier.get("id")) or self._safe_tier_text(tier.get("label")) or "?"
            if tier_id in seen:
                continue
            seen.add(tier_id)
            label = self._safe_tier_text(tier.get("label")) or tier_id
            color = self._hex_to_rgb(str(tier.get("color") or "")) or (88, 101, 242)
            result.append(RenderTier(id=tier_id, label=label, color=color))
        return result

    def _hex_to_rgb(self, value: str) -> tuple[int, int, int] | None:
        cleaned = value.strip().lstrip("#")
        if len(cleaned) != 6:
            return None
        try:
            return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))
        except ValueError:
            return None

    def _font(self, size: int, *, bold: bool = False) -> ImageFont.ImageFont:
        for path in (
            "assets/fonts/DejaVuSans-Bold.ttf" if bold else "assets/fonts/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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
        value = self._safe_tier_text(text)
        if value is None:
            return
        bbox = draw.textbbox((0, 0), value, font=font)
        x = box[0] + ((box[2] - box[0]) - (bbox[2] - bbox[0])) // 2
        y = box[1] + ((box[3] - box[1]) - (bbox[3] - bbox[1])) // 2
        draw.text((x, y), value, font=font, fill=fill)

    def _draw_single_line(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        box: tuple[int, int, int, int],
        font: ImageFont.ImageFont,
        fill: tuple[int, int, int],
        *,
        align: str = "center",
    ) -> None:
        value = self._safe_text(text)
        if value is None:
            return
        value = self._truncate_to_width(draw, value, font, box[2] - box[0])
        bbox = draw.textbbox((0, 0), value, font=font)
        if align == "left":
            x = box[0]
        else:
            x = box[0] + max(0, ((box[2] - box[0]) - (bbox[2] - bbox[0])) // 2)
        y = box[1] + max(0, ((box[3] - box[1]) - (bbox[3] - bbox[1])) // 2)
        draw.text((x, y), value, font=font, fill=fill)

    def _draw_wrapped_center(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        box: tuple[int, int, int, int],
        font: ImageFont.ImageFont,
        fill: tuple[int, int, int],
        *,
        max_lines: int,
    ) -> None:
        value = self._safe_text(text)
        if value is None:
            return
        lines = self._wrap_text(draw, value, font, box[2] - box[0], max_lines=max_lines)
        if not lines:
            return
        line_heights = [draw.textbbox((0, 0), line, font=font)[3] - draw.textbbox((0, 0), line, font=font)[1] for line in lines]
        line_gap = 3
        total_height = sum(line_heights) + line_gap * max(0, len(lines) - 1)
        y = box[1] + max(0, ((box[3] - box[1]) - total_height) // 2)
        for line, line_height in zip(lines, line_heights):
            bbox = draw.textbbox((0, 0), line, font=font)
            x = box[0] + max(0, ((box[2] - box[0]) - (bbox[2] - bbox[0])) // 2)
            draw.text((x, y), line, font=font, fill=fill)
            y += line_height + line_gap

    def _wrap_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
        *,
        max_lines: int,
    ) -> list[str]:
        words = text.split()
        if not words or max_lines <= 0:
            return []
        lines: list[str] = []
        current = ""
        overflow = False
        for word in words:
            candidate = f"{current} {word}".strip()
            if self._text_width(draw, candidate, font) <= max_width:
                current = candidate
                continue
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
        return lines[:max_lines]

    def _truncate_to_width(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
    ) -> str:
        value = text
        if self._text_width(draw, value, font) <= max_width:
            return value
        suffix = "..."
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

    def _safe_tier_text(self, value: object | None) -> str | None:
        if value is None:
            return None
        text = " ".join(str(value).split()).strip()
        return text or None

    def _author_name(self, author: object | None) -> str:
        if author is None:
            return ""
        value = getattr(author, "display_name", None) or getattr(author, "name", None)
        if not isinstance(value, str):
            return ""
        return self._safe_text(value) or ""
