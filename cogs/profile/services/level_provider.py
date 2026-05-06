from __future__ import annotations

import logging
from dataclasses import replace
from typing import Protocol

import discord
from discord.ext import commands

from ..schemas import LevelSnapshot


LOGGER = logging.getLogger("baphomet.profile.level_provider")


class LevelProvider(Protocol):
    async def get_level_snapshot(self, guild: discord.Guild, member: discord.Member) -> LevelSnapshot:
        ...


class NullLevelProvider:
    provider_name = "none"

    async def get_level_snapshot(self, guild: discord.Guild, member: discord.Member) -> LevelSnapshot:
        return LevelSnapshot(
            guild_id=guild.id,
            user_id=member.id,
            total_xp=0,
            level=0,
            xp_into_level=0,
            xp_for_next_level=0,
            remaining_to_next=0,
            progress_ratio=0.0,
            position=None,
            badge_role_id=None,
            badge_role_name=None,
            badge_role_color=None,
            provider_name=self.provider_name,
            available=False,
            unavailable_reason="level provider nao configurado",
        )


class XpRuntimeLevelProvider:
    provider_name = "xp_runtime"

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def get_level_snapshot(self, guild: discord.Guild, member: discord.Member) -> LevelSnapshot:
        runtime = getattr(self.bot, "xp_runtime", None)
        if runtime is None:
            return await NullLevelProvider().get_level_snapshot(guild, member)

        try:
            rank_snapshot = await runtime.service.get_rank_snapshot(guild, member)
            config = await runtime.service.get_guild_config(guild.id)
        except Exception:
            LOGGER.exception(
                "profile_level_snapshot_failed guild_id=%s user_id=%s",
                guild.id,
                member.id,
            )
            snapshot = await NullLevelProvider().get_level_snapshot(guild, member)
            return replace(
                snapshot,
                provider_name=self.provider_name,
                unavailable_reason="xp_runtime falhou ao montar o snapshot",
            )

        badge_role = self._resolve_badge_role(guild, member, rank_snapshot.level, config.level_roles)
        return LevelSnapshot(
            guild_id=guild.id,
            user_id=member.id,
            total_xp=rank_snapshot.total_xp,
            level=rank_snapshot.level,
            xp_into_level=rank_snapshot.xp_into_level,
            xp_for_next_level=rank_snapshot.xp_for_next_level,
            remaining_to_next=rank_snapshot.remaining_to_next,
            progress_ratio=rank_snapshot.progress_ratio,
            position=rank_snapshot.position,
            badge_role_id=badge_role.id if badge_role else None,
            badge_role_name=badge_role.name if badge_role else None,
            badge_role_color=badge_role.color.value if badge_role else None,
            provider_name=self.provider_name,
            available=True,
            unavailable_reason=None,
        )

    def _resolve_badge_role(
        self,
        guild: discord.Guild,
        member: discord.Member,
        level: int,
        level_roles: dict[int, int],
    ) -> discord.Role | None:
        if not level_roles:
            return None
        member_role_ids = {role.id for role in member.roles}
        eligible: list[tuple[int, discord.Role, bool]] = []
        for required_level, role_id in level_roles.items():
            if required_level > level:
                continue
            role = guild.get_role(role_id)
            if role is None:
                continue
            eligible.append((required_level, role, role_id in member_role_ids))
        if not eligible:
            return None
        assigned = [item for item in eligible if item[2]]
        best = max(assigned or eligible, key=lambda item: item[0])
        return best[1]
