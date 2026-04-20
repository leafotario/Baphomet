from __future__ import annotations

import json
import pathlib

import aiosqlite

from .xp_config import normalize_difficulty, utc_now_iso

SCHEMA_VERSION = 2
DATA_DIR = pathlib.Path("data")
LEGACY_XP_JSON = DATA_DIR / "xp_data.json"
LEGACY_CONFIG_JSON = DATA_DIR / "xp_config.json"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS xp_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS xp_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS xp_guild_config (
    guild_id INTEGER PRIMARY KEY,
    difficulty TEXT NOT NULL DEFAULT 'normal',
    cooldown_seconds INTEGER NOT NULL DEFAULT 60,
    min_xp_per_message INTEGER NOT NULL DEFAULT 15,
    max_xp_per_message INTEGER NOT NULL DEFAULT 25,
    min_message_length INTEGER NOT NULL DEFAULT 8,
    min_unique_words INTEGER NOT NULL DEFAULT 2,
    anti_repeat_window_seconds INTEGER NOT NULL DEFAULT 180,
    anti_repeat_similarity REAL NOT NULL DEFAULT 0.92,
    ignore_bots INTEGER NOT NULL DEFAULT 1,
    ignore_webhooks INTEGER NOT NULL DEFAULT 1,
    levelup_channel_id INTEGER NULL,
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

CREATE TABLE IF NOT EXISTS xp_level_roles (
    guild_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, level)
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


async def _get_meta(conn: aiosqlite.Connection, key: str) -> str | None:
    rows = await conn.execute_fetchall("SELECT value FROM xp_meta WHERE key = ?", (key,))
    return str(rows[0][0]) if rows else None


async def _set_meta(conn: aiosqlite.Connection, key: str, value: str) -> None:
    await conn.execute(
        "INSERT INTO xp_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


async def _import_legacy_json(conn: aiosqlite.Connection) -> None:
    if await _get_meta(conn, "legacy_json_imported") == "1":
        return

    xp_payload: dict[str, dict] = {}
    config_payload: dict[str, dict] = {}

    if LEGACY_XP_JSON.exists():
        try:
            xp_payload = json.loads(LEGACY_XP_JSON.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            xp_payload = {}

    if LEGACY_CONFIG_JSON.exists():
        try:
            config_payload = json.loads(LEGACY_CONFIG_JSON.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            config_payload = {}

    now = utc_now_iso()

    for guild_id_str, cfg in config_payload.items():
        guild_id = int(guild_id_str)
        difficulty = normalize_difficulty(cfg.get("difficulty", "normal")).value
        await conn.execute(
            """
            INSERT INTO xp_guild_config(
                guild_id,
                difficulty,
                cooldown_seconds,
                min_xp_per_message,
                max_xp_per_message,
                levelup_channel_id,
                created_at,
                updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                difficulty = excluded.difficulty,
                cooldown_seconds = excluded.cooldown_seconds,
                min_xp_per_message = excluded.min_xp_per_message,
                max_xp_per_message = excluded.max_xp_per_message,
                levelup_channel_id = excluded.levelup_channel_id,
                updated_at = excluded.updated_at
            """,
            (
                guild_id,
                difficulty,
                int(cfg.get("cooldown", 60)),
                int(cfg.get("xp_min", 15)),
                int(cfg.get("xp_max", 25)),
                int(cfg["levelup_channel"]) if cfg.get("levelup_channel") else None,
                now,
                now,
            ),
        )

        for channel_id in cfg.get("blacklist", []) or []:
            await conn.execute(
                "INSERT OR IGNORE INTO xp_ignored_channels(guild_id, channel_id) VALUES(?, ?)",
                (guild_id, int(channel_id)),
            )

        for level_str, role_id in (cfg.get("level_roles") or {}).items():
            await conn.execute(
                "INSERT OR REPLACE INTO xp_level_roles(guild_id, level, role_id) VALUES(?, ?, ?)",
                (guild_id, int(level_str), int(role_id)),
            )

    for guild_id_str, guild_payload in xp_payload.items():
        guild_id = int(guild_id_str)
        for user_id_str, profile in guild_payload.items():
            user_id = int(user_id_str)
            total_xp = max(0, int((profile or {}).get("xp", 0)))
            await conn.execute(
                """
                INSERT INTO xp_profiles(
                    guild_id,
                    user_id,
                    total_xp,
                    message_count,
                    created_at,
                    updated_at
                ) VALUES(?, ?, ?, 0, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    total_xp = MAX(xp_profiles.total_xp, excluded.total_xp),
                    updated_at = excluded.updated_at
                """,
                (guild_id, user_id, total_xp, now, now),
            )

    await _set_meta(conn, "legacy_json_imported", "1")


async def run_migrations(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA_SQL)
    await _ensure_column(conn, "xp_guild_config", "levelup_channel_id INTEGER NULL", "levelup_channel_id")
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

    await _import_legacy_json(conn)
    await conn.commit()
