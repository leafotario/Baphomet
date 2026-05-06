from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import FieldDefinition, GuildProfileSettings, ProfileFieldStatus, ProfileRecord


@dataclass(frozen=True, slots=True)
class LiveProfileData:
    guild_id: int
    user_id: int
    display_name: str
    username: str
    avatar_url: str | None
    mention: str


@dataclass(frozen=True, slots=True)
class LevelSnapshot:
    guild_id: int
    user_id: int
    total_xp: int
    level: int
    xp_into_level: int
    xp_for_next_level: int
    remaining_to_next: int
    progress_ratio: float
    position: int | None
    badge_role_id: int | None
    badge_role_name: str | None
    badge_role_color: int | None
    provider_name: str
    available: bool = True
    unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProfileFieldSnapshot:
    definition: FieldDefinition
    value: Any
    raw_value: Any
    status: ProfileFieldStatus | None
    source_message_ids: tuple[int, ...]
    updated_at: str | None
    updated_by: int | None
    moderation_reason: str | None


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    profile: ProfileRecord
    settings: GuildProfileSettings
    live: LiveProfileData
    level: LevelSnapshot
    fields: dict[str, ProfileFieldSnapshot]

    def rendered_fields(self) -> dict[str, Any]:
        return {
            key: field.value
            for key, field in self.fields.items()
            if field.definition.rendered and field.value not in (None, "", (), [])
        }
