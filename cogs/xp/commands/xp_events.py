from __future__ import annotations

"""Eventos do Sistema de XP."""

import random

import discord
from discord.ext import commands

from ..xp_constants import LEVEL_UP_MESSAGES
from ..xp_runtime import XpRuntime


class XpEvents(commands.Cog):
    def __init__(self, bot: commands.Bot, runtime: XpRuntime) -> None:
        self.bot = bot
        self.runtime = runtime

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        try:
            result = await self.runtime.service.process_message(message)
            if (
                result is None
                or message.guild is None
                or not isinstance(message.author, discord.Member)
                or result.new_level <= result.old_level
            ):
                return
            config = await self.runtime.service.get_guild_config(message.guild.id)
            await self.runtime.service.grant_level_rewards(message.author, result.new_level)
            channel = message.guild.get_channel(config.levelup_channel_id) if config.levelup_channel_id else message.channel
            if channel is None:
                return
            message_template = random.choice(LEVEL_UP_MESSAGES)
            embed = discord.Embed(
                description=message_template.format(mention=message.author.mention, level=result.new_level),
                color=discord.Color.from_rgb(120, 60, 240),
            )
            try:
                await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                return
        except Exception:
            self.runtime.service.logger.exception("falha ao processar ganho de xp", exc_info=True)
