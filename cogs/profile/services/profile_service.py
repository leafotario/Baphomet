from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

import discord

from ..field_registry import FieldRegistry
from ..models import (
    FieldDefinition,
    ProfileFieldSourceType,
    ProfileFieldStatus,
    ProfileFieldType,
    ProfileFieldValue,
    ProfileModerationAction,
    ProfileRecord,
)
from ..repositories import ProfileRepository
from ..schemas import LiveProfileData, ProfileFieldSnapshot, ProfileSnapshot
from .level_provider import LevelProvider
from .profile_moderation_service import ProfileModerationService


class ProfileValidationError(ValueError):
    pass


class ProfileFieldNotFoundError(LookupError):
    pass


VISUAL_FIELD_KEYS = ("theme_preset", "accent_palette", "charm_preset")
TEXT_FIELD_KEYS = ("pronouns", "headline", "bio")
CONNECTION_FIELD_KEYS = ("basic_info", "ask_me_about", "mood", "interests")
MAX_CONSECUTIVE_BLANK_LINES = 1


class ProfileService:
    def __init__(
        self,
        *,
        repository: ProfileRepository,
        field_registry: FieldRegistry,
        level_provider: LevelProvider,
        moderation_service: ProfileModerationService,
    ) -> None:
        self.repository = repository
        self.field_registry = field_registry
        self.level_provider = level_provider
        self.moderation_service = moderation_service

    async def ensure_profile(self, guild_id: int, user_id: int) -> ProfileRecord:
        return await self.repository.ensure_profile(guild_id, user_id)

    async def mark_onboarding_completed(self, guild_id: int, user_id: int, completed: bool = True) -> ProfileRecord:
        return await self.repository.mark_onboarding_completed(guild_id, user_id, completed)

    async def get_profile_snapshot(self, guild: discord.Guild, member: discord.Member) -> ProfileSnapshot:
        profile = await self.ensure_profile(guild.id, member.id)
        stored_fields = await self.repository.list_fields(guild.id, member.id)
        settings = await self.repository.get_settings(guild.id)
        level = await self.level_provider.get_level_snapshot(guild, member)
        live = self._build_live_data(guild, member)

        fields: dict[str, ProfileFieldSnapshot] = {}
        for definition in self.field_registry.all():
            stored = stored_fields.get(definition.key)
            fields[definition.key] = self._build_field_snapshot(definition, stored)

        return ProfileSnapshot(
            profile=profile,
            settings=settings,
            live=live,
            level=level,
            fields=fields,
        )

    async def set_field(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        value: Any,
        updated_by: int | None,
        source_type: ProfileFieldSourceType = ProfileFieldSourceType.USER,
        source_message_ids: Iterable[int] = (),
    ) -> ProfileFieldValue:
        definition = self.field_registry.get(field_key)
        if source_type is ProfileFieldSourceType.USER and not definition.user_editable:
            raise PermissionError(f"campo nao editavel pelo usuario: {definition.key}")
        if source_type is ProfileFieldSourceType.AUTO_SYNC and not definition.accepts_auto_sync:
            raise PermissionError(f"campo nao aceita auto-sync: {definition.key}")

        normalized_value = self.normalize_field_value(definition.key, value)
        encoded_value = self._encode_value(definition, normalized_value)
        message_ids = tuple(int(message_id) for message_id in source_message_ids)
        return await self.repository.upsert_field(
            guild_id=guild_id,
            user_id=user_id,
            field_key=definition.key,
            value=encoded_value,
            source_type=source_type,
            source_message_ids=message_ids,
            updated_by=updated_by,
        )

    async def admin_set_field(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        value: Any,
        actor_id: int,
        reason: str | None = None,
    ) -> ProfileFieldValue:
        definition = self.moderation_service.assert_can_moderate(field_key)
        field = await self.set_field(
            guild_id=guild_id,
            user_id=user_id,
            field_key=definition.key,
            value=value,
            updated_by=actor_id,
            source_type=ProfileFieldSourceType.MODERATION,
        )
        await self.repository.record_moderation_event(
            guild_id=guild_id,
            user_id=user_id,
            field_key=definition.key,
            action=ProfileModerationAction.EDIT,
            actor_id=actor_id,
            reason=reason,
        )
        return field

    async def reset_field(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        actor_id: int,
        reason: str | None = None,
    ) -> bool:
        definition = self.field_registry.get(field_key)
        return await self.repository.reset_field(
            guild_id=guild_id,
            user_id=user_id,
            field_key=definition.key,
            actor_id=actor_id,
            reason=reason,
        )

    async def reset_profile(
        self,
        *,
        guild_id: int,
        user_id: int,
        actor_id: int,
        reason: str | None = None,
    ) -> int:
        return await self.repository.reset_profile_fields(
            guild_id=guild_id,
            user_id=user_id,
            actor_id=actor_id,
            reason=reason,
        )

    async def reset_visual_fields(
        self,
        *,
        guild_id: int,
        user_id: int,
        actor_id: int,
        reason: str | None = None,
    ) -> int:
        return await self.repository.reset_fields(
            guild_id=guild_id,
            user_id=user_id,
            field_keys=VISUAL_FIELD_KEYS,
            actor_id=actor_id,
            action=ProfileModerationAction.RESET_VISUAL,
            reason=reason,
        )

    async def moderate_field(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        status: ProfileFieldStatus,
        actor_id: int,
        reason: str | None,
    ) -> None:
        definition = self.moderation_service.assert_can_moderate(field_key)
        if status not in {ProfileFieldStatus.HIDDEN, ProfileFieldStatus.REJECTED, ProfileFieldStatus.REMOVED_BY_MOD}:
            raise ProfileValidationError("moderate_field aceita apenas hidden, rejected ou removed_by_mod; use restore_field para reativar")
        updated = await self.repository.moderate_field(
            guild_id=guild_id,
            user_id=user_id,
            field_key=definition.key,
            status=status,
            actor_id=actor_id,
            reason=reason,
        )
        if not updated:
            raise ProfileFieldNotFoundError(f"campo sem valor persistido: {definition.key}")

    async def restore_field(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        actor_id: int,
        reason: str | None = None,
    ) -> None:
        definition = self.moderation_service.assert_can_moderate(field_key)
        restored = await self.repository.restore_field(
            guild_id=guild_id,
            user_id=user_id,
            field_key=definition.key,
            actor_id=actor_id,
            reason=reason,
        )
        if not restored:
            raise ProfileFieldNotFoundError(f"campo sem valor persistido: {definition.key}")

    def _build_live_data(self, guild: discord.Guild, member: discord.Member) -> LiveProfileData:
        display_avatar = getattr(member, "display_avatar", None)
        avatar_url = str(display_avatar.url) if display_avatar is not None else None
        return LiveProfileData(
            guild_id=guild.id,
            user_id=member.id,
            display_name=member.display_name,
            username=member.name,
            avatar_url=avatar_url,
            mention=member.mention,
        )

    def _build_field_snapshot(
        self,
        definition: FieldDefinition,
        stored: ProfileFieldValue | None,
    ) -> ProfileFieldSnapshot:
        if stored is None:
            return ProfileFieldSnapshot(
                definition=definition,
                value=self._default_value(definition),
                raw_value=None,
                status=None,
                source_message_ids=(),
                updated_at=None,
                updated_by=None,
                moderation_reason=None,
            )

        raw_value = self._decode_value(definition, stored.value)
        value = self.moderation_service.render_value_for(stored, raw_value, self._default_value(definition))

        return ProfileFieldSnapshot(
            definition=definition,
            value=value,
            raw_value=raw_value,
            status=stored.status,
            source_message_ids=stored.source_message_ids,
            updated_at=stored.updated_at,
            updated_by=stored.updated_by,
            moderation_reason=stored.moderation_reason,
        )

    def normalize_field_value(self, field_key: str, value: Any) -> Any:
        return self._normalize_value(self.field_registry.get(field_key), value)

    def _normalize_value(self, definition: FieldDefinition, value: Any) -> Any:
        if definition.field_type is ProfileFieldType.TAG_LIST:
            return self._normalize_tag_list(definition, value)
        if definition.field_type is ProfileFieldType.ENUM:
            normalized = self._sanitize_short_text(str(value)).casefold()
            if normalized not in definition.choices:
                raise ProfileValidationError(
                    f"valor invalido para {definition.key}; use um destes: {', '.join(definition.choices)}"
                )
            return normalized

        if definition.field_type is ProfileFieldType.TEXT_LONG:
            normalized_text = self._sanitize_long_text(str(value))
        else:
            normalized_text = self._sanitize_short_text(str(value))
        if not normalized_text:
            raise ProfileValidationError(f"{definition.key} nao pode ficar vazio")
        if len(normalized_text) > definition.max_length:
            raise ProfileValidationError(
                f"{definition.key} excede {definition.max_length} caracteres"
            )
        return normalized_text

    def _normalize_tag_list(self, definition: FieldDefinition, value: Any) -> list[str]:
        if isinstance(value, str):
            raw_items = value.replace("\n", ",").split(",")
        elif isinstance(value, Iterable):
            raw_items = list(value)
        else:
            raise ProfileValidationError(f"{definition.key} precisa ser uma lista")

        items: list[str] = []
        seen: set[str] = set()
        for raw_item in raw_items:
            item = self._sanitize_short_text(str(raw_item))
            if not item:
                continue
            if len(item) > definition.max_length:
                raise ProfileValidationError(
                    f"item de {definition.key} excede {definition.max_length} caracteres: {item}"
                )
            dedupe_key = item.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            items.append(item)

        if not items:
            raise ProfileValidationError(f"{definition.key} precisa ter pelo menos um item")
        if definition.max_items is not None and len(items) > definition.max_items:
            raise ProfileValidationError(f"{definition.key} aceita no maximo {definition.max_items} itens")
        return items

    def _sanitize_short_text(self, value: str) -> str:
        cleaned = self._remove_dangerous_control_chars(value)
        cleaned = cleaned.replace("\r", " ").replace("\n", " ").replace("\t", " ")
        return re.sub(r" {2,}", " ", cleaned).strip()

    def _sanitize_long_text(self, value: str) -> str:
        cleaned = self._remove_dangerous_control_chars(value)
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
        lines: list[str] = []
        blank_run = 0
        for line in cleaned.split("\n"):
            normalized_line = re.sub(r" {2,}", " ", line).strip()
            if not normalized_line:
                blank_run += 1
                if blank_run <= MAX_CONSECUTIVE_BLANK_LINES and lines:
                    lines.append("")
                continue
            blank_run = 0
            lines.append(normalized_line)
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines).strip()

    def _remove_dangerous_control_chars(self, value: str) -> str:
        allowed = {"\n", "\r", "\t"}
        chars: list[str] = []
        for char in value:
            if char in allowed:
                chars.append(char)
                continue
            category = unicodedata.category(char)
            if category in {"Cc", "Cf"}:
                continue
            chars.append(char)
        return "".join(chars)

    def _encode_value(self, definition: FieldDefinition, value: Any) -> str:
        if definition.field_type is ProfileFieldType.TAG_LIST:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value)

    def _decode_value(self, definition: FieldDefinition, value: str) -> Any:
        if definition.field_type is not ProfileFieldType.TAG_LIST:
            return value
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed]

    def _default_value(self, definition: FieldDefinition) -> Any:
        fallback = self.moderation_service.fallback_for(definition)
        if definition.field_type is ProfileFieldType.TAG_LIST:
            return list(fallback or [])
        return fallback
