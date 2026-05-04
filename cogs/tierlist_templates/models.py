from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class TemplateVisibility(StrEnum):
    PRIVATE = "private"
    GUILD = "guild"
    PUBLIC = "public"


class TemplateVersionStatus(StrEnum):
    PUBLISHED = "published"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    FINALIZED = "finalized"
    EXPIRED = "expired"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class TierListTemplate:
    id: int
    name: str
    description: str
    creator_id: int
    guild_id: int | None
    visibility: TemplateVisibility
    current_version_id: int | None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


@dataclass(frozen=True)
class TemplateVersion:
    id: int
    template_id: int
    version_number: int
    status: TemplateVersionStatus
    published_by: int
    published_at: str


@dataclass(frozen=True)
class TemplateTier:
    id: int
    owner_id: int
    name: str
    position: int
    color_hex: str | None = None


@dataclass(frozen=True)
class TemplateItem:
    id: int
    owner_id: int
    name: str | None
    source_type: str
    source_query: str | None
    asset_sha256: str | None
    metadata: dict[str, Any]
    position: int
    created_at: str | None = None


@dataclass(frozen=True)
class StoredAsset:
    sha256: str
    relative_path: str
    mime_type: str
    byte_size: int
    width: int
    height: int
    created_at: str | None = None


@dataclass(frozen=True)
class TemplateSession:
    id: int
    template_version_id: int
    owner_id: int
    guild_id: int | None
    channel_id: int | None
    message_id: int | None
    title: str
    status: SessionStatus
    created_at: str
    updated_at: str
    expires_at: str | None
    finalized_at: str | None = None


@dataclass(frozen=True)
class TemplateSessionItem:
    id: int
    session_id: int
    template_item_id: int
    name: str | None
    source_type: str
    asset_sha256: str | None
    metadata: dict[str, Any]
    tier_name: str | None
    position: int
    created_at: str | None = None


@dataclass(frozen=True)
class TemplateDraftSnapshot:
    template: TierListTemplate
    tiers: tuple[TemplateTier, ...]
    items: tuple[TemplateItem, ...]


@dataclass(frozen=True)
class TemplateVersionSnapshot:
    template: TierListTemplate
    version: TemplateVersion
    tiers: tuple[TemplateTier, ...]
    items: tuple[TemplateItem, ...]


@dataclass(frozen=True)
class TemplateSessionSnapshot:
    session: TemplateSession
    template: TierListTemplate
    version: TemplateVersion
    tiers: tuple[TemplateTier, ...]
    items: tuple[TemplateSessionItem, ...]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
