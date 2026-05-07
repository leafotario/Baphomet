from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

import discord

from cogs.tierlist_templates.assets import TierTemplateAssetStore
from cogs.tierlist_templates.downloads import DownloadedImage, SafeImageDownloader
from cogs.tierlist_templates.exceptions import AssetDownloadError, AssetValidationError, TemplateItemResolveError
from cogs.tierlist_wikipedia.wikipedia import WikipediaImageService, WikipediaUserError

from ..models import ItemSource, ItemSourceType, MAX_ITEM_TITLE_LENGTH, normalize_text


LOGGER = logging.getLogger("baphomet.iceberg.sources")
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024


class IcebergUserError(Exception):
    def __init__(self, user_message: str, *, code: str = "iceberg_error", detail: str | None = None) -> None:
        super().__init__(detail or user_message)
        self.user_message = user_message
        self.code = code
        self.detail = detail or user_message


@dataclass(frozen=True)
class ResolvedIcebergSource:
    source: ItemSource
    title: str


class TextItemProvider:
    source_type = ItemSourceType.TEXT

    async def resolve(self, *, title: str | None = None, value: str | None = None, **_: Any) -> ResolvedIcebergSource:
        text = normalize_text(value or title, max_length=MAX_ITEM_TITLE_LENGTH)
        if text is None:
            raise IcebergUserError("⚠️ Informe um texto para esse item.", code="text_empty")
        return ResolvedIcebergSource(
            source=ItemSource(type=ItemSourceType.TEXT, value=text, metadata={}),
            title=text,
        )


class ImageUrlProvider:
    source_type = ItemSourceType.IMAGE_URL

    def __init__(self, *, downloader: SafeImageDownloader, asset_store: TierTemplateAssetStore) -> None:
        self.downloader = downloader
        self.asset_store = asset_store

    async def resolve(self, *, title: str | None = None, value: str | None = None, **_: Any) -> ResolvedIcebergSource:
        image_url = normalize_text(value, max_length=500)
        if image_url is None:
            raise IcebergUserError("⚠️ Informe a URL da imagem.", code="image_url_empty")
        try:
            downloaded = await self.downloader.download(image_url)
            stored = await self.asset_store.store_image_bytes(
                downloaded.data,
                source_type=ItemSourceType.IMAGE_URL.value,
                metadata=self._download_metadata(downloaded),
            )
        except (AssetDownloadError, AssetValidationError, TemplateItemResolveError) as exc:
            user_message = getattr(exc, "user_message", None) or str(exc)
            code = getattr(exc, "code", "image_url_error")
            raise IcebergUserError(user_message, code=str(code), detail=str(exc)) from exc
        item_title = normalize_text(title, max_length=MAX_ITEM_TITLE_LENGTH) or self._title_from_url(downloaded.final_url)
        return ResolvedIcebergSource(
            source=ItemSource(
                type=ItemSourceType.IMAGE_URL,
                value=image_url,
                asset_id=stored.asset_id,
                metadata={
                    **self._download_metadata(downloaded),
                    **self._asset_metadata(stored),
                },
            ),
            title=item_title,
        )

    def _download_metadata(self, downloaded: DownloadedImage) -> dict[str, Any]:
        return {
            "source_url": downloaded.url,
            "final_url": downloaded.final_url,
            "content_type": downloaded.content_type,
        }

    def _asset_metadata(self, stored: Any) -> dict[str, Any]:
        return {
            "asset_hash": stored.asset_hash,
            "asset_storage_path": stored.storage_path,
            "asset_mime_type": stored.mime_type,
            "asset_width": stored.width,
            "asset_height": stored.height,
            "asset_size_bytes": stored.size_bytes,
        }

    def _title_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        filename = unquote(parsed.path.rsplit("/", 1)[-1] or "").strip()
        if filename:
            return normalize_text(filename.rsplit(".", 1)[0], max_length=MAX_ITEM_TITLE_LENGTH, fallback="Imagem") or "Imagem"
        return normalize_text(parsed.netloc, max_length=MAX_ITEM_TITLE_LENGTH, fallback="Imagem") or "Imagem"


