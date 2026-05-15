from __future__ import annotations

"""Regra De Negócio Do Sistema De XP Do Baphomet."""

import asyncio
import difflib
import hashlib
import logging
import random
from collections import defaultdict, deque
from datetime import timedelta
from typing import Protocol

import discord

from .utils import (
    GuildXpConfig,
    LeaderboardEntry,
    PageResult,
    RankSnapshot,
    VINCULO_RESONANCE_WINDOW_SECONDS,
    VinculoXpContext,
    XpChangeResult,
    build_progress_snapshot,
    calculate_vinculo_xp_context,
    normalize_message_content,
    utc_now,
)
from .db import XpRepository


class VinculoXpContextProvider(Protocol):
    async def get_xp_context(self, guild_id: int, user_id: int, base_xp: int) -> VinculoXpContext:
        ...


class VinculoMultiplierProvider(Protocol):
    async def get_xp_multiplier(self, guild_id: int, user_id: int) -> float:
        ...


class XpService:
    def __init__(
        self,
        repository: XpRepository,
        *,
        rng: random.Random | None = None,
        logger: logging.Logger | None = None,
        vinculos_provider: VinculoXpContextProvider | VinculoMultiplierProvider | None = None,
    ) -> None:
        self.repository = repository
        self.rng = rng or random.Random()
        self.logger = logger or logging.getLogger("baphomet.xp")
        self.vinculos_provider = vinculos_provider
        self._config_cache: dict[int, GuildXpConfig] = {}
        self._user_locks: dict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._recent_fingerprints: dict[tuple[int, int], deque[tuple[str, float]]] = defaultdict(lambda: deque(maxlen=5))

    async def get_guild_config(self, guild_id: int) -> GuildXpConfig:
        cached = self._config_cache.get(guild_id)
        if cached is not None:
            return cached
        config = await self.repository.get_guild_config(guild_id)
        self._config_cache[guild_id] = config
        return config

    async def refresh_guild_config(self, guild_id: int) -> GuildXpConfig:
        config = await self.repository.get_guild_config(guild_id)
        self._config_cache[guild_id] = config
        return config

    async def update_guild_config(self, guild_id: int, **fields: object) -> GuildXpConfig:
        await self.repository.update_guild_config(guild_id, **fields)
        return await self.refresh_guild_config(guild_id)

    async def set_ignored_channel(self, guild_id: int, channel_id: int, enabled: bool) -> GuildXpConfig:
        await self.repository.set_ignored_target("xp_ignored_channels", "channel_id", guild_id, channel_id, enabled)
        return await self.refresh_guild_config(guild_id)

    async def set_ignored_category(self, guild_id: int, category_id: int, enabled: bool) -> GuildXpConfig:
        await self.repository.set_ignored_target("xp_ignored_categories", "category_id", guild_id, category_id, enabled)
        return await self.refresh_guild_config(guild_id)

    async def set_ignored_role(self, guild_id: int, role_id: int, enabled: bool) -> GuildXpConfig:
        await self.repository.set_ignored_target("xp_ignored_roles", "role_id", guild_id, role_id, enabled)
        return await self.refresh_guild_config(guild_id)

    async def set_level_role(self, guild_id: int, level: int, role_id: int) -> GuildXpConfig:
        await self.repository.set_level_role(guild_id, level, role_id)
        return await self.refresh_guild_config(guild_id)

    async def remove_level_role(self, guild_id: int, level: int) -> tuple[GuildXpConfig, bool]:
        removed = await self.repository.remove_level_role(guild_id, level)
        return await self.refresh_guild_config(guild_id), removed

    def _resolve_category_id(self, message: discord.Message) -> int | None:
        category_id = getattr(message.channel, "category_id", None)
        if category_id is not None:
            return int(category_id)
        parent = getattr(message.channel, "parent", None)
        parent_category_id = getattr(parent, "category_id", None)
        if parent_category_id is not None:
            return int(parent_category_id)
        return None

    def _basic_message_checks(self, message: discord.Message, config: GuildXpConfig) -> tuple[bool, str | None, str | None]:
        if message.guild is None:
            return False, "dm", None
        if not isinstance(message.author, discord.Member):
            return False, "autor_invalido", None
        if config.ignore_bots and message.author.bot:
            return False, "bot", None
        if config.ignore_webhooks and message.webhook_id is not None:
            return False, "webhook", None
        if message.channel.id in config.ignored_channel_ids:
            return False, "canal_ignorado", None
        category_id = self._resolve_category_id(message)
        if category_id is not None and category_id in config.ignored_category_ids:
            return False, "categoria_ignorada", None
        if any(role.id in config.ignored_role_ids for role in message.author.roles):
            return False, "cargo_ignorado", None

        normalized = normalize_message_content(message.content)
        if len(normalized) < config.min_message_length:
            return False, "mensagem_curta", None
        unique_words = {token for token in normalized.split(" ") if token}
        if len(unique_words) < config.min_unique_words:
            return False, "poucas_palavras_unicas", None
        return True, None, normalized

    def _passes_local_repeat_check(
        self,
        guild_id: int,
        user_id: int,
        normalized: str,
        config: GuildXpConfig,
        now_ts: float,
    ) -> tuple[bool, str | None]:
        recent = self._recent_fingerprints[(guild_id, user_id)]
        cutoff = now_ts - float(config.anti_repeat_window_seconds)
        while recent and recent[0][1] < cutoff:
            recent.popleft()
        for previous, _ in recent:
            if previous == normalized:
                return False, "mensagem_repetida"
            similarity = difflib.SequenceMatcher(a=previous, b=normalized).ratio()
            if similarity >= config.anti_repeat_similarity:
                return False, "mensagem_muito_parecida"
        return True, None

    def _remember_fingerprint(self, guild_id: int, user_id: int, normalized: str, now_ts: float) -> None:
        self._recent_fingerprints[(guild_id, user_id)].append((normalized, now_ts))

    async def _build_base_vinculo_context(self, base_xp: int, *, source: str = "none") -> VinculoXpContext:
        return calculate_vinculo_xp_context(
            base_xp=base_xp,
            bonds=(),
            penalties=(),
            source=source,
        )

    async def _resolve_vinculo_context(
        self,
        guild_id: int,
        user_id: int,
        base_xp: int,
        awarded_at_iso: str,
    ) -> VinculoXpContext:
        rich_getter = getattr(self.vinculos_provider, "get_xp_context", None) if self.vinculos_provider else None
        if callable(rich_getter):
            try:
                rich_value = await rich_getter(guild_id, user_id, base_xp)
                if isinstance(rich_value, VinculoXpContext):
                    return rich_value
            except Exception:
                self.logger.exception(
                    "falha ao calcular contexto rico de vínculos guild_id=%s user_id=%s",
                    guild_id,
                    user_id,
                )
                return await self._build_base_vinculo_context(base_xp, source="provider_failed")

        try:
            rich_context = await self.repository.get_vinculo_xp_context(
                guild_id=guild_id,
                user_id=user_id,
                base_xp=base_xp,
                awarded_at_iso=awarded_at_iso,
                resonance_window_seconds=VINCULO_RESONANCE_WINDOW_SECONDS,
            )
            if rich_context.source != "none":
                return rich_context
        except Exception:
            self.logger.exception(
                "falha ao calcular contexto de vínculos guild_id=%s user_id=%s",
                guild_id,
                user_id,
            )

        if self.vinculos_provider is None:
            return await self._build_base_vinculo_context(base_xp)

        try:
            multiplier = await self.vinculos_provider.get_xp_multiplier(guild_id, user_id)
            multiplier_value = float(multiplier)
        except Exception:
            self.logger.exception(
                "falha ao calcular multiplicador legado de vínculos guild_id=%s user_id=%s",
                guild_id,
                user_id,
            )
            return await self._build_base_vinculo_context(base_xp, source="provider_failed")

        safe_base_xp = max(0, int(base_xp))
        return VinculoXpContext(
            base_xp=safe_base_xp,
            final_xp=max(0, int(round(safe_base_xp * multiplier_value))),
            final_multiplier=multiplier_value,
            positive_bonus_rate=max(0.0, multiplier_value - 1.0),
            penalty_rate=max(0.0, 1.0 - multiplier_value),
            positive_bonus_pool=0,
            penalty_pool=0,
            source="legacy_multiplier",
        )

    async def process_message(self, message: discord.Message) -> XpChangeResult | None:
        if message.guild is None or not isinstance(message.author, discord.Member):
            return None
        config = await self.get_guild_config(message.guild.id)
        allowed, _, normalized = self._basic_message_checks(message, config)
        if not allowed or normalized is None:
            return None

        base_delta_xp = self.rng.randint(config.min_xp_per_message, config.max_xp_per_message)
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        now = utc_now()
        now_iso = now.isoformat()
        cooldown_cutoff_iso = (now - timedelta(seconds=config.cooldown_seconds)).isoformat()
        repeat_cutoff_iso = (now - timedelta(seconds=config.anti_repeat_window_seconds)).isoformat()
        lock = self._user_locks[(message.guild.id, message.author.id)]

        async with lock:
            passes_repeat, _ = self._passes_local_repeat_check(
                message.guild.id,
                message.author.id,
                normalized,
                config,
                now.timestamp(),
            )
            if not passes_repeat:
                return None
            vinculo_context = await self._resolve_vinculo_context(
                message.guild.id,
                message.author.id,
                base_delta_xp,
                now_iso,
            )
            awarded, _, old_total, new_total = await self.repository.try_add_message_xp(
                guild_id=message.guild.id,
                user_id=message.author.id,
                delta_xp=vinculo_context.final_xp,
                last_known_name=message.author.display_name,
                awarded_at_iso=now_iso,
                cooldown_cutoff_iso=cooldown_cutoff_iso,
                message_hash=fingerprint,
                repeat_cutoff_iso=repeat_cutoff_iso,
                vinculo_context=vinculo_context,
            )
            if not awarded:
                return None
            self._remember_fingerprint(message.guild.id, message.author.id, normalized, now.timestamp())

        old_progress = build_progress_snapshot(old_total, config.difficulty)
        new_progress = build_progress_snapshot(new_total, config.difficulty)
        return XpChangeResult(
            awarded=True,
            reason=None,
            old_total_xp=old_total,
            new_total_xp=new_total,
            old_level=old_progress.level,
            new_level=new_progress.level,
            levels_gained=max(0, new_progress.level - old_progress.level),
            delta_xp=vinculo_context.final_xp,
        )

    async def grant_level_rewards(self, member: discord.Member, new_level: int) -> list[discord.Role]:
        config = await self.get_guild_config(member.guild.id)
        granted: list[discord.Role] = []
        for level, role_id in sorted(config.level_roles.items()):
            if new_level < level:
                continue
            role = member.guild.get_role(role_id)
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason=f"XP: Nível {level} Alcançado")
                    granted.append(role)
                except (discord.Forbidden, discord.HTTPException):
                    continue
        return granted

    async def get_rank_snapshot(self, guild: discord.Guild, user: discord.Member | discord.User) -> RankSnapshot:
        config = await self.get_guild_config(guild.id)
        profile = await self.repository.get_profile(guild.id, user.id)
        progress = build_progress_snapshot(profile.total_xp, config.difficulty)
        position = await self.repository.get_rank_position(guild.id, user.id, profile.total_xp)
        return RankSnapshot(
            guild_id=guild.id,
            user_id=user.id,
            display_name=getattr(user, "display_name", getattr(user, "name", str(user.id))),
            total_xp=profile.total_xp,
            level=progress.level,
            xp_into_level=progress.xp_into_level,
            xp_for_next_level=progress.xp_for_next_level,
            remaining_to_next=progress.remaining_to_next,
            progress_ratio=progress.progress_ratio,
            position=position,
        )

    async def get_leaderboard_entries(self, guild: discord.Guild, limit: int) -> list[LeaderboardEntry]:
        config = await self.get_guild_config(guild.id)
        profiles = await self.repository.get_top_profiles(guild.id, limit)
        entries: list[LeaderboardEntry] = []
        for index, profile in enumerate(profiles, start=1):
            progress = build_progress_snapshot(profile.total_xp, config.difficulty)
            member = guild.get_member(profile.user_id)
            display_name = profile.last_known_name or (member.display_name if member else f"Alma {profile.user_id}")
            entries.append(
                LeaderboardEntry(
                    position=index,
                    user_id=profile.user_id,
                    display_name=display_name,
                    total_xp=profile.total_xp,
                    level=progress.level,
                    remaining_to_next=progress.remaining_to_next,
                    progress_ratio=progress.progress_ratio,
                )
            )
        return entries

    async def get_leaderboard_page(self, guild: discord.Guild, page: int, page_size: int = 10) -> PageResult:
        page = max(0, page)
        offset = page * page_size
        total_entries = await self.repository.count_ranked_profiles(guild.id)
        config = await self.get_guild_config(guild.id)
        profiles = await self.repository.get_profiles_page(guild.id, offset, page_size)
        entries: list[LeaderboardEntry] = []
        for index, profile in enumerate(profiles, start=offset + 1):
            progress = build_progress_snapshot(profile.total_xp, config.difficulty)
            entries.append(
                LeaderboardEntry(
                    position=index,
                    user_id=profile.user_id,
                    display_name=profile.last_known_name or f"Alma {profile.user_id}",
                    total_xp=profile.total_xp,
                    level=progress.level,
                    remaining_to_next=progress.remaining_to_next,
                    progress_ratio=progress.progress_ratio,
                )
            )
        return PageResult(entries=entries, total_entries=total_entries, page=page, page_size=page_size)

    async def give_xp(self, guild: discord.Guild, member: discord.Member, amount: int, actor_user_id: int | None, reason: str | None) -> XpChangeResult:
        config = await self.get_guild_config(guild.id)
        old_total, new_total = await self.repository.adjust_xp(
            guild_id=guild.id,
            user_id=member.id,
            delta_xp=abs(amount),
            last_known_name=member.display_name,
            actor_user_id=actor_user_id,
            reason=reason,
        )
        old_progress = build_progress_snapshot(old_total, config.difficulty)
        new_progress = build_progress_snapshot(new_total, config.difficulty)
        return XpChangeResult(True, None, old_total, new_total, old_progress.level, new_progress.level, max(0, new_progress.level - old_progress.level), abs(amount))

    async def reset_guild_xp(self, guild: discord.Guild, actor_user_id: int) -> int:
        """Apaga os perfis de XP de toda a guild."""
        deleted_count = await self.repository.reset_guild_xp(guild.id, actor_user_id)
        self.logger.info(f"Guild {guild.id} teve o XP zerado por {actor_user_id}. {deleted_count} perfis apagados.")
        return deleted_count

    async def reset_xp(self, guild: discord.Guild, member: discord.Member, actor_user_id: int | None, reason: str | None) -> XpChangeResult:
        config = await self.get_guild_config(guild.id)
        old_total, new_total = await self.repository.reset_profile(guild.id, member.id, actor_user_id, reason)
        old_progress = build_progress_snapshot(old_total, config.difficulty)
        new_progress = build_progress_snapshot(new_total, config.difficulty)
        return XpChangeResult(True, None, old_total, new_total, old_progress.level, new_progress.level, new_progress.level - old_progress.level, -old_total)
