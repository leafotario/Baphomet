from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

from .assets import StoredTemplateAsset, TierTemplateAssetStore
from .downloads import DownloadedImage, SafeImageDownloader
from .exceptions import (
    AssetDownloadError,
    ConflictingImageSourcesError,
    EmptyTemplateItemError,
    TemplateItemResolveError,
    UnsafeWikipediaImageError,
)
from .migrations import dumps_json
from .models import TemplateItemType, TemplateSourceType


LOGGER = logging.getLogger("baphomet.tierlist_templates.item_resolver")

CONFLICTING_IMAGE_SOURCES_MESSAGE = (
    "⚠️ Honra e proveito não cabem no mesmo saco estreito.\n\n"
    "Você preencheu mais de uma fonte de imagem ao mesmo tempo. Eu preciso saber qual imagem usar: "
    "avatar de usuário, link direto, Wikipedia, Spotify ou outra fonte — mas não tudo junto no mesmo item.\n\n"
    "Escolha só uma fonte de imagem e tente de novo."
)


@dataclass(frozen=True)
class FilledImageSource:
    key: str
    label: str
    value: str


@dataclass(frozen=True)
class ResolvedTemplateItem:
    item_type: TemplateItemType
    source_type: str
    user_caption: str | None
    render_caption: str | None
    has_visible_caption: bool
    internal_title: str | None
    source_query: str | None
    asset_id: str | None
    metadata: dict[str, Any]

    @property
    def metadata_json(self) -> str:
        return dumps_json(self.metadata)

    def to_repository_kwargs(self) -> dict[str, Any]:
        return {
            "item_type": self.item_type,
            "source_type": self.source_type,
            "asset_id": self.asset_id,
            "user_caption": self.user_caption,
            "render_caption": self.render_caption,
            "internal_title": self.internal_title,
            "source_query": self.source_query,
            "metadata": self.metadata,
        }


def normalize_caption(value: object) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    if text.casefold() in {"none", "null"}:
        return None
    return text


def get_filled_image_sources(
    *,
    image_url: str | None = None,
    discord_user_id: str | int | None = None,
    avatar_user_id: str | int | None = None,
    wikipedia_query: str | None = None,
    spotify_input: str | None = None,
    extra_sources: dict[str, str | None] | None = None,
) -> list[FilledImageSource]:
    fields: list[tuple[str, str, Any]] = [
        ("image_url", "link direto", image_url),
        ("avatar_user_id", "avatar de usuário", avatar_user_id if avatar_user_id is not None else discord_user_id),
        ("wikipedia_query", "Wikipedia", wikipedia_query),
        ("spotify_input", "Spotify", spotify_input),
    ]
    if extra_sources:
        fields.extend((key, key, value) for key, value in extra_sources.items())
    sources: list[FilledImageSource] = []
    for key, label, value in fields:
        clean = normalize_caption(value)
        if clean is not None:
            sources.append(FilledImageSource(key=key, label=label, value=clean))
    return sources


