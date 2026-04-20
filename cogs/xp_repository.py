from __future__ import annotations

import asyncio
from typing import Any

import aiosqlite

from .xp_config import build_default_guild_config, parse_iso, utc_now_iso
from .xp_migrations import run_migrations
from .xp_models import GuildXpConfig, UserXpProfile, XpDifficulty


class XpRepository:
    """camada única de persistência do sistema de xp."""

    CONFIG_FIELDS = {
        "difficulty",
        "cooldown_seconds",
        "min_xp_per_message",
        "max_xp_per_message",
        "min_message_length",
        "min_unique_words",
        "anti_repeat_window_seconds",
        "anti_repeat_similarity",
        "ignore_bots",
        "ignore_webhooks",
    }

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._tx_lock = asyncio.Lock()

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("xp repository ainda não foi conectado")
        return self._conn

    async def connect(self) -> None:
        if self._conn is not None:
            return

        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA synchronous = FULL")
        await self._conn.execute("PRAGMA busy_timeout = 5000")
        await run_migrations(self._conn)

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def ensure_guild_config(self, guild_id: int) -> None:
        config = build_default_guild_config(guild_id)
        await self.connection.execute(
            """
            INSERT OR IGNORE INTO xp_guild_config(
                guild_id,
                difficulty,
                cooldown_seconds,
                min_xp_per_message,
                max_xp_per_message,
                min_message_length,
                min_unique_words,
                anti_repeat_window_seconds,
                anti_repeat_similarity,
                ignore_bots,
                ignore_webhooks,
                created_at,
                updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                int(config.difficulty),
                config.cooldown_seconds,
                config.min_xp_per_message,
                config.max_xp_per_message,
                config.min_message_length,
                config.min_unique_words,
                config.anti_repeat_window_seconds,
                config.anti_repeat_similarity,
                int(config.ignore_bots),
                int(config.ignore_webhooks),
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        await self.connection.commit()

    async def get_guild_config(self, guild_id: int) -> GuildXpConfig:
        await self.ensure_guild_config(guild_id)
        row = await self.connection.execute_fetchall(
            "SELECT * FROM xp_guild_config WHERE guild_id = ?",
            (guild_id,),
        )
        config_row = row[0]
        ignored_channels = await self.connection.execute_fetchall(
            "SELECT channel_id FROM xp_ignored_channels WHERE guild_id = ?",
            (guild_id,),
        )
        ignored_categories = await self.connection.execute_fetchall(
            "SELECT category_id FROM xp_ignored_categories WHERE guild_id = ?",
            (guild_id,),
        )
        ignored_roles = await self.connection.execute_fetchall(
            "SELECT role_id FROM xp_ignored_roles WHERE guild_id = ?",
            (guild_id,),
        )

        return GuildXpConfig(
            guild_id=guild_id,
            difficulty=XpDifficulty(int(config_row["difficulty"])),
            cooldown_seconds=int(config_row["cooldown_seconds"]),
            min_xp_per_message=int(config_row["min_xp_per_message"]),
            max_xp_per_message=int(config_row["max_xp_per_message"]),
            min_message_length=int(config_row["min_message_length"]),
            min_unique_words=int(config_row["min_unique_words"]),
            anti_repeat_window_seconds=int(config_row["anti_repeat_window_seconds"]),
            anti_repeat_similarity=float(config_row["anti_repeat_similarity"]),
            ignore_bots=bool(config_row["ignore_bots"]),
            ignore_webhooks=bool(config_row["ignore_webhooks"]),
            ignored_channel_ids={int(row[0]) for row in ignored_channels},
            ignored_category_ids={int(row[0]) for row in ignored_categories},
            ignored_role_ids={int(row[0]) for row in ignored_roles},
        )

    async def update_guild_config(self, guild_id: int, **fields: Any) -> None:
        invalid = set(fields) - self.CONFIG_FIELDS
        if invalid:
            raise ValueError(f"campos de config inválidos: {sorted(invalid)}")
        if not fields:
            return

        await self.ensure_guild_config(guild_id)
        payload = dict(fields)
        if "difficulty" in payload and isinstance(payload["difficulty"], XpDifficulty):
            payload["difficulty"] = int(payload["difficulty"])
        for boolean_field in ("ignore_bots", "ignore_webhooks"):
            if boolean_field in payload:
                payload[boolean_field] = int(bool(payload[boolean_field]))

        assignments = ", ".join(f"{key} = ?" for key in payload)
        values = list(payload.values()) + [utc_now_iso(), guild_id]
        await self.connection.execute(
            f"UPDATE xp_guild_config SET {assignments}, updated_at = ? WHERE guild_id = ?",
            values,
        )
        await self.connection.commit()

    async def set_ignored_target(self, table: str, column: str, guild_id: int, target_id: int, enabled: bool) -> None:
        if table not in {"xp_ignored_channels", "xp_ignored_categories", "xp_ignored_roles"}:
            raise ValueError("tabela de ignore inválida")
        if column not in {"channel_id", "category_id", "role_id"}:
            raise ValueError("coluna de ignore inválida")

        if enabled:
            await self.connection.execute(
                f"INSERT OR IGNORE INTO {table}(guild_id, {column}) VALUES(?, ?)",
                (guild_id, target_id),
            )
        else:
            await self.connection.execute(
                f"DELETE FROM {table} WHERE guild_id = ? AND {column} = ?",
                (guild_id, target_id),
            )
        await self.connection.commit()

    async def get_profile(self, guild_id: int, user_id: int) -> UserXpProfile:
        rows = await self.connection.execute_fetchall(
            "SELECT * FROM xp_profiles WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        if not rows:
            return UserXpProfile(guild_id=guild_id, user_id=user_id)
        row = rows[0]
        return UserXpProfile(
            guild_id=int(row["guild_id"]),
            user_id=int(row["user_id"]),
            total_xp=int(row["total_xp"]),
            message_count=int(row["message_count"]),
            last_awarded_at=row["last_awarded_at"],
            last_message_hash=row["last_message_hash"],
            last_message_at=row["last_message_at"],
            last_known_name=row["last_known_name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def try_add_message_xp(
        self,
        *,
        guild_id: int,
        user_id: int,
        delta_xp: int,
        last_known_name: str,
        awarded_at_iso: str,
        cooldown_cutoff_iso: str,
        message_hash: str,
        repeat_cutoff_iso: str,
    ) -> tuple[bool, str | None, int, int]:
        async with self._tx_lock:
            conn = self.connection
            await conn.execute("BEGIN IMMEDIATE")
            try:
                profile = await self.get_profile(guild_id, user_id)
                old_total = profile.total_xp

                last_awarded_at = parse_iso(profile.last_awarded_at)
                if last_awarded_at and last_awarded_at > parse_iso(cooldown_cutoff_iso):
                    await conn.rollback()
                    return False, "cooldown", old_total, old_total

                last_message_at = parse_iso(profile.last_message_at)
                if (
                    profile.last_message_hash
                    and profile.last_message_hash == message_hash
                    and last_message_at
                    and last_message_at > parse_iso(repeat_cutoff_iso)
                ):
                    await conn.rollback()
                    return False, "repeat_db", old_total, old_total

                await conn.execute(
                    """
                    INSERT INTO xp_profiles(
                        guild_id,
                        user_id,
                        total_xp,
                        message_count,
                        last_awarded_at,
                        last_message_hash,
                        last_message_at,
                        last_known_name,
                        created_at,
                        updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        total_xp = MAX(0, xp_profiles.total_xp + excluded.total_xp),
                        message_count = xp_profiles.message_count + excluded.message_count,
                        last_awarded_at = excluded.last_awarded_at,
                        last_message_hash = excluded.last_message_hash,
                        last_message_at = excluded.last_message_at,
                        last_known_name = excluded.last_known_name,
                        updated_at = excluded.updated_at
                    """,
                    (
                        guild_id,
                        user_id,
                        delta_xp,
                        1,
                        awarded_at_iso,
                        message_hash,
                        awarded_at_iso,
                        last_known_name,
                        awarded_at_iso,
                        awarded_at_iso,
                    ),
                )

                updated = await self.get_profile(guild_id, user_id)
                await conn.commit()
                return True, None, old_total, updated.total_xp
            except Exception:
                await conn.rollback()
                raise

    async def adjust_xp(
        self,
        *,
        guild_id: int,
        user_id: int,
        delta_xp: int,
        last_known_name: str,
        actor_user_id: int | None,
        reason: str | None,
    ) -> tuple[int, int]:
        now = utc_now_iso()

        async with self._tx_lock:
            conn = self.connection
            await conn.execute("BEGIN IMMEDIATE")
            try:
                profile = await self.get_profile(guild_id, user_id)
                old_total = profile.total_xp
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO xp_profiles(
                        guild_id,
                        user_id,
                        total_xp,
                        message_count,
                        last_known_name,
                        created_at,
                        updated_at
                    ) VALUES(?, ?, 0, 0, ?, ?, ?)
                    """,
                    (guild_id, user_id, last_known_name, now, now),
                )
                await conn.execute(
                    """
                    UPDATE xp_profiles
                    SET total_xp = MAX(0, total_xp + ?),
                        last_known_name = ?,
                        updated_at = ?
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (delta_xp, last_known_name, now, guild_id, user_id),
                )
                await conn.execute(
                    """
                    INSERT INTO xp_adjustments(guild_id, target_user_id, actor_user_id, delta_xp, reason, created_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, user_id, actor_user_id, delta_xp, reason, now),
                )
                updated = await self.get_profile(guild_id, user_id)
                await conn.commit()
                return old_total, updated.total_xp
            except Exception:
                await conn.rollback()
                raise

    async def reset_profile(self, guild_id: int, user_id: int, actor_user_id: int | None, reason: str | None) -> tuple[int, int]:
        now = utc_now_iso()
        async with self._tx_lock:
            conn = self.connection
            await conn.execute("BEGIN IMMEDIATE")
            try:
                profile = await self.get_profile(guild_id, user_id)
                old_total = profile.total_xp
                await conn.execute(
                    """
                    INSERT INTO xp_profiles(
                        guild_id,
                        user_id,
                        total_xp,
                        message_count,
                        created_at,
                        updated_at
                    ) VALUES(?, ?, 0, 0, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        total_xp = 0,
                        message_count = 0,
                        last_awarded_at = NULL,
                        last_message_hash = NULL,
                        last_message_at = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (guild_id, user_id, now, now),
                )
                await conn.execute(
                    """
                    INSERT INTO xp_adjustments(guild_id, target_user_id, actor_user_id, delta_xp, reason, created_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, user_id, actor_user_id, -old_total, reason or "reset", now),
                )
                await conn.commit()
                return old_total, 0
            except Exception:
                await conn.rollback()
                raise

    async def count_ranked_profiles(self, guild_id: int) -> int:
        rows = await self.connection.execute_fetchall(
            "SELECT COUNT(*) AS c FROM xp_profiles WHERE guild_id = ? AND total_xp > 0",
            (guild_id,),
        )
        return int(rows[0]["c"])

    async def get_rank_position(self, guild_id: int, user_id: int, total_xp: int) -> int | None:
        if total_xp <= 0:
            return None
        rows = await self.connection.execute_fetchall(
            """
            SELECT COUNT(*) AS ahead
            FROM xp_profiles
            WHERE guild_id = ?
              AND total_xp > 0
              AND (
                total_xp > ?
                OR (total_xp = ? AND user_id < ?)
              )
            """,
            (guild_id, total_xp, total_xp, user_id),
        )
        return int(rows[0]["ahead"]) + 1

    async def get_top_profiles(self, guild_id: int, limit: int) -> list[UserXpProfile]:
        rows = await self.connection.execute_fetchall(
            """
            SELECT *
            FROM xp_profiles
            WHERE guild_id = ? AND total_xp > 0
            ORDER BY total_xp DESC, user_id ASC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return [
            UserXpProfile(
                guild_id=int(row["guild_id"]),
                user_id=int(row["user_id"]),
                total_xp=int(row["total_xp"]),
                message_count=int(row["message_count"]),
                last_awarded_at=row["last_awarded_at"],
                last_message_hash=row["last_message_hash"],
                last_message_at=row["last_message_at"],
                last_known_name=row["last_known_name"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    async def get_profiles_page(self, guild_id: int, offset: int, limit: int) -> list[UserXpProfile]:
        rows = await self.connection.execute_fetchall(
            """
            SELECT *
            FROM xp_profiles
            WHERE guild_id = ? AND total_xp > 0
            ORDER BY total_xp DESC, user_id ASC
            LIMIT ? OFFSET ?
            """,
            (guild_id, limit, offset),
        )
        return [
            UserXpProfile(
                guild_id=int(row["guild_id"]),
                user_id=int(row["user_id"]),
                total_xp=int(row["total_xp"]),
                message_count=int(row["message_count"]),
                last_awarded_at=row["last_awarded_at"],
                last_message_hash=row["last_message_hash"],
                last_message_at=row["last_message_at"],
                last_known_name=row["last_known_name"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
