from __future__ import annotations

import unittest

import discord
from discord.ext import commands

from cogs.tierlist_templates.cog import TierTemplateCog


class TierListCommandRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())

    async def asyncTearDown(self) -> None:
        await self.bot.close()

    async def test_original_tierlist_and_template_commands_coexist(self) -> None:
        await self.bot.load_extension("cogs.tierlist")
        await self.bot.add_cog(TierTemplateCog(self.bot))

        command_names = [command.qualified_name for command in self.bot.tree.walk_commands()]

        self.assertIn("tierlist criar", command_names)
        self.assertIn("tierlist-template criar", command_names)
        self.assertIn("tierlist-template usar", command_names)
        self.assertIn("tierlist-template listar", command_names)
        self.assertEqual(
            sorted(name for name in command_names if command_names.count(name) > 1),
            [],
        )

    async def test_tierlist_safety_group_is_not_registered(self) -> None:
        await self.bot.load_extension("cogs.tierlist")

        command_names = [command.qualified_name for command in self.bot.tree.walk_commands()]

        self.assertNotIn("tierlist-safety", command_names)
        self.assertFalse(any(name.startswith("tierlist-safety ") for name in command_names))
