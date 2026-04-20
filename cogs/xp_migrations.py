from __future__ import annotations

from collections.abc import Callable

import aiosqlite

from .xp_config import utc_now_iso

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS xp_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS xp_guild_config (
    guild_id INTEGER PRIMARY KEY,
    difficulty INTEGER NOT NULL DEFAULT 3,
    cooldown_seconds INTEGER NOT NULL DEFAULT 60,
    min_xp_per_message INTEGER NOT NULL DEFAULT 15,
    max_xp_per_message INTEGER NOT NULL DEFAULT 25,
    min_message_length INTEGER NOT NULL DEFAULT 8,
    min_unique_words INTEGER NOT NULL DEFAULT 2,
    anti_repeat_window_seconds INTEGER NOT NULL DEFAULT 180,
    anti_repeat_similarity REAL NOT NULL DEFAULT 0.92,
    ignore_bots INTEGER NOT NULL DEFAULT 1,
    ignore_webhooks INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS xp_profiles (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    total_xp INTEGER NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0,
    last_awarded_at TEXT NULL,
    last_message_hash TEXT NULL,
    last_message_at TEXT NULL,
    last_known_name TEXT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS xp_ignored_channels (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS xp_ignored_categories (
    guild_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, category_id)
);

CREATE TABLE IF NOT EXISTS xp_ignored_roles (
    guild_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);

CREATE TABLE IF NOT EXISTS xp_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    target_user_id INTEGER NOT NULL,
    actor_user_id INTEGER NULL,
    delta_xp INTEGER NOT NULL,
    reason TEXT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_xp_profiles_guild_total_xp
    ON xp_profiles (guild_id, total_xp DESC, user_id ASC);

CREATE INDEX IF NOT EXISTS idx_xp_profiles_guild_updated_at
    ON xp_profiles (guild_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_xp_adjustments_guild_target_created
    ON xp_adjustments (guild_id, target_user_id, created_at DESC);
"""


async def _column_exists(conn: aiosqlite.Connection, table: str, column: str) -> bool:
    rows = await conn.execute_fetchall(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in rows)


async def _ensure_column(conn: aiosqlite.Connection, table: str, definition: str, column: str) -> None:
    if not await _column_exists(conn, table, column):
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


async def run_migrations(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA_SQL)

    await _ensure_column(conn, "xp_profiles", "last_message_hash TEXT NULL", "last_message_hash")
    await _ensure_column(conn, "xp_profiles", "last_message_at TEXT NULL", "last_message_at")
    await _ensure_column(conn, "xp_profiles", "last_known_name TEXT NULL", "last_known_name")

    applied_rows = await conn.execute_fetchall("SELECT version FROM xp_schema_migrations")
    applied = {int(row[0]) for row in applied_rows}
    if SCHEMA_VERSION not in applied:
        await conn.execute(
            "INSERT INTO xp_schema_migrations(version, applied_at) VALUES(?, ?)",
            (SCHEMA_VERSION, utc_now_iso()),
        )

    await conn.commit()


async def import_legacy_table(
    conn: aiosqlite.Connection,
    *,
    legacy_table: str,
    guild_column: str = "guild_id",
    user_column: str = "user_id",
    total_xp_column: str | None = None,
    level_column: str | None = None,
    xp_in_level_column: str | None = None,
    name_column: str | None = None,
    legacy_total_xp_builder: Callable[[int, int], int] | None = None,
) -> int:
    """
    importa dados de um schema antigo para `xp_profiles`.

    regras:
    - se `total_xp_column` existir, ele é usado como fonte da verdade;
    - se não existir, é obrigatório informar `level_column`, `xp_in_level_column`
      e `legacy_total_xp_builder(level, xp_in_level)`.
    """
    rows = await conn.execute_fetchall(f"SELECT * FROM {legacy_table}")
    if not rows:
        return 0

    imported = 0
    for row in rows:
        mapping = dict(row)
        guild_id = int(mapping[guild_column])
        user_id = int(mapping[user_column])

        if total_xp_column:
            total_xp = max(0, int(mapping[total_xp_column]))
        else:
            if not (level_column and xp_in_level_column and legacy_total_xp_builder):
                raise ValueError("não há informação suficiente para reconstruir o xp total")
            total_xp = max(
                0,
                int(
                    legacy_total_xp_builder(
                        int(mapping[level_column]),
                        int(mapping[xp_in_level_column]),
                    )
                ),
            )

        last_known_name = str(mapping[name_column]) if name_column and mapping.get(name_column) else None
        now = utc_now_iso()

        await conn.execute(
            """
            INSERT INTO xp_profiles(
                guild_id,
                user_id,
                total_xp,
                message_count,
                last_known_name,
                created_at,
                updated_at
            ) VALUES(?, ?, ?, 0, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                total_xp = MAX(xp_profiles.total_xp, excluded.total_xp),
                last_known_name = COALESCE(excluded.last_known_name, xp_profiles.last_known_name),
                updated_at = excluded.updated_at
            """,
            (guild_id, user_id, total_xp, last_known_name, now, now),
        )
        imported += 1

    await conn.commit()
    return imported
