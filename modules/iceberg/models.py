from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


PROJECT_JSON_VERSION = 1
MAX_PROJECT_NAME_LENGTH = 90
MAX_LAYER_NAME_LENGTH = 48
MAX_ITEM_TITLE_LENGTH = 90


class IcebergStatus(StrEnum):
    DRAFT = "DRAFT"
    FINALIZED = "FINALIZED"
    DELETED = "DELETED"


class ItemSourceType(StrEnum):
    TEXT = "TEXT"
    IMAGE_URL = "IMAGE_URL"
    DISCORD_AVATAR = "DISCORD_AVATAR"
    WIKIPEDIA = "WIKIPEDIA"
    ATTACHMENT = "ATTACHMENT"


_IMAGE_SOURCE_TYPES = frozenset({
    ItemSourceType.IMAGE_URL,
    ItemSourceType.DISCORD_AVATAR,
    ItemSourceType.WIKIPEDIA,
    ItemSourceType.ATTACHMENT,
})


def is_image_source(source_type: ItemSourceType) -> bool:
    """Return True if the source type represents an image (not pure text)."""
    return source_type in _IMAGE_SOURCE_TYPES


class LayerLayoutMode(StrEnum):
    SCATTER = "SCATTER"
    GRID = "GRID"
    MASONRY = "MASONRY"
    MANUAL = "MANUAL"


class ItemDisplayStyle(StrEnum):
    CHIP = "CHIP"
    CARD = "CARD"
    STICKER = "STICKER"


def new_uuid() -> str:
    return uuid.uuid4().hex


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(value: object, *, max_length: int, fallback: str | None = None) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return fallback
    if text.casefold() in {"none", "null"}:
        return fallback
    return text[:max_length].strip() or fallback


def clamp_float(value: object, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


@dataclass
class PlacementConfig:
    x: float | None = None
    y: float | None = None
    scale: float = 1.0
    rotation: float = 0.0
    opacity: float = 1.0
    z_index: int = 0

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "PlacementConfig":
        payload = payload or {}
        raw_x = payload.get("x")
        raw_y = payload.get("y")
        return cls(
            x=None if raw_x is None else clamp_float(raw_x, default=0.5, minimum=0.0, maximum=1.0),
            y=None if raw_y is None else clamp_float(raw_y, default=0.5, minimum=0.0, maximum=1.0),
            scale=clamp_float(payload.get("scale"), default=1.0, minimum=0.35, maximum=2.5),
            rotation=clamp_float(payload.get("rotation"), default=0.0, minimum=-35.0, maximum=35.0),
            opacity=clamp_float(payload.get("opacity"), default=1.0, minimum=0.0, maximum=1.0),
            z_index=int(payload.get("z_index") or 0),
        )


@dataclass
class ItemSource:
    type: ItemSourceType
    value: str | None = None
    asset_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ItemSource":
        source_type = ItemSourceType(str(payload.get("type") or ItemSourceType.TEXT.value).upper())
        metadata = payload.get("metadata")
        return cls(
            type=source_type,
            value=normalize_text(payload.get("value"), max_length=500),
            asset_id=normalize_text(payload.get("asset_id"), max_length=80),
            metadata=metadata if isinstance(metadata, dict) else {},
        )


@dataclass
class ItemConfig:
    id: str
    layer_id: str
    title: str
    source: ItemSource
    display_style: ItemDisplayStyle = ItemDisplayStyle.CARD
    placement: PlacementConfig = field(default_factory=PlacementConfig)
    sort_order: int = 0
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ItemConfig":
        return cls(
            id=normalize_text(payload.get("id"), max_length=64, fallback=new_uuid()) or new_uuid(),
            layer_id=normalize_text(payload.get("layer_id"), max_length=64, fallback="layer-1") or "layer-1",
            title=normalize_text(payload.get("title"), max_length=MAX_ITEM_TITLE_LENGTH, fallback="Item") or "Item",
            source=ItemSource.from_dict(payload.get("source") or {}),
            display_style=coerce_display_style(payload.get("display_style"), ItemDisplayStyle.CARD),
            placement=PlacementConfig.from_dict(payload.get("placement")),
            sort_order=int(payload.get("sort_order") or 0),
            created_at=normalize_text(payload.get("created_at"), max_length=40, fallback=utc_now_iso()) or utc_now_iso(),
            updated_at=normalize_text(payload.get("updated_at"), max_length=40, fallback=utc_now_iso()) or utc_now_iso(),
        )


@dataclass
class LayerConfig:
    id: str
    name: str
    order: int
    height_weight: float = 1.0
    color: tuple[int, int, int, int] | None = None
    layout_mode: LayerLayoutMode = LayerLayoutMode.SCATTER

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, fallback_order: int = 0) -> "LayerConfig":
        color = payload.get("color")
        layout_mode_raw = payload.get("layout_mode") or LayerLayoutMode.SCATTER.value
        return cls(
            id=normalize_text(payload.get("id"), max_length=64, fallback=f"layer-{fallback_order + 1}") or f"layer-{fallback_order + 1}",
            name=normalize_text(payload.get("name"), max_length=MAX_LAYER_NAME_LENGTH, fallback=f"Camada {fallback_order + 1}") or f"Camada {fallback_order + 1}",
            order=int(payload.get("order") if payload.get("order") is not None else fallback_order),
            height_weight=clamp_float(payload.get("height_weight"), default=1.0, minimum=0.15, maximum=8.0),
            color=coerce_rgba(color) if color is not None else None,
            layout_mode=LayerLayoutMode(str(layout_mode_raw).upper()),
        )


