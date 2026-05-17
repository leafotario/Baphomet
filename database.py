from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Final

import aiosqlite


DEFAULT_SCHEDULE_TIME: Final[str] = "18:00"
LIKE_EMOJI_COLUMN: Final[str] = "like_emoji"
DISLIKE_EMOJI_COLUMN: Final[str] = "dislike_emoji"
NEVER_WATCHED_EMOJI_COLUMN: Final[str] = "never_watched_emoji"
REACTION_EMOJI_COLUMNS: Final[tuple[str, str, str]] = (
    LIKE_EMOJI_COLUMN,
    DISLIKE_EMOJI_COLUMN,
    NEVER_WATCHED_EMOJI_COLUMN,
)
_UNSET: Final[object] = object()


@dataclass(frozen=True, slots=True)
class GuildConfig:
    guild_id: int
    channel_id: int | None
    role_id: int | None
    schedule_time: str = DEFAULT_SCHEDULE_TIME
    like_emoji: str | None = None
    dislike_emoji: str | None = None
    never_watched_emoji: str | None = None


@dataclass(frozen=True, slots=True)
class BlacklistEntry:
    guild_id: int
    tmdb_id: int
    movie_title: str | None
    date_added: str


class DatabaseManager:
    def __init__(self, db_path: str | Path = "data/filme_do_dia.sqlite3") -> None:
        self.db_path = Path(db_path)
        self._write_lock = asyncio.Lock()

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            await self._apply_pragmas(db)
            yield db

    @staticmethod
    async def _apply_pragmas(db: aiosqlite.Connection) -> None:
        async with db.execute("PRAGMA journal_mode=WAL;") as cursor:
            await cursor.fetchone()

        async with db.execute("PRAGMA synchronous=NORMAL;"):
            pass

        async with db.execute("PRAGMA busy_timeout=5000;"):
            pass

    async def init_db(self) -> None:
        try:
            async with self._connect() as db:
                async with db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS guild_configs (
                        guild_id INTEGER PRIMARY KEY,
                        channel_id INTEGER,
                        role_id INTEGER,
                        schedule_time TEXT NOT NULL DEFAULT '18:00',
                        like_emoji TEXT,
                        dislike_emoji TEXT,
                        never_watched_emoji TEXT
                    );
                    """
                ):
                    pass

                await self._ensure_guild_config_columns(db)

                async with db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS movie_blacklist (
                        guild_id INTEGER NOT NULL,
                        tmdb_id INTEGER NOT NULL,
                        movie_title TEXT,
                        date_added TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(guild_id, tmdb_id)
                    );
                    """
                ):
                    pass

                await db.commit()
        except Exception:
            logging.error("Falha ao inicializar o banco de dados.", exc_info=True)
            raise

    @staticmethod
    async def _ensure_guild_config_columns(db: aiosqlite.Connection) -> None:
        async with db.execute("PRAGMA table_info(guild_configs);") as cursor:
            rows = await cursor.fetchall()

        existing_columns = {str(row["name"]) for row in rows}
        for column_name in REACTION_EMOJI_COLUMNS:
            if column_name in existing_columns:
                continue

            async with db.execute(
                f"ALTER TABLE guild_configs ADD COLUMN {column_name} TEXT;"
            ):
                pass

    async def set_config(
        self,
        guild_id: int,
        channel_id: int | None,
        role_id: int | None,
        schedule_time: str = DEFAULT_SCHEDULE_TIME,
        *,
        like_emoji: str | None | object = _UNSET,
        dislike_emoji: str | None | object = _UNSET,
        never_watched_emoji: str | None | object = _UNSET,
    ) -> None:
        try:
            async with self._write_lock:
                async with self._connect() as db:
                    try:
                        async with db.execute("BEGIN IMMEDIATE;"):
                            pass

                        current_emojis: dict[str, str | None] = {}
                        if (
                            like_emoji is _UNSET
                            or dislike_emoji is _UNSET
                            or never_watched_emoji is _UNSET
                        ):
                            current_emojis = await self._fetch_current_emojis(
                                db,
                                guild_id,
                            )

                        next_like_emoji = self._resolve_emoji_value(
                            like_emoji,
                            current_emojis.get(LIKE_EMOJI_COLUMN),
                        )
                        next_dislike_emoji = self._resolve_emoji_value(
                            dislike_emoji,
                            current_emojis.get(DISLIKE_EMOJI_COLUMN),
                        )
                        next_never_watched_emoji = self._resolve_emoji_value(
                            never_watched_emoji,
                            current_emojis.get(NEVER_WATCHED_EMOJI_COLUMN),
                        )

                        async with db.execute(
                            """
                            INSERT INTO guild_configs (
                                guild_id,
                                channel_id,
                                role_id,
                                schedule_time,
                                like_emoji,
                                dislike_emoji,
                                never_watched_emoji
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(guild_id) DO UPDATE SET
                                channel_id = excluded.channel_id,
                                role_id = excluded.role_id,
                                schedule_time = excluded.schedule_time,
                                like_emoji = excluded.like_emoji,
                                dislike_emoji = excluded.dislike_emoji,
                                never_watched_emoji = excluded.never_watched_emoji;
                            """,
                            (
                                guild_id,
                                channel_id,
                                role_id,
                                schedule_time,
                                next_like_emoji,
                                next_dislike_emoji,
                                next_never_watched_emoji,
                            ),
                        ):
                            pass

                        await db.commit()
                    except Exception:
                        await db.rollback()
                        raise
        except Exception:
            logging.error(
                "Falha ao gravar configuracao da guild_id=%s.",
                guild_id,
                exc_info=True,
            )
            raise

    @staticmethod
    async def _fetch_current_emojis(
        db: aiosqlite.Connection,
        guild_id: int,
    ) -> dict[str, str | None]:
        async with db.execute(
            f"""
            SELECT
                {LIKE_EMOJI_COLUMN},
                {DISLIKE_EMOJI_COLUMN},
                {NEVER_WATCHED_EMOJI_COLUMN}
            FROM guild_configs
            WHERE guild_id = ?;
            """,
            (guild_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return {}

        return {
            LIKE_EMOJI_COLUMN: DatabaseManager._normalize_optional_text(
                row[LIKE_EMOJI_COLUMN]
            ),
            DISLIKE_EMOJI_COLUMN: DatabaseManager._normalize_optional_text(
                row[DISLIKE_EMOJI_COLUMN]
            ),
            NEVER_WATCHED_EMOJI_COLUMN: DatabaseManager._normalize_optional_text(
                row[NEVER_WATCHED_EMOJI_COLUMN]
            ),
        }

    @staticmethod
    def _resolve_emoji_value(value: object, current_value: str | None) -> str | None:
        if value is _UNSET:
            return current_value
        return DatabaseManager._normalize_optional_text(value)

    @staticmethod
    def _normalize_optional_text(value: object) -> str | None:
        if value is None:
            return None

        text = str(value).strip()
        return text or None

    async def get_config(self, guild_id: int) -> GuildConfig | None:
        try:
            async with self._connect() as db:
                async with db.execute(
                    """
                    SELECT
                        guild_id,
                        channel_id,
                        role_id,
                        COALESCE(schedule_time, ?) AS schedule_time,
                        like_emoji,
                        dislike_emoji,
                        never_watched_emoji
                    FROM guild_configs
                    WHERE guild_id = ?;
                    """,
                    (DEFAULT_SCHEDULE_TIME, guild_id),
                ) as cursor:
                    row = await cursor.fetchone()

            if row is None:
                return None

            return GuildConfig(
                guild_id=int(row["guild_id"]),
                channel_id=(
                    int(row["channel_id"])
                    if row["channel_id"] is not None
                    else None
                ),
                role_id=int(row["role_id"]) if row["role_id"] is not None else None,
                schedule_time=str(row["schedule_time"] or DEFAULT_SCHEDULE_TIME),
                like_emoji=self._normalize_optional_text(row["like_emoji"]),
                dislike_emoji=self._normalize_optional_text(row["dislike_emoji"]),
                never_watched_emoji=self._normalize_optional_text(
                    row["never_watched_emoji"]
                ),
            )
        except Exception:
            logging.error(
                "Falha ao buscar configuracao da guild_id=%s.",
                guild_id,
                exc_info=True,
            )
            raise

    async def add_to_blacklist(
        self,
        guild_id: int,
        tmdb_id: int,
        movie_title: str,
    ) -> bool:
        try:
            async with self._write_lock:
                async with self._connect() as db:
                    try:
                        async with db.execute("BEGIN IMMEDIATE;"):
                            pass

                        async with db.execute(
                            """
                            INSERT INTO movie_blacklist (
                                guild_id,
                                tmdb_id,
                                movie_title
                            )
                            VALUES (?, ?, ?)
                            ON CONFLICT(guild_id, tmdb_id) DO UPDATE SET
                                movie_title = excluded.movie_title;
                            """,
                            (guild_id, tmdb_id, movie_title),
                        ) as cursor:
                            changed_rows = cursor.rowcount

                        await db.commit()
                        return changed_rows > 0
                    except Exception:
                        await db.rollback()
                        raise
        except Exception:
            logging.error(
                "Falha ao adicionar tmdb_id=%s na blacklist da guild_id=%s.",
                tmdb_id,
                guild_id,
                exc_info=True,
            )
            raise

    async def remove_from_blacklist(self, guild_id: int, tmdb_id: int) -> bool:
        try:
            async with self._write_lock:
                async with self._connect() as db:
                    try:
                        async with db.execute("BEGIN IMMEDIATE;"):
                            pass

                        async with db.execute(
                            """
                            DELETE FROM movie_blacklist
                            WHERE guild_id = ?
                              AND tmdb_id = ?;
                            """,
                            (guild_id, tmdb_id),
                        ) as cursor:
                            changed_rows = cursor.rowcount

                        await db.commit()
                        return changed_rows > 0
                    except Exception:
                        await db.rollback()
                        raise
        except Exception:
            logging.error(
                "Falha ao remover tmdb_id=%s da blacklist da guild_id=%s.",
                tmdb_id,
                guild_id,
                exc_info=True,
            )
            raise

    async def is_blacklisted(self, guild_id: int, tmdb_id: int) -> bool:
        try:
            async with self._connect() as db:
                async with db.execute(
                    """
                    SELECT 1
                    FROM movie_blacklist
                    WHERE guild_id = ?
                      AND tmdb_id = ?
                    LIMIT 1;
                    """,
                    (guild_id, tmdb_id),
                ) as cursor:
                    row = await cursor.fetchone()

            return row is not None
        except Exception:
            logging.error(
                "Falha ao validar blacklist tmdb_id=%s guild_id=%s.",
                tmdb_id,
                guild_id,
                exc_info=True,
            )
            raise

    async def get_blacklist(self, guild_id: int) -> list[BlacklistEntry]:
        try:
            async with self._connect() as db:
                async with db.execute(
                    """
                    SELECT
                        guild_id,
                        tmdb_id,
                        movie_title,
                        date_added
                    FROM movie_blacklist
                    WHERE guild_id = ?
                    ORDER BY date_added DESC, tmdb_id ASC;
                    """,
                    (guild_id,),
                ) as cursor:
                    rows = await cursor.fetchall()

            return [
                BlacklistEntry(
                    guild_id=int(row["guild_id"]),
                    tmdb_id=int(row["tmdb_id"]),
                    movie_title=(
                        str(row["movie_title"])
                        if row["movie_title"] is not None
                        else None
                    ),
                    date_added=str(row["date_added"]),
                )
                for row in rows
            ]
        except Exception:
            logging.error(
                "Falha ao listar blacklist da guild_id=%s.",
                guild_id,
                exc_info=True,
            )
            raise


__all__ = [
    "BlacklistEntry",
    "DatabaseManager",
    "GuildConfig",
]
