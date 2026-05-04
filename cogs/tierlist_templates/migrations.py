from __future__ import annotations

import aiosqlite

from .models import utc_now_iso


SCHEMA_VERSION = 1


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tierlist_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tierlist_assets (
    sha256 TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tierlist_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    creator_id INTEGER NOT NULL,
    guild_id INTEGER NULL,
    visibility TEXT NOT NULL DEFAULT 'guild',
    current_version_id INTEGER NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT NULL,
    CHECK (visibility IN ('private', 'guild', 'public'))
);

CREATE TABLE IF NOT EXISTS tierlist_template_draft_tiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    position INTEGER NOT NULL,
    color_hex TEXT NULL,
    FOREIGN KEY (template_id) REFERENCES tierlist_templates(id) ON DELETE CASCADE,
    UNIQUE (template_id, position),
    UNIQUE (template_id, name)
);

CREATE TABLE IF NOT EXISTS tierlist_template_draft_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL,
    name TEXT NULL,
    source_type TEXT NOT NULL,
    source_query TEXT NULL,
    asset_sha256 TEXT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (template_id) REFERENCES tierlist_templates(id) ON DELETE CASCADE,
    FOREIGN KEY (asset_sha256) REFERENCES tierlist_assets(sha256),
    UNIQUE (template_id, position)
);

CREATE TABLE IF NOT EXISTS tierlist_template_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL,
    version_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    published_by INTEGER NOT NULL,
    published_at TEXT NOT NULL,
    FOREIGN KEY (template_id) REFERENCES tierlist_templates(id) ON DELETE CASCADE,
    UNIQUE (template_id, version_number),
    CHECK (status IN ('published'))
);

CREATE TABLE IF NOT EXISTS tierlist_template_version_tiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    position INTEGER NOT NULL,
    color_hex TEXT NULL,
    FOREIGN KEY (version_id) REFERENCES tierlist_template_versions(id) ON DELETE CASCADE,
    UNIQUE (version_id, position),
    UNIQUE (version_id, name)
);

CREATE TABLE IF NOT EXISTS tierlist_template_version_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL,
    name TEXT NULL,
    source_type TEXT NOT NULL,
    source_query TEXT NULL,
    asset_sha256 TEXT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (version_id) REFERENCES tierlist_template_versions(id) ON DELETE CASCADE,
    FOREIGN KEY (asset_sha256) REFERENCES tierlist_assets(sha256),
    UNIQUE (version_id, position)
);

CREATE TABLE IF NOT EXISTS tierlist_template_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_version_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL,
    guild_id INTEGER NULL,
    channel_id INTEGER NULL,
    message_id INTEGER NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NULL,
    finalized_at TEXT NULL,
    FOREIGN KEY (template_version_id) REFERENCES tierlist_template_versions(id),
    CHECK (status IN ('active', 'finalized', 'expired', 'abandoned'))
);

CREATE TABLE IF NOT EXISTS tierlist_template_session_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    template_item_id INTEGER NOT NULL,
    name TEXT NULL,
    source_type TEXT NOT NULL,
    asset_sha256 TEXT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    tier_name TEXT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES tierlist_template_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (template_item_id) REFERENCES tierlist_template_version_items(id),
    FOREIGN KEY (asset_sha256) REFERENCES tierlist_assets(sha256)
);

CREATE INDEX IF NOT EXISTS idx_tierlist_templates_lookup
    ON tierlist_templates(guild_id, visibility, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_tierlist_versions_template
    ON tierlist_template_versions(template_id, version_number DESC);

CREATE INDEX IF NOT EXISTS idx_tierlist_sessions_owner_status
    ON tierlist_template_sessions(owner_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_tierlist_session_items_session_tier
    ON tierlist_template_session_items(session_id, tier_name, position);
"""


async def run_tierlist_template_migrations(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA_SQL)
    applied_rows = await conn.execute_fetchall("SELECT version FROM tierlist_schema_migrations")
    applied = {int(row[0]) for row in applied_rows}
    if SCHEMA_VERSION not in applied:
        await conn.execute(
            "INSERT INTO tierlist_schema_migrations(version, applied_at) VALUES(?, ?)",
            (SCHEMA_VERSION, utc_now_iso()),
        )
    await conn.commit()
