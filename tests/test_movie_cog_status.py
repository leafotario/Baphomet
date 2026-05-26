from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import discord
from discord.ext import commands

from cogs.movie_cog import MovieCog, MovieGuildConfig
from movie_logic import DEFAULT_DISLIKE_EMOJI, DEFAULT_NEVER_WATCHED_EMOJI


class MovieCogStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())

    async def asyncTearDown(self) -> None:
        await self.bot.close()

    async def test_motd_status_command_is_registered(self) -> None:
        with patch.dict(os.environ, {"TMDB_API_KEY": "test-key"}):
            await self.bot.add_cog(MovieCog(self.bot))

        command_names = [
            command.qualified_name for command in self.bot.tree.walk_commands()
        ]

        self.assertIn("motd_config status", command_names)

    async def test_status_embed_lists_current_motd_configuration(self) -> None:
        config = MovieGuildConfig(
            guild_id=123,
            channel_id=456,
            role_id=789,
            schedule_time="20:30",
            like_emoji="🔥",
            dislike_emoji=None,
            never_watched_emoji=None,
        )

        embed = MovieCog._build_status_embed(config, has_saved_config=True)
        fields = {field.name: field.value for field in embed.fields}

        self.assertEqual(fields["Canal de publicação"], "<#456> (`456`)")
        self.assertEqual(fields["Cargo mencionado"], "<@&789> (`789`)")
        self.assertEqual(fields["Horário diário"], "`20:30` (America/Sao_Paulo)")
        self.assertIn(
            "🔥 — Eu gosto desse filme (configurado)",
            fields["Emojis de reação"],
        )
        self.assertIn(
            f"{DEFAULT_DISLIKE_EMOJI} — Não gosto desse filme (padrão)",
            fields["Emojis de reação"],
        )
        self.assertIn(
            f"{DEFAULT_NEVER_WATCHED_EMOJI} — Nunca assisti esse filme (padrão)",
            fields["Emojis de reação"],
        )
        self.assertNotIn("Observação", fields)

    async def test_status_embed_marks_missing_saved_config(self) -> None:
        config = MovieGuildConfig(
            guild_id=123,
            channel_id=None,
            role_id=None,
            schedule_time="18:00",
        )

        embed = MovieCog._build_status_embed(config, has_saved_config=False)
        fields = {field.name: field.value for field in embed.fields}

        self.assertEqual(fields["Canal de publicação"], "Não definido")
        self.assertEqual(fields["Cargo mencionado"], "Não definido")
        self.assertIn("Observação", fields)
