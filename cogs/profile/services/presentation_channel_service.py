from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import discord

from ..models import PresentationMode, ProfileFieldSourceType
from .profile_service import ProfileService, ProfileValidationError


LOGGER = logging.getLogger("baphomet.profile.presentation")

DEFAULT_BURST_WINDOW_SECONDS = 90
DEFAULT_DEBOUNCE_SECONDS = 2.0


@dataclass(slots=True)
class PresentationSourceMessage:
    guild_id: int
    user_id: int
    channel_id: int
    message_id: int
    created_at_ts: float
    content: str


@dataclass(slots=True)
class PendingPresentationBlock:
    guild_id: int
    user_id: int
    channel_id: int
    message_ids: list[int] = field(default_factory=list)


class PresentationChannelService:
    def __init__(
        self,
        profile_service: ProfileService,
        *,
        burst_window_seconds: int = DEFAULT_BURST_WINDOW_SECONDS,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    ) -> None:
        self.profile_service = profile_service
        self.burst_window_seconds = burst_window_seconds
        self.debounce_seconds = debounce_seconds
        self._message_cache: dict[int, PresentationSourceMessage] = {}
        self._pending: dict[tuple[int, int], PendingPresentationBlock] = {}
        self._tasks: dict[tuple[int, int], asyncio.Task[None]] = {}
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}

    async def set_presentation_channel(self, guild_id: int, channel_id: int) -> None:
        await self.profile_service.repository.update_settings(
            guild_id,
            presentation_channel_id=channel_id,
            presentation_mode=PresentationMode.MANUAL,
            auto_sync_enabled=True,
        )

    async def process_message(self, message: discord.Message) -> bool:
        if not await self._is_configured_presentation_message(message):
            return False
        source = self._source_from_message(message)
        if source is None:
            return False
        async with self._lock_for(source.guild_id, source.user_id):
            self._message_cache[source.message_id] = source
            message_ids = await self._message_ids_for_new_source(source, message.channel)
            await self._queue_block(source.guild_id, source.user_id, source.channel_id, message_ids)
        return True

    async def process_message_edit(self, before: discord.Message, after: discord.Message) -> bool:
        if not self._basic_message_checks(after):
            return False
        guild_id = after.guild.id
        user_id = after.author.id
        source = self._source_from_message(after)
        tracked_ids = await self._tracked_source_ids(guild_id, user_id, after.id)
        if not tracked_ids:
            return False

        async with self._lock_for(guild_id, user_id):
            if source is None:
                self._message_cache.pop(after.id, None)
                tracked_ids = [message_id for message_id in tracked_ids if message_id != after.id]
            else:
                self._message_cache[after.id] = source
            await self._load_sources(guild_id, user_id, after.channel.id, tracked_ids, after.channel)
            await self._queue_block(guild_id, user_id, after.channel.id, tracked_ids)
        return True

    async def process_message_delete(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False
        cached = self._message_cache.pop(message.id, None)
        guild_id = message.guild.id
        user_id = cached.user_id if cached else getattr(getattr(message, "author", None), "id", None)
        field = None
        if user_id is None:
            field = await self.profile_service.repository.find_presentation_basic_info_by_message_id(guild_id, message.id)
            if field is None:
                return False
            user_id = field.user_id
        tracked_ids = await self._tracked_source_ids(guild_id, int(user_id), message.id)
        if not tracked_ids and field is None:
            field = await self.profile_service.repository.find_presentation_basic_info_by_message_id(guild_id, message.id)
            if field is None:
                return False
            tracked_ids = list(field.source_message_ids)
            user_id = field.user_id
        if message.id not in tracked_ids:
            return False

        remaining_ids = [message_id for message_id in tracked_ids if message_id != message.id]
        async with self._lock_for(guild_id, int(user_id)):
            await self._load_sources(guild_id, int(user_id), message.channel.id, remaining_ids, message.channel)
            await self._queue_block(guild_id, int(user_id), message.channel.id, remaining_ids)
        return True

    async def use_message_as_basic_info(self, message: discord.Message, actor_id: int | None = None) -> bool:
        if not self._basic_message_checks(message):
            return False
        source = self._source_from_message(message)
        if source is None:
            return False
        async with self._lock_for(source.guild_id, source.user_id):
            self._message_cache[source.message_id] = source
            await self._write_block(
                guild_id=source.guild_id,
                user_id=source.user_id,
                channel_id=source.channel_id,
                message_ids=[source.message_id],
                updated_by=actor_id or source.user_id,
            )
        return True

    async def flush_pending(self, guild_id: int, user_id: int) -> None:
        key = (guild_id, user_id)
        task = self._tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
        block = self._pending.pop(key, None)
        if block is None:
            return
        async with self._lock_for(guild_id, user_id):
            await self._write_block(
                guild_id=block.guild_id,
                user_id=block.user_id,
                channel_id=block.channel_id,
                message_ids=block.message_ids,
                updated_by=block.user_id,
            )

    async def _queue_block(self, guild_id: int, user_id: int, channel_id: int, message_ids: list[int]) -> None:
        key = (guild_id, user_id)
        self._pending[key] = PendingPresentationBlock(
            guild_id=guild_id,
            user_id=user_id,
            channel_id=channel_id,
            message_ids=self._unique_ids(message_ids),
        )
        task = self._tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
        if self.debounce_seconds <= 0:
            block = self._pending.pop(key, None)
            if block is not None:
                await self._write_block(
                    guild_id=block.guild_id,
                    user_id=block.user_id,
                    channel_id=block.channel_id,
                    message_ids=block.message_ids,
                    updated_by=block.user_id,
                )
            return
        self._tasks[key] = asyncio.create_task(self._debounced_flush(guild_id, user_id))

    async def _debounced_flush(self, guild_id: int, user_id: int) -> None:
        try:
            await asyncio.sleep(self.debounce_seconds)
            await self.flush_pending(guild_id, user_id)
        except asyncio.CancelledError:
            return

    async def _write_block(
        self,
        *,
        guild_id: int,
        user_id: int,
        channel_id: int,
        message_ids: list[int],
        updated_by: int,
    ) -> None:
        sources = [source for source in (self._message_cache.get(message_id) for message_id in message_ids) if source is not None]
        sources = [
            source
            for source in sources
            if source.guild_id == guild_id and source.user_id == user_id and source.channel_id == channel_id and source.content
        ]
        sources.sort(key=lambda source: (source.created_at_ts, source.message_id))
        if not sources:
            current = await self.profile_service.repository.get_field(guild_id, user_id, "basic_info")
            if current is not None and current.source_type is ProfileFieldSourceType.PRESENTATION_CHANNEL:
                await self.profile_service.reset_field(
                    guild_id=guild_id,
                    user_id=user_id,
                    field_key="basic_info",
                    actor_id=updated_by,
                    reason="todas as mensagens de apresentacao foram apagadas",
                )
            return

        content = "\n\n".join(source.content for source in sources)
        await self.profile_service.set_field(
            guild_id=guild_id,
            user_id=user_id,
            field_key="basic_info",
            value=content,
            updated_by=updated_by,
            source_type=ProfileFieldSourceType.PRESENTATION_CHANNEL,
            source_message_ids=tuple(source.message_id for source in sources),
        )

    async def _message_ids_for_new_source(self, source: PresentationSourceMessage, channel: Any) -> list[int]:
        ids = [source.message_id]
        current = await self.profile_service.repository.get_field(source.guild_id, source.user_id, "basic_info")
        if current is not None and current.source_type is ProfileFieldSourceType.PRESENTATION_CHANNEL:
            existing_sources = await self._load_sources(source.guild_id, source.user_id, source.channel_id, current.source_message_ids, channel)
            if existing_sources and source.created_at_ts - max(item.created_at_ts for item in existing_sources) <= self.burst_window_seconds:
                ids = [item.message_id for item in existing_sources] + ids

        pending = self._pending.get((source.guild_id, source.user_id))
        if pending is not None and pending.channel_id == source.channel_id:
            pending_sources = await self._load_sources(source.guild_id, source.user_id, source.channel_id, pending.message_ids, channel)
            if pending_sources and source.created_at_ts - max(item.created_at_ts for item in pending_sources) <= self.burst_window_seconds:
                ids = [item.message_id for item in pending_sources] + ids
        return self._unique_ids(ids)

    async def _load_sources(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        message_ids: tuple[int, ...] | list[int],
        channel: Any,
    ) -> list[PresentationSourceMessage]:
        sources: list[PresentationSourceMessage] = []
        for message_id in message_ids:
            cached = self._message_cache.get(int(message_id))
            if cached is not None:
                sources.append(cached)
                continue
            fetched = await self._fetch_message(channel, int(message_id))
            if fetched is None:
                continue
            source = self._source_from_message(fetched)
            if source is None:
                continue
            if source.guild_id != guild_id or source.user_id != user_id or source.channel_id != channel_id:
                continue
            self._message_cache[source.message_id] = source
            sources.append(source)
        return sources

    async def _tracked_source_ids(self, guild_id: int, user_id: int, message_id: int) -> list[int]:
        ids: list[int] = []
        pending = self._pending.get((guild_id, user_id))
        if pending is not None and message_id in pending.message_ids:
            ids.extend(pending.message_ids)
        current = await self.profile_service.repository.get_field(guild_id, user_id, "basic_info")
        if (
            current is not None
            and current.source_type is ProfileFieldSourceType.PRESENTATION_CHANNEL
            and message_id in current.source_message_ids
        ):
            ids.extend(current.source_message_ids)
        if ids:
            return self._unique_ids(ids)

        field = await self.profile_service.repository.find_presentation_basic_info_by_message_id(guild_id, message_id)
        if field is not None and field.user_id == user_id:
            return list(field.source_message_ids)
        return []

    async def _is_configured_presentation_message(self, message: discord.Message) -> bool:
        if not self._basic_message_checks(message):
            return False
        settings = await self.profile_service.repository.get_settings(message.guild.id)
        return bool(
            settings.auto_sync_enabled
            and settings.presentation_channel_id is not None
            and settings.presentation_channel_id == message.channel.id
        )

    def _basic_message_checks(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False
        if getattr(message, "webhook_id", None) is not None:
            return False
        author = getattr(message, "author", None)
        if author is None or getattr(author, "bot", False):
            return False
        if getattr(message, "type", discord.MessageType.default) is not discord.MessageType.default:
            return False
        return True

    def _source_from_message(self, message: discord.Message) -> PresentationSourceMessage | None:
        raw_content = str(getattr(message, "content", "") or "")
        try:
            content = self.profile_service.normalize_field_value("basic_info", raw_content)
        except ProfileValidationError:
            return None
        return PresentationSourceMessage(
            guild_id=message.guild.id,
            user_id=message.author.id,
            channel_id=message.channel.id,
            message_id=message.id,
            created_at_ts=self._timestamp(getattr(message, "created_at", None)),
            content=content,
        )

    async def _fetch_message(self, channel: Any, message_id: int) -> discord.Message | None:
        fetch = getattr(channel, "fetch_message", None)
        if fetch is None:
            return None
        try:
            return await fetch(message_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return None

    def _lock_for(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return self._locks.setdefault((guild_id, user_id), asyncio.Lock())

    def _timestamp(self, value: object) -> float:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.timestamp()
        return datetime.now(timezone.utc).timestamp()

    def _unique_ids(self, message_ids: list[int] | tuple[int, ...]) -> list[int]:
        seen: set[int] = set()
        unique: list[int] = []
        for message_id in message_ids:
            normalized = int(message_id)
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append(normalized)
        return unique