class DiscordAvatarProvider(ImageUrlProvider):
    source_type = ItemSourceType.DISCORD_AVATAR

    async def resolve(
        self,
        *,
        title: str | None = None,
        value: str | None = None,
        client: Any | None = None,
        guild: Any | None = None,
        **_: Any,
    ) -> ResolvedIcebergSource:
        user_id = self._parse_user_id(value)
        user = await self._fetch_user(user_id, client=client, guild=guild)
        avatar_url = self._avatar_url(user)
        try:
            downloaded = await self.downloader.download(avatar_url)
            metadata = {
                **self._download_metadata(downloaded),
                "discord_user_id": user_id,
            }
            stored = await self.asset_store.store_image_bytes(
                downloaded.data,
                source_type=ItemSourceType.DISCORD_AVATAR.value,
                metadata=metadata,
            )
        except (AssetDownloadError, AssetValidationError, TemplateItemResolveError) as exc:
            user_message = getattr(exc, "user_message", None) or "Não consegui baixar o avatar desse usuário."
            code = getattr(exc, "code", "avatar_download_error")
            raise IcebergUserError(user_message, code=str(code), detail=str(exc)) from exc
        item_title = normalize_text(title, max_length=MAX_ITEM_TITLE_LENGTH) or normalize_text(str(user), max_length=MAX_ITEM_TITLE_LENGTH, fallback=f"Usuário {user_id}") or f"Usuário {user_id}"
        return ResolvedIcebergSource(
            source=ItemSource(
                type=ItemSourceType.DISCORD_AVATAR,
                value=str(user_id),
                asset_id=stored.asset_id,
                metadata={
                    **metadata,
                    **self._asset_metadata(stored),
                },
            ),
            title=item_title,
        )

    def _parse_user_id(self, value: object) -> int:
        raw = str(value or "").strip()
        match = re.fullmatch(r"<?@?!?(\d{15,25})>?", raw)
        if not match:
            raise IcebergUserError("⚠️ O ID de usuário informado não parece válido.", code="avatar_user_id_invalid")
        return int(match.group(1))

    async def _fetch_user(self, user_id: int, *, client: Any | None, guild: Any | None) -> Any:
        if guild is not None and hasattr(guild, "get_member"):
            member = guild.get_member(user_id)
            if member is not None:
                return member
        if client is not None and hasattr(client, "get_user"):
            user = client.get_user(user_id)
            if user is not None:
                return user
        if client is not None and hasattr(client, "fetch_user"):
            try:
                return await client.fetch_user(user_id)
            except (discord.HTTPException, ValueError, TypeError) as exc:
                raise IcebergUserError("⚠️ Não consegui encontrar esse usuário para usar o avatar.", code="avatar_user_not_found", detail=str(exc)) from exc
        raise IcebergUserError("⚠️ Não consegui resolver avatar sem acesso ao cliente do Discord.", code="avatar_client_missing")

    def _avatar_url(self, user: Any) -> str:
        avatar = getattr(user, "display_avatar", None) or getattr(user, "avatar", None)
        if avatar is None:
            raise IcebergUserError("⚠️ Esse usuário não possui avatar utilizável.", code="avatar_missing")
        try:
            return avatar.replace(format="png", size=512).url
        except TypeError:
            return avatar.replace(size=512).url
        except AttributeError:
            return str(avatar)


