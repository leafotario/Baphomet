from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from ..db import ProfileDatabase
from ..models import (
    GuildProfileSettings,
    PresentationMode,
    ProfileFieldSourceType,
    ProfileFieldStatus,
    ProfileFieldValue,
    ProfileDeletionResult,
    ProfileModerationAction,
    ProfileRecord,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads_message_ids(payload: str | None) -> tuple[int, ...]:
    if not payload:
        return ()
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError:
        return ()
    if not isinstance(raw, list):
        return ()
    ids: list[int] = []
    for item in raw:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(ids)


class ProfileRepository:
    def __init__(self, database: ProfileDatabase) -> None:
        self.database = database

    async def ensure_profile(self, guild_id: int, user_id: int) -> ProfileRecord:
        now = utc_now_iso()
        async with self.database.transaction() as conn:
            await self._ensure_profile_in_conn(conn, guild_id, user_id, now)
            return await self._get_profile_in_conn(conn, guild_id, user_id)

    async def mark_onboarding_completed(self, guild_id: int, user_id: int, completed: bool = True) -> ProfileRecord:
        now = utc_now_iso()
        async with self.database.transaction() as conn:
            await self._ensure_profile_in_conn(conn, guild_id, user_id, now)
            await conn.execute(
                """
                UPDATE profiles
                SET onboarding_completed = ?, updated_at = ?
                WHERE guild_id = ? AND user_id = ?
                """,
                (int(completed), now, guild_id, user_id),
            )
            return await self._get_profile_in_conn(conn, guild_id, user_id)

    async def get_profile(self, guild_id: int, user_id: int) -> ProfileRecord | None:
        async with self.database.session() as conn:
            rows = await conn.execute_fetchall(
                "SELECT * FROM profiles WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
        if not rows:
            return None
        return self._row_to_profile(rows[0])

    async def delete_user_profile_data(self, guild_id: int, user_id: int) -> ProfileDeletionResult:
        """Remove todos os dados persistidos da ficha do usuario neste servidor.

        Dados vivos como nome, avatar, XP, level e cargos nunca sao persistidos
        aqui; esta limpeza cobre apenas `profiles`, `profile_fields` e eventos
        de moderacao/auditoria associados ao par `(guild_id, user_id)`.
        """
        async with self.database.transaction() as conn:
            field_cur = await conn.execute(
                "DELETE FROM profile_fields WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            events_cur = await conn.execute(
                "DELETE FROM profile_moderation_events WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            profile_cur = await conn.execute(
                "DELETE FROM profiles WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
        return ProfileDeletionResult(
            guild_id=guild_id,
            user_id=user_id,
            profile_deleted=profile_cur.rowcount > 0,
            fields_deleted=max(0, field_cur.rowcount),
            moderation_events_deleted=max(0, events_cur.rowcount),
        )

    async def list_fields(self, guild_id: int, user_id: int) -> dict[str, ProfileFieldValue]:
        async with self.database.session() as conn:
            rows = await conn.execute_fetchall(
                """
                SELECT *
                FROM profile_fields
                WHERE guild_id = ? AND user_id = ?
                ORDER BY field_key ASC
                """,
                (guild_id, user_id),
            )
        return {str(row["field_key"]): self._row_to_field(row) for row in rows}

    async def get_field(self, guild_id: int, user_id: int, field_key: str) -> ProfileFieldValue | None:
        async with self.database.session() as conn:
            rows = await conn.execute_fetchall(
                """
                SELECT *
                FROM profile_fields
                WHERE guild_id = ? AND user_id = ? AND field_key = ?
                """,
                (guild_id, user_id, field_key),
            )
        return self._row_to_field(rows[0]) if rows else None

    async def find_presentation_basic_info_by_message_id(
        self,
        guild_id: int,
        message_id: int,
    ) -> ProfileFieldValue | None:
        async with self.database.session() as conn:
            rows = await conn.execute_fetchall(
                """
                SELECT *
                FROM profile_fields
                WHERE guild_id = ?
                  AND field_key = 'basic_info'
                  AND source_type = ?
                  AND source_message_ids LIKE ?
                """,
                (guild_id, ProfileFieldSourceType.PRESENTATION_CHANNEL.value, f"%{message_id}%"),
            )
        for row in rows:
            field = self._row_to_field(row)
            if message_id in field.source_message_ids:
                return field
        return None

    async def get_settings(self, guild_id: int) -> GuildProfileSettings:
        async with self.database.session() as conn:
            rows = await conn.execute_fetchall(
                "SELECT * FROM guild_profile_settings WHERE guild_id = ?",
                (guild_id,),
            )
        if not rows:
            return GuildProfileSettings(
                guild_id=guild_id,
                presentation_channel_id=None,
                presentation_mode=PresentationMode.MANUAL,
                auto_sync_enabled=False,
            )
        return self._row_to_settings(rows[0])

    async def update_settings(
        self,
        guild_id: int,
        *,
        presentation_channel_id: int | None = None,
        presentation_mode: PresentationMode | None = None,
        auto_sync_enabled: bool | None = None,
    ) -> GuildProfileSettings:
        now = utc_now_iso()
        current = await self.get_settings(guild_id)
        next_channel_id = presentation_channel_id if presentation_channel_id is not None else current.presentation_channel_id
        next_mode = presentation_mode or current.presentation_mode
        next_auto_sync = current.auto_sync_enabled if auto_sync_enabled is None else auto_sync_enabled
        async with self.database.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO guild_profile_settings(
                    guild_id,
                    presentation_channel_id,
                    presentation_mode,
                    auto_sync_enabled,
                    created_at,
                    updated_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    presentation_channel_id = excluded.presentation_channel_id,
                    presentation_mode = excluded.presentation_mode,
                    auto_sync_enabled = excluded.auto_sync_enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    guild_id,
                    next_channel_id,
                    next_mode.value,
                    int(next_auto_sync),
                    now,
                    now,
                ),
            )
        return await self.get_settings(guild_id)

    async def upsert_field(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        value: str,
        source_type: ProfileFieldSourceType,
        source_message_ids: tuple[int, ...],
        updated_by: int | None,
    ) -> ProfileFieldValue:
        now = utc_now_iso()
        async with self.database.transaction() as conn:
            await self._ensure_profile_in_conn(conn, guild_id, user_id, now)
            await conn.execute(
                """
                INSERT INTO profile_fields(
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
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                ON CONFLICT(guild_id, user_id, field_key) DO UPDATE SET
                    value = excluded.value,
                    status = excluded.status,
                    source_type = excluded.source_type,
                    source_message_ids = excluded.source_message_ids,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by,
                    moderated_by = NULL,
                    moderated_at = NULL,
                    moderation_reason = NULL
                """,
                (
                    guild_id,
                    user_id,
                    field_key,
                    value,
                    ProfileFieldStatus.ACTIVE.value,
                    source_type.value,
                    _dumps_json(list(source_message_ids)),
                    now,
                    updated_by,
                ),
            )
            await self._touch_profile_in_conn(conn, guild_id, user_id, now)
            return await self._get_field_in_conn(conn, guild_id, user_id, field_key)

    async def reset_field(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        actor_id: int,
        reason: str | None = None,
    ) -> bool:
        now = utc_now_iso()
        async with self.database.transaction() as conn:
            await self._ensure_profile_in_conn(conn, guild_id, user_id, now)
            cur = await conn.execute(
                "DELETE FROM profile_fields WHERE guild_id = ? AND user_id = ? AND field_key = ?",
                (guild_id, user_id, field_key),
            )
            removed = cur.rowcount > 0
            if removed:
                await self._touch_profile_in_conn(conn, guild_id, user_id, now)
                await self._insert_event_in_conn(
                    conn,
                    guild_id=guild_id,
                    user_id=user_id,
                    field_key=field_key,
                    action=ProfileModerationAction.RESET,
                    actor_id=actor_id,
                    reason=reason,
                    created_at=now,
                )
            return removed

    async def reset_profile_fields(
        self,
        *,
        guild_id: int,
        user_id: int,
        actor_id: int,
        reason: str | None = None,
    ) -> int:
        now = utc_now_iso()
        async with self.database.transaction() as conn:
            await self._ensure_profile_in_conn(conn, guild_id, user_id, now)
            cur = await conn.execute(
                "DELETE FROM profile_fields WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            removed_count = max(0, cur.rowcount)
            if removed_count:
                await self._touch_profile_in_conn(conn, guild_id, user_id, now)
            await self._insert_event_in_conn(
                conn,
                guild_id=guild_id,
                user_id=user_id,
                field_key="*",
                action=ProfileModerationAction.RESET_ALL,
                actor_id=actor_id,
                reason=reason,
                created_at=now,
            )
            return removed_count

    async def reset_fields(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_keys: tuple[str, ...],
        actor_id: int,
        action: ProfileModerationAction,
        reason: str | None = None,
    ) -> int:
        if not field_keys:
            return 0
        now = utc_now_iso()
        placeholders = ", ".join("?" for _ in field_keys)
        async with self.database.transaction() as conn:
            await self._ensure_profile_in_conn(conn, guild_id, user_id, now)
            cur = await conn.execute(
                f"""
                DELETE FROM profile_fields
                WHERE guild_id = ? AND user_id = ? AND field_key IN ({placeholders})
                """,
                (guild_id, user_id, *field_keys),
            )
            removed_count = max(0, cur.rowcount)
            if removed_count:
                await self._touch_profile_in_conn(conn, guild_id, user_id, now)
            await self._insert_event_in_conn(
                conn,
                guild_id=guild_id,
                user_id=user_id,
                field_key=",".join(field_keys),
                action=action,
                actor_id=actor_id,
                reason=reason,
                created_at=now,
            )
            return removed_count

    async def moderate_field(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        status: ProfileFieldStatus,
        actor_id: int,
        reason: str | None,
    ) -> bool:
        now = utc_now_iso()
        if status is ProfileFieldStatus.REMOVED_BY_MOD:
            action = ProfileModerationAction.REMOVE
        elif status is ProfileFieldStatus.REJECTED:
            action = ProfileModerationAction.REJECT
        else:
            action = ProfileModerationAction.HIDE
        async with self.database.transaction() as conn:
            await self._ensure_profile_in_conn(conn, guild_id, user_id, now)
            await conn.execute(
                """
                INSERT OR IGNORE INTO profile_fields(
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
                ) VALUES(?, ?, ?, '', ?, ?, '[]', ?, NULL, ?, ?, ?)
                """,
                (
                    guild_id,
                    user_id,
                    field_key,
                    status.value,
                    ProfileFieldSourceType.MODERATION.value,
                    now,
                    actor_id,
                    now,
                    reason,
                ),
            )
            cur = await conn.execute(
                """
                UPDATE profile_fields
                SET status = ?,
                    moderated_by = ?,
                    moderated_at = ?,
                    moderation_reason = ?,
                    updated_at = ?
                WHERE guild_id = ? AND user_id = ? AND field_key = ?
                """,
                (status.value, actor_id, now, reason, now, guild_id, user_id, field_key),
            )
            if cur.rowcount <= 0:
                return False
            await self._touch_profile_in_conn(conn, guild_id, user_id, now)
            await self._insert_event_in_conn(
                conn,
                guild_id=guild_id,
                user_id=user_id,
                field_key=field_key,
                action=action,
                actor_id=actor_id,
                reason=reason,
                created_at=now,
            )
            return True

    async def record_moderation_event(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        action: ProfileModerationAction,
        actor_id: int,
        reason: str | None,
    ) -> None:
        now = utc_now_iso()
        async with self.database.transaction() as conn:
            await self._ensure_profile_in_conn(conn, guild_id, user_id, now)
            await self._insert_event_in_conn(
                conn,
                guild_id=guild_id,
                user_id=user_id,
                field_key=field_key,
                action=action,
                actor_id=actor_id,
                reason=reason,
                created_at=now,
            )

    async def restore_field(
        self,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        actor_id: int,
        reason: str | None,
    ) -> bool:
        now = utc_now_iso()
        async with self.database.transaction() as conn:
            cur = await conn.execute(
                """
                UPDATE profile_fields
                SET status = ?,
                    moderated_by = NULL,
                    moderated_at = NULL,
                    moderation_reason = NULL,
                    updated_at = ?
                WHERE guild_id = ? AND user_id = ? AND field_key = ?
                """,
                (ProfileFieldStatus.ACTIVE.value, now, guild_id, user_id, field_key),
            )
            if cur.rowcount <= 0:
                return False
            await self._touch_profile_in_conn(conn, guild_id, user_id, now)
            await self._insert_event_in_conn(
                conn,
                guild_id=guild_id,
                user_id=user_id,
                field_key=field_key,
                action=ProfileModerationAction.RESTORE,
                actor_id=actor_id,
                reason=reason,
                created_at=now,
            )
            return True

    async def _ensure_profile_in_conn(
        self,
        conn: aiosqlite.Connection,
        guild_id: int,
        user_id: int,
        now: str,
    ) -> None:
        await conn.execute(
            """
            INSERT OR IGNORE INTO profiles(
                guild_id,
                user_id,
                created_at,
                updated_at,
                onboarding_completed,
                render_revision
            ) VALUES(?, ?, ?, ?, 0, 0)
            """,
            (guild_id, user_id, now, now),
        )

    async def _touch_profile_in_conn(
        self,
        conn: aiosqlite.Connection,
        guild_id: int,
        user_id: int,
        now: str,
    ) -> None:
        await conn.execute(
            """
            UPDATE profiles
            SET updated_at = ?,
                render_revision = render_revision + 1
            WHERE guild_id = ? AND user_id = ?
            """,
            (now, guild_id, user_id),
        )

    async def _get_profile_in_conn(self, conn: aiosqlite.Connection, guild_id: int, user_id: int) -> ProfileRecord:
        rows = await conn.execute_fetchall(
            "SELECT * FROM profiles WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        if not rows:
            raise RuntimeError("profile row was not created")
        return self._row_to_profile(rows[0])

    async def _get_field_in_conn(
        self,
        conn: aiosqlite.Connection,
        guild_id: int,
        user_id: int,
        field_key: str,
    ) -> ProfileFieldValue:
        rows = await conn.execute_fetchall(
            "SELECT * FROM profile_fields WHERE guild_id = ? AND user_id = ? AND field_key = ?",
            (guild_id, user_id, field_key),
        )
        if not rows:
            raise RuntimeError("profile field row was not created")
        return self._row_to_field(rows[0])

    async def _insert_event_in_conn(
        self,
        conn: aiosqlite.Connection,
        *,
        guild_id: int,
        user_id: int,
        field_key: str,
        action: ProfileModerationAction,
        actor_id: int,
        reason: str | None,
        created_at: str,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO profile_moderation_events(
                guild_id,
                user_id,
                field_key,
                action,
                actor_id,
                reason,
                created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, field_key, action.value, actor_id, reason, created_at),
        )

    def _row_to_profile(self, row: aiosqlite.Row) -> ProfileRecord:
        return ProfileRecord(
            guild_id=int(row["guild_id"]),
            user_id=int(row["user_id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            onboarding_completed=bool(row["onboarding_completed"]),
            render_revision=int(row["render_revision"]),
        )

    def _row_to_field(self, row: aiosqlite.Row) -> ProfileFieldValue:
        source_type = str(row["source_type"])
        if source_type == "user":
            source_type = ProfileFieldSourceType.MANUAL.value
        elif source_type == "auto_sync":
            source_type = ProfileFieldSourceType.PRESENTATION_CHANNEL.value
        return ProfileFieldValue(
            guild_id=int(row["guild_id"]),
            user_id=int(row["user_id"]),
            field_key=str(row["field_key"]),
            value=str(row["value"]),
            status=ProfileFieldStatus(str(row["status"])),
            source_type=ProfileFieldSourceType(source_type),
            source_message_ids=_loads_message_ids(row["source_message_ids"]),
            updated_at=str(row["updated_at"]),
            updated_by=int(row["updated_by"]) if row["updated_by"] is not None else None,
            moderated_by=int(row["moderated_by"]) if row["moderated_by"] is not None else None,
            moderated_at=str(row["moderated_at"]) if row["moderated_at"] is not None else None,
            moderation_reason=str(row["moderation_reason"]) if row["moderation_reason"] is not None else None,
        )

    def _row_to_settings(self, row: aiosqlite.Row) -> GuildProfileSettings:
        return GuildProfileSettings(
            guild_id=int(row["guild_id"]),
            presentation_channel_id=int(row["presentation_channel_id"]) if row["presentation_channel_id"] is not None else None,
            presentation_mode=PresentationMode(str(row["presentation_mode"])),
            auto_sync_enabled=bool(row["auto_sync_enabled"]),
        )
