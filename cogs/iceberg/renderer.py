from __future__ import annotations

import io
import logging
import math
import pathlib
import re
import textwrap
from dataclasses import dataclass
from typing import Iterable

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError

from .models import IcebergProject, ItemConfig, ItemDisplayStyle, ItemSourceType, LayerConfig, ThemeConfig


LOGGER = logging.getLogger("baphomet.iceberg.renderer")
MASK_SCALE = 3
MAX_TEXT_LINES = 3
Image.MAX_IMAGE_PIXELS = 25_000_000


@dataclass(frozen=True)
class LayerRenderBox:
    layer: LayerConfig
    index: int
    top_y: int
    bottom_y: int
    top_width: int
    bottom_width: int
    polygon: tuple[tuple[int, int], ...]

    @property
    def height(self) -> int:
        return max(1, self.bottom_y - self.top_y)

    @property
    def min_left(self) -> int:
        return min(point[0] for point in self.polygon)

    @property
    def max_right(self) -> int:
        return max(point[0] for point in self.polygon)


class IcebergRenderer:
    def __init__(self, *, font_dir: str | pathlib.Path | None = None) -> None:
        repo_root = pathlib.Path(__file__).resolve().parents[2]
        self.font_dir = pathlib.Path(font_dir) if font_dir else repo_root / "assets" / "fonts"
        self._font_cache: dict[tuple[str, int], ImageFont.ImageFont] = {}
        self._mask_cache: dict[tuple[int, int, int], Image.Image] = {}

    def render_project(
        self,
        project: IcebergProject,
        *,
        asset_bytes_by_item_id: dict[str, bytes] | None = None,
    ) -> io.BytesIO:
        asset_bytes_by_item_id = asset_bytes_by_item_id or {}
        theme = project.theme
        image = Image.new("RGBA", (theme.canvas_width, theme.canvas_height), theme.sky_color)
        self._draw_background(image, theme)
        draw = ImageDraw.Draw(image, "RGBA")

        title = re.sub(r"\s+", " ", project.name.strip()) or "Iceberg"
        title_font = self._fit_font(draw, title, theme, start_size=theme.title_font_size, max_width=theme.canvas_width - theme.padding_x * 2, min_size=30, bold=True)
        title_box = (theme.padding_x, theme.title_top, theme.canvas_width - theme.padding_x, theme.title_top + theme.title_height)
        self._draw_centered_text(draw, title_box, title, title_font, theme.title_color)

        layer_boxes = self._build_layer_boxes(project)
        self._draw_iceberg_body(image, layer_boxes, theme)
        self._draw_layer_lines_and_labels(image, layer_boxes, theme)
        for layer_box in layer_boxes:
            items = project.ordered_items_for_layer(layer_box.layer.id)
            self._draw_items_for_layer(image, project, layer_box, items, asset_bytes_by_item_id)
        self._draw_footer(image, project)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    def _draw_background(self, image: Image.Image, theme: ThemeConfig) -> None:
        draw = ImageDraw.Draw(image, "RGBA")
        water_y = self._waterline_y(theme)
        draw.rectangle((0, 0, theme.canvas_width, water_y), fill=theme.sky_color)
        ocean_height = max(1, theme.canvas_height - water_y)
        for offset in range(ocean_height):
            ratio = offset / ocean_height
            color = self._lerp_color(theme.ocean_color, theme.deep_ocean_color, ratio)
            draw.line((0, water_y + offset, theme.canvas_width, water_y + offset), fill=color)
        draw.rectangle((0, water_y - 4, theme.canvas_width, water_y + 4), fill=theme.horizon_color)

    def _draw_iceberg_body(self, image: Image.Image, layer_boxes: list[LayerRenderBox], theme: ThemeConfig) -> None:
        if not layer_boxes:
            return
        draw = ImageDraw.Draw(image, "RGBA")
        total = max(1, len(layer_boxes) - 1)
        for box in layer_boxes:
            ratio = box.index / total
            fill = box.layer.color or self._lerp_color(theme.iceberg_top_color, theme.iceberg_bottom_color, ratio)
            draw.polygon(box.polygon, fill=fill)
            draw.line((*box.polygon, box.polygon[0]), fill=theme.iceberg_edge_color, width=3)

        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow, "RGBA")
        full_polygon = (
            layer_boxes[0].polygon[0],
            layer_boxes[0].polygon[1],
            layer_boxes[-1].polygon[2],
            layer_boxes[-1].polygon[3],
        )
        shadow_draw.polygon(full_polygon, fill=(0, 0, 0, 34))
        shadow = shadow.filter(ImageFilter.GaussianBlur(18))
        image.alpha_composite(shadow)

    def _draw_layer_lines_and_labels(self, image: Image.Image, layer_boxes: list[LayerRenderBox], theme: ThemeConfig) -> None:
        draw = ImageDraw.Draw(image, "RGBA")
        font = self._font(theme, theme.layer_label_font_size, bold=True)
        for box in layer_boxes:
            draw.line((box.polygon[0][0], box.top_y, box.polygon[1][0], box.top_y), fill=theme.layer_line_color, width=2)
            label = re.sub(r"\s+", " ", box.layer.name).strip()
            if not label:
                continue
            label_x = max(18, box.min_left - theme.layer_inner_padding_x - 120)
            label_box = (label_x, box.top_y, box.min_left - 16, box.bottom_y)
            fitted = self._fit_font(draw, label, theme, start_size=theme.layer_label_font_size, max_width=max(80, label_box[2] - label_box[0]), min_size=14, bold=True)
            clipped = self._clip_text(draw, label, fitted, max(80, label_box[2] - label_box[0]))
            _, text_y = self._centered_text_pos(draw, label_box, clipped, fitted)
            draw.text((label_box[0], text_y), clipped, font=fitted, fill=theme.layer_label_color)

    def _draw_items_for_layer(
        self,
        image: Image.Image,
        project: IcebergProject,
        layer_box: LayerRenderBox,
        items: list[ItemConfig],
        asset_bytes_by_item_id: dict[str, bytes],
    ) -> None:
        if not items:
            return
        auto_positions = self._auto_positions(project.theme, layer_box, items)
        for index, item in enumerate(items):
            box = auto_positions.get(item.id)
            if item.placement.x is not None and item.placement.y is not None:
                box = self._manual_item_box(project.theme, layer_box, item)
            if box is None:
                continue
            if item.placement.scale != 1.0:
                box = self._scale_box(box, item.placement.scale, layer_box)
            self._draw_item(image, project.theme, item, box, asset_bytes_by_item_id.get(item.id))

    def _auto_positions(
        self,
        theme: ThemeConfig,
        layer_box: LayerRenderBox,
        items: list[ItemConfig],
    ) -> dict[str, tuple[int, int, int, int]]:
        content_left = layer_box.min_left + theme.layer_inner_padding_x
        content_right = layer_box.max_right - theme.layer_inner_padding_x
        content_top = layer_box.top_y + theme.layer_inner_padding_y
        content_bottom = layer_box.bottom_y - theme.layer_inner_padding_y
        if content_right <= content_left or content_bottom <= content_top:
            return {}
        result: dict[str, tuple[int, int, int, int]] = {}
        cursor_x = content_left
        cursor_y = content_top
        line_height = 0
        for item in items:
            item_w, item_h = self._item_size(theme, item)
            if cursor_x > content_left and cursor_x + item_w > content_right:
                cursor_x = content_left
                cursor_y += line_height + theme.item_gap_y
                line_height = 0
            if cursor_y + item_h > content_bottom:
                item_w = max(60, min(item_w, content_right - content_left))
                item_h = max(42, min(item_h, content_bottom - content_top))
            result[item.id] = (cursor_x, cursor_y, cursor_x + item_w, cursor_y + item_h)
            cursor_x += item_w + theme.item_gap_x
            line_height = max(line_height, item_h)
        return result

    def _manual_item_box(self, theme: ThemeConfig, layer_box: LayerRenderBox, item: ItemConfig) -> tuple[int, int, int, int]:
        item_w, item_h = self._item_size(theme, item)
        content_left = layer_box.min_left + theme.layer_inner_padding_x
        content_right = layer_box.max_right - theme.layer_inner_padding_x
        content_top = layer_box.top_y + theme.layer_inner_padding_y
        content_bottom = layer_box.bottom_y - theme.layer_inner_padding_y
        available_w = max(1, content_right - content_left - item_w)
        available_h = max(1, content_bottom - content_top - item_h)
        x = content_left + int((item.placement.x or 0.5) * available_w)
        y = content_top + int((item.placement.y or 0.5) * available_h)
        return x, y, x + item_w, y + item_h

    def _draw_item(
        self,
        image: Image.Image,
        theme: ThemeConfig,
        item: ItemConfig,
        box: tuple[int, int, int, int],
        image_bytes: bytes | None,
    ) -> None:
        if item.display_style is ItemDisplayStyle.CHIP or item.source.type is ItemSourceType.TEXT or not image_bytes:
            self._draw_chip_item(image, theme, item, box, image_bytes)
            return
        if item.display_style is ItemDisplayStyle.STICKER:
            self._draw_sticker_item(image, theme, item, box, image_bytes)
            return
        self._draw_card_item(image, theme, item, box, image_bytes)

    def _draw_card_item(
        self,
        image: Image.Image,
        theme: ThemeConfig,
        item: ItemConfig,
        box: tuple[int, int, int, int],
        image_bytes: bytes,
    ) -> None:
        x1, y1, x2, y2 = box
        width, height = x2 - x1, y2 - y1
        caption_h = 42 if item.title else 0
        card = Image.new("RGBA", (width, height), theme.item_fill_color)
        draw = ImageDraw.Draw(card, "RGBA")
        try:
            raw = self._open_image(image_bytes)
            fitted = ImageOps.fit(raw, (width, max(1, height - caption_h)), method=Image.Resampling.LANCZOS)
            card.paste(fitted, (0, 0), fitted if fitted.mode == "RGBA" else None)
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            LOGGER.warning("iceberg_item_image_fallback item_id=%s source_type=%s error=%s", item.id, item.source.type, exc)
            self._draw_missing_image(draw, (0, 0, width, max(1, height - caption_h)), theme)
        if caption_h:
            draw.rectangle((0, height - caption_h, width, height), fill=theme.item_fill_color)
            font = self._fit_font(draw, item.title, theme, start_size=theme.item_caption_font_size, max_width=width - theme.item_padding_x, min_size=11, bold=True)
            clipped = self._clip_text(draw, item.title, font, width - theme.item_padding_x)
            self._draw_centered_text(draw, (0, height - caption_h, width, height), clipped, font, theme.item_text_color)
        card = self._rounded(card, theme.item_radius)
        border = ImageDraw.Draw(card, "RGBA")
        border.rounded_rectangle((0, 0, width - 1, height - 1), radius=theme.item_radius, outline=theme.item_outline_color, width=2)
        image.paste(card, (x1, y1), card)

    def _draw_sticker_item(
        self,
        image: Image.Image,
        theme: ThemeConfig,
        item: ItemConfig,
        box: tuple[int, int, int, int],
        image_bytes: bytes,
    ) -> None:
        x1, y1, x2, y2 = box
        width, height = x2 - x1, y2 - y1
        caption_h = 28 if item.title else 0
        try:
            raw = self._open_image(image_bytes)
            sticker = ImageOps.fit(raw, (width, max(1, height - caption_h)), method=Image.Resampling.LANCZOS)
            sticker = self._rounded(sticker, theme.item_radius)
            image.paste(sticker, (x1, y1), sticker)
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            LOGGER.warning("iceberg_sticker_fallback item_id=%s error=%s", item.id, exc)
            self._draw_chip_item(image, theme, item, box, None)
            return
        if caption_h:
            draw = ImageDraw.Draw(image, "RGBA")
            font = self._fit_font(draw, item.title, theme, start_size=theme.item_caption_font_size, max_width=width, min_size=11, bold=True)
            clipped = self._clip_text(draw, item.title, font, width)
            self._draw_centered_text(draw, (x1, y2 - caption_h, x2, y2), clipped, font, theme.item_text_color)

    def _draw_chip_item(
        self,
        image: Image.Image,
        theme: ThemeConfig,
        item: ItemConfig,
        box: tuple[int, int, int, int],
        image_bytes: bytes | None,
    ) -> None:
        x1, y1, x2, y2 = box
        width, height = x2 - x1, y2 - y1
        chip = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(chip, "RGBA")
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=min(theme.item_radius, height // 2), fill=theme.item_fill_color, outline=theme.item_outline_color, width=2)
        text_left = theme.item_padding_x
        if image_bytes:
            thumb_size = max(34, height - theme.item_padding_y * 2)
            try:
                raw = self._open_image(image_bytes)
                thumb = ImageOps.fit(raw, (thumb_size, thumb_size), method=Image.Resampling.LANCZOS)
                thumb = self._rounded(thumb, max(8, theme.item_radius - 4))
                chip.paste(thumb, (theme.item_padding_x, (height - thumb_size) // 2), thumb)
                text_left += thumb_size + theme.item_padding_x
            except (OSError, ValueError, UnidentifiedImageError):
                pass
        font = self._fit_font(draw, item.title, theme, start_size=theme.item_font_size, max_width=max(20, width - text_left - theme.item_padding_x), min_size=11, bold=True)
        lines = self._wrap_lines(draw, item.title, font, max(20, width - text_left - theme.item_padding_x), max_lines=2)
        text = "\n".join(lines)
        _, top, _, bottom = draw.multiline_textbbox((0, 0), text, font=font, spacing=3)
        draw.multiline_text((text_left, (height - (bottom - top)) / 2 - top), text, font=font, fill=theme.item_text_color, spacing=3, align="left")
        image.paste(chip, (x1, y1), chip)

    def _draw_missing_image(self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], theme: ThemeConfig) -> None:
        draw.rectangle(box, fill=(205, 231, 242, 255))
        x1, y1, x2, y2 = box
        draw.line((x1, y1, x2, y2), fill=theme.item_outline_color, width=3)
        draw.line((x2, y1, x1, y2), fill=theme.item_outline_color, width=3)

    def _draw_footer(self, image: Image.Image, project: IcebergProject) -> None:
        theme = project.theme
        draw = ImageDraw.Draw(image, "RGBA")
        y = theme.canvas_height - theme.footer_height
        draw.rectangle((0, y, theme.canvas_width, theme.canvas_height), fill=(5, 25, 45, 255))
        text = f"Baphomet Iceberg • {len(project.layers)} camadas • {len(project.items)} itens"
        font = self._font(theme, 18, bold=True)
        self._draw_centered_text(draw, (theme.padding_x, y, theme.canvas_width - theme.padding_x, theme.canvas_height), text, font, (236, 249, 255, 230))

    def _build_layer_boxes(self, project: IcebergProject) -> list[LayerRenderBox]:
        theme = project.theme
        layers = project.ordered_layers()
        weights = [max(0.15, layer.height_weight) for layer in layers]
        total_weight = sum(weights) or 1.0
        total_height = theme.iceberg_bottom_y - theme.iceberg_top_y
        y_values = [theme.iceberg_top_y]
        carried = float(theme.iceberg_top_y)
        for weight in weights:
            carried += total_height * (weight / total_weight)
            y_values.append(int(round(carried)))
        y_values[-1] = theme.iceberg_bottom_y
        boxes: list[LayerRenderBox] = []
        for index, layer in enumerate(layers):
            top_y = y_values[index]
            bottom_y = y_values[index + 1]
            top_width = self._boundary_width(theme, index / max(1, len(layers)))
            bottom_width = self._boundary_width(theme, (index + 1) / max(1, len(layers)))
            top_left = theme.iceberg_center_x - top_width // 2
            top_right = theme.iceberg_center_x + top_width // 2
            bottom_left = theme.iceberg_center_x - bottom_width // 2
            bottom_right = theme.iceberg_center_x + bottom_width // 2
            boxes.append(
                LayerRenderBox(
                    layer=layer,
                    index=index,
                    top_y=top_y,
                    bottom_y=bottom_y,
                    top_width=top_width,
                    bottom_width=bottom_width,
                    polygon=((top_left, top_y), (top_right, top_y), (bottom_right, bottom_y), (bottom_left, bottom_y)),
                )
            )
        return boxes

    def _boundary_width(self, theme: ThemeConfig, ratio: float) -> int:
        ratios = theme.iceberg_boundary_width_ratios or (0.2, 0.8)
        if len(ratios) == 1:
            width_ratio = ratios[0]
        else:
            scaled = max(0.0, min(1.0, ratio)) * (len(ratios) - 1)
            left_index = min(len(ratios) - 2, int(math.floor(scaled)))
            local_ratio = scaled - left_index
            width_ratio = ratios[left_index] + (ratios[left_index + 1] - ratios[left_index]) * local_ratio
        max_width = theme.canvas_width - theme.padding_x * 2
        return max(80, int(max_width * width_ratio))

    def _waterline_y(self, theme: ThemeConfig) -> int:
        return theme.iceberg_top_y + int((theme.iceberg_bottom_y - theme.iceberg_top_y) * 0.18)

    def _item_size(self, theme: ThemeConfig, item: ItemConfig) -> tuple[int, int]:
        if item.display_style is ItemDisplayStyle.CHIP or item.source.type is ItemSourceType.TEXT:
            estimated_chars = max(1, len(item.title))
            width = min(theme.item_card_width * 2, max(theme.item_chip_min_width, estimated_chars * 11 + theme.item_padding_x * 2))
            return width, theme.item_chip_height
        return theme.item_card_width, theme.item_card_height

    def _scale_box(self, box: tuple[int, int, int, int], scale: float, layer_box: LayerRenderBox) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1
        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2
        scaled_w = max(40, int(width * scale))
        scaled_h = max(32, int(height * scale))
        return (
            max(layer_box.min_left, center_x - scaled_w // 2),
            max(layer_box.top_y, center_y - scaled_h // 2),
            min(layer_box.max_right, center_x + scaled_w // 2),
            min(layer_box.bottom_y, center_y + scaled_h // 2),
        )

    def _open_image(self, image_bytes: bytes) -> Image.Image:
        if not image_bytes:
            raise ValueError("imagem vazia")
        with Image.open(io.BytesIO(image_bytes)) as raw:
            try:
                raw.seek(0)
            except EOFError:
                pass
            return raw.convert("RGBA")

    def _rounded(self, source: Image.Image, radius: int) -> Image.Image:
        rounded = source.convert("RGBA")
        mask = self._rounded_mask(rounded.size, radius)
        alpha = rounded.getchannel("A")
        rounded.putalpha(ImageChops.multiply(alpha, mask))
        return rounded

    def _rounded_mask(self, size: tuple[int, int], radius: int) -> Image.Image:
        key = (size[0], size[1], radius)
        cached = self._mask_cache.get(key)
        if cached is not None:
            return cached.copy()
        hi_size = (size[0] * MASK_SCALE, size[1] * MASK_SCALE)
        mask = Image.new("L", hi_size, 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0, hi_size[0] - 1, hi_size[1] - 1), radius=radius * MASK_SCALE, fill=255)
        mask = mask.resize(size, Image.Resampling.LANCZOS)
        self._mask_cache[key] = mask
        return mask.copy()

    def _font(self, theme: ThemeConfig, size: int, *, bold: bool) -> ImageFont.ImageFont:
        filename = theme.font_bold if bold else theme.font_regular
        cache_key = (filename, size)
        cached = self._font_cache.get(cache_key)
        if cached is not None:
            return cached
        candidates = [
            self.font_dir / filename,
            self.font_dir / "Poppins-Bold.ttf",
            self.font_dir / "Poppins-Regular.ttf",
            pathlib.Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
        for candidate in candidates:
            try:
                font = ImageFont.truetype(str(candidate), size)
                self._font_cache[cache_key] = font
                return font
            except (OSError, ValueError):
                continue
        font = ImageFont.load_default()
        self._font_cache[cache_key] = font
        return font

    def _fit_font(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        theme: ThemeConfig,
        *,
        start_size: int,
        max_width: int,
        min_size: int,
        bold: bool,
    ) -> ImageFont.ImageFont:
        for size in range(start_size, min_size - 1, -2):
            font = self._font(theme, size, bold=bold)
            if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
                return font
        return self._font(theme, min_size, bold=bold)

    def _draw_centered_text(
        self,
        draw: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        text: str,
        font: ImageFont.ImageFont,
        fill: tuple[int, int, int, int],
    ) -> None:
        x, y = self._centered_text_pos(draw, box, text, font)
        draw.text((x, y), text, font=font, fill=fill)

    def _centered_text_pos(
        self,
        draw: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        text: str,
        font: ImageFont.ImageFont,
    ) -> tuple[int, int]:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        text_w = right - left
        text_h = bottom - top
        x1, y1, x2, y2 = box
        return int(x1 + (x2 - x1 - text_w) / 2 - left), int(y1 + (y2 - y1 - text_h) / 2 - top)

    def _wrap_lines(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
        *,
        max_lines: int = MAX_TEXT_LINES,
    ) -> list[str]:
        words = textwrap.wrap(re.sub(r"\s+", " ", text).strip(), width=22, break_long_words=False, break_on_hyphens=False) or [text]
        lines: list[str] = []
        for candidate in words:
            line = candidate
            while line and draw.textbbox((0, 0), line, font=font)[2] > max_width:
                line = line[:-1].rstrip()
            if line:
                lines.append(line if line == candidate else f"{line}...")
            if len(lines) >= max_lines:
                break
        return lines or [text[:18]]

    def _clip_text(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
        value = re.sub(r"\s+", " ", text).strip()
        if draw.textbbox((0, 0), value, font=font)[2] <= max_width:
            return value
        suffix = "..."
        while value and draw.textbbox((0, 0), value + suffix, font=font)[2] > max_width:
            value = value[:-1]
        return value + suffix if value else suffix

    def _lerp_color(
        self,
        start: tuple[int, int, int, int],
        end: tuple[int, int, int, int],
        ratio: float,
    ) -> tuple[int, int, int, int]:
        clamped = max(0.0, min(1.0, ratio))
        return tuple(int(start[index] + (end[index] - start[index]) * clamped) for index in range(4))  # type: ignore[return-value]