class WikipediaImageProvider(ImageUrlProvider):
    source_type = ItemSourceType.WIKIPEDIA

    def __init__(
        self,
        *,
        asset_store: TierTemplateAssetStore,
        wikipedia_service: WikipediaImageService,
    ) -> None:
        self.asset_store = asset_store
        self.wikipedia_service = wikipedia_service

    async def search_candidates(self, query: str, locale: str | None = None) -> list[dict[str, Any]]:
        normalized_query = normalize_text(query, max_length=100)
        if not normalized_query:
            return []

        # Prioritize locale from context, with fallback to pt and en
        requested_locales = [locale] if locale else []
        requested_locales.extend(["pt-BR", "pt", "en"])

        # Deduplicate to unique language codes to avoid redundant API calls
        unique_lang_codes = []
        for lang in requested_locales:
            if lang:
                lang_code = lang.split("-")[0]
                if lang_code not in unique_lang_codes:
                    unique_lang_codes.append(lang_code)

        for lang_code in unique_lang_codes:
            try:
                results = await self.wikipedia_service.search(normalized_query, language=lang_code)
                if results and results.candidates:
                    return [
                        {
                            "pageid": c.pageid,
                            "title": c.title,
                            "description": c.description,
                            "thumbnail_url": c.thumbnail_url,
                        }
                        for c in results.candidates
                    ]
            except WikipediaUserError:
                continue

        return []

    async def resolve(
        self,
        *,
        title: str | None = None,
        value: str | None = None,
        guild_id: int | None = None,
        user_id: int | None = None,
        candidate: Any | None = None,
        **_: Any,
    ) -> ResolvedIcebergSource:
        query = normalize_text(value, max_length=100)

        try:
            if candidate is not None:
                resolution = await self.wikipedia_service.resolve_candidate(
                    candidate,
                    guild_id=guild_id,
                    user_id=user_id,
                    term=query or "",
                )
            else:
                if query is None:
                    raise IcebergUserError("⚠️ Informe um termo para pesquisar na Wikipedia.", code="wiki_empty")
                resolution = await self.wikipedia_service.resolve(
                    query,
                    allow_ambiguous=False,
                    guild_id=guild_id,
                    user_id=user_id,
                )
        except WikipediaUserError as exc:
            raise IcebergUserError(exc.user_message, code=exc.code, detail=str(exc)) from exc

        item = resolution.item if not candidate else resolution
        if item is None:
            raise IcebergUserError("⚠️ Não consegui escolher uma imagem da Wikipedia para esse termo.", code="wiki_no_resolved_item")
        metadata = {
            "source_url": item.image_url,
            "wikipedia_url": item.wikipedia_url,
            "wikipedia_pageid": item.wikipedia_pageid,
            "wikipedia_title": item.wikipedia_title,
            "wiki_language": item.wiki_language,
            "wikimedia_file_title": item.wikimedia_file_title,
            "wikimedia_file_description_url": item.wikimedia_file_description_url,
            "image_mime": item.image_mime,
            "license_short_name": item.license_short_name,
            "license_url": item.license_url,
            "metadata_source": item.metadata_source,
        }
        try:
            stored = await self.asset_store.store_image_bytes(
                item.image_bytes,
                source_type=ItemSourceType.WIKIPEDIA.value,
                metadata=metadata,
            )
        except AssetValidationError as exc:
            raise IcebergUserError("⚠️ A imagem da Wikipedia foi encontrada, mas não passou pela validação local.", code=getattr(exc, "code", "wiki_asset_invalid"), detail=str(exc)) from exc
        item_title = normalize_text(title, max_length=MAX_ITEM_TITLE_LENGTH) or item.wikipedia_title or item.display_name
        return ResolvedIcebergSource(
            source=ItemSource(
                type=ItemSourceType.WIKIPEDIA,
                value=query,
                asset_id=stored.asset_id,
                metadata={
                    **metadata,
                    **self._asset_metadata(stored),
                },
            ),
            title=normalize_text(item_title, max_length=MAX_ITEM_TITLE_LENGTH, fallback="Wikipedia") or "Wikipedia",
        )