@dataclass
class ThemeConfig:
    id: str
    name: str
    canvas_width: int
    canvas_height: int
    padding_x: int
    title_top: int
    title_height: int
    iceberg_top_y: int
    iceberg_bottom_y: int
    iceberg_center_x: int
    iceberg_boundary_width_ratios: tuple[float, ...]
    sky_color: tuple[int, int, int, int]
    ocean_color: tuple[int, int, int, int]
    deep_ocean_color: tuple[int, int, int, int]
    horizon_color: tuple[int, int, int, int]
    iceberg_top_color: tuple[int, int, int, int]
    iceberg_bottom_color: tuple[int, int, int, int]
    iceberg_edge_color: tuple[int, int, int, int]
    layer_line_color: tuple[int, int, int, int]
    layer_label_color: tuple[int, int, int, int]
    title_color: tuple[int, int, int, int]
    item_fill_color: tuple[int, int, int, int]
    item_outline_color: tuple[int, int, int, int]
    item_text_color: tuple[int, int, int, int]
    item_muted_text_color: tuple[int, int, int, int]
    font_regular: str
    font_bold: str
    title_font_size: int
    layer_label_font_size: int
    item_font_size: int
    item_caption_font_size: int
    item_card_width: int
    item_card_height: int
    item_chip_min_width: int
    item_chip_height: int
    item_gap_x: int
    item_gap_y: int
    item_padding_x: int
    item_padding_y: int
    item_radius: int
    layer_inner_padding_x: int
    layer_inner_padding_y: int
    footer_height: int
    item_stroke_width: int = 0
    item_stroke_color: tuple[int, int, int, int] | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ThemeConfig":
        ratios = payload.get("iceberg_boundary_width_ratios") or (0.18, 0.38, 0.58, 0.76, 0.94, 0.82)
        stroke_color = payload.get("item_stroke_color")
        return cls(
            id=normalize_text(payload.get("id"), max_length=64, fallback="classic_iceberg") or "classic_iceberg",
            name=normalize_text(payload.get("name"), max_length=80, fallback="Iceberg classico") or "Iceberg classico",
            canvas_width=int(payload.get("canvas_width") or 1200),
            canvas_height=int(payload.get("canvas_height") or 1600),
            padding_x=int(payload.get("padding_x") or 96),
            title_top=int(payload.get("title_top") or 44),
            title_height=int(payload.get("title_height") or 118),
            iceberg_top_y=int(payload.get("iceberg_top_y") or 210),
            iceberg_bottom_y=int(payload.get("iceberg_bottom_y") or 1450),
            iceberg_center_x=int(payload.get("iceberg_center_x") or 600),
            iceberg_boundary_width_ratios=tuple(float(value) for value in ratios),
            sky_color=coerce_rgba(payload.get("sky_color"), fallback=(159, 214, 239, 255)),
            ocean_color=coerce_rgba(payload.get("ocean_color"), fallback=(59, 147, 194, 255)),
            deep_ocean_color=coerce_rgba(payload.get("deep_ocean_color"), fallback=(18, 55, 100, 255)),
            horizon_color=coerce_rgba(payload.get("horizon_color"), fallback=(231, 248, 255, 180)),
            iceberg_top_color=coerce_rgba(payload.get("iceberg_top_color"), fallback=(248, 253, 255, 255)),
            iceberg_bottom_color=coerce_rgba(payload.get("iceberg_bottom_color"), fallback=(124, 207, 232, 255)),
            iceberg_edge_color=coerce_rgba(payload.get("iceberg_edge_color"), fallback=(34, 105, 150, 210)),
            layer_line_color=coerce_rgba(payload.get("layer_line_color"), fallback=(45, 116, 158, 135)),
            layer_label_color=coerce_rgba(payload.get("layer_label_color"), fallback=(9, 37, 61, 230)),
            title_color=coerce_rgba(payload.get("title_color"), fallback=(7, 32, 52, 255)),
            item_fill_color=coerce_rgba(payload.get("item_fill_color"), fallback=(250, 253, 255, 235)),
            item_outline_color=coerce_rgba(payload.get("item_outline_color"), fallback=(42, 94, 132, 170)),
            item_text_color=coerce_rgba(payload.get("item_text_color"), fallback=(13, 34, 51, 255)),
            item_muted_text_color=coerce_rgba(payload.get("item_muted_text_color"), fallback=(43, 74, 96, 240)),
            font_regular=str(payload.get("font_regular") or "Poppins-Regular.ttf"),
            font_bold=str(payload.get("font_bold") or "Poppins-Bold.ttf"),
            title_font_size=int(payload.get("title_font_size") or 58),
            layer_label_font_size=int(payload.get("layer_label_font_size") or 25),
            item_font_size=int(payload.get("item_font_size") or 22),
            item_caption_font_size=int(payload.get("item_caption_font_size") or 18),
            item_card_width=int(payload.get("item_card_width") or 138),
            item_card_height=int(payload.get("item_card_height") or 164),
            item_chip_min_width=int(payload.get("item_chip_min_width") or 132),
            item_chip_height=int(payload.get("item_chip_height") or 58),
            item_gap_x=int(payload.get("item_gap_x") or 18),
            item_gap_y=int(payload.get("item_gap_y") or 16),
            item_padding_x=int(payload.get("item_padding_x") or 18),
            item_padding_y=int(payload.get("item_padding_y") or 10),
            item_radius=int(payload.get("item_radius") or 14),
            layer_inner_padding_x=int(payload.get("layer_inner_padding_x") or 46),
            layer_inner_padding_y=int(payload.get("layer_inner_padding_y") or 24),
            footer_height=int(payload.get("footer_height") or 70),
            item_stroke_width=int(payload.get("item_stroke_width") or 0),
            item_stroke_color=coerce_rgba(stroke_color) if stroke_color is not None else None,
        )


