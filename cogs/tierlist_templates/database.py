from __future__ import annotations

import asyncio
import logging
import pathlib
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite

from .migrations import run_tier_template_migrations


LOGGER = logging.getLogger("baphomet.tierlist_templates.database")


class DatabaseManager:
    def __init__(self, db_path: str | pathlib.Path = "data/tier_templates.sqlite3") -> None:
        self.db_path = pathlib.Path(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("DatabaseManager ainda não foi conectado.")
        return self._conn

    async def connect(self) -> None:
        if self._conn is not None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.db_path))
        self._conn.row_factory = aiosqlite.Row
        await self.apply_pragmas()
        await self.run_migrations()

    async def apply_pragmas(self) -> None:
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        await self.conn.execute("PRAGMA foreign_keys=ON")
        await self.conn.execute("PRAGMA busy_timeout=5000")

    async def run_migrations(self) -> None:
        await run_tier_template_migrations(self.conn)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

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

    async def _begin_immediate_with_retry(self) -> None:
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                await self.conn.execute("BEGIN IMMEDIATE")
                return
            except aiosqlite.OperationalError as exc:
                if not self._is_busy_error(exc) or attempt >= attempts:
                    if self._is_busy_error(exc):
                        LOGGER.error(
                            "sqlite_busy_failure db_path=%s attempts=%s error=%s",
                            self.db_path,
                            attempt,
                            exc,
                        )
                    raise
                delay = 0.08 * attempt
                LOGGER.warning(
                    "sqlite_busy_retry db_path=%s attempt=%s next_delay_seconds=%.2f",
                    self.db_path,
                    attempt,
                    delay,
                )
                await asyncio.sleep(delay)

    @asynccontextmanager
    async def immediate_transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._write_lock:
            await self._begin_immediate_with_retry()
            try:
                yield self.conn
            except Exception:
                await self.conn.rollback()
                raise
            else:
                await self.conn.commit()
