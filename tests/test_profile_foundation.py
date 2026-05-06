from __future__ import annotations

import asyncio
import io
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageStat

from cogs.profile.cog import ProfileCog
from cogs.profile.cog import profile_admin_check, profile_settings_check
from cogs.profile.field_registry import PROFILE_FIELD_REGISTRY
from cogs.profile.field_registry import FieldRegistry, UnknownProfileFieldError
from cogs.profile.models import (
    FieldDefinition,
    GuildProfileSettings,
    PresentationMode,
    ProfileDeletionResult,
    ProfileFieldSourceType,
    ProfileFieldStatus,
    ProfileFieldType,
    ProfileFieldValue,
    ProfileModerationAction,
    ProfileRecord,
)
from cogs.profile.schemas import LevelSnapshot
from cogs.profile.services import (
    NullLevelProvider,
    PresentationChannelService,
    ProfileModerationService,
    ProfileRenderService,
    ProfileService,
    ProfileValidationError,
    XpRuntimeLevelProvider,
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
        self.assertIn("ficha excluir-meus-dados", command_names)
        self.assertIn("ficha set-apresentacao", command_names)
        self.assertIn("ficha admin remover-campo", command_names)
        self.assertIn("ficha admin restaurar-campo", command_names)
        self.assertIn("ficha admin editar-campo", command_names)

    async def test_authorial_fields_are_guild_scoped_and_display_name_is_not_persisted(self) -> None:
        self.assertNotIn("display_name", PROFILE_FIELD_REGISTRY.keys())
        self.assertNotIn("name", PROFILE_FIELD_REGISTRY.keys())
        self.assertEqual(PROFILE_FIELD_REGISTRY.get("pronouns").key, "pronouns")

    async def test_admin_permission_checks_are_explicit(self) -> None:
        allowed_admin = SimpleNamespace(user=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=False, moderate_members=True)))
        allowed_settings = SimpleNamespace(user=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True, moderate_members=False)))
        denied = SimpleNamespace(user=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=False, moderate_members=False)))

        self.assertTrue(await profile_admin_check(allowed_admin))
        self.assertTrue(await profile_settings_check(allowed_settings))
        with self.assertRaises(app_commands.MissingPermissions):
            await profile_admin_check(denied)
        with self.assertRaises(app_commands.MissingPermissions):
            await profile_settings_check(denied)

    async def test_admin_commands_keep_permission_guards(self) -> None:
        await self.bot.add_cog(ProfileCog(self.bot, self.service, ProfileRenderService()))
        commands_by_name = {command.qualified_name: command for command in self.bot.tree.walk_commands()}

        for name in ("ficha admin remover-campo", "ficha admin restaurar-campo", "ficha admin editar-campo"):
            command = commands_by_name[name]
            self.assertTrue(command.checks)
            self.assertIsNotNone(command.default_permissions)

    async def test_view_cooldown_blocks_immediate_reuse(self) -> None:
        cog = ProfileCog(self.bot, self.service, ProfileRenderService())

        self.assertEqual(cog._consume_view_cooldown(1, 2), 0.0)
        self.assertGreater(cog._consume_view_cooldown(1, 2), 0.0)


class ProfileFieldRegistryTests(unittest.TestCase):
    def test_unknown_field_and_duplicate_keys_are_rejected(self) -> None:
        with self.assertRaises(UnknownProfileFieldError):
            PROFILE_FIELD_REGISTRY.get("campo-inexistente")
        with self.assertRaises(ValueError):
            FieldRegistry(
                (
                    FieldDefinition("x", "X", ProfileFieldType.TEXT_SHORT, 10, False, "", True, True, True),
                    FieldDefinition("X", "X2", ProfileFieldType.TEXT_SHORT, 10, False, "", True, True, True),
                )
            )


class _FakeProfileService:
    field_registry = PROFILE_FIELD_REGISTRY

    async def ensure_profile(self, guild_id: int, user_id: int) -> None:
        return None


