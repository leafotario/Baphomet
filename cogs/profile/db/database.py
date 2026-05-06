from __future__ import annotations

import asyncio
import logging
import pathlib
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite

from .migrations import run_profile_migrations


LOGGER = logging.getLogger("baphomet.profile.database")


class ProfileDatabase:
    def __init__(self, db_path: str | pathlib.Path = "data/baphomet_profiles.sqlite3") -> None:
        self.db_path = pathlib.Path(db_path)

    async def run_migrations(self) -> None:
        async with self.session() as conn:
            await run_profile_migrations(conn)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[aiosqlite.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(self.db_path))
        conn.row_factory = aiosqlite.Row
        try:
            await self._apply_pragmas(conn)
            yield conn
        finally:
            await conn.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self.session() as conn:
            await self._begin_immediate_with_retry(conn)
            try:
                yield conn
            except Exception:
                await conn.rollback()
                raise
            else:
                await conn.commit()

    async def _apply_pragmas(self, conn: aiosqlite.Connection) -> None:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")

    async def _begin_immediate_with_retry(self, conn: aiosqlite.Connection) -> None:
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                await conn.execute("BEGIN IMMEDIATE")
                return
            except aiosqlite.OperationalError as exc:
                if not self._is_busy_error(exc) or attempt >= attempts:
                    if self._is_busy_error(exc):
                        LOGGER.error(
                            "profile_sqlite_busy_failure db_path=%s attempts=%s error=%s",
                            self.db_path,
                            attempt,
                            exc,
                        )
                    raise
                delay = 0.08 * attempt
                LOGGER.warning(
                    "profile_sqlite_busy_retry db_path=%s attempt=%s next_delay_seconds=%.2f",
                    self.db_path,
                    attempt,
                    delay,
                )
                await asyncio.sleep(delay)

    @staticmethod
    def _is_busy_error(exc: BaseException) -> bool:
        message = str(exc).casefold()
        return any(
            phrase in message
            for phrase in (
                "database is locked",
                "database is busy",
                "database table is locked",
                "database schema is locked",
            )
        )
