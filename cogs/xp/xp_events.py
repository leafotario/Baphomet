from __future__ import annotations

"""Eventos do Sistema de XP."""

import asyncio
import secrets
import traceback

import discord
from discord.ext import commands, tasks

from ..xp_constants import LEVEL_UP_MESSAGES
from ..xp_runtime import XpRuntime
from core.logger import log_exception



class XpEvents(commands.Cog):
    def __init__(self, bot: commands.Bot, runtime: XpRuntime) -> None:
        self.bot = bot
        self.runtime = runtime
        self._sync_task_started = False

    async def cog_load(self) -> None:
        return None

    def cog_unload(self) -> None:
        if self.sync_level_roles_task.is_running():
            self.sync_level_roles_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._sync_task_started:
            self._sync_task_started = True
            self.sync_level_roles_task.start()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        try:
            result = await self.runtime.service.process_message(message)
            if (
                result is None
                or message.guild is None
                or not isinstance(message.author, discord.Member)
            ):
                return
            if result.new_level != result.old_level:
                await self.runtime.service.sync_member_level_roles(
                    message.author,
                    reason=f"XP: sincronização após mudança para nível {result.new_level}",
                )
            if result.new_level <= result.old_level:
                return
            config = await self.runtime.service.get_guild_config(message.guild.id)
            channel = message.guild.get_channel(config.levelup_channel_id) if config.levelup_channel_id else message.channel
            if channel is None:
                return
            message_template = secrets.SystemRandom().choice(LEVEL_UP_MESSAGES)
            embed = discord.Embed(
                description=message_template.format(mention=message.author.mention, level=result.new_level),
                color=discord.Color.from_rgb(120, 60, 240),
            )
            try:
                await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                return
        except Exception as e:
            log_exception(e)
            tb_str = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            guild_id = message.guild.id if message.guild else "DM"
            user_id = message.author.id
            self.runtime.service.logger.error(
                f"❌ [XP EVENTS] Falha Crítica no on_message\n"
                f"➤ Usuário: {message.author} (ID: {user_id})\n"
                f"➤ Guilda: ID: {guild_id}\n"
                f"➤ Erro: {type(e).__name__}: {e}\n"
                f"➤ Traceback Integral:\n{tb_str}"
            )

    @tasks.loop(minutes=10)
    async def sync_level_roles_task(self) -> None:
        logger = self.runtime.service.logger
        logger.info("sync recorrente de cargos de nível iniciado")
        total_members = 0
        total_added = 0
        total_removed = 0
        for guild in self.bot.guilds:
            try:
                stats = await self.runtime.service.sync_guild_level_roles(guild)
            except Exception as exc:
                log_exception(exc)
                logger.exception("falha no sync recorrente de cargos de nível guild_id=%s", guild.id)
                continue
            total_members += stats["members"]
            total_added += stats["added"]
            total_removed += stats["removed"]
            if stats["members"] or stats["added"] or stats["removed"]:
                logger.info(
                    "sync de cargos de nível guild_id=%s members=%s added=%s removed=%s skipped=%s missing=%s",
                    guild.id,
                    stats["members"],
                    stats["added"],
                    stats["removed"],
                    stats["skipped"],
                    stats["missing"],
                )
            await asyncio.sleep(1)
        logger.info(
            "sync recorrente de cargos de nível finalizado members=%s added=%s removed=%s",
            total_members,
            total_added,
            total_removed,
        )

    @sync_level_roles_task.before_loop
    async def before_sync_level_roles_task(self) -> None:
        await self.bot.wait_until_ready()
