from __future__ import annotations

import json
from typing import Any

from .database import DatabaseManager
from .models import SessionStatus, TierSession, TierSessionItem, utc_now_iso
from .repository_utils import fetch_one, new_uuid


def _session_from_row(row: Any | None) -> TierSession | None:
    if row is None:
        return None
    return TierSession(
        id=row["id"],
        template_version_id=row["template_version_id"],
        owner_id=int(row["owner_id"]),
        guild_id=row["guild_id"],
        channel_id=row["channel_id"],
        message_id=row["message_id"],
        status=SessionStatus(row["status"]),
        selected_item_id=row["selected_item_id"],
        selected_tier_id=row["selected_tier_id"],
        current_inventory_page=int(row["current_inventory_page"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        finalized_at=row["finalized_at"],
        expires_at=row["expires_at"],
    )


def _session_item_from_row(row: Any | None) -> TierSessionItem | None:
    if row is None:
        return None
    return TierSessionItem(
        id=row["id"],
        session_id=row["session_id"],
        template_item_id=row["template_item_id"],
        current_tier_id=row["current_tier_id"],
        position=int(row["position"]),
        is_unused=bool(row["is_unused"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class TierSessionRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    async def create_session(
        self,
        *,
        template_version_id: str,
        owner_id: int,
        guild_id: int | None = None,
        channel_id: int | None = None,
        message_id: int | None = None,
        expires_at: str | None = None,
    ) -> TierSession:
        session_id = new_uuid()
        now = utc_now_iso()
        async with self.db.immediate_transaction() as conn:
            version = await fetch_one(
                conn,
                """
                SELECT id, is_locked FROM tier_template_versions
                WHERE id = ? AND deleted_at IS NULL
                """,
                (template_version_id,),
            )
            if version is None:
                raise ValueError("Versão de template inexistente.")
            if not bool(version["is_locked"]):
                raise ValueError("Sessões só podem ser criadas a partir de versões publicadas/congeladas.")
            await conn.execute(
                """
                INSERT INTO tier_sessions(
                    id, template_version_id, owner_id, guild_id, channel_id, message_id,
                    status, selected_item_id, selected_tier_id, current_inventory_page,
                    created_at, updated_at, finalized_at, expires_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, ?, ?, NULL, ?)
                """,
                (
                    session_id,
                    template_version_id,
                    int(owner_id),
                    guild_id,
                    channel_id,
                    message_id,
                    SessionStatus.ACTIVE.value,
                    now,
                    now,
                    expires_at,
                ),
            )
            await self._create_session_items_from_template(conn, session_id, template_version_id, now=now)

        session = await self.get_session(session_id)
        if session is None:
            raise RuntimeError("Sessão criada não pôde ser recuperada.")
        return session

    async def create_session_items_from_template(
        self,
        *,
        session_id: str,
        template_version_id: str,
    ) -> list[TierSessionItem]:
        now = utc_now_iso()
        async with self.db.immediate_transaction() as conn:
            session = await fetch_one(
                conn,
                """
                SELECT id, template_version_id, status
                FROM tier_sessions
                WHERE id = ?
                """,
                (session_id,),
            )
            if session is None:
                raise ValueError("Sessão não encontrada.")
            if session["template_version_id"] != template_version_id:
                raise ValueError("Versão informada não pertence à sessão.")
            if session["status"] != SessionStatus.ACTIVE.value:
                raise ValueError("Só é possível popular sessões ativas.")
            await self._create_session_items_from_template(conn, session_id, template_version_id, now=now)
            await conn.execute(
                "UPDATE tier_sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
        return await self.list_session_items(session_id)

    async def _create_session_items_from_template(
        self,
        conn: Any,
        session_id: str,
        template_version_id: str,
        *,
        now: str,
    ) -> None:
        item_rows = await conn.execute_fetchall(
            """
            SELECT id, sort_order
            FROM tier_template_items
            WHERE template_version_id = ? AND deleted_at IS NULL
            ORDER BY sort_order ASC, created_at ASC
            """,
            (template_version_id,),
        )
        for item in item_rows:
            await conn.execute(
                """
                INSERT OR IGNORE INTO tier_session_items(
                    id, session_id, template_item_id, current_tier_id,
                    position, is_unused, created_at, updated_at
                )
                VALUES(?, ?, ?, NULL, ?, 1, ?, ?)
                """,
                (new_uuid(), session_id, item["id"], int(item["sort_order"]), now, now),
            )

    async def get_session(self, session_id: str) -> TierSession | None:
        row = await fetch_one(
            self.db.conn,
            "SELECT * FROM tier_sessions WHERE id = ?",
            (session_id,),
        )
        return _session_from_row(row)

    async def get_session_by_message_id(self, message_id: int) -> TierSession | None:
        row = await fetch_one(
            self.db.conn,
            "SELECT * FROM tier_sessions WHERE message_id = ?",
            (int(message_id),),
        )
        return _session_from_row(row)

    async def list_session_items(self, session_id: str) -> list[TierSessionItem]:
        rows = await self.db.conn.execute_fetchall(
            """
            SELECT * FROM tier_session_items
            WHERE session_id = ?
            ORDER BY is_unused DESC, current_tier_id ASC, position ASC, created_at ASC
            """,
            (session_id,),
        )
        return [item for row in rows if (item := _session_item_from_row(row)) is not None]

    async def move_item_to_tier(
        self,
        *,
        session_id: str,
        session_item_id: str,
        tier_id: str,
        position: int | None = None,
        owner_id: int | None = None,
    ) -> TierSessionItem:
        now = utc_now_iso()
        async with self.db.immediate_transaction() as conn:
            session = await self._require_active_session(conn, session_id, owner_id=owner_id)
            await self._require_tier_id(conn, session["template_version_id"], tier_id)
            await self._require_session_item(conn, session_id, session_item_id)
            if position is None:
                position = await self._next_position(conn, session_id, tier_id=tier_id, is_unused=False)
            await conn.execute(
                """
                UPDATE tier_session_items
                SET current_tier_id = ?, is_unused = 0, position = ?, updated_at = ?
                WHERE id = ? AND session_id = ?
                """,
                (tier_id, int(position), now, session_item_id, session_id),
            )
            await conn.execute(
                "UPDATE tier_sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
        item = await self.get_session_item(session_item_id)
        if item is None:
            raise RuntimeError("Item movido não pôde ser recuperado.")
        return item

    async def move_item_to_inventory(
        self,
        *,
        session_id: str,
        session_item_id: str,
        position: int | None = None,
        owner_id: int | None = None,
    ) -> TierSessionItem:
        now = utc_now_iso()
        async with self.db.immediate_transaction() as conn:
            await self._require_active_session(conn, session_id, owner_id=owner_id)
            await self._require_session_item(conn, session_id, session_item_id)
            if position is None:
                position = await self._next_position(conn, session_id, tier_id=None, is_unused=True)
            await conn.execute(
                """
                UPDATE tier_session_items
                SET current_tier_id = NULL, is_unused = 1, position = ?, updated_at = ?
                WHERE id = ? AND session_id = ?
                """,
                (int(position), now, session_item_id, session_id),
            )
            await conn.execute(
                "UPDATE tier_sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
        item = await self.get_session_item(session_item_id)
        if item is None:
            raise RuntimeError("Item movido não pôde ser recuperado.")
        return item

    async def reset_session(self, session_id: str, *, owner_id: int | None = None) -> TierSession:
        now = utc_now_iso()
        async with self.db.immediate_transaction() as conn:
            await self._require_active_session(conn, session_id, owner_id=owner_id)
            rows = await conn.execute_fetchall(
                """
                SELECT id FROM tier_session_items
                WHERE session_id = ?
                ORDER BY position ASC, created_at ASC
                """,
                (session_id,),
            )
            for position, row in enumerate(rows):
                await conn.execute(
                    """
                    UPDATE tier_session_items
                    SET current_tier_id = NULL, is_unused = 1, position = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (position, now, row["id"]),
                )
            await conn.execute(
                """
                UPDATE tier_sessions
                SET selected_item_id = NULL, selected_tier_id = NULL,
                    current_inventory_page = 0, updated_at = ?
                WHERE id = ?
                """,
                (now, session_id),
            )
        session = await self.get_session(session_id)
        if session is None:
            raise RuntimeError("Sessão resetada não pôde ser recuperada.")
        return session

    async def get_session_item(self, session_item_id: str) -> TierSessionItem | None:
        row = await fetch_one(
            self.db.conn,
            "SELECT * FROM tier_session_items WHERE id = ?",
            (session_item_id,),
        )
        return _session_item_from_row(row)

    async def set_selected_item(
        self,
        *,
        session_id: str,
        session_item_id: str | None,
        owner_id: int | None = None,
    ) -> TierSession:
        now = utc_now_iso()
        async with self.db.immediate_transaction() as conn:
            await self._require_active_session(conn, session_id, owner_id=owner_id)
            if session_item_id is not None:
                await self._require_session_item(conn, session_id, session_item_id)
            await conn.execute(
                """
                UPDATE tier_sessions
                SET selected_item_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (session_item_id, now, session_id),
            )
        session = await self.get_session(session_id)
        if session is None:
            raise RuntimeError("Sessão atualizada não pôde ser recuperada.")
        return session

    async def set_selected_tier(
        self,
        *,
        session_id: str,
        tier_id: str | None,
        owner_id: int | None = None,
    ) -> TierSession:
        now = utc_now_iso()
        async with self.db.immediate_transaction() as conn:
            session = await self._require_active_session(conn, session_id, owner_id=owner_id)
            if tier_id is not None:
                await self._require_tier_id(conn, session["template_version_id"], tier_id)
            await conn.execute(
                """
                UPDATE tier_sessions
                SET selected_tier_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (tier_id, now, session_id),
            )
        session_obj = await self.get_session(session_id)
        if session_obj is None:
            raise RuntimeError("Sessão atualizada não pôde ser recuperada.")
        return session_obj

    async def update_inventory_page(
        self,
        *,
        session_id: str,
        page: int,
        owner_id: int | None = None,
    ) -> TierSession:
        if page < 0:
            raise ValueError("current_inventory_page não pode ser negativo.")
        return await self._update_session_fields(
            session_id=session_id,
            owner_id=owner_id,
            fields={"current_inventory_page": int(page)},
        )

    async def update_message_id(
        self,
        *,
        session_id: str,
        message_id: int | None,
        channel_id: int | None = None,
        owner_id: int | None = None,
    ) -> TierSession:
        fields: dict[str, Any] = {"message_id": message_id}
        if channel_id is not None:
            fields["channel_id"] = channel_id
        return await self._update_session_fields(session_id=session_id, owner_id=owner_id, fields=fields)

    async def finalize_session(self, session_id: str, *, owner_id: int | None = None) -> TierSession:
        return await self._set_status(session_id, SessionStatus.FINALIZED, owner_id=owner_id)

    async def expire_session(self, session_id: str, *, owner_id: int | None = None) -> TierSession:
        return await self._set_status(session_id, SessionStatus.EXPIRED, owner_id=owner_id)

    async def abandon_session(self, session_id: str, *, owner_id: int | None = None) -> TierSession:
        return await self._set_status(session_id, SessionStatus.ABANDONED, owner_id=owner_id)

    async def get_active_sessions_for_user(
        self,
        owner_id: int,
        *,
        guild_id: int | None = None,
        limit: int = 25,
    ) -> list[TierSession]:
        query = """
            SELECT * FROM tier_sessions
            WHERE owner_id = ? AND status = ?
        """
        params: list[Any] = [int(owner_id), SessionStatus.ACTIVE.value]
        if guild_id is not None:
            query += " AND guild_id = ?"
            params.append(int(guild_id))
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(int(limit))
        rows = await self.db.conn.execute_fetchall(query, tuple(params))
        return [session for row in rows if (session := _session_from_row(row)) is not None]

    async def list_active_sessions(self, *, limit: int = 500) -> list[TierSession]:
        rows = await self.db.conn.execute_fetchall(
            """
            SELECT * FROM tier_sessions
            WHERE status = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (SessionStatus.ACTIVE.value, int(limit)),
        )
        return [session for row in rows if (session := _session_from_row(row)) is not None]

    async def expire_stale_sessions(self, *, updated_before: str, limit: int = 500) -> int:
        now = utc_now_iso()
        async with self.db.immediate_transaction() as conn:
            rows = await conn.execute_fetchall(
                """
                SELECT id
                FROM tier_sessions
                WHERE status = ? AND updated_at < ?
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (SessionStatus.ACTIVE.value, updated_before, int(limit)),
            )
            for row in rows:
                await conn.execute(
                    """
                    UPDATE tier_sessions
                    SET status = ?, updated_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (SessionStatus.EXPIRED.value, now, row["id"], SessionStatus.ACTIVE.value),
                )
        return len(rows)

    async def _update_session_fields(
        self,
        *,
        session_id: str,
        owner_id: int | None,
        fields: dict[str, Any],
    ) -> TierSession:
        if not fields:
            raise ValueError("Nenhum campo informado para atualização.")
        now = utc_now_iso()
        async with self.db.immediate_transaction() as conn:
            await self._require_active_session(conn, session_id, owner_id=owner_id)
            assignments = [f"{field} = ?" for field in fields]
            params = list(fields.values())
            assignments.append("updated_at = ?")
            params.append(now)
            params.append(session_id)
            await conn.execute(
                f"UPDATE tier_sessions SET {', '.join(assignments)} WHERE id = ?",
                tuple(params),
            )
        session = await self.get_session(session_id)
        if session is None:
            raise RuntimeError("Sessão atualizada não pôde ser recuperada.")
        return session

    async def _set_status(
        self,
        session_id: str,
        status: SessionStatus,
        *,
        owner_id: int | None,
    ) -> TierSession:
        now = utc_now_iso()
        async with self.db.immediate_transaction() as conn:
            await self._require_active_session(conn, session_id, owner_id=owner_id)
            finalized_at = now if status is SessionStatus.FINALIZED else None
            await conn.execute(
                """
                UPDATE tier_sessions
                SET status = ?, updated_at = ?, finalized_at = COALESCE(?, finalized_at)
                WHERE id = ?
                """,
                (status.value, now, finalized_at, session_id),
            )
        session = await self.get_session(session_id)
        if session is None:
            raise RuntimeError("Sessão atualizada não pôde ser recuperada.")
        return session

    async def _require_active_session(self, conn: Any, session_id: str, *, owner_id: int | None) -> Any:
        session = await fetch_one(
            conn,
            "SELECT * FROM tier_sessions WHERE id = ?",
            (session_id,),
        )
        if session is None:
            raise ValueError("Sessão não encontrada.")
        if owner_id is not None and int(session["owner_id"]) != int(owner_id):
            raise ValueError("Sessão não pertence ao usuário informado.")
        if session["status"] != SessionStatus.ACTIVE.value:
            raise ValueError("Só é possível alterar sessões ativas.")
        return session

    async def _require_session_item(self, conn: Any, session_id: str, session_item_id: str) -> Any:
        item = await fetch_one(
            conn,
            """
            SELECT * FROM tier_session_items
            WHERE id = ? AND session_id = ?
            """,
            (session_item_id, session_id),
        )
        if item is None:
            raise ValueError("Item não pertence à sessão.")
        return item

    async def _require_tier_id(self, conn: Any, template_version_id: str, tier_id: str) -> None:
        row = await fetch_one(
            conn,
            """
            SELECT default_tiers_json
            FROM tier_template_versions
            WHERE id = ? AND deleted_at IS NULL
            """,
            (template_version_id,),
        )
        if row is None:
            raise ValueError("Versão de template não encontrada.")
        tiers = json.loads(row["default_tiers_json"])
        valid_ids = {str(tier.get("id")) for tier in tiers if isinstance(tier, dict)}
        if tier_id not in valid_ids:
            raise ValueError("Tier não existe na versão do template.")

    async def _next_position(
        self,
        conn: Any,
        session_id: str,
        *,
        tier_id: str | None,
        is_unused: bool,
    ) -> int:
        if is_unused:
            row = await fetch_one(
                conn,
                """
                SELECT COALESCE(MAX(position), -1) + 1 AS next_position
                FROM tier_session_items
                WHERE session_id = ? AND is_unused = 1
                """,
                (session_id,),
            )
        else:
            row = await fetch_one(
                conn,
                """
                SELECT COALESCE(MAX(position), -1) + 1 AS next_position
                FROM tier_session_items
                WHERE session_id = ? AND is_unused = 0 AND current_tier_id = ?
                """,
                (session_id, tier_id),
            )
        return int(row["next_position"])
