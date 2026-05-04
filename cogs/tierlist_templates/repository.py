from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import datetime
from typing import Any

import aiosqlite

from .migrations import run_tierlist_template_migrations
from .models import (
    SessionStatus,
    StoredAsset,
    TemplateDraftSnapshot,
    TemplateItem,
    TemplateSession,
    TemplateSessionItem,
    TemplateSessionSnapshot,
    TemplateTier,
    TemplateVersion,
    TemplateVersionSnapshot,
    TemplateVersionStatus,
    TemplateVisibility,
    TierListTemplate,
    utc_now_iso,
)


DEFAULT_TIERS = ("S", "A", "B", "C", "D")


class TierListTemplateRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._tx_lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("TierListTemplateRepository não conectado.")
        return self._conn

    async def connect(self) -> None:
        if self._conn is not None:
            return
        pathlib.Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA synchronous = NORMAL")
        await self._conn.execute("PRAGMA busy_timeout = 5000")
        await run_tierlist_template_migrations(self._conn)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def upsert_asset(self, asset: StoredAsset) -> None:
        await self.conn.execute(
            """
            INSERT INTO tierlist_assets(
                sha256, relative_path, mime_type, byte_size, width, height, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sha256) DO UPDATE SET
                relative_path = excluded.relative_path,
                mime_type = excluded.mime_type,
                byte_size = excluded.byte_size,
                width = excluded.width,
                height = excluded.height
            """,
            (
                asset.sha256,
                asset.relative_path,
                asset.mime_type,
                asset.byte_size,
                asset.width,
                asset.height,
                asset.created_at or utc_now_iso(),
            ),
        )
        await self.conn.commit()

    async def get_asset(self, sha256: str) -> StoredAsset | None:
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM tierlist_assets WHERE sha256 = ?",
            (sha256,),
        )
        return self._row_to_asset(rows[0]) if rows else None

    async def create_template(
        self,
        *,
        name: str,
        description: str,
        creator_id: int,
        guild_id: int | None,
        visibility: TemplateVisibility = TemplateVisibility.GUILD,
        default_tiers: tuple[str, ...] = DEFAULT_TIERS,
    ) -> TierListTemplate:
        now = utc_now_iso()
        async with self._tx_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    """
                    INSERT INTO tierlist_templates(
                        name, description, creator_id, guild_id, visibility,
                        current_version_id, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (name, description, creator_id, guild_id, visibility.value, now, now),
                )
                template_id = int(cursor.lastrowid)
                for position, tier_name in enumerate(default_tiers):
                    await self.conn.execute(
                        """
                        INSERT INTO tierlist_template_draft_tiers(
                            template_id, name, position, color_hex
                        ) VALUES(?, ?, ?, NULL)
                        """,
                        (template_id, tier_name, position),
                    )
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise
        template = await self.get_template(template_id)
        if template is None:
            raise RuntimeError("template criado não encontrado")
        return template

    async def get_template(self, template_id: int) -> TierListTemplate | None:
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM tierlist_templates WHERE id = ? AND deleted_at IS NULL",
            (template_id,),
        )
        return self._row_to_template(rows[0]) if rows else None

    async def list_visible_templates(
        self,
        *,
        guild_id: int | None,
        user_id: int,
        limit: int = 25,
    ) -> list[TierListTemplate]:
        rows = await self.conn.execute_fetchall(
            """
            SELECT *
            FROM tierlist_templates
            WHERE deleted_at IS NULL
              AND current_version_id IS NOT NULL
              AND (
                    visibility = 'public'
                 OR (visibility = 'guild' AND guild_id IS NOT NULL AND guild_id = ?)
                 OR (visibility = 'private' AND creator_id = ?)
              )
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (guild_id, user_id, limit),
        )
        return [self._row_to_template(row) for row in rows]

    async def list_owned_templates(self, *, user_id: int, limit: int = 25) -> list[TierListTemplate]:
        rows = await self.conn.execute_fetchall(
            """
            SELECT *
            FROM tierlist_templates
            WHERE deleted_at IS NULL AND creator_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [self._row_to_template(row) for row in rows]

    async def set_draft_tiers(
        self,
        *,
        template_id: int,
        tiers: list[tuple[str, str | None]],
    ) -> None:
        now = utc_now_iso()
        async with self._tx_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self.conn.execute(
                    "DELETE FROM tierlist_template_draft_tiers WHERE template_id = ?",
                    (template_id,),
                )
                for position, (tier_name, color_hex) in enumerate(tiers):
                    await self.conn.execute(
                        """
                        INSERT INTO tierlist_template_draft_tiers(
                            template_id, name, position, color_hex
                        ) VALUES(?, ?, ?, ?)
                        """,
                        (template_id, tier_name, position, color_hex),
                    )
                await self.conn.execute(
                    "UPDATE tierlist_templates SET updated_at = ? WHERE id = ?",
                    (now, template_id),
                )
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise

    async def add_draft_item(
        self,
        *,
        template_id: int,
        name: str | None,
        source_type: str,
        source_query: str | None,
        asset_sha256: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> TemplateItem:
        now = utc_now_iso()
        async with self._tx_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                position_row = await self.conn.execute_fetchall(
                    """
                    SELECT COALESCE(MAX(position), -1) + 1 AS next_position
                    FROM tierlist_template_draft_items
                    WHERE template_id = ?
                    """,
                    (template_id,),
                )
                position = int(position_row[0]["next_position"])
                cursor = await self.conn.execute(
                    """
                    INSERT INTO tierlist_template_draft_items(
                        template_id, name, source_type, source_query, asset_sha256,
                        metadata_json, position, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        template_id,
                        name,
                        source_type,
                        source_query,
                        asset_sha256,
                        self._json_dumps(metadata or {}),
                        position,
                        now,
                    ),
                )
                item_id = int(cursor.lastrowid)
                await self.conn.execute(
                    "UPDATE tierlist_templates SET updated_at = ? WHERE id = ?",
                    (now, template_id),
                )
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise

        rows = await self.conn.execute_fetchall(
            "SELECT * FROM tierlist_template_draft_items WHERE id = ?",
            (item_id,),
        )
        return self._row_to_template_item(rows[0], owner_id=template_id)

    async def get_draft_snapshot(self, template_id: int) -> TemplateDraftSnapshot | None:
        template = await self.get_template(template_id)
        if template is None:
            return None
        tier_rows = await self.conn.execute_fetchall(
            """
            SELECT * FROM tierlist_template_draft_tiers
            WHERE template_id = ?
            ORDER BY position ASC
            """,
            (template_id,),
        )
        item_rows = await self.conn.execute_fetchall(
            """
            SELECT * FROM tierlist_template_draft_items
            WHERE template_id = ?
            ORDER BY position ASC
            """,
            (template_id,),
        )
        return TemplateDraftSnapshot(
            template=template,
            tiers=tuple(self._row_to_tier(row, owner_id=template_id) for row in tier_rows),
            items=tuple(self._row_to_template_item(row, owner_id=template_id) for row in item_rows),
        )

    async def publish_template(self, *, template_id: int, published_by: int) -> TemplateVersion:
        now = utc_now_iso()
        async with self._tx_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                template_rows = await self.conn.execute_fetchall(
                    "SELECT * FROM tierlist_templates WHERE id = ? AND deleted_at IS NULL",
                    (template_id,),
                )
                if not template_rows:
                    raise ValueError("template não encontrado")

                tier_rows = await self.conn.execute_fetchall(
                    """
                    SELECT * FROM tierlist_template_draft_tiers
                    WHERE template_id = ?
                    ORDER BY position ASC
                    """,
                    (template_id,),
                )
                if not tier_rows:
                    raise ValueError("template precisa ter pelo menos uma tier")

                version_row = await self.conn.execute_fetchall(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version
                    FROM tierlist_template_versions
                    WHERE template_id = ?
                    """,
                    (template_id,),
                )
                version_number = int(version_row[0]["next_version"])
                cursor = await self.conn.execute(
                    """
                    INSERT INTO tierlist_template_versions(
                        template_id, version_number, status, published_by, published_at
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        template_id,
                        version_number,
                        TemplateVersionStatus.PUBLISHED.value,
                        published_by,
                        now,
                    ),
                )
                version_id = int(cursor.lastrowid)

                for row in tier_rows:
                    await self.conn.execute(
                        """
                        INSERT INTO tierlist_template_version_tiers(
                            version_id, name, position, color_hex
                        ) VALUES(?, ?, ?, ?)
                        """,
                        (version_id, row["name"], row["position"], row["color_hex"]),
                    )

                item_rows = await self.conn.execute_fetchall(
                    """
                    SELECT * FROM tierlist_template_draft_items
                    WHERE template_id = ?
                    ORDER BY position ASC
                    """,
                    (template_id,),
                )
                for row in item_rows:
                    await self.conn.execute(
                        """
                        INSERT INTO tierlist_template_version_items(
                            version_id, name, source_type, source_query, asset_sha256,
                            metadata_json, position, created_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            version_id,
                            row["name"],
                            row["source_type"],
                            row["source_query"],
                            row["asset_sha256"],
                            row["metadata_json"],
                            row["position"],
                            row["created_at"],
                        ),
                    )

                await self.conn.execute(
                    """
                    UPDATE tierlist_templates
                    SET current_version_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (version_id, now, template_id),
                )
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise

        version = await self.get_version(version_id)
        if version is None:
            raise RuntimeError("versão publicada não encontrada")
        return version

    async def get_version(self, version_id: int) -> TemplateVersion | None:
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM tierlist_template_versions WHERE id = ?",
            (version_id,),
        )
        return self._row_to_version(rows[0]) if rows else None

    async def get_current_version_snapshot(self, template_id: int) -> TemplateVersionSnapshot | None:
        template = await self.get_template(template_id)
        if template is None or template.current_version_id is None:
            return None
        return await self.get_version_snapshot(template.current_version_id)

    async def get_version_snapshot(self, version_id: int) -> TemplateVersionSnapshot | None:
        version_rows = await self.conn.execute_fetchall(
            """
            SELECT v.*, t.id AS template_row_id
            FROM tierlist_template_versions v
            JOIN tierlist_templates t ON t.id = v.template_id
            WHERE v.id = ? AND t.deleted_at IS NULL
            """,
            (version_id,),
        )
        if not version_rows:
            return None
        version = self._row_to_version(version_rows[0])
        template = await self.get_template(version.template_id)
        if template is None:
            return None
        tier_rows = await self.conn.execute_fetchall(
            """
            SELECT * FROM tierlist_template_version_tiers
            WHERE version_id = ?
            ORDER BY position ASC
            """,
            (version_id,),
        )
        item_rows = await self.conn.execute_fetchall(
            """
            SELECT * FROM tierlist_template_version_items
            WHERE version_id = ?
            ORDER BY position ASC
            """,
            (version_id,),
        )
        return TemplateVersionSnapshot(
            template=template,
            version=version,
            tiers=tuple(self._row_to_tier(row, owner_id=version_id) for row in tier_rows),
            items=tuple(self._row_to_template_item(row, owner_id=version_id) for row in item_rows),
        )

    async def create_session_from_version(
        self,
        *,
        version_id: int,
        owner_id: int,
        guild_id: int | None,
        channel_id: int | None,
        title: str,
        expires_at: str | None,
    ) -> TemplateSession:
        now = utc_now_iso()
        async with self._tx_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                version_rows = await self.conn.execute_fetchall(
                    """
                    SELECT v.id, t.name
                    FROM tierlist_template_versions v
                    JOIN tierlist_templates t ON t.id = v.template_id
                    WHERE v.id = ? AND t.deleted_at IS NULL
                    """,
                    (version_id,),
                )
                if not version_rows:
                    raise ValueError("versão de template não encontrada")

                cursor = await self.conn.execute(
                    """
                    INSERT INTO tierlist_template_sessions(
                        template_version_id, owner_id, guild_id, channel_id,
                        message_id, title, status, created_at, updated_at, expires_at, finalized_at
                    ) VALUES(?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        version_id,
                        owner_id,
                        guild_id,
                        channel_id,
                        title or str(version_rows[0]["name"]),
                        SessionStatus.ACTIVE.value,
                        now,
                        now,
                        expires_at,
                    ),
                )
                session_id = int(cursor.lastrowid)

                item_rows = await self.conn.execute_fetchall(
                    """
                    SELECT * FROM tierlist_template_version_items
                    WHERE version_id = ?
                    ORDER BY position ASC
                    """,
                    (version_id,),
                )
                for position, row in enumerate(item_rows):
                    await self.conn.execute(
                        """
                        INSERT INTO tierlist_template_session_items(
                            session_id, template_item_id, name, source_type,
                            asset_sha256, metadata_json, tier_name, position, created_at
                        ) VALUES(?, ?, ?, ?, ?, ?, NULL, ?, ?)
                        """,
                        (
                            session_id,
                            row["id"],
                            row["name"],
                            row["source_type"],
                            row["asset_sha256"],
                            row["metadata_json"],
                            position,
                            now,
                        ),
                    )

                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise

        session = await self.get_session(session_id)
        if session is None:
            raise RuntimeError("sessão criada não encontrada")
        return session

    async def get_session(self, session_id: int) -> TemplateSession | None:
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM tierlist_template_sessions WHERE id = ?",
            (session_id,),
        )
        return self._row_to_session(rows[0]) if rows else None

    async def get_session_snapshot(self, session_id: int) -> TemplateSessionSnapshot | None:
        session = await self.get_session(session_id)
        if session is None:
            return None
        version_snapshot = await self.get_version_snapshot(session.template_version_id)
        if version_snapshot is None:
            return None
        item_rows = await self.conn.execute_fetchall(
            """
            SELECT * FROM tierlist_template_session_items
            WHERE session_id = ?
            ORDER BY
                CASE WHEN tier_name IS NULL THEN 1 ELSE 0 END,
                tier_name ASC,
                position ASC,
                id ASC
            """,
            (session_id,),
        )
        return TemplateSessionSnapshot(
            session=session,
            template=version_snapshot.template,
            version=version_snapshot.version,
            tiers=version_snapshot.tiers,
            items=tuple(self._row_to_session_item(row) for row in item_rows),
        )

    async def set_session_message(
        self,
        *,
        session_id: int,
        channel_id: int | None,
        message_id: int | None,
    ) -> None:
        await self.conn.execute(
            """
            UPDATE tierlist_template_sessions
            SET channel_id = ?, message_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (channel_id, message_id, utc_now_iso(), session_id),
        )
        await self.conn.commit()

    async def move_session_item(
        self,
        *,
        session_id: int,
        item_id: int,
        tier_name: str | None,
    ) -> TemplateSessionItem:
        now = utc_now_iso()
        async with self._tx_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                session_rows = await self.conn.execute_fetchall(
                    "SELECT * FROM tierlist_template_sessions WHERE id = ?",
                    (session_id,),
                )
                if not session_rows:
                    raise ValueError("sessão não encontrada")
                session = self._row_to_session(session_rows[0])
                if session.status != SessionStatus.ACTIVE:
                    raise ValueError("sessão não está ativa")

                if tier_name is not None:
                    tier_rows = await self.conn.execute_fetchall(
                        """
                        SELECT 1
                        FROM tierlist_template_version_tiers
                        WHERE version_id = ? AND name = ?
                        """,
                        (session.template_version_id, tier_name),
                    )
                    if not tier_rows:
                        raise ValueError("tier inválida para essa sessão")

                item_rows = await self.conn.execute_fetchall(
                    """
                    SELECT *
                    FROM tierlist_template_session_items
                    WHERE id = ? AND session_id = ?
                    """,
                    (item_id, session_id),
                )
                if not item_rows:
                    raise ValueError("item não encontrado nessa sessão")

                position_rows = await self.conn.execute_fetchall(
                    """
                    SELECT COALESCE(MAX(position), -1) + 1 AS next_position
                    FROM tierlist_template_session_items
                    WHERE session_id = ?
                      AND ((tier_name IS NULL AND ? IS NULL) OR tier_name = ?)
                    """,
                    (session_id, tier_name, tier_name),
                )
                next_position = int(position_rows[0]["next_position"])
                await self.conn.execute(
                    """
                    UPDATE tierlist_template_session_items
                    SET tier_name = ?, position = ?
                    WHERE id = ? AND session_id = ?
                    """,
                    (tier_name, next_position, item_id, session_id),
                )
                await self.conn.execute(
                    "UPDATE tierlist_template_sessions SET updated_at = ? WHERE id = ?",
                    (now, session_id),
                )
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise

        rows = await self.conn.execute_fetchall(
            "SELECT * FROM tierlist_template_session_items WHERE id = ?",
            (item_id,),
        )
        return self._row_to_session_item(rows[0])

    async def update_session_status(self, *, session_id: int, status: SessionStatus) -> None:
        now = utc_now_iso()
        finalized_at = now if status == SessionStatus.FINALIZED else None
        await self.conn.execute(
            """
            UPDATE tierlist_template_sessions
            SET status = ?, updated_at = ?, finalized_at = COALESCE(?, finalized_at)
            WHERE id = ?
            """,
            (status.value, now, finalized_at, session_id),
        )
        await self.conn.commit()

    async def expire_stale_sessions(self, *, now_iso: str | None = None) -> int:
        now_value = now_iso or utc_now_iso()
        cursor = await self.conn.execute(
            """
            UPDATE tierlist_template_sessions
            SET status = 'expired', updated_at = ?
            WHERE status = 'active'
              AND expires_at IS NOT NULL
              AND expires_at <= ?
            """,
            (now_value, now_value),
        )
        await self.conn.commit()
        return int(cursor.rowcount or 0)

    def _row_to_template(self, row: aiosqlite.Row) -> TierListTemplate:
        return TierListTemplate(
            id=int(row["id"]),
            name=str(row["name"]),
            description=str(row["description"] or ""),
            creator_id=int(row["creator_id"]),
            guild_id=int(row["guild_id"]) if row["guild_id"] is not None else None,
            visibility=TemplateVisibility(str(row["visibility"])),
            current_version_id=int(row["current_version_id"]) if row["current_version_id"] is not None else None,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            deleted_at=str(row["deleted_at"]) if row["deleted_at"] is not None else None,
        )

    def _row_to_version(self, row: aiosqlite.Row) -> TemplateVersion:
        return TemplateVersion(
            id=int(row["id"]),
            template_id=int(row["template_id"]),
            version_number=int(row["version_number"]),
            status=TemplateVersionStatus(str(row["status"])),
            published_by=int(row["published_by"]),
            published_at=str(row["published_at"]),
        )

    def _row_to_tier(self, row: aiosqlite.Row, *, owner_id: int) -> TemplateTier:
        return TemplateTier(
            id=int(row["id"]),
            owner_id=owner_id,
            name=str(row["name"]),
            position=int(row["position"]),
            color_hex=str(row["color_hex"]) if row["color_hex"] is not None else None,
        )

    def _row_to_template_item(self, row: aiosqlite.Row, *, owner_id: int) -> TemplateItem:
        return TemplateItem(
            id=int(row["id"]),
            owner_id=owner_id,
            name=str(row["name"]) if row["name"] is not None else None,
            source_type=str(row["source_type"]),
            source_query=str(row["source_query"]) if row["source_query"] is not None else None,
            asset_sha256=str(row["asset_sha256"]) if row["asset_sha256"] is not None else None,
            metadata=self._json_loads(row["metadata_json"]),
            position=int(row["position"]),
            created_at=str(row["created_at"]) if "created_at" in row.keys() and row["created_at"] is not None else None,
        )

    def _row_to_asset(self, row: aiosqlite.Row) -> StoredAsset:
        return StoredAsset(
            sha256=str(row["sha256"]),
            relative_path=str(row["relative_path"]),
            mime_type=str(row["mime_type"]),
            byte_size=int(row["byte_size"]),
            width=int(row["width"]),
            height=int(row["height"]),
            created_at=str(row["created_at"]),
        )

    def _row_to_session(self, row: aiosqlite.Row) -> TemplateSession:
        return TemplateSession(
            id=int(row["id"]),
            template_version_id=int(row["template_version_id"]),
            owner_id=int(row["owner_id"]),
            guild_id=int(row["guild_id"]) if row["guild_id"] is not None else None,
            channel_id=int(row["channel_id"]) if row["channel_id"] is not None else None,
            message_id=int(row["message_id"]) if row["message_id"] is not None else None,
            title=str(row["title"]),
            status=SessionStatus(str(row["status"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            expires_at=str(row["expires_at"]) if row["expires_at"] is not None else None,
            finalized_at=str(row["finalized_at"]) if row["finalized_at"] is not None else None,
        )

    def _row_to_session_item(self, row: aiosqlite.Row) -> TemplateSessionItem:
        return TemplateSessionItem(
            id=int(row["id"]),
            session_id=int(row["session_id"]),
            template_item_id=int(row["template_item_id"]),
            name=str(row["name"]) if row["name"] is not None else None,
            source_type=str(row["source_type"]),
            asset_sha256=str(row["asset_sha256"]) if row["asset_sha256"] is not None else None,
            metadata=self._json_loads(row["metadata_json"]),
            tier_name=str(row["tier_name"]) if row["tier_name"] is not None else None,
            position=int(row["position"]),
            created_at=str(row["created_at"]) if row["created_at"] is not None else None,
        )

    def _json_loads(self, value: Any) -> dict[str, Any]:
        try:
            payload = json.loads(str(value or "{}"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _json_dumps(self, value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
