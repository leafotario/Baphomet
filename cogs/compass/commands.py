from __future__ import annotations

import asyncio
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from .models import COMPASS_ROTULOS
from .modals import CompassCreateModal


LOGGER = logging.getLogger("baphomet.compass.commands")


class CompassCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.http_session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        timeout = aiohttp.ClientTimeout(total=20, connect=8, sock_read=12)
        self.http_session = aiohttp.ClientSession(timeout=timeout)
        LOGGER.info("Compass cog inicializado com aiohttp.ClientSession compartilhada.")

    def cog_unload(self) -> None:
        if self.http_session is not None and not self.http_session.closed:
            asyncio.create_task(self.http_session.close())

    @app_commands.command(name="compass_criar", description="Cria um Compass transitorio por modal.")
    async def compass_criar(self, interaction: discord.Interaction) -> None:
        if self.http_session is None or self.http_session.closed:
            self.http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20, connect=8, sock_read=12)
            )

        await interaction.response.send_modal(
            CompassCreateModal(
                bot=self.bot,
                http_session=self.http_session,
                autor_id=interaction.user.id,
                rotulos=COMPASS_ROTULOS,
            )
        )


async def setup(bot: commands.Bot) -> None:
    cog = CompassCog(bot)
    await cog.start()
    await bot.add_cog(cog)