class TierTemplateItemResolver:
    def __init__(
        self,
        *,
        asset_store: TierTemplateAssetStore,
        downloader: SafeImageDownloader | None = None,
        wikipedia_service: Any | None = None,
        spotify_resolver: Any | None = None,
        spotify_service: Any | None = None,
    ) -> None:
        self.asset_store = asset_store
        self.downloader = downloader or SafeImageDownloader()
        self.wikipedia_service = wikipedia_service
        self.spotify_resolver = spotify_resolver
        self.spotify_service = spotify_service

    async def resolve_item(
        self,
        *,
        user_caption_raw: object | None = None,
        text_value: object | None = None,
        image_url: str | None = None,
        discord_user_id: str | int | None = None,
        avatar_user_id: str | int | None = None,
        wikipedia_query: str | None = None,
        spotify_input: str | None = None,
        client: Any | None = None,
        bot: Any | None = None,
        interaction: Any | None = None,
        guild: Any | None = None,
        guild_id: int | None = None,
        user_id: int | None = None,
    ) -> ResolvedTemplateItem:
        del text_value
        user_caption = normalize_caption(user_caption_raw)
        filled_sources = get_filled_image_sources(
            image_url=image_url,
            discord_user_id=discord_user_id,
            avatar_user_id=avatar_user_id,
            wikipedia_query=wikipedia_query,
            spotify_input=spotify_input,
        )
        if len(filled_sources) > 1:
            raise ConflictingImageSourcesError(
                CONFLICTING_IMAGE_SOURCES_MESSAGE,
                detail=f"Fontes conflitantes: {[source.key for source in filled_sources]}",
                code="conflicting_image_sources",
            )
        if not filled_sources:
            if user_caption is None:
                raise EmptyTemplateItemError(
                    "⚠️ Esse item veio tão vazio que nem o abismo respondeu. Preencha um nome ou escolha uma fonte de imagem.",
                    detail="Item sem caption e sem fonte visual.",
                    code="empty_template_item",
                )
            return self._resolved_text_item(user_caption)

        source = filled_sources[0]
        if source.key == "image_url":
            return await self._resolve_image_url(source.value, user_caption=user_caption)
        if source.key == "avatar_user_id":
            return await self._resolve_avatar(
                source.value,
                user_caption=user_caption,
                client=client or bot or getattr(interaction, "client", None),
                guild=guild or getattr(interaction, "guild", None),
            )
        if source.key == "wikipedia_query":
            return await self._resolve_wikipedia(
                source.value,
                user_caption=user_caption,
                guild_id=guild_id if guild_id is not None else getattr(interaction, "guild_id", None),
                user_id=user_id if user_id is not None else getattr(getattr(interaction, "user", None), "id", None),
            )
        if source.key == "spotify_input":
            return await self._resolve_spotify(source.value, user_caption=user_caption)
        raise TemplateItemResolveError(
            "Essa fonte de imagem ainda não é suportada.",
            detail=f"Fonte visual desconhecida: {source.key}",
            code="template_source_unsupported",
        )

    def _resolved_text_item(self, user_caption: str) -> ResolvedTemplateItem:
        return ResolvedTemplateItem(
            item_type=TemplateItemType.TEXT_ONLY,
            source_type=TemplateSourceType.TEXT.value,
            user_caption=user_caption,
            render_caption=user_caption,
            has_visible_caption=True,
            internal_title=None,
            source_query=None,
            asset_id=None,
            metadata={},
        )

    async def _resolve_image_url(self, image_url: str, *, user_caption: str | None) -> ResolvedTemplateItem:
        downloaded = await self.downloader.download(image_url)
        stored = await self.asset_store.store_image_bytes(
            downloaded.data,
            source_type=TemplateSourceType.IMAGE_URL.value,
            metadata=self._download_metadata(downloaded),
        )
        return self._resolved_image_item(
            source_type=TemplateSourceType.IMAGE_URL.value,
            user_caption=user_caption,
            asset=stored,
            internal_title=self._title_from_url(downloaded.final_url),
            source_query=image_url,
            metadata=self._download_metadata(downloaded),
        )

    async def _resolve_avatar(
        self,
        user_id_raw: str,
        *,
        user_caption: str | None,
        client: Any | None,
        guild: Any | None,
    ) -> ResolvedTemplateItem:
        try:
            discord_user_id = int(str(user_id_raw).strip())
        except ValueError as exc:
            raise TemplateItemResolveError(
                "O ID de usuário informado para avatar não é válido.",
                detail=f"ID de usuário inválido: {user_id_raw!r}",
                code="avatar_user_id_invalid",
            ) from exc
        user = await self._fetch_discord_user(discord_user_id, client=client, guild=guild)
        avatar_url = self._avatar_url_for_user(user)
        downloaded = await self.downloader.download(avatar_url)
        metadata = {
            **self._download_metadata(downloaded),
            "discord_user_id": discord_user_id,
        }
        stored = await self.asset_store.store_image_bytes(
            downloaded.data,
            source_type=TemplateSourceType.DISCORD_AVATAR.value,
            metadata=metadata,
        )
        return self._resolved_image_item(
            source_type=TemplateSourceType.DISCORD_AVATAR.value,
            user_caption=user_caption,
            asset=stored,
            internal_title=str(user) if user is not None else f"Usuário {discord_user_id}",
            source_query=str(discord_user_id),
            metadata=metadata,
        )

    async def _resolve_wikipedia(
        self,
        query: str,
        *,
        user_caption: str | None,
        guild_id: int | None,
        user_id: int | None,
    ) -> ResolvedTemplateItem:
        service = self.wikipedia_service or self._build_wikipedia_service()
        try:
            resolution = await service.resolve(
                query,
                allow_ambiguous=False,
                guild_id=guild_id,
                user_id=user_id,
            )
        except Exception as exc:
            code = getattr(exc, "code", "wikipedia_error")
            user_message = getattr(exc, "user_message", "Não consegui resolver essa imagem pela Wikipedia.")
            if "safety" in str(code):
                raise UnsafeWikipediaImageError(user_message, detail=str(exc), code=str(code)) from exc
            raise TemplateItemResolveError(user_message, detail=str(exc), code=str(code)) from exc
        item = resolution.item
        if item is None:
            raise TemplateItemResolveError(
                "Não consegui escolher uma imagem da Wikipedia para esse termo.",
                detail=f"Wikipedia sem item resolvido para query={query!r}",
                code="wikipedia_no_resolved_item",
            )
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
        stored = await self.asset_store.store_image_bytes(
            item.image_bytes,
            source_type=TemplateSourceType.WIKIPEDIA.value,
            metadata=metadata,
        )
        return self._resolved_image_item(
            source_type=TemplateSourceType.WIKIPEDIA.value,
            user_caption=user_caption,
            asset=stored,
            internal_title=item.wikipedia_title,
            source_query=query,
            metadata=metadata,
        )

    async def _resolve_spotify(self, spotify_input: str, *, user_caption: str | None) -> ResolvedTemplateItem:
        resolver = self.spotify_resolver or self._build_spotify_resolver()
        try:
            resolution = await resolver.resolve(spotify_input, allow_ambiguous=False)
        except Exception as exc:
            code = getattr(exc, "code", "spotify_error")
            user_message = getattr(exc, "user_message", "Não consegui resolver esse item do Spotify.")
            raise TemplateItemResolveError(user_message, detail=str(exc), code=str(code)) from exc
        item = resolution.item
        if item is None:
            raise TemplateItemResolveError(
                "Não consegui escolher uma capa do Spotify para essa entrada.",
                detail=f"Spotify sem item resolvido para input={spotify_input!r}",
                code="spotify_no_resolved_item",
            )
        downloaded = await self.downloader.download(item.image_url)
        metadata = {
            **self._download_metadata(downloaded),
            "spotify_type": item.spotify_type,
            "spotify_id": item.spotify_id,
            "spotify_url": item.spotify_url,
            "spotify_name": item.spotify_name,
            "album_name": item.album_name,
            "track_name": item.track_name,
            "artists": list(item.artists),
            "release_date": item.release_date,
        }
        stored = await self.asset_store.store_image_bytes(
            downloaded.data,
            source_type=TemplateSourceType.SPOTIFY.value,
            metadata=metadata,
        )
        return self._resolved_image_item(
            source_type=TemplateSourceType.SPOTIFY.value,
            user_caption=user_caption,
            asset=stored,
            internal_title=item.display_name,
            source_query=spotify_input,
            metadata=metadata,
        )

    def _resolved_image_item(
        self,
        *,
        source_type: str,
        user_caption: str | None,
        asset: StoredTemplateAsset,
        internal_title: str | None,
        source_query: str | None,
        metadata: dict[str, Any],
    ) -> ResolvedTemplateItem:
        item_metadata = {
            **metadata,
            "asset_hash": asset.asset_hash,
            "asset_storage_path": asset.storage_path,
            "asset_mime_type": asset.mime_type,
            "asset_width": asset.width,
            "asset_height": asset.height,
            "asset_size_bytes": asset.size_bytes,
        }
        return ResolvedTemplateItem(
            item_type=TemplateItemType.IMAGE,
            source_type=source_type,
            user_caption=user_caption,
            render_caption=user_caption,
            has_visible_caption=user_caption is not None,
            internal_title=normalize_caption(internal_title),
            source_query=source_query,
            asset_id=asset.asset_id,
            metadata=item_metadata,
        )

    def _download_metadata(self, downloaded: DownloadedImage) -> dict[str, Any]:
        return {
            "source_url": downloaded.url,
            "final_url": downloaded.final_url,
            "content_type": downloaded.content_type,
        }

    async def _fetch_discord_user(self, user_id: int, *, client: Any | None, guild: Any | None) -> Any:
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
            except Exception as exc:
                raise TemplateItemResolveError(
                    "Não consegui encontrar esse usuário para usar o avatar.",
                    detail=f"fetch_user falhou para {user_id}: {exc!r}",
                    code="avatar_user_not_found",
                ) from exc
        raise TemplateItemResolveError(
            "Não consegui resolver avatar sem acesso ao cliente do Discord.",
            detail="client/bot ausente no resolver de avatar.",
            code="avatar_client_missing",
        )

    def _avatar_url_for_user(self, user: Any) -> str:
        avatar = getattr(user, "display_avatar", None) or getattr(user, "avatar", None)
        if avatar is None:
            raise TemplateItemResolveError(
                "Esse usuário não possui avatar utilizável.",
                detail=f"Objeto de usuário sem avatar: {user!r}",
                code="avatar_missing",
            )
        try:
            return avatar.replace(format="png", size=512).url
        except TypeError:
            return avatar.replace(size=512).url
        except AttributeError:
            return str(avatar)

    def _build_wikipedia_service(self) -> Any:
        try:
            from cogs.tierlist_wikipedia.wikipedia import WikipediaImageService
        except Exception as exc:
            raise TemplateItemResolveError(
                "A integração com Wikipedia não está disponível neste ambiente.",
                detail=f"Import WikipediaImageService falhou: {exc!r}",
                code="wikipedia_unavailable",
            ) from exc
        self.wikipedia_service = WikipediaImageService()
        return self.wikipedia_service

    def _build_spotify_resolver(self) -> Any:
        try:
            from cogs.tierlist_spotify.spotify import SpotifyInputResolver, SpotifyService
        except Exception as exc:
            raise TemplateItemResolveError(
                "A integração com Spotify não está disponível neste ambiente.",
                detail=f"Import SpotifyInputResolver falhou: {exc!r}",
                code="spotify_unavailable",
            ) from exc
        service = self.spotify_service or SpotifyService()
        self.spotify_service = service
        self.spotify_resolver = SpotifyInputResolver(service)
        return self.spotify_resolver

    def _title_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        host = parsed.netloc or "imagem"
        filename = unquote(parsed.path.rsplit("/", 1)[-1] or "").strip()
        if filename:
            return f"{host}/{filename}"
        return host
