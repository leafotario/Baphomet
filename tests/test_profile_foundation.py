from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
from discord.ext import commands

from cogs.profile.cog import ProfileCog
from cogs.profile.field_registry import PROFILE_FIELD_REGISTRY
from cogs.profile.models import (
    GuildProfileSettings,
    PresentationMode,
    ProfileFieldSourceType,
    ProfileFieldStatus,
    ProfileFieldValue,
    ProfileModerationAction,
    ProfileRecord,
)
from cogs.profile.services import (
    NullLevelProvider,
    PresentationChannelService,
    ProfileModerationService,
    ProfileRenderService,
    ProfileService,
    ProfileValidationError,
)


class ProfileFoundationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.service = _FakeProfileService()
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())

    async def asyncTearDown(self) -> None:
        await self.bot.close()

    async def test_profile_commands_register_under_ficha_group(self) -> None:
        await self.bot.add_cog(ProfileCog(self.bot, self.service, ProfileRenderService()))

        command_names = [command.qualified_name for command in self.bot.tree.walk_commands()]

        self.assertIn("ficha criar", command_names)
        self.assertIn("ficha ver", command_names)
        self.assertIn("ficha editar", command_names)
        self.assertIn("ficha resetar", command_names)
        self.assertIn("ficha set-apresentacao", command_names)
        self.assertIn("ficha admin remover-campo", command_names)
        self.assertIn("ficha admin restaurar-campo", command_names)
        self.assertIn("ficha admin editar-campo", command_names)

    async def test_authorial_fields_are_guild_scoped_and_display_name_is_not_persisted(self) -> None:
        self.assertNotIn("display_name", PROFILE_FIELD_REGISTRY.keys())
        self.assertNotIn("name", PROFILE_FIELD_REGISTRY.keys())
        self.assertEqual(PROFILE_FIELD_REGISTRY.get("pronouns").key, "pronouns")


class _FakeProfileService:
    field_registry = PROFILE_FIELD_REGISTRY

    async def ensure_profile(self, guild_id: int, user_id: int) -> None:
        return None


class ProfileServiceValidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = _MemoryProfileRepository()
        self.service = ProfileService(
            repository=self.repository,
            field_registry=PROFILE_FIELD_REGISTRY,
            level_provider=NullLevelProvider(),
            moderation_service=ProfileModerationService(PROFILE_FIELD_REGISTRY),
        )

    async def test_text_sanitization_removes_control_chars_and_limits_blank_lines(self) -> None:
        normalized = self.service.normalize_field_value("bio", "  oi\x00  \n\n\n  tudo\tbem\u202e  ")

        self.assertEqual(normalized, "oi\n\ntudo bem")

    async def test_interests_are_trimmed_deduped_and_limited(self) -> None:
        normalized = self.service.normalize_field_value("interests", "music, Music, RPG, cinema")

        self.assertEqual(normalized, ["music", "RPG", "cinema"])
        with self.assertRaises(ProfileValidationError):
            self.service.normalize_field_value("interests", ",".join(f"tag{i}" for i in range(13)))

    async def test_removed_by_mod_renders_placeholder_until_user_saves_new_value(self) -> None:
        await self.service.set_field(
            guild_id=1,
            user_id=2,
            field_key="bio",
            value="conteudo original",
            updated_by=2,
        )
        await self.service.moderate_field(
            guild_id=1,
            user_id=2,
            field_key="bio",
            status=ProfileFieldStatus.REMOVED_BY_MOD,
            actor_id=99,
            reason="fora das regras",
        )

        snapshot = await self.service.get_profile_snapshot(_fake_guild(1), _fake_member(2))
        self.assertEqual(snapshot.fields["bio"].status, ProfileFieldStatus.REMOVED_BY_MOD)
        self.assertEqual(snapshot.fields["bio"].value, "[Conteúdo removido]")
        self.assertEqual(self.repository.events[-1]["action"], ProfileModerationAction.REMOVE)

        await self.service.set_field(
            guild_id=1,
            user_id=2,
            field_key="bio",
            value="novo conteudo valido",
            updated_by=2,
        )

        snapshot = await self.service.get_profile_snapshot(_fake_guild(1), _fake_member(2))
        self.assertEqual(snapshot.fields["bio"].status, ProfileFieldStatus.ACTIVE)
        self.assertEqual(snapshot.fields["bio"].value, "novo conteudo valido")

    async def test_admin_edit_records_event_and_reset_visual_keeps_text_fields(self) -> None:
        await self.service.set_field(guild_id=1, user_id=2, field_key="headline", value="titulo", updated_by=2)
        await self.service.set_field(guild_id=1, user_id=2, field_key="theme_preset", value="neon", updated_by=2)
        await self.service.set_field(guild_id=1, user_id=2, field_key="charm_preset", value="stars", updated_by=2)
        await self.service.set_field(
            guild_id=1,
            user_id=2,
            field_key="accent_palette",
            value=["#FFFFFF", "#111111"],
            updated_by=2,
        )

        await self.service.admin_set_field(
            guild_id=1,
            user_id=2,
            field_key="mood",
            value="calmo",
            actor_id=99,
        )
        removed_count = await self.service.reset_visual_fields(guild_id=1, user_id=2, actor_id=2)

        self.assertEqual(removed_count, 3)
        fields = await self.repository.list_fields(1, 2)
        self.assertIn("headline", fields)
        self.assertIn("mood", fields)
        self.assertNotIn("theme_preset", fields)
        self.assertEqual(self.repository.events[-2]["action"], ProfileModerationAction.EDIT)
        self.assertEqual(self.repository.events[-1]["action"], ProfileModerationAction.RESET_VISUAL)


class PresentationChannelServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = _MemoryProfileRepository()
        self.service = ProfileService(
            repository=self.repository,
            field_registry=PROFILE_FIELD_REGISTRY,
            level_provider=NullLevelProvider(),
            moderation_service=ProfileModerationService(PROFILE_FIELD_REGISTRY),
        )
        self.presentation = PresentationChannelService(
            self.service,
            burst_window_seconds=90,
            debounce_seconds=0,
        )
        self.guild = _fake_guild(1)
        self.user = _fake_member(2)
        self.channel = _FakeTextChannel(100)
        await self.presentation.set_presentation_channel(self.guild.id, self.channel.id)

    async def test_simple_message_updates_basic_info_with_source(self) -> None:
        message = self._message(10, "Oi, eu sou a Lia.")

        processed = await self.presentation.process_message(message)

        field = await self.repository.get_field(1, 2, "basic_info")
        self.assertTrue(processed)
        self.assertEqual(field.value, "Oi, eu sou a Lia.")
        self.assertEqual(field.source_type, ProfileFieldSourceType.PRESENTATION_CHANNEL)
        self.assertEqual(field.source_message_ids, (10,))

    async def test_burst_messages_merge_into_one_basic_info_block(self) -> None:
        await self.presentation.process_message(self._message(10, "Primeira parte.", seconds=0))
        await self.presentation.process_message(self._message(11, "Segunda parte.", seconds=30))

        field = await self.repository.get_field(1, 2, "basic_info")
        self.assertEqual(field.value, "Primeira parte.\n\nSegunda parte.")
        self.assertEqual(field.source_message_ids, (10, 11))

    async def test_edit_recompiles_existing_block(self) -> None:
        first = self._message(10, "Primeira parte.", seconds=0)
        second = self._message(11, "Segunda parte.", seconds=30)
        await self.presentation.process_message(first)
        await self.presentation.process_message(second)

        edited = self._message(10, "Primeira parte editada.", seconds=0)
        await self.presentation.process_message_edit(first, edited)

        field = await self.repository.get_field(1, 2, "basic_info")
        self.assertEqual(field.value, "Primeira parte editada.\n\nSegunda parte.")
        self.assertEqual(field.source_message_ids, (10, 11))

    async def test_partial_and_total_delete_recompile_or_clear(self) -> None:
        first = self._message(10, "Primeira parte.", seconds=0)
        second = self._message(11, "Segunda parte.", seconds=30)
        await self.presentation.process_message(first)
        await self.presentation.process_message(second)

        await self.presentation.process_message_delete(first)
        field = await self.repository.get_field(1, 2, "basic_info")
        self.assertEqual(field.value, "Segunda parte.")
        self.assertEqual(field.source_message_ids, (11,))

        await self.presentation.process_message_delete(second)
        self.assertIsNone(await self.repository.get_field(1, 2, "basic_info"))

    async def test_new_user_content_restores_removed_by_mod_basic_info(self) -> None:
        await self.service.set_field(guild_id=1, user_id=2, field_key="basic_info", value="velho", updated_by=2)
        await self.service.moderate_field(
            guild_id=1,
            user_id=2,
            field_key="basic_info",
            status=ProfileFieldStatus.REMOVED_BY_MOD,
            actor_id=99,
            reason="moderado",
        )

        await self.presentation.process_message(self._message(10, "Novo texto valido."))

        field = await self.repository.get_field(1, 2, "basic_info")
        self.assertEqual(field.status, ProfileFieldStatus.ACTIVE)
        self.assertEqual(field.value, "Novo texto valido.")
        self.assertEqual(field.source_type, ProfileFieldSourceType.PRESENTATION_CHANNEL)

    def _message(self, message_id: int, content: str, *, seconds: int = 0) -> SimpleNamespace:
        message = SimpleNamespace(
            id=message_id,
            guild=self.guild,
            channel=self.channel,
            author=self.user,
            content=content,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds),
            webhook_id=None,
            type=discord.MessageType.default,
        )
        self.channel.messages[message_id] = message
        return message