@dataclass
class IcebergProject:
    id: str
    owner_id: int
    guild_id: int | None
    name: str
    theme_id: str
    theme: ThemeConfig
    layers: list[LayerConfig]
    items: list[ItemConfig] = field(default_factory=list)
    default_item_style: ItemDisplayStyle = ItemDisplayStyle.CARD
    status: IcebergStatus = IcebergStatus.DRAFT
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    version: int = PROJECT_JSON_VERSION

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IcebergProject":
        theme = ThemeConfig.from_dict(payload.get("theme") or {})
        layers_payload = payload.get("layers") if isinstance(payload.get("layers"), list) else []
        layers = [
            LayerConfig.from_dict(layer, fallback_order=index)
            for index, layer in enumerate(layers_payload)
            if isinstance(layer, dict)
        ]
        if not layers:
            layers = [LayerConfig(id="layer-1", name="Ponta", order=0)]
        items_payload = payload.get("items") if isinstance(payload.get("items"), list) else []
        items = [
            ItemConfig.from_dict(item)
            for item in items_payload
            if isinstance(item, dict)
        ]
        project_id = normalize_text(payload.get("id"), max_length=64, fallback=new_uuid()) or new_uuid()
        return cls(
            id=project_id,
            owner_id=int(payload.get("owner_id") or 0),
            guild_id=int(payload["guild_id"]) if payload.get("guild_id") is not None else None,
            name=normalize_text(payload.get("name"), max_length=MAX_PROJECT_NAME_LENGTH, fallback="Iceberg") or "Iceberg",
            theme_id=normalize_text(payload.get("theme_id"), max_length=64, fallback=theme.id) or theme.id,
            theme=theme,
            layers=sorted(layers, key=lambda layer: (layer.order, layer.name.casefold())),
            items=sorted(items, key=lambda item: (item.layer_id, item.sort_order, item.created_at)),
            default_item_style=coerce_display_style(payload.get("default_item_style"), ItemDisplayStyle.CARD),
            status=coerce_status(payload.get("status"), IcebergStatus.DRAFT),
            created_at=normalize_text(payload.get("created_at"), max_length=40, fallback=utc_now_iso()) or utc_now_iso(),
            updated_at=normalize_text(payload.get("updated_at"), max_length=40, fallback=utc_now_iso()) or utc_now_iso(),
            version=int(payload.get("version") or PROJECT_JSON_VERSION),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["default_item_style"] = self.default_item_style.value
        payload["theme"] = theme_to_dict(self.theme)
        for layer in payload["layers"]:
            if "layout_mode" in layer and isinstance(layer["layout_mode"], LayerLayoutMode):
                layer["layout_mode"] = layer["layout_mode"].value
        for item in payload["items"]:
            item["display_style"] = item["display_style"].value if isinstance(item["display_style"], ItemDisplayStyle) else str(item["display_style"])
            item["source"]["type"] = item["source"]["type"].value if isinstance(item["source"]["type"], ItemSourceType) else str(item["source"]["type"])
        return payload

    def to_json(self, *, pretty: bool = True) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )

    def layer_by_id(self, layer_id: str) -> LayerConfig | None:
        return next((layer for layer in self.layers if layer.id == layer_id), None)

    def ordered_layers(self) -> list[LayerConfig]:
        return sorted(self.layers, key=lambda layer: (layer.order, layer.name.casefold()))

    def ordered_items_for_layer(self, layer_id: str) -> list[ItemConfig]:
        return sorted(
            (item for item in self.items if item.layer_id == layer_id),
            key=lambda item: (item.sort_order, item.created_at, item.id),
        )

    def touch(self) -> None:
        self.updated_at = utc_now_iso()


