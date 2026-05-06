from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ProfileFieldType(StrEnum):
    TEXT_SHORT = "text_short"
    TEXT_LONG = "text_long"
    TAG_LIST = "tag_list"
    ENUM = "enum"


class ProfileFieldStatus(StrEnum):
    ACTIVE = "active"
    HIDDEN = "hidden"
    REJECTED = "rejected"
    REMOVED_BY_MOD = "removed_by_mod"


class ProfileFieldSourceType(StrEnum):
    USER = "user"
    AUTO_SYNC = "auto_sync"
    MODERATION = "moderation"


class ProfileModerationAction(StrEnum):
    HIDE = "hide"
    REJECT = "reject"
    REMOVE = "remove"
    EDIT = "edit"
    RESTORE = "restore"
    RESET = "reset"
    RESET_ALL = "reset_all"
    RESET_VISUAL = "reset_visual"


class PresentationMode(StrEnum):
    MANUAL = "manual"
    AUTO_POST = "auto_post"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    key: str
    label: str
    field_type: ProfileFieldType
    max_length: int
    accepts_auto_sync: bool
    moderation_fallback: Any
    rendered: bool
    user_editable: bool
    moderation_admin: bool
    choices: tuple[str, ...] = ()
    max_items: int | None = None


@dataclass(frozen=True, slots=True)
class ProfileRecord:
    guild_id: int
    user_id: int
    created_at: str
    updated_at: str
    onboarding_completed: bool
    render_revision: int


@dataclass(frozen=True, slots=True)
class ProfileFieldValue:
    guild_id: int
    user_id: int
    field_key: str
    value: str
    status: ProfileFieldStatus
    source_type: ProfileFieldSourceType
    source_message_ids: tuple[int, ...]
    updated_at: str
    updated_by: int | None
    moderated_by: int | None
    moderated_at: str | None
    moderation_reason: str | None


@dataclass(frozen=True, slots=True)
class GuildProfileSettings:
    guild_id: int
    presentation_channel_id: int | None
    presentation_mode: PresentationMode
    auto_sync_enabled: bool


@dataclass(frozen=True, slots=True)
class ProfileModerationEvent:
    id: int
    guild_id: int
    user_id: int
    field_key: str
    action: ProfileModerationAction
    actor_id: int
    reason: str | None
    created_at: str
