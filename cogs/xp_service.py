from __future__ import annotations

"""Regra De Negócio Do Sistema De XP Do Baphomet."""

import asyncio
import difflib
import hashlib
import logging
import random
import secrets
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord

from .xp_config import build_progress_snapshot, normalize_message_content, utc_now
from .xp_models import GuildXpConfig, LeaderboardEntry, PageResult, RankSnapshot, XpChangeResult
from .xp_repository import XpRepository
from cogs.vinculos import ContractType
from core_logger import log_exception



class XpService:
    def __init__(self, repository: XpRepository, *, rng: random.Random | None = None, logger: logging.Logger | None = None) -> None:
        self.repository = repository
        self.rng = rng or secrets.SystemRandom()
        self.logger = logger or logging.getLogger("baphomet.xp")
        self._config_cache: dict[int, GuildXpConfig] = {}
        self._user_locks: dict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._recent_fingerprints: dict[tuple[int, int], deque[tuple[str, float]]] = defaultdict(lambda: deque(maxlen=5))
        self._fusion_resonance_cache: dict[int, tuple[int, float, float]] = {}  # vinculo_id -> (last_sender_id, timestamp_utc, multiplier_stack)

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

    async def _resolve_vinculo_context(self, user_id: int, message: discord.Message) -> float:
        """Processa o contexto de vínculos do usuário para a mensagem atual, calculando bônus de Fusão de Almas."""
        if message.guild is None:
            return 0.0

        vinculos_runtime = getattr(message.guild._state._get_client(), "vinculos_runtime", None)
        if vinculos_runtime is None:
            return 0.0

        vinculos = await vinculos_runtime.repository.list_active_vinculos_for_user(message.guild.id, user_id)
        if not vinculos:
            return 0.0

        multiplier = 0.0
        now_ts = datetime.now(timezone.utc).timestamp()

        expired_keys = [k for k, v in self._fusion_resonance_cache.items() if now_ts - v[1] > 60]
        for k in expired_keys:
            del self._fusion_resonance_cache[k]

        is_eclipse_active = False
        try:
            settings = await vinculos_runtime.repository.get_guild_settings(message.guild.id)
            if settings.eclipse_ends_at:
                eclipse_end = datetime.fromisoformat(settings.eclipse_ends_at)
                if datetime.now(timezone.utc) < eclipse_end:
                    is_eclipse_active = True
        except Exception as exc:
            log_exception(exc)
            pass

        for vinculo in vinculos:
            partner_id = vinculo.partner_id_for(user_id)
            if not partner_id:
                continue

            snapshot = await vinculos_runtime.repository.get_status_snapshot(message.guild.id, user_id, partner_id)
            if snapshot is None:
                continue
            
            base_bonus = snapshot.affinity.bonus
            if is_eclipse_active:
                base_bonus *= 2.0

            if vinculo.is_fused and snapshot.resonance.active:
                cached = self._fusion_resonance_cache.get(vinculo.id)
                if cached:
                    c_partner_id, c_timestamp, c_stack = cached
                    if c_partner_id == partner_id and (now_ts - c_timestamp) <= 60:
                        new_stack = c_stack + 0.05
                        multiplier += base_bonus + new_stack
                        self._fusion_resonance_cache[vinculo.id] = (user_id, now_ts, new_stack)
                        continue
                
                self._fusion_resonance_cache[vinculo.id] = (user_id, now_ts, 0.0)
                multiplier += base_bonus
            else:
                multiplier += base_bonus
                
        return multiplier

    async def _dispatch_contract_evaluation(
        self,
        vinculos_runtime,
        vinculo_id: int,
        guild_id: int,
        user_id: int,
        channel_id: int,
        xp_result: XpChangeResult,
        contract_type: ContractType,
        amount: int = 1,
    ) -> None:
        """Avaliador de Contratos em Segundo Plano."""
        try:
            db = vinculos_runtime.repository.connection
            
            # A) Recupera contratos ativos
            rows = await db.execute_fetchall(
                "SELECT id, target_value, current_value, expires_at FROM vinculo_contracts WHERE vinculo_id = ? AND status = 'active' AND contract_type = ?",
                (vinculo_id, contract_type.value)
            )
            if not rows:
                return

            for row in rows:
                contract_id = int(row["id"])
                target_value = int(row["target_value"])
                current_value = int(row["current_value"])
                expires_at_str = str(row["expires_at"])

                # B) Procedimento de Tempo
                expires_at = datetime.fromisoformat(expires_at_str)
                if datetime.now(timezone.utc) > expires_at:
                    await db.execute("UPDATE vinculo_contracts SET status = 'expired' WHERE id = ?", (contract_id,))
                    await db.commit()
                    continue

                # C) Atualização de Métrica
                updated_current_value = current_value + amount
                await db.execute("UPDATE vinculo_contracts SET current_value = ? WHERE id = ?", (updated_current_value, contract_id))
                await db.commit()

                # D) Porta Lógica de Cumprimento
                if updated_current_value >= target_value:
                    # E) Fase de Recompensa Atómica
                    async with vinculos_runtime.repository._tx_lock:
                        # Re-verifica estado para segurança transacional
                        check_row = await db.execute_fetchall("SELECT status FROM vinculo_contracts WHERE id = ?", (contract_id,))
                        if not check_row or check_row[0]["status"] != "active":
                            continue

                        await db.execute("UPDATE vinculo_contracts SET status = 'completed' WHERE id = ?", (contract_id,))
                        
                        # Extrai dados adicionais para o histórico e recompensa (XP de bónus massivo)
                        bonus_xp_reward = target_value * 50
                        now_str = datetime.now(timezone.utc).isoformat()
                        
                        row_vinc = await db.execute_fetchall("SELECT user_low_id, user_high_id FROM vinculos WHERE id = ?", (vinculo_id,))
                        partner_id = None
                        if row_vinc:
                            u1 = int(row_vinc[0]["user_low_id"])
                            u2 = int(row_vinc[0]["user_high_id"])
                            partner_id = u2 if user_id == u1 else (u1 if user_id == u2 else None)

                        await db.execute(
                            """
                            INSERT INTO vinculo_xp_bonus_history (
                                guild_id, vinculo_id, user_id, partner_id, bond_type,
                                base_xp, bonus_xp, multiplier, affinity_level, resonance_active, penalty_delta, created_at
                            ) VALUES (?, ?, ?, ?, 'pacto_sangue', 0, ?, 1.0, 1, 1, 0.0, ?)
                            """,
                            (guild_id, vinculo_id, user_id, partner_id, bonus_xp_reward, now_str)
                        )
                        # Adiciona fisicamente o XP recompensado ao total
                        await self.repository.try_add_message_xp(
                            guild_id=guild_id,
                            user_id=user_id,
                            delta_xp=bonus_xp_reward,
                            last_known_name="Recompensa de Contrato",
                            awarded_at_iso=now_str,
                            cooldown_cutoff_iso=now_str,
                            message_hash=f"contract_reward_{contract_id}",
                            repeat_cutoff_iso=now_str,
                        )
                        await db.commit()
                        
                        # Localiza canal e notifica vitória retumbante
                        client = getattr(self.repository, "_bot", None) or getattr(vinculos_runtime, "bot", None)
                        if client:
                            channel = client.get_channel(channel_id)
                            if isinstance(channel, discord.TextChannel):
                                embed = discord.Embed(
                                    title="📜 O Contrato Foi Selado",
                                    description=(
                                        f"As entidades sombrias testemunharam a conclusão imaculada do contrato **{contract_type.value}**.\n\n"
                                        f"<@{user_id}> {f'e <@{partner_id}>' if partner_id else ''} provaram a resiliência do vosso vínculo e superaram o desafio de **{target_value}** interações.\n"
                                        f"O Submundo recompensa a vossa dedicação com **+{bonus_xp_reward} XP** concedido ao avatar que cravou o limite final!"
                                    ),
                                    color=discord.Color.from_str("#4A0404")
                                )
                                await channel.send(embed=embed)
        except Exception as e:
            log_exception(e)
            self.logger.error("Falha colossal na Avaliação de Contratos em Background: %s", e, exc_info=True)

    async def process_message(self, message: discord.Message) -> XpChangeResult | None:
        if message.guild is None or not isinstance(message.author, discord.Member):
            return None
        config = await self.get_guild_config(message.guild.id)
        allowed, _, normalized = self._basic_message_checks(message, config)
        if not allowed or normalized is None:
            return None

        delta_xp = self.rng.randint(config.min_xp_per_message, config.max_xp_per_message)
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
            
            vinculo_multiplier = await self._resolve_vinculo_context(message.author.id, message)
            final_delta_xp = int(delta_xp * (1.0 + vinculo_multiplier))

            awarded, _, old_total, new_total = await self.repository.try_add_message_xp(
                guild_id=message.guild.id,
                user_id=message.author.id,
                delta_xp=final_delta_xp,
                last_known_name=message.author.display_name,
                awarded_at_iso=now_iso,
                cooldown_cutoff_iso=cooldown_cutoff_iso,
                message_hash=fingerprint,
                repeat_cutoff_iso=repeat_cutoff_iso,
            )
            if not awarded:
                return None
            self._remember_fingerprint(message.guild.id, message.author.id, normalized, now.timestamp())

        old_progress = build_progress_snapshot(old_total, config.difficulty)
        new_progress = build_progress_snapshot(new_total, config.difficulty)
        result = XpChangeResult(
            awarded=True,
            reason=None,
            old_total_xp=old_total,
            new_total_xp=new_total,
            old_level=old_progress.level,
            new_level=new_progress.level,
            levels_gained=max(0, new_progress.level - old_progress.level),
            delta_xp=final_delta_xp,
        )

        vinculos_runtime = getattr(message.guild._state._get_client(), "vinculos_runtime", None)
        if vinculos_runtime:
            vinculos = await vinculos_runtime.repository.list_active_vinculos_for_user(message.guild.id, message.author.id)
            for vinculo in vinculos:
                asyncio.create_task(
                    self._dispatch_contract_evaluation(
                        vinculos_runtime,
                        vinculo.id,
                        message.guild.id,
                        message.author.id,
                        message.channel.id,
                        result,
                        ContractType.MESSAGES_SHARED
                    )
                )

        return result

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

    async def remove_xp(self, guild: discord.Guild, member: discord.Member, amount: int, actor_user_id: int | None, reason: str | None) -> XpChangeResult:
        config = await self.get_guild_config(guild.id)
        old_total, new_total = await self.repository.adjust_xp(
            guild_id=guild.id,
            user_id=member.id,
            delta_xp=-abs(amount),
            last_known_name=member.display_name,
            actor_user_id=actor_user_id,
            reason=reason,
        )
        old_progress = build_progress_snapshot(old_total, config.difficulty)
        new_progress = build_progress_snapshot(new_total, config.difficulty)
        return XpChangeResult(True, None, old_total, new_total, old_progress.level, new_progress.level, max(0, new_progress.level - old_progress.level), -abs(amount))

    async def reset_xp(self, guild: discord.Guild, member: discord.Member, actor_user_id: int | None, reason: str | None) -> XpChangeResult:
        config = await self.get_guild_config(guild.id)
        old_total, new_total = await self.repository.reset_profile(guild.id, member.id, actor_user_id, reason)
        old_progress = build_progress_snapshot(old_total, config.difficulty)
        new_progress = build_progress_snapshot(new_total, config.difficulty)
        return XpChangeResult(True, None, old_total, new_total, old_progress.level, new_progress.level, new_progress.level - old_progress.level, -old_total)