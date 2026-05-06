from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from typing import OrderedDict as OrderedDictType

from cogs.tierlist import TierItem, TierListRenderer
from cogs.tierlist_wikipedia.wikipedia import WIKIPEDIA_SOURCE_TYPE

from .asset_repository import TierAssetRepository
from .assets import TierTemplateAssetStore
from .models import (
    TemplateItemType,
    TemplateSourceType,
    TierSession,
    TierSessionItem,
    TierTemplate,
    TierTemplateItem,
    TierTemplateVersion,
)
from .session_repository import TierSessionRepository
from .template_repository import TierTemplateRepository


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionRenderSnapshot:
    template: TierTemplate
    version: TierTemplateVersion
    session: TierSession
    session_items: list[TierSessionItem]
    template_items_by_id: dict[str, TierTemplateItem]
    tiers: list[dict[str, Any]]


class TierSessionRenderer:
    def __init__(
        self,
        *,
        template_repository: TierTemplateRepository,
        session_repository: TierSessionRepository,
        asset_repository: TierAssetRepository,
        asset_store: TierTemplateAssetStore,
        renderer: Any | None = None,
    ) -> None:
        self.template_repository = template_repository
        self.session_repository = session_repository
        self.asset_repository = asset_repository
        self.asset_store = asset_store
        self.renderer = renderer or TierListRenderer()

    async def build_snapshot(self, session_id: str) -> SessionRenderSnapshot:
        session = await self.session_repository.get_session(session_id)
        if session is None:
            raise ValueError("Sessão não encontrada.")
        version = await self.template_repository.get_template_version(session.template_version_id)
        if version is None:
            raise ValueError("Versão do template não encontrada.")
        template = await self.template_repository.get_template_by_id(version.template_id)
        if template is None:
            raise ValueError("Template não encontrado.")
        template_items = await self.template_repository.list_template_items(version.id)
        session_items = await self.session_repository.list_session_items(session.id)
        tiers = self._tiers(version.default_tiers_json)
        return SessionRenderSnapshot(
            template=template,
            version=version,
            session=session,
            session_items=session_items,
            template_items_by_id={item.id: item for item in template_items},
            tiers=tiers,
        )

    async def render_session(
        self,
        session_id: str,
        *,
        author: object | None = None,
        guild_icon_bytes: bytes | None = None,
    ) -> io.BytesIO:
        snapshot = await self.build_snapshot(session_id)
        return await self.render_session_snapshot(
            snapshot,
            author=author,
            guild_icon_bytes=guild_icon_bytes,
        )

    async def render_session_snapshot(
        self,
        snapshot: SessionRenderSnapshot,
        *,
        author: object | None = None,
        guild_icon_bytes: bytes | None = None,
    ) -> io.BytesIO:
        allocated = [
            item
            for item in snapshot.session_items
            if not item.is_unused and item.current_tier_id
        ]
        asset_bytes = await self._load_asset_bytes(snapshot, allocated)
        tiers_dict, tier_colors = self.session_snapshot_to_tier_items(snapshot, asset_bytes)
        return await asyncio.to_thread(
            self.renderer.generate_tierlist_image,
            snapshot.template.name,
            tiers_dict,
            author=author,
            guild_icon_bytes=guild_icon_bytes,
            tier_colors=tier_colors,
        )

    def session_snapshot_to_tier_items(
        self,
        snapshot: SessionRenderSnapshot,
        asset_bytes: dict[str, bytes],
    ) -> tuple[OrderedDictType[str, list[TierItem]], dict[str, tuple[int, int, int]]]:
        tiers_dict, tier_colors, tier_names_by_id = self._template_tiers_for_renderer(snapshot.tiers)

        allocated = sorted(
            (
                item
                for item in snapshot.session_items
                if not item.is_unused and item.current_tier_id
            ),
            key=lambda item: (item.current_tier_id or "", item.position, item.created_at),
        )
        for session_item in allocated:
            tier_name = tier_names_by_id.get(str(session_item.current_tier_id))
            if tier_name is None:
                LOGGER.warning(
                    "Session item com tier desconhecida ignorado no render session_id=%s session_item_id=%s tier_id=%s.",
                    snapshot.session.id,
                    session_item.id,
                    session_item.current_tier_id,
                )
                continue

            template_item = snapshot.template_items_by_id.get(session_item.template_item_id)
            if template_item is None:
                LOGGER.warning(
                    "Template item ausente no render session_id=%s session_item_id=%s template_item_id=%s.",
                    snapshot.session.id,
                    session_item.id,
                    session_item.template_item_id,
                )
                continue

            tier_item = self._session_item_to_tier_item(
                template_item,
                session_item,
                asset_bytes.get(session_item.id),
            )
            if tier_item is not None:
                tiers_dict[tier_name].append(tier_item)

        return tiers_dict, tier_colors

    def _session_item_to_tier_item(
        self,
        template_item: TierTemplateItem,
        session_item: TierSessionItem,
        image_bytes: bytes | None,
    ) -> TierItem | None:
        if template_item.item_type is TemplateItemType.TEXT_ONLY:
            caption = self._safe_text(template_item.render_caption)
            if caption is None:
                return None
            return TierItem(
                name=caption,
                source_type="text",
                caption=caption,
                user_caption=caption,
                render_caption=caption,
                has_visible_caption=True,
                internal_title=template_item.internal_title,
                source_query=template_item.source_query,
            )

        if template_item.item_type is not TemplateItemType.IMAGE:
            LOGGER.warning(
                "Tipo de item de template ignorado no render session_item_id=%s template_item_id=%s item_type=%s.",
                session_item.id,
                template_item.id,
                template_item.item_type,
            )
            return None

        caption = self._safe_text(template_item.render_caption) if template_item.has_visible_caption else None
        metadata = template_item.metadata or {}
        return TierItem(
            name=caption or "",
            image_bytes=image_bytes,
            source_type=self._canonical_source_type(template_item),
            caption=caption,
            user_caption=caption,
            render_caption=caption,
            has_visible_caption=bool(caption),
            internal_title=template_item.internal_title,
            source_query=template_item.source_query,
            image_cache_key=self._safe_text(metadata.get("asset_hash")) or template_item.asset_id,
            spotify_type=self._safe_text(metadata.get("spotify_type")),
            spotify_id=self._safe_text(metadata.get("spotify_id")),
            spotify_url=self._safe_text(metadata.get("spotify_url")),
            spotify_name=self._safe_text(metadata.get("spotify_name")),
            spotify_artists=self._metadata_artists(metadata.get("artists")),
            album_name=self._safe_text(metadata.get("album_name")),
            track_name=self._safe_text(metadata.get("track_name")),
            release_date=self._safe_text(metadata.get("release_date")),
            wiki_language=self._safe_text(metadata.get("wiki_language")),
            wikipedia_pageid=self._int_or_none(metadata.get("wikipedia_pageid")),
            wikipedia_title=self._safe_text(metadata.get("wikipedia_title")),
            wikipedia_url=self._safe_text(metadata.get("wikipedia_url")),
            wikimedia_file_title=self._safe_text(metadata.get("wikimedia_file_title")),
            wikimedia_file_description_url=self._safe_text(metadata.get("wikimedia_file_description_url")),
            image_mime=self._safe_text(metadata.get("image_mime") or metadata.get("asset_mime_type")),
            license_short_name=self._safe_text(metadata.get("license_short_name")),
            license_url=self._safe_text(metadata.get("license_url")),
            metadata_source=self._safe_text(metadata.get("metadata_source")),
        )

    async def _load_asset_bytes(
        self,
        snapshot: SessionRenderSnapshot,
        session_items: list[TierSessionItem],
    ) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for session_item in session_items:
            template_item = snapshot.template_items_by_id.get(session_item.template_item_id)
            if template_item is None or template_item.item_type is not TemplateItemType.IMAGE:
                continue
            if not template_item.asset_id:
                LOGGER.warning(
                    "Item de imagem sem asset_id no render session_id=%s session_item_id=%s template_item_id=%s.",
                    snapshot.session.id,
                    session_item.id,
                    session_item.template_item_id,
                )
                continue
            asset = await self.asset_repository.get_asset(template_item.asset_id)
            if asset is None:
                LOGGER.warning(
                    "asset_missing surface=session_render session_id=%s session_item_id=%s asset_id=%s reason=db_row_missing",
                    snapshot.session.id,
                    session_item.id,
                    template_item.asset_id,
                )
                continue
            try:
                result[session_item.id] = await self.asset_store.load_asset_bytes(asset)
            except (OSError, ValueError):
                LOGGER.exception(
                    "asset_missing surface=session_render session_id=%s session_item_id=%s asset_id=%s reason=file_unavailable",
                    snapshot.session.id,
                    session_item.id,
                    asset.id,
                )
        return result

    def _template_tiers_for_renderer(
        self,
        tiers: list[dict[str, Any]],
    ) -> tuple[
        OrderedDictType[str, list[TierItem]],
        dict[str, tuple[int, int, int]],
        dict[str, str],
    ]:
        tiers_dict: OrderedDictType[str, list[TierItem]] = OrderedDict()
        tier_colors: dict[str, tuple[int, int, int]] = {}
        tier_names_by_id: dict[str, str] = {}

        for index, tier in enumerate(tiers):
            tier_id = self._safe_tier_text(tier.get("id")) or self._safe_tier_text(tier.get("label")) or "?"
            label = self._safe_tier_text(tier.get("label")) or tier_id
            tier_name = self._unique_tier_name(label, tier_id, index, tiers_dict)
            tiers_dict[tier_name] = []
            tier_names_by_id.setdefault(tier_id, tier_name)

            color = self._hex_to_rgb(str(tier.get("color") or ""))
            if color is not None:
                tier_colors[tier_name] = color

        if not tiers_dict:
            tiers_dict["S"] = []
            tier_names_by_id["S"] = "S"

        return tiers_dict, tier_colors, tier_names_by_id

    def _unique_tier_name(
        self,
        label: str,
        tier_id: str,
        index: int,
        tiers_dict: OrderedDictType[str, list[TierItem]],
    ) -> str:
        if label not in tiers_dict:
            return label
        if tier_id not in tiers_dict:
            return tier_id
        suffix = index + 1
        while f"{label} {suffix}" in tiers_dict:
            suffix += 1
        return f"{label} {suffix}"

    def _canonical_source_type(self, item: TierTemplateItem) -> str:
        source_type = str(item.source_type or "").strip().upper()
        if source_type == TemplateSourceType.SPOTIFY.value:
            return "spotify"
        if source_type == TemplateSourceType.WIKIPEDIA.value:
            return WIKIPEDIA_SOURCE_TYPE
        if item.item_type is TemplateItemType.TEXT_ONLY:
            return "text"
        return "image"

    def _tiers(self, raw_json: str) -> list[dict[str, Any]]:
        try:
            tiers = json.loads(raw_json)
        except json.JSONDecodeError:
            return []
        if not isinstance(tiers, list):
            return []
        return [tier for tier in tiers if isinstance(tier, dict)]

    def _hex_to_rgb(self, value: str) -> tuple[int, int, int] | None:
        cleaned = value.strip().lstrip("#")
        if len(cleaned) != 6:
            return None
        try:
            return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))
        except ValueError:
            return None

    def _safe_text(self, text: object, *, max_length: int = 80) -> str | None:
        if text is None:
            return None
        value = re.sub(r"\s+", " ", str(text)).strip()
        if not value or value.casefold() in {"none", "null"}:
            return None
        return value[:max_length].strip() or None

    def _safe_tier_text(self, text: object, *, max_length: int = 20) -> str | None:
        return self._safe_text(text, max_length=max_length)

    def _metadata_artists(self, value: object) -> tuple[str, ...]:
        if isinstance(value, (list, tuple)):
            return tuple(artist for artist in (self._safe_text(item) for item in value) if artist)
        artist = self._safe_text(value)
        return (artist,) if artist else tuple()

    def _int_or_none(self, value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