def _fake_guild(guild_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=guild_id)


def _fake_member(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        display_name="Nome vivo",
        name="username",
        mention=f"<@{user_id}>",
        display_avatar=SimpleNamespace(url=f"https://cdn.example/{user_id}.png"),
    )


class _MemoryProfileRepository:
    def __init__(self) -> None:
        self.profiles: dict[tuple[int, int], ProfileRecord] = {}
        self.fields: dict[tuple[int, int, str], ProfileFieldValue] = {}
        self.settings: dict[int, GuildProfileSettings] = {}
        self.events: list[dict[str, object]] = []

    async def ensure_profile(self, guild_id: int, user_id: int) -> ProfileRecord:
        key = (guild_id, user_id)
        if key not in self.profiles:
            self.profiles[key] = ProfileRecord(
                guild_id=guild_id,
                user_id=user_id,
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
                onboarding_completed=False,
                render_revision=0,
            )
        return self.profiles[key]

    async def mark_onboarding_completed(self, guild_id: int, user_id: int, completed: bool = True) -> ProfileRecord:
        profile = await self.ensure_profile(guild_id, user_id)
        updated = replace(profile, onboarding_completed=completed, render_revision=profile.render_revision + 1)
        self.profiles[(guild_id, user_id)] = updated
        return updated

    async def list_fields(self, guild_id: int, user_id: int) -> dict[str, ProfileFieldValue]:
        return {
            field_key: field
            for (stored_guild_id, stored_user_id, field_key), field in self.fields.items()
            if stored_guild_id == guild_id and stored_user_id == user_id
        }

    async def get_settings(self, guild_id: int) -> GuildProfileSettings:
        return self.settings.get(guild_id, GuildProfileSettings(
            guild_id=guild_id,
            presentation_channel_id=None,
            presentation_mode=PresentationMode.MANUAL,
            auto_sync_enabled=False,
        ))

    async def update_settings(
        self,
        guild_id: int,
        *,
        presentation_channel_id: int | None = None,
        presentation_mode: PresentationMode | None = None,
        auto_sync_enabled: bool | None = None,
    ) -> GuildProfileSettings:
        current = await self.get_settings(guild_id)
        updated = GuildProfileSettings(
            guild_id=guild_id,
            presentation_channel_id=presentation_channel_id if presentation_channel_id is not None else current.presentation_channel_id,
            presentation_mode=presentation_mode or current.presentation_mode,
            auto_sync_enabled=current.auto_sync_enabled if auto_sync_enabled is None else auto_sync_enabled,
        )
        self.settings[guild_id] = updated
        return updated

    async def upsert_field(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        value: str,
        source_type: ProfileFieldSourceType,
        source_message_ids: tuple[int, ...],
        updated_by: int | None,
    ) -> ProfileFieldValue:
        await self.ensure_profile(guild_id, user_id)
        field = ProfileFieldValue(
            guild_id=guild_id,
            user_id=user_id,
            field_key=field_key,
            value=value,
            status=ProfileFieldStatus.ACTIVE,
            source_type=source_type,
            source_message_ids=source_message_ids,
            updated_at="2026-01-01T00:00:00+00:00",
            updated_by=updated_by,
            moderated_by=None,
            moderated_at=None,
            moderation_reason=None,
        )
        self.fields[(guild_id, user_id, field_key)] = field
        return field

    async def get_field(self, guild_id: int, user_id: int, field_key: str) -> ProfileFieldValue | None:
        return self.fields.get((guild_id, user_id, field_key))

    async def find_presentation_basic_info_by_message_id(
        self,
        guild_id: int,
        message_id: int,
    ) -> ProfileFieldValue | None:
        for (stored_guild_id, _stored_user_id, field_key), field in self.fields.items():
            if stored_guild_id != guild_id or field_key != "basic_info":
                continue
            if field.source_type is not ProfileFieldSourceType.PRESENTATION_CHANNEL:
                continue
            if message_id in field.source_message_ids:
                return field
        return None

    async def reset_field(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        actor_id: int,
        reason: str | None = None,
    ) -> bool:
        removed = self.fields.pop((guild_id, user_id, field_key), None) is not None
        if removed:
            self.events.append({"field_key": field_key, "action": ProfileModerationAction.RESET, "actor_id": actor_id})
        return removed

    async def reset_profile_fields(
        self,
        *,
        guild_id: int,
        user_id: int,
        actor_id: int,
        reason: str | None = None,
    ) -> int:
        keys = [key for key in self.fields if key[0] == guild_id and key[1] == user_id]
        for key in keys:
            self.fields.pop(key)
        self.events.append({"field_key": "*", "action": ProfileModerationAction.RESET_ALL, "actor_id": actor_id})
        return len(keys)

    async def reset_fields(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_keys: tuple[str, ...],
        actor_id: int,
        action: ProfileModerationAction,
        reason: str | None = None,
    ) -> int:
        removed_count = 0
        for field_key in field_keys:
            if self.fields.pop((guild_id, user_id, field_key), None) is not None:
                removed_count += 1
        self.events.append({"field_key": ",".join(field_keys), "action": action, "actor_id": actor_id})
        return removed_count

    async def moderate_field(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        status: ProfileFieldStatus,
        actor_id: int,
        reason: str | None,
    ) -> bool:
        await self.ensure_profile(guild_id, user_id)
        key = (guild_id, user_id, field_key)
        current = self.fields.get(key)
        if current is None:
            current = ProfileFieldValue(
                guild_id=guild_id,
                user_id=user_id,
                field_key=field_key,
                value="",
                status=ProfileFieldStatus.ACTIVE,
                source_type=ProfileFieldSourceType.MODERATION,
                source_message_ids=(),
                updated_at="2026-01-01T00:00:00+00:00",
                updated_by=None,
                moderated_by=None,
                moderated_at=None,
                moderation_reason=None,
            )
        self.fields[key] = replace(
            current,
            status=status,
            moderated_by=actor_id,
            moderated_at="2026-01-01T00:00:00+00:00",
            moderation_reason=reason,
        )
        action = ProfileModerationAction.REMOVE if status is ProfileFieldStatus.REMOVED_BY_MOD else ProfileModerationAction.HIDE
        self.events.append({"field_key": field_key, "action": action, "actor_id": actor_id})
        return True

    async def restore_field(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        actor_id: int,
        reason: str | None,
    ) -> bool:
        key = (guild_id, user_id, field_key)
        current = self.fields.get(key)
        if current is None:
            return False
        self.fields[key] = replace(current, status=ProfileFieldStatus.ACTIVE, moderated_by=None, moderated_at=None, moderation_reason=None)
        self.events.append({"field_key": field_key, "action": ProfileModerationAction.RESTORE, "actor_id": actor_id})
        return True

    async def record_moderation_event(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        action: ProfileModerationAction,
        actor_id: int,
        reason: str | None,
    ) -> None:
        self.events.append({"field_key": field_key, "action": action, "actor_id": actor_id, "reason": reason})


class _FakeTextChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.messages: dict[int, SimpleNamespace] = {}

    async def fetch_message(self, message_id: int) -> SimpleNamespace:
        message = self.messages.get(message_id)
        if message is None:
            raise discord.NotFound(response=SimpleNamespace(status=404, reason="not found"), message="not found")
        return message
