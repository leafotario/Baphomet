from __future__ import annotations

import aiosqlite


SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS profile_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    onboarding_completed INTEGER NOT NULL DEFAULT 0,
    render_revision INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS profile_fields (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    field_key TEXT NOT NULL,
    value TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    source_type TEXT NOT NULL DEFAULT 'user',
    source_message_ids TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    updated_by INTEGER NULL,
    moderated_by INTEGER NULL,
    moderated_at TEXT NULL,
    moderation_reason TEXT NULL,
    PRIMARY KEY (guild_id, user_id, field_key),
    FOREIGN KEY (guild_id, user_id)
        REFERENCES profiles(guild_id, user_id)
        ON DELETE CASCADE,
    CHECK (status IN ('active', 'hidden', 'rejected', 'removed_by_mod')),
    CHECK (source_type IN ('user', 'auto_sync', 'moderation'))
);

CREATE TABLE IF NOT EXISTS guild_profile_settings (
    guild_id INTEGER PRIMARY KEY,
    presentation_channel_id INTEGER NULL,
    presentation_mode TEXT NOT NULL DEFAULT 'manual',
    auto_sync_enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (presentation_mode IN ('manual', 'auto_post', 'disabled'))
);

CREATE TABLE IF NOT EXISTS profile_moderation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    field_key TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_id INTEGER NOT NULL,
    reason TEXT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_profile_fields_status
    ON profile_fields(guild_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_profile_moderation_events_target
    ON profile_moderation_events(guild_id, user_id, created_at DESC);
"""


async def run_profile_migrations(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA_SQL)
    await _ensure_removed_by_mod_status(conn)
    await conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_profile_fields_status
            ON profile_fields(guild_id, status, updated_at DESC);

        CREATE INDEX IF NOT EXISTS idx_profile_moderation_events_target
            ON profile_moderation_events(guild_id, user_id, created_at DESC);
        """
    )
    applied_rows = await conn.execute_fetchall("SELECT version FROM profile_schema_migrations")
    applied = {int(row[0]) for row in applied_rows}
    if SCHEMA_VERSION not in applied:
        await conn.execute(
            "INSERT INTO profile_schema_migrations(version, applied_at) VALUES(?, CURRENT_TIMESTAMP)",
            (SCHEMA_VERSION,),
        )
    await conn.commit()


async def _ensure_removed_by_mod_status(conn: aiosqlite.Connection) -> None:
    rows = await conn.execute_fetchall(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'profile_fields'"
    )
    if not rows:
        return
    table_sql = str(rows[0][0] or "")
    if "removed_by_mod" in table_sql:
        return

    await conn.executescript(
        """
        CREATE TABLE profile_fields_new (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            field_key TEXT NOT NULL,
            value TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            source_type TEXT NOT NULL DEFAULT 'user',
            source_message_ids TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL,
            updated_by INTEGER NULL,
            moderated_by INTEGER NULL,
            moderated_at TEXT NULL,
            moderation_reason TEXT NULL,
            PRIMARY KEY (guild_id, user_id, field_key),
            FOREIGN KEY (guild_id, user_id)
                REFERENCES profiles(guild_id, user_id)
                ON DELETE CASCADE,
            CHECK (status IN ('active', 'hidden', 'rejected', 'removed_by_mod')),
            CHECK (source_type IN ('user', 'auto_sync', 'moderation'))
        );

        INSERT INTO profile_fields_new(
            guild_id,
            user_id,
            field_key,
            value,
            status,
            source_type,
            source_message_ids,
            updated_at,
            updated_by,
            moderated_by,
            moderated_at,
            moderation_reason
        )
        SELECT
            guild_id,
            user_id,
            field_key,
            value,
            status,
            source_type,
            source_message_ids,
            updated_at,
            updated_by,
            moderated_by,
            moderated_at,
            moderation_reason
        FROM profile_fields;

        DROP TABLE profile_fields;
        ALTER TABLE profile_fields_new RENAME TO profile_fields;
        """
    )