class _RichLevelProvider:
    provider_name = "test"

    async def get_level_snapshot(self, guild, member) -> LevelSnapshot:
        return LevelSnapshot(
            guild_id=guild.id,
            user_id=member.id,
            total_xp=12450,
            level=17,
            xp_into_level=450,
            xp_for_next_level=900,
            remaining_to_next=450,
            progress_ratio=0.5,
            position=4,
            badge_role_id=123,
            badge_role_name="Guardia do Arquivo",
            badge_role_color=0xB58900,
            provider_name=self.provider_name,
            available=True,
            unavailable_reason=None,
        )


class _CountingRenderService(ProfileRenderService):
    def __init__(self) -> None:
        super().__init__()
        self.render_calls = 0

    def render_profile_card(self, snapshot, *, avatar_image=None) -> bytes:
        self.render_calls += 1
        return super().render_profile_card(snapshot, avatar_image=avatar_image)

    async def _fetch_avatar(self, avatar_url: str | None) -> Image.Image | None:
        return None


class _FailingXpService:
    async def get_rank_snapshot(self, guild, member):
        raise RuntimeError("xp indisponivel")


class _WorkingXpService:
    async def get_rank_snapshot(self, guild, member):
        return SimpleNamespace(
            total_xp=3200,
            level=7,
            xp_into_level=200,
            xp_for_next_level=500,
            remaining_to_next=300,
            progress_ratio=0.4,
            position=3,
        )

    async def get_guild_config(self, guild_id: int):
        return SimpleNamespace(level_roles={5: 55})


class _ExplodingLevelProvider:
    provider_name = "exploding"

    async def get_level_snapshot(self, guild, member):
        raise RuntimeError("falha controlada")


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

    async def test_delete_user_data_removes_profile_fields_and_events(self) -> None:
        await self.service.set_field(guild_id=1, user_id=2, field_key="basic_info", value="origem", updated_by=2, source_message_ids=(10, 11))
        await self.service.moderate_field(
            guild_id=1,
            user_id=2,
            field_key="basic_info",
            status=ProfileFieldStatus.REMOVED_BY_MOD,
            actor_id=99,
            reason="teste",
        )

        result = await self.service.delete_user_data(guild_id=1, user_id=2)

        self.assertTrue(result.profile_deleted)
        self.assertEqual(result.fields_deleted, 1)
        self.assertGreaterEqual(result.moderation_events_deleted, 1)
        self.assertIsNone(await self.repository.get_profile(1, 2))
        self.assertEqual(await self.repository.list_fields(1, 2), {})

    async def test_snapshot_degrades_when_level_provider_raises(self) -> None:
        service = ProfileService(
            repository=self.repository,
            field_registry=PROFILE_FIELD_REGISTRY,
            level_provider=_ExplodingLevelProvider(),
            moderation_service=ProfileModerationService(PROFILE_FIELD_REGISTRY),
        )

        with self.assertLogs("baphomet.profile.service", level="ERROR") as logs:
            snapshot = await service.get_profile_snapshot(_fake_guild(1), _fake_member(2))

        self.assertFalse(snapshot.level.available)
        self.assertIn("nao configurado", snapshot.level.unavailable_reason)
        self.assertTrue(any("xp_snapshot_failed" in line for line in logs.output))


class ProfileRenderServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = _MemoryProfileRepository()
        self.service = ProfileService(
            repository=self.repository,
            field_registry=PROFILE_FIELD_REGISTRY,
            level_provider=_RichLevelProvider(),
            moderation_service=ProfileModerationService(PROFILE_FIELD_REGISTRY),
        )

    async def test_renderer_outputs_png_with_required_ratio_and_texture(self) -> None:
        snapshot = await self._snapshot(theme="classic", charm="laurels")
        avatar = Image.new("RGB", (320, 220), (90, 35, 145))

        data = ProfileRenderService().render_profile_card(snapshot, avatar_image=avatar)
        image = Image.open(io.BytesIO(data))

        self.assertEqual(image.size, (1500, 1000))
        self.assertEqual(image.format, "PNG")
        sampled = image.convert("RGB").resize((75, 50)).tobytes()
        colors = {sampled[index:index + 3] for index in range(0, len(sampled), 3)}
        self.assertGreater(len(colors), 100)

    async def test_renderer_snapshot_statistics_remain_in_expected_band(self) -> None:
        snapshot = await self._snapshot(theme="minimal", charm="vinyl")

        data = ProfileRenderService().render_profile_card(snapshot)
        image = Image.open(io.BytesIO(data)).convert("RGB").resize((32, 32))
        stat = ImageStat.Stat(image)
        mean_luma = sum(stat.mean) / 3
        variance = sum(stat.var) / 3

        self.assertGreater(mean_luma, 65)
        self.assertLess(mean_luma, 225)
        self.assertGreater(variance, 450)

    async def test_renderer_keeps_template_cache_per_theme(self) -> None:
        renderer = ProfileRenderService()
        snapshot = await self._snapshot(theme="celestial", charm="stars")

        renderer.render_profile_card(snapshot)
        cached_template = renderer._template_cache["celestial"]
        renderer.render_profile_card(snapshot)

        self.assertIs(renderer._template_cache["celestial"], cached_template)

    async def test_async_renderer_deduplicates_concurrent_renders_and_uses_revision_cache(self) -> None:
        renderer = _CountingRenderService()
        snapshot = await self._snapshot(theme="classic", charm="none")
        snapshot = replace(snapshot, live=replace(snapshot.live, avatar_url=None))

        await asyncio.gather(renderer.render_profile(snapshot), renderer.render_profile(snapshot))
        self.assertEqual(renderer.render_calls, 1)

        await self.service.set_field(guild_id=1, user_id=2, field_key="headline", value="novo titulo", updated_by=2)
        updated_snapshot = await self.service.get_profile_snapshot(_fake_guild(1), _fake_member_without_avatar(2))
        await renderer.render_profile(updated_snapshot)

        self.assertEqual(renderer.render_calls, 2)

    async def test_renderer_pixel_wrap_does_not_exceed_width(self) -> None:
        renderer = ProfileRenderService()
        font = renderer._fonts.ui_20
        lines = renderer._wrap_text_pixels(
            "uma frase deliberadamente comprida para validar quebra por largura real de pixel",
            font,
            180,
            max_lines=3,
        )

        self.assertLessEqual(len(lines), 3)
        self.assertTrue(all(renderer._text_width(line, font) <= 180 for line in lines))

    async def test_renderer_accepts_moderated_placeholder_and_unavailable_xp(self) -> None:
        service = ProfileService(
            repository=self.repository,
            field_registry=PROFILE_FIELD_REGISTRY,
            level_provider=NullLevelProvider(),
            moderation_service=ProfileModerationService(PROFILE_FIELD_REGISTRY),
        )
        await service.set_field(guild_id=1, user_id=2, field_key="basic_info", value="original", updated_by=2)
        await service.set_field(guild_id=1, user_id=2, field_key="interests", value=["musica"], updated_by=2)
        await service.moderate_field(
            guild_id=1,
            user_id=2,
            field_key="basic_info",
            status=ProfileFieldStatus.REMOVED_BY_MOD,
            actor_id=99,
            reason="teste",
        )
        await service.moderate_field(
            guild_id=1,
            user_id=2,
            field_key="interests",
            status=ProfileFieldStatus.REMOVED_BY_MOD,
            actor_id=99,
            reason="teste",
        )
        snapshot = await service.get_profile_snapshot(_fake_guild(1), _fake_member(2))
        renderer = ProfileRenderService()

        self.assertEqual(snapshot.rendered_fields()["basic_info"], "[Conteúdo removido]")
        self.assertEqual(renderer._field_list(snapshot, "interests"), ["[Conteúdo removido]"])
        data = renderer.render_profile_card(snapshot)

        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))

    async def _snapshot(self, *, theme: str, charm: str):
        await self.service.set_field(guild_id=1, user_id=2, field_key="pronouns", value="ela/dela", updated_by=2)
        await self.service.set_field(guild_id=1, user_id=2, field_key="headline", value="Curadora de caos elegante", updated_by=2)
        await self.service.set_field(
            guild_id=1,
            user_id=2,
            field_key="basic_info",
            value="Gosto de comunidades pequenas, rituais de cafe e sistemas bem cuidados.",
            updated_by=2,
        )
        await self.service.set_field(
            guild_id=1,
            user_id=2,
            field_key="bio",
            value="Construo pontes entre pessoas, ferramentas e historias. Sempre procurando um jeito mais bonito de organizar ideias.",
            updated_by=2,
        )
        await self.service.set_field(guild_id=1, user_id=2, field_key="ask_me_about", value="musica, RPG e automacoes", updated_by=2)
        await self.service.set_field(guild_id=1, user_id=2, field_key="mood", value="concentrada", updated_by=2)
        await self.service.set_field(guild_id=1, user_id=2, field_key="interests", value=["design", "python", "lore"], updated_by=2)
        await self.service.set_field(guild_id=1, user_id=2, field_key="theme_preset", value=theme, updated_by=2)
        await self.service.set_field(guild_id=1, user_id=2, field_key="charm_preset", value=charm, updated_by=2)
        await self.service.set_field(
            guild_id=1,
            user_id=2,
            field_key="accent_palette",
            value=["#7A5CFF", "#FFB000", "#2E2E2E"],
            updated_by=2,
        )
        return await self.service.get_profile_snapshot(_fake_guild(1), _fake_member(2))


class ProfileXpAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_xp_adapter_returns_degraded_snapshot_when_runtime_fails(self) -> None:
        provider = XpRuntimeLevelProvider(SimpleNamespace(xp_runtime=SimpleNamespace(service=_FailingXpService())))

        with self.assertLogs("baphomet.profile.level_provider", level="ERROR") as logs:
            snapshot = await provider.get_level_snapshot(_fake_guild(1), _fake_member(2))

        self.assertFalse(snapshot.available)
        self.assertEqual(snapshot.provider_name, "xp_runtime")
        self.assertIn("falhou", snapshot.unavailable_reason)
        self.assertTrue(any("xp_snapshot_failed" in line for line in logs.output))

    async def test_xp_adapter_resolves_badge_role_from_live_member_roles(self) -> None:
        role = SimpleNamespace(id=55, name="Insignia Viva", color=SimpleNamespace(value=0x123456))
        guild = SimpleNamespace(id=1, get_role=lambda role_id: role if role_id == 55 else None)
        member = SimpleNamespace(id=2, roles=[role])
        provider = XpRuntimeLevelProvider(SimpleNamespace(xp_runtime=SimpleNamespace(service=_WorkingXpService())))

        snapshot = await provider.get_level_snapshot(guild, member)

        self.assertTrue(snapshot.available)
        self.assertEqual(snapshot.level, 7)
        self.assertEqual(snapshot.badge_role_id, 55)
        self.assertEqual(snapshot.badge_role_name, "Insignia Viva")


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

    async def test_debounce_flush_writes_latest_burst_once(self) -> None:
        presentation = PresentationChannelService(
            self.service,
            burst_window_seconds=90,
            debounce_seconds=60,
        )
        await presentation.set_presentation_channel(self.guild.id, self.channel.id)

        await presentation.process_message(self._message(20, "Primeira.", seconds=0))
        await presentation.process_message(self._message(21, "Segunda.", seconds=30))
        self.assertIsNone(await self.repository.get_field(1, 2, "basic_info"))

        await presentation.flush_pending(1, 2)

        field = await self.repository.get_field(1, 2, "basic_info")
        self.assertEqual(field.value, "Primeira.\n\nSegunda.")
        self.assertEqual(field.source_message_ids, (20, 21))
        self.assertEqual(self.repository.upsert_calls, 1)

    async def test_forget_user_clears_pending_sources_before_privacy_delete(self) -> None:
        presentation = PresentationChannelService(
            self.service,
            burst_window_seconds=90,
            debounce_seconds=60,
        )
        await presentation.set_presentation_channel(self.guild.id, self.channel.id)
        await presentation.process_message(self._message(30, "Privado.", seconds=0))

        await presentation.forget_user(1, 2)
        await presentation.flush_pending(1, 2)

        self.assertIsNone(await self.repository.get_field(1, 2, "basic_info"))

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


