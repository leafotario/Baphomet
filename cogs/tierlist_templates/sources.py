from __future__ import annotations

import asyncio
import io
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import aiohttp
from PIL import Image, UnidentifiedImageError

from cogs.tierlist_spotify.spotify import (
    SpotifyImageDownloader,
    SpotifyImageError,
    SpotifyImageProcessor,
    SpotifyInputResolver,
    SpotifyService,
    SpotifyUserError,
)
from cogs.tierlist_wikipedia.wikipedia import (
    WIKIPEDIA_SOURCE_TYPE,
    WikipediaImageService,
    WikipediaUserError,
)


LOGGER = logging.getLogger("baphomet.tierlist.templates.sources")

SOURCE_TEXT = "text"
SOURCE_IMAGE_URL = "image_url"
SOURCE_AVATAR_USER_ID = "avatar_user_id"
SOURCE_SPOTIFY = "spotify"
SOURCE_WIKIPEDIA = WIKIPEDIA_SOURCE_TYPE


class TemplateSourceError(Exception):
    def __init__(self, user_message: str, *, code: str = "template_source_error") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.code = code


@dataclass(frozen=True)
class FilledImageSource:
    key: str
    label: str
    value: str


@dataclass(frozen=True)
class ResolvedTemplateSource:
    source_type: str
    source_query: str | None
    image_bytes: bytes
    metadata: dict[str, Any]