class AttachmentImageProvider(ImageUrlProvider):
    source_type = ItemSourceType.ATTACHMENT

    async def resolve(
        self,
        *,
        title: str | None = None,
        value: str | None = None,
        attachment: discord.Attachment | None = None,
        **_: Any,
    ) -> ResolvedIcebergSource:
        if attachment is None:
            raise IcebergUserError("⚠️ Envie um attachment de imagem para esse item.", code="attachment_missing")
        if attachment.size and attachment.size > MAX_ATTACHMENT_BYTES:
            raise IcebergUserError("⚠️ Esse attachment é grande demais para virar item.", code="attachment_too_large")
        content_type = (attachment.content_type or "").split(";", 1)[0].strip().lower()
        if content_type and not content_type.startswith("image/"):
            raise IcebergUserError("⚠️ Esse attachment não parece ser uma imagem.", code="attachment_not_image")
        try:
            raw = await attachment.read(use_cached=True)
            metadata = {
                "filename": attachment.filename,
                "content_type": content_type or "application/octet-stream",
                "discord_attachment_url": attachment.url,
            }
            stored = await self.asset_store.store_image_bytes(
                raw,
                source_type=ItemSourceType.ATTACHMENT.value,
                metadata=metadata,
            )
        except (discord.HTTPException, AssetValidationError) as exc:
            raise IcebergUserError("⚠️ Não consegui validar esse attachment como imagem.", code=getattr(exc, "code", "attachment_invalid"), detail=str(exc)) from exc
        item_title = normalize_text(title, max_length=MAX_ITEM_TITLE_LENGTH) or normalize_text(attachment.filename.rsplit(".", 1)[0], max_length=MAX_ITEM_TITLE_LENGTH, fallback="Imagem") or "Imagem"
        return ResolvedIcebergSource(
            source=ItemSource(
                type=ItemSourceType.ATTACHMENT,
                value=value or attachment.filename,
                asset_id=stored.asset_id,
                metadata={
                    **metadata,
                    **self._asset_metadata(stored),
                },
            ),
            title=item_title,
        )


class IcebergSourceProviderRegistry:
    def __init__(
        self,
        *,
        downloader: SafeImageDownloader,
        asset_store: TierTemplateAssetStore,
        wikipedia_service: WikipediaImageService,
    ) -> None:
        self.providers = {
            ItemSourceType.TEXT: TextItemProvider(),
            ItemSourceType.IMAGE_URL: ImageUrlProvider(downloader=downloader, asset_store=asset_store),
            ItemSourceType.DISCORD_AVATAR: DiscordAvatarProvider(downloader=downloader, asset_store=asset_store),
            ItemSourceType.WIKIPEDIA: WikipediaImageProvider(asset_store=asset_store, wikipedia_service=wikipedia_service),
            ItemSourceType.ATTACHMENT: AttachmentImageProvider(downloader=downloader, asset_store=asset_store),
        }
        self._cache: dict[tuple[ItemSourceType, Any], ResolvedIcebergSource] = {}

    async def resolve(self, source_type: ItemSourceType, **kwargs: Any) -> ResolvedIcebergSource:
        provider = self.providers.get(source_type)
        if provider is None:
            raise IcebergUserError("⚠️ Essa fonte de item ainda não é suportada.", code="source_unsupported")

        # Determine the cache key based on the primary input value
        cache_value = kwargs.get("value")
        if source_type == ItemSourceType.ATTACHMENT and "attachment" in kwargs:
            attachment = kwargs["attachment"]
            if attachment is not None:
                # Use attachment ID or URL as the unique identifier for caching
                cache_value = getattr(attachment, "id", getattr(attachment, "url", cache_value))

        cache_key = (source_type, cache_value)

        # Prevent caching if we cannot identify a consistent cache value
        if cache_value is not None and cache_key in self._cache:
            return self._cache[cache_key]

        resolved = await provider.resolve(**kwargs)

        if cache_value is not None:
            if len(self._cache) > 100:
                self._cache.pop(next(iter(self._cache)))
            self._cache[cache_key] = resolved

        return resolved