def _fake_member_without_avatar(user_id: int) -> SimpleNamespace:
    member = _fake_member(user_id)
    member.display_avatar = None
    return member


class _MemoryProfileRepository:
    def __init__(self) -> None:
        self.profiles: dict[tuple[int, int], ProfileRecord] = {}
        self.fields: dict[tuple[int, int, str], ProfileFieldValue] = {}
        self.settings: dict[int, GuildProfileSettings] = {}
        self.events: list[dict[str, object]] = []
        self.upsert_calls = 0

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

    async def get_profile(self, guild_id: int, user_id: int) -> ProfileRecord | None:
        return self.profiles.get((guild_id, user_id))

    async def mark_onboarding_completed(self, guild_id: int, user_id: int, completed: bool = True) -> ProfileRecord:
        profile = await self.ensure_profile(guild_id, user_id)
        updated = replace(profile, onboarding_completed=completed, render_revision=profile.render_revision + 1)
        self.profiles[(guild_id, user_id)] = updated
        return updated

    async def delete_user_profile_data(self, guild_id: int, user_id: int) -> ProfileDeletionResult:
        field_keys = [key for key in self.fields if key[0] == guild_id and key[1] == user_id]
        for key in field_keys:
            self.fields.pop(key)
        events = [
            event
            for event in self.events
            if event.get("guild_id") == guild_id and event.get("user_id") == user_id
        ]
        self.events = [
            event
            for event in self.events
            if not (event.get("guild_id") == guild_id and event.get("user_id") == user_id)
        ]
        profile_deleted = self.profiles.pop((guild_id, user_id), None) is not None
        return ProfileDeletionResult(
            guild_id=guild_id,
            user_id=user_id,
            profile_deleted=profile_deleted,
            fields_deleted=len(field_keys),
            moderation_events_deleted=len(events),
        )

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
        self.upsert_calls += 1
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
        self._touch(guild_id, user_id)
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
            self._touch(guild_id, user_id)
            self.events.append({"guild_id": guild_id, "user_id": user_id, "field_key": field_key, "action": ProfileModerationAction.RESET, "actor_id": actor_id})
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
        if keys:
            self._touch(guild_id, user_id)
        self.events.append({"guild_id": guild_id, "user_id": user_id, "field_key": "*", "action": ProfileModerationAction.RESET_ALL, "actor_id": actor_id})
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
        if removed_count:
            self._touch(guild_id, user_id)
        self.events.append({"guild_id": guild_id, "user_id": user_id, "field_key": ",".join(field_keys), "action": action, "actor_id": actor_id})
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
        self._touch(guild_id, user_id)
        action = ProfileModerationAction.REMOVE if status is ProfileFieldStatus.REMOVED_BY_MOD else ProfileModerationAction.HIDE
        self.events.append({"guild_id": guild_id, "user_id": user_id, "field_key": field_key, "action": action, "actor_id": actor_id})
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
        self._touch(guild_id, user_id)
        self.events.append({"guild_id": guild_id, "user_id": user_id, "field_key": field_key, "action": ProfileModerationAction.RESTORE, "actor_id": actor_id})
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
        self.events.append({"guild_id": guild_id, "user_id": user_id, "field_key": field_key, "action": action, "actor_id": actor_id, "reason": reason})

    def _touch(self, guild_id: int, user_id: int) -> None:
        profile = self.profiles[(guild_id, user_id)]
        self.profiles[(guild_id, user_id)] = replace(
            profile,
            render_revision=profile.render_revision + 1,
            updated_at="2026-01-01T00:00:01+00:00",
        )


class _FakeTextChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.messages: dict[int, SimpleNamespace] = {}

    async def fetch_message(self, message_id: int) -> SimpleNamespace:
        message = self.messages.get(message_id)
        if message is None:
            raise discord.NotFound(response=SimpleNamespace(status=404, reason="not found"), message="not found")
        return message
