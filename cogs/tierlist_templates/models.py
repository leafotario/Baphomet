from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class TemplateVisibility(StrEnum):
    PRIVATE = "PRIVATE"
    GUILD = "GUILD"
    GLOBAL = "GLOBAL"


class TemplateItemType(StrEnum):
    TEXT_ONLY = "TEXT_ONLY"
    IMAGE = "IMAGE"


class TemplateSourceType(StrEnum):
    TEXT = "TEXT"
    IMAGE_URL = "IMAGE_URL"
    DISCORD_AVATAR = "DISCORD_AVATAR"
    WIKIPEDIA = "WIKIPEDIA"
    SPOTIFY = "SPOTIFY"


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FINALIZED = "FINALIZED"
    EXPIRED = "EXPIRED"
    ABANDONED = "ABANDONED"
    DELETED = "DELETED"


@dataclass(frozen=True)
class TierTemplate:
    id: str
    slug: str
    name: str
    description: str | None
    creator_id: int
    guild_id: int | None
    visibility: TemplateVisibility
    current_version_id: str | None
    created_at: str
    updated_at: str
    deleted_at: str | None


@dataclass(frozen=True)
class TierTemplateVersion:
    id: str
    template_id: str
    version_number: int
    default_tiers_json: str
    style_json: str | None
    is_locked: bool
    created_by: int
    created_at: str
    published_at: str | None
    deleted_at: str | None


@dataclass(frozen=True)
class TierTemplateItem:
    id: str
    template_version_id: str
    item_type: TemplateItemType
    source_type: str | None
    asset_id: str | None
    user_caption: str | None
    render_caption: str | None
    has_visible_caption: bool
    internal_title: str | None
    source_query: str | None
    metadata: dict[str, Any]
    sort_order: int
    created_at: str
    deleted_at: str | None


@dataclass(frozen=True)
class TierAsset:
    id: str
    asset_hash: str
    storage_path: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    source_type: str | None
    metadata: dict[str, Any]
    created_at: str
    marked_orphan_at: str | None
    deleted_at: str | None


@dataclass(frozen=True)
class TierSession:
    id: str
    template_version_id: str
    owner_id: int
    guild_id: int | None
    channel_id: int | None
    message_id: int | None
    status: SessionStatus
    selected_item_id: str | None
    selected_tier_id: str | None
    current_inventory_page: int
    created_at: str
    updated_at: str
    finalized_at: str | None
    expires_at: str | None


@dataclass(frozen=True)
class TierSessionItem:
    id: str
    session_id: str
    template_item_id: str
    current_tier_id: str | None
    position: int
    is_unused: bool
    created_at: str
    updated_at: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
