from __future__ import annotations

import json
from typing import Any

from .models import utc_now_iso


SCHEMA_VERSION = 1

DEFAULT_TIERS: list[dict[str, str]] = [
    {"id": "S", "label": "S", "color": "#ff5c5c"},
    {"id": "A", "label": "A", "color": "#ffbd4a"},
    {"id": "B", "label": "B", "color": "#fff176"},
    {"id": "C", "label": "C", "color": "#81c784"},
    {"id": "D", "label": "D", "color": "#64b5f6"},
]

DEFAULT_TIERS_JSON = json.dumps(DEFAULT_TIERS, ensure_ascii=False, separators=(",", ":"))
DEFAULT_STYLE_JSON = json.dumps({"renderer": "baphomet_pillow_v1"}, ensure_ascii=False, separators=(",", ":"))

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tier_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tier_templates (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NULL,
    creator_id INTEGER NOT NULL,
    guild_id INTEGER NULL,
    visibility TEXT NOT NULL,
    current_version_id TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT NULL,
    CHECK (visibility IN ('PRIVATE', 'GUILD', 'GLOBAL')),
    FOREIGN KEY (current_version_id) REFERENCES tier_template_versions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tier_template_versions (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    default_tiers_json TEXT NOT NULL,
    style_json TEXT NULL,
    is_locked INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    published_at TEXT NULL,
    deleted_at TEXT NULL,
    FOREIGN KEY (template_id) REFERENCES tier_templates(id) ON DELETE CASCADE,
    UNIQUE (template_id, version_number)
);

CREATE TABLE IF NOT EXISTS tier_assets (
    id TEXT PRIMARY KEY,
    asset_hash TEXT NOT NULL UNIQUE,
    storage_path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    source_type TEXT NULL,
    metadata_json TEXT NULL,
    created_at TEXT NOT NULL,
    marked_orphan_at TEXT NULL,
    deleted_at TEXT NULL
);

CREATE TABLE IF NOT EXISTS tier_template_items (
    id TEXT PRIMARY KEY,
    template_version_id TEXT NOT NULL,
    item_type TEXT NOT NULL,
    source_type TEXT NULL,
    asset_id TEXT NULL,
    user_caption TEXT NULL,
    render_caption TEXT NULL,
    has_visible_caption INTEGER NOT NULL DEFAULT 0,
    internal_title TEXT NULL,
    source_query TEXT NULL,
    metadata_json TEXT NULL,
    sort_order INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    deleted_at TEXT NULL,
    CHECK (item_type IN ('TEXT_ONLY', 'IMAGE')),
    CHECK (
        item_type != 'TEXT_ONLY'
        OR (render_caption IS NOT NULL AND length(trim(render_caption)) > 0)
    ),
    CHECK (item_type != 'IMAGE' OR asset_id IS NOT NULL),
    FOREIGN KEY (template_version_id) REFERENCES tier_template_versions(id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id) REFERENCES tier_assets(id)
);

CREATE TABLE IF NOT EXISTS tier_sessions (
    id TEXT PRIMARY KEY,
    template_version_id TEXT NOT NULL,
    owner_id INTEGER NOT NULL,
    guild_id INTEGER NULL,
    channel_id INTEGER NULL,
    message_id INTEGER NULL UNIQUE,
    status TEXT NOT NULL,
    selected_item_id TEXT NULL,
    selected_tier_id TEXT NULL,
    current_inventory_page INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finalized_at TEXT NULL,
    expires_at TEXT NULL,
    CHECK (status IN ('ACTIVE', 'FINALIZED', 'EXPIRED', 'ABANDONED', 'DELETED')),
    FOREIGN KEY (template_version_id) REFERENCES tier_template_versions(id)
);

CREATE TABLE IF NOT EXISTS tier_session_items (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    template_item_id TEXT NOT NULL,
    current_tier_id TEXT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    is_unused INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES tier_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (template_item_id) REFERENCES tier_template_items(id),
    UNIQUE (session_id, template_item_id)
);

CREATE INDEX IF NOT EXISTS idx_tier_templates_slug
    ON tier_templates(slug);

CREATE INDEX IF NOT EXISTS idx_tier_templates_creator_id
    ON tier_templates(creator_id);

CREATE INDEX IF NOT EXISTS idx_tier_templates_guild_id
    ON tier_templates(guild_id);

CREATE INDEX IF NOT EXISTS idx_tier_template_versions_template_id
    ON tier_template_versions(template_id);

CREATE INDEX IF NOT EXISTS idx_tier_template_items_version_order
    ON tier_template_items(template_version_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_tier_assets_asset_hash
    ON tier_assets(asset_hash);

CREATE INDEX IF NOT EXISTS idx_tier_sessions_owner_id
    ON tier_sessions(owner_id);

CREATE INDEX IF NOT EXISTS idx_tier_sessions_guild_id
    ON tier_sessions(guild_id);

CREATE INDEX IF NOT EXISTS idx_tier_sessions_message_id
    ON tier_sessions(message_id);

CREATE INDEX IF NOT EXISTS idx_tier_sessions_status
    ON tier_sessions(status);

CREATE INDEX IF NOT EXISTS idx_tier_sessions_template_version_id
    ON tier_sessions(template_version_id);

CREATE INDEX IF NOT EXISTS idx_tier_session_items_session_position
    ON tier_session_items(session_id, is_unused, current_tier_id, position);
"""


async def run_tier_template_migrations(conn: Any) -> None:
    await conn.executescript(SCHEMA_SQL)
    applied_rows = await conn.execute_fetchall("SELECT version FROM tier_schema_migrations")
    applied = {int(row[0]) for row in applied_rows}
    if SCHEMA_VERSION not in applied:
        await conn.execute(
            "INSERT INTO tier_schema_migrations(version, applied_at) VALUES(?, ?)",
            (SCHEMA_VERSION, utc_now_iso()),
        )
    await conn.commit()


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