def coerce_display_style(value: object, fallback: ItemDisplayStyle) -> ItemDisplayStyle:
    try:
        return ItemDisplayStyle(str(value or fallback.value).upper())
    except ValueError:
        return fallback


def coerce_status(value: object, fallback: IcebergStatus) -> IcebergStatus:
    try:
        return IcebergStatus(str(value or fallback.value).upper())
    except ValueError:
        return fallback


def coerce_rgba(value: object, fallback: tuple[int, int, int, int] = (255, 255, 255, 255)) -> tuple[int, int, int, int]:
    if isinstance(value, str):
        cleaned = value.strip().lstrip("#")
        if len(cleaned) in {6, 8}:
            try:
                red = int(cleaned[0:2], 16)
                green = int(cleaned[2:4], 16)
                blue = int(cleaned[4:6], 16)
                alpha = int(cleaned[6:8], 16) if len(cleaned) == 8 else 255
                return red, green, blue, alpha
            except ValueError:
                return fallback
    try:
        channels = tuple(int(channel) for channel in value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    if len(channels) == 3:
        channels = (*channels, 255)
    if len(channels) != 4:
        return fallback
    if any(channel < 0 or channel > 255 for channel in channels):
        return fallback
    return channels  # type: ignore[return-value]


def theme_to_dict(theme: ThemeConfig) -> dict[str, Any]:
    payload = asdict(theme)
    for key, value in list(payload.items()):
        if isinstance(value, tuple):
            payload[key] = list(value)
    return payload