class TemplateSourceResolver:
    MAX_IMAGE_BYTES = 8 * 1024 * 1024
    IMAGE_TIMEOUT_SECONDS = 8

    def __init__(
        self,
        *,
        spotify_service: SpotifyService | None = None,
        spotify_resolver: SpotifyInputResolver | None = None,
        spotify_downloader: SpotifyImageDownloader | None = None,
        wikipedia_service: WikipediaImageService | None = None,
    ) -> None:
        self.spotify_service = spotify_service or SpotifyService()
        self.spotify_resolver = spotify_resolver or SpotifyInputResolver(self.spotify_service)
        self.spotify_downloader = spotify_downloader or SpotifyImageDownloader(
            processor=SpotifyImageProcessor(),
            max_bytes=self.MAX_IMAGE_BYTES,
            timeout_seconds=self.IMAGE_TIMEOUT_SECONDS,
        )
        self.wikipedia_service = wikipedia_service or WikipediaImageService(max_image_bytes=self.MAX_IMAGE_BYTES)

    async def resolve(
        self,
        *,
        source_type: str,
        raw_value: str,
        client: Any,
        guild_id: int | None,
        user_id: int | None,
    ) -> ResolvedTemplateSource:
        value = re.sub(r"\s+", " ", (raw_value or "").strip())
        if not value:
            raise TemplateSourceError("Informe a fonte da imagem.", code="source_empty")

        if source_type == SOURCE_IMAGE_URL:
            return await self.resolve_url(value)
        if source_type == SOURCE_AVATAR_USER_ID:
            return await self.resolve_avatar(value, client=client)
        if source_type == SOURCE_SPOTIFY:
            return await self.resolve_spotify(value)
        if source_type == SOURCE_WIKIPEDIA:
            return await self.resolve_wikipedia(value, guild_id=guild_id, user_id=user_id)

        raise TemplateSourceError("Fonte de imagem não suportada.", code="source_unsupported")

    async def resolve_url(self, url: str) -> ResolvedTemplateSource:
        if not self._looks_like_url(url):
            raise TemplateSourceError("A URL informada não parece válida.", code="url_invalid")
        image_bytes = await self._fetch_image(url)
        return ResolvedTemplateSource(
            source_type=SOURCE_IMAGE_URL,
            source_query=url,
            image_bytes=image_bytes,
            metadata={"internal_title": "Imagem por URL"},
        )

    async def resolve_avatar(self, raw_user_id: str, *, client: Any) -> ResolvedTemplateSource:
        import discord

        try:
            target_user_id = int(raw_user_id)
        except ValueError as exc:
            raise TemplateSourceError("O ID do usuário precisa ser numérico.", code="avatar_invalid_user_id") from exc

        try:
            user = await client.fetch_user(target_user_id)
            image_bytes = await user.display_avatar.replace(format="png", size=512).read()
        except (discord.NotFound, discord.HTTPException, ValueError, TypeError) as exc:
            raise TemplateSourceError(
                "Não consegui encontrar esse usuário para usar o avatar.",
                code="avatar_fetch_failed",
            ) from exc

        return ResolvedTemplateSource(
            source_type=SOURCE_AVATAR_USER_ID,
            source_query=str(target_user_id),
            image_bytes=image_bytes,
            metadata={"internal_title": f"Avatar de usuário {target_user_id}"},
        )

    async def resolve_spotify(self, raw: str) -> ResolvedTemplateSource:
        try:
            resolution = await self.spotify_resolver.resolve(raw, allow_ambiguous=False)
            if resolution.item is None:
                raise SpotifyUserError("Não consegui encontrar esse álbum ou música.", code="spotify_no_results")
            item = resolution.item
            image_bytes = await self.spotify_downloader.download(item.image_url, cache_key=item.cache_key)
        except SpotifyUserError as exc:
            raise TemplateSourceError(exc.user_message, code=exc.code) from exc
        except SpotifyImageError as exc:
            raise TemplateSourceError(exc.user_message, code=exc.code) from exc

        return ResolvedTemplateSource(
            source_type=SOURCE_SPOTIFY,
            source_query=raw,
            image_bytes=image_bytes,
            metadata={
                "internal_title": item.display_name,
                "image_cache_key": item.cache_key,
                "spotify_type": item.spotify_type,
                "spotify_id": item.spotify_id,
                "spotify_url": item.spotify_url,
                "spotify_name": item.spotify_name,
                "spotify_artists": list(item.artists),
                "album_name": item.album_name,
                "track_name": item.track_name,
                "release_date": item.release_date,
                "attribution_text": item.attribution_text,
            },
        )

    async def resolve_wikipedia(
        self,
        raw: str,
        *,
        guild_id: int | None,
        user_id: int | None,
    ) -> ResolvedTemplateSource:
        try:
            resolution = await self.wikipedia_service.resolve(
                raw,
                allow_ambiguous=False,
                guild_id=guild_id,
                user_id=user_id,
            )
            if resolution.item is None:
                raise WikipediaUserError("Não encontrei nenhum artigo com imagem segura.", code="wiki_no_results")
            item = resolution.item
        except WikipediaUserError as exc:
            raise TemplateSourceError(exc.user_message, code=exc.code) from exc

        return ResolvedTemplateSource(
            source_type=SOURCE_WIKIPEDIA,
            source_query=raw,
            image_bytes=item.image_bytes,
            metadata={
                "internal_title": item.wikipedia_title or item.display_name,
                "display_name": item.display_name,
                "image_cache_key": item.image_cache_key,
                "wiki_language": item.wiki_language,
                "wikipedia_pageid": item.wikipedia_pageid,
                "wikipedia_title": item.wikipedia_title,
                "wikipedia_url": item.wikipedia_url,
                "wikimedia_file_title": item.wikimedia_file_title,
                "wikimedia_file_description_url": item.wikimedia_file_description_url,
                "image_mime": item.image_mime,
                "artist": item.artist,
                "credit": item.credit,
                "license_short_name": item.license_short_name,
                "license_url": item.license_url,
                "usage_terms": item.usage_terms,
                "attribution_required": item.attribution_required,
                "metadata_source": item.metadata_source,
            },
        )

    async def _fetch_image(self, url: str) -> bytes:
        timeout = aiohttp.ClientTimeout(total=self.IMAGE_TIMEOUT_SECONDS, connect=3, sock_read=6)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        try:
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.get(url, headers=headers, allow_redirects=True) as response:
                    if response.status != 200:
                        raise TemplateSourceError("Não consegui baixar essa imagem.", code="url_http_error")
                    content_type = response.headers.get("Content-Type", "").lower()
                    if "image/" not in content_type:
                        raise TemplateSourceError("Esse link não retornou uma imagem.", code="url_content_type")

                    data = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        data.extend(chunk)
                        if len(data) > self.MAX_IMAGE_BYTES:
                            raise TemplateSourceError("Essa imagem é grande demais.", code="url_too_large")
        except TemplateSourceError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise TemplateSourceError("Não consegui baixar essa imagem agora.", code="url_network") from exc

        self._validate_image_bytes(bytes(data))
        return bytes(data)

    def _validate_image_bytes(self, image_bytes: bytes) -> None:
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                image.verify()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise TemplateSourceError("O arquivo baixado não parece uma imagem válida.", code="url_invalid_image") from exc

    def _looks_like_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def get_filled_image_sources(
    *,
    image_url: str = "",
    avatar_user_id: str = "",
    wikipedia: str = "",
    spotify: str = "",
) -> list[FilledImageSource]:
    source_fields = (
        (SOURCE_IMAGE_URL, "Link de imagem", image_url),
        (SOURCE_AVATAR_USER_ID, "ID de usuário", avatar_user_id),
        (SOURCE_WIKIPEDIA, "Wikipedia", wikipedia),
        (SOURCE_SPOTIFY, "Spotify", spotify),
    )
    return [
        FilledImageSource(key=key, label=label, value=str(value or "").strip())
        for key, label, value in source_fields
        if str(value or "").strip()
    ]


def conflicting_image_sources_message(sources: list[FilledImageSource]) -> str:
    labels = [source.label for source in sources]
    filled = ", ".join(labels[:-1]) + f" e {labels[-1]}" if len(labels) > 1 else (labels[0] if labels else "")
    suffix = f"\n\nFontes preenchidas: {filled}." if filled else ""
    return (
        "⚠️ Você preencheu mais de uma fonte de imagem ao mesmo tempo. "
        "Escolha apenas uma fonte para esse item do template."
        f"{suffix}"
    )
