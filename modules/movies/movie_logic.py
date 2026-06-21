from __future__ import annotations

import asyncio
import io
import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Protocol
from urllib.parse import urlparse

import aiohttp
import discord
from PIL import Image, ImageFile, UnidentifiedImageError


LOGGER = logging.getLogger("baphomet.movie_logic")

# Alguns posters vindos de CDN chegam com poucos bytes finais ausentes. O Pillow
# ainda consegue carregar a imagem nesses casos, então toleramos esse cenário para
# não perder a cor dinâmica por um JPEG levemente truncado.
ImageFile.LOAD_TRUNCATED_IMAGES = True

FALLBACK_EMBED_COLOR: Final[discord.Color] = discord.Color.gold()
DEFAULT_LIKE_EMOJI: Final[str] = "👍"
DEFAULT_DISLIKE_EMOJI: Final[str] = "👎"
DEFAULT_NEVER_WATCHED_EMOJI: Final[str] = "🤔"
EMOJI_GOSTO: Final[str] = DEFAULT_LIKE_EMOJI
EMOJI_NAO_GOSTO: Final[str] = DEFAULT_DISLIKE_EMOJI
EMOJI_NUNCA_ASSISTI: Final[str] = DEFAULT_NEVER_WATCHED_EMOJI
MAX_REACTION_EMOJI_LENGTH: Final[int] = 100
MAX_UNICODE_REACTION_EMOJI_LENGTH: Final[int] = 16
POSTER_COLOR_DOWNLOAD_TIMEOUT_SECONDS: Final[float] = 5.0
POSTER_COLOR_MAX_BYTES: Final[int] = 5 * 1024 * 1024
POSTER_COLOR_ANALYSIS_SIZE: Final[tuple[int, int]] = (80, 80)
POSTER_COLOR_PALETTE_SIZE: Final[int] = 12
POSTER_COLOR_MIN_ALPHA: Final[int] = 128
POSTER_COLOR_MIN_BRIGHTNESS: Final[float] = 24.0
POSTER_COLOR_MAX_BRIGHTNESS: Final[float] = 235.0
PT_BR_MONTHS: Final[tuple[str, ...]] = (
    "",
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)
CUSTOM_EMOJI_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^<a?:[A-Za-z0-9_]{2,32}:\d{15,25}>$"
)
ISO_RELEASE_DATE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RELEASE_YEAR_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{4}$")


@dataclass(frozen=True, slots=True)
class ReactionEmojiSet:
    like: str
    dislike: str
    never_watched: str


class SupportsBot(Protocol):
    user: discord.ClientUser | None

    def get_guild(self, guild_id: int) -> discord.Guild | None:
        ...

    def get_channel(self, channel_id: int) -> discord.abc.GuildChannel | None:
        ...

    async def fetch_channel(self, channel_id: int) -> discord.abc.GuildChannel:
        ...


class SupportsDatabaseManager(Protocol):
    async def get_config(self, guild_id: int) -> Any | None:
        ...

    async def add_to_blacklist(
        self,
        guild_id: int,
        tmdb_id: int,
        movie_title: str,
    ) -> bool:
        ...
        
    async def add_motd_history(
        self,
        guild_id: int,
        movie_title: str,
        poster_url: str | None,
    ) -> None:
        ...

    async def pop_from_motd_queue(self, guild_id: int) -> Any | None:
        ...


class SupportsTMDBClient(Protocol):
    async def get_random_valid_movie(
        self,
        guild_id: int,
        db_manager: SupportsDatabaseManager,
    ) -> Any:
        ...

    async def get_movie_by_id(self, tmdb_id: int) -> Any | None:
        ...


async def post_movie_of_the_day(
    bot: SupportsBot,
    guild_id: int,
    db_manager: SupportsDatabaseManager,
    tmdb_client: SupportsTMDBClient,
    is_test: bool = False,
) -> bool:
    try:
        config = await db_manager.get_config(guild_id)
        if config is None:
            LOGGER.error(
                "Postagem do Filme do Dia abortada: configuracao ausente guild_id=%s.",
                guild_id,
            )
            return False

        if not getattr(config, "is_active", True):
            return False

        channel_id = _coerce_optional_int(_read_value(config, "channel_id"))
        if channel_id is None:
            LOGGER.error(
                "Postagem do Filme do Dia abortada: channel_id ausente guild_id=%s.",
                guild_id,
            )
            return False
        reaction_emojis = _resolve_reaction_emojis(config)

        channel = await _resolve_text_channel(bot, guild_id, channel_id)
        if channel is None:
            return False

        if not _has_required_permissions(channel):
            return False

        movie = None
        user_id_sugestao: int | None = None
        try:
            queue_entry = await db_manager.pop_from_motd_queue(guild_id)
            if queue_entry:
                user_id_sugestao = getattr(queue_entry, "user_id_sugestao", None)
                tmdb_id = getattr(queue_entry, "tmdb_id", None)
                if tmdb_id:
                    movie = await tmdb_client.get_movie_by_id(tmdb_id)
            
            if not movie:
                movie = await tmdb_client.get_random_valid_movie(guild_id, db_manager)
        except Exception:
            LOGGER.error(
                "Postagem do Filme do Dia abortada: nao foi possivel selecionar filme guild_id=%s.",
                guild_id,
                exc_info=True,
            )
            return False

        if not movie:
            return False

        tmdb_id = _resolve_movie_id(movie)
        title = _read_text(movie, "title")
        genres = _read_text(movie, "genres")
        runtime = _read_text(movie, "runtime")
        director = _read_text(movie, "director")
        release_date = _read_first_text(
            movie,
            ("release_date", "release", "release_year", "launch_date"),
        )
        release_date = _format_release_date_pt_br(release_date)
        poster_url = _read_text(movie, "poster_url")
        embed_color = await extract_dominant_color_from_url(poster_url)

        embed = discord.Embed(
            title=title,
            color=embed_color,
        )
        if _is_valid_embed_image_url(poster_url):
            embed.set_image(url=poster_url)

        embed.add_field(name="Gênero", value=genres, inline=True)
        embed.add_field(name="Duração", value=runtime, inline=True)
        embed.add_field(name="Direção", value=director, inline=True)
        embed.add_field(name="Lançamento", value=release_date, inline=True)

        role_id = _coerce_optional_int(_read_value(config, "role_id"))
        content = _build_content(role_id, reaction_emojis)
        message = await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                users=False,
                roles=role_id is not None,
            ),
        )

        await _add_reaction_with_fallback(
            message,
            emoji_text=reaction_emojis.like,
            default_emoji=DEFAULT_LIKE_EMOJI,
            guild_id=guild_id,
            label="like",
        )
        await _add_reaction_with_fallback(
            message,
            emoji_text=reaction_emojis.dislike,
            default_emoji=DEFAULT_DISLIKE_EMOJI,
            guild_id=guild_id,
            label="dislike",
        )
        await _add_reaction_with_fallback(
            message,
            emoji_text=reaction_emojis.never_watched,
            default_emoji=DEFAULT_NEVER_WATCHED_EMOJI,
            guild_id=guild_id,
            label="never_watched",
        )

        try:
            thread = await message.create_thread(
                name=f"{title}"[:100],
                auto_archive_duration=10080,
            )
            if user_id_sugestao:
                await thread.send(f"Sugerido por <@{user_id_sugestao}>")
        except discord.Forbidden:
            pass

        if not is_test:
            await db_manager.add_to_blacklist(guild_id, tmdb_id, title)
            await db_manager.add_motd_history(guild_id, title, poster_url)

        return True
    except discord.Forbidden:
        LOGGER.error(
            "Postagem do Filme do Dia bloqueada por permissao guild_id=%s.",
            guild_id,
            exc_info=True,
        )
        return False
    except Exception:
        LOGGER.error(
            "Falha inesperada na postagem do Filme do Dia guild_id=%s.",
            guild_id,
            exc_info=True,
        )
        raise


async def _resolve_text_channel(
    bot: SupportsBot,
    guild_id: int,
    channel_id: int,
) -> discord.TextChannel | None:
    guild = bot.get_guild(guild_id)
    if guild is None:
        LOGGER.error(
            "Postagem do Filme do Dia abortada: guild nao encontrada guild_id=%s.",
            guild_id,
        )
        return None

    channel = guild.get_channel(channel_id) or bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            LOGGER.error(
                "Postagem do Filme do Dia abortada: canal indisponivel channel_id=%s guild_id=%s.",
                channel_id,
                guild_id,
                exc_info=True,
            )
            return None

    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild_id:
        LOGGER.error(
            "Postagem do Filme do Dia abortada: canal invalido channel_id=%s guild_id=%s.",
            channel_id,
            guild_id,
        )
        return None

    return channel


def _has_required_permissions(channel: discord.TextChannel) -> bool:
    me = channel.guild.me
    if me is None:
        LOGGER.error(
            "Postagem do Filme do Dia abortada: membro do bot nao encontrado guild_id=%s.",
            channel.guild.id,
        )
        return False

    permissions = channel.permissions_for(me)
    missing_permissions: list[str] = []
    if not permissions.send_messages:
        missing_permissions.append("send_messages")
    if not permissions.embed_links:
        missing_permissions.append("embed_links")
    if not permissions.create_public_threads:
        missing_permissions.append("create_public_threads")

    if missing_permissions:
        LOGGER.error(
            "Postagem do Filme do Dia abortada: permissoes ausentes guild_id=%s channel_id=%s missing=%s.",
            channel.guild.id,
            channel.id,
            ", ".join(missing_permissions),
        )
        return False

    return True


async def extract_dominant_color_from_url(url: str) -> discord.Color:
    if not _is_valid_embed_image_url(url):
        return FALLBACK_EMBED_COLOR

    try:
        image_bytes = await _download_image_bytes(url)
        dominant_rgb = await asyncio.to_thread(
            _extract_dominant_rgb_from_image_bytes,
            image_bytes,
        )
    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        OSError,
        TypeError,
        UnidentifiedImageError,
        ValueError,
    ) as exc:
        LOGGER.warning(
            "Falha ao extrair cor dominante do poster; usando cor padrao url=%s erro=%s.",
            url,
            exc,
            exc_info=LOGGER.isEnabledFor(logging.DEBUG),
        )
        return FALLBACK_EMBED_COLOR

    if dominant_rgb is None:
        return FALLBACK_EMBED_COLOR

    return discord.Color.from_rgb(*dominant_rgb)


async def _download_image_bytes(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=POSTER_COLOR_DOWNLOAD_TIMEOUT_SECONDS)
    headers = {"Accept": "image/*"}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url) as response:
            if response.status < 200 or response.status >= 300:
                raise ValueError(f"download do poster retornou HTTP {response.status}")

            content_type = response.headers.get("Content-Type", "")
            if content_type and not content_type.lower().startswith("image/"):
                raise ValueError(f"conteudo do poster nao parece imagem: {content_type}")

            content_length = _coerce_optional_int(response.headers.get("Content-Length"))
            if content_length is not None and content_length > POSTER_COLOR_MAX_BYTES:
                raise ValueError("poster excede o tamanho maximo permitido")

            image_bytes = await response.content.read(POSTER_COLOR_MAX_BYTES + 1)

    if len(image_bytes) > POSTER_COLOR_MAX_BYTES:
        raise ValueError("poster excede o tamanho maximo permitido")

    if not image_bytes:
        raise ValueError("download do poster retornou conteudo vazio")

    return image_bytes


def _extract_dominant_rgb_from_image_bytes(image_bytes: bytes) -> tuple[int, int, int] | None:
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.thumbnail(POSTER_COLOR_ANALYSIS_SIZE, Image.Resampling.LANCZOS)
        rgba_image = image.convert("RGBA")
        candidate_pixels: list[tuple[int, int, int]] = []
        fallback_pixels: list[tuple[int, int, int]] = []

        for red, green, blue, alpha in rgba_image.getdata():
            if alpha < POSTER_COLOR_MIN_ALPHA:
                continue

            rgb = (red, green, blue)
            fallback_pixels.append(rgb)
            if _is_representative_poster_color(red, green, blue):
                candidate_pixels.append(rgb)

    return _find_dominant_rgb(candidate_pixels or fallback_pixels)


def _find_dominant_rgb(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int] | None:
    if not pixels:
        return None

    palette_size = max(1, min(POSTER_COLOR_PALETTE_SIZE, len(set(pixels))))
    pixel_image = Image.new("RGB", (len(pixels), 1))
    pixel_image.putdata(pixels)
    quantized_image = pixel_image.quantize(colors=palette_size)
    colors = quantized_image.getcolors(maxcolors=len(pixels))
    palette = quantized_image.getpalette()

    if not colors or not palette:
        return pixels[0]

    for _, palette_index in sorted(colors, reverse=True):
        palette_offset = int(palette_index) * 3
        rgb = tuple(palette[palette_offset : palette_offset + 3])
        if len(rgb) != 3:
            continue

        red, green, blue = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        if _is_representative_poster_color(red, green, blue):
            return red, green, blue

    _, palette_index = max(colors)
    palette_offset = int(palette_index) * 3
    rgb = palette[palette_offset : palette_offset + 3]
    if len(rgb) != 3:
        return pixels[0]
    return int(rgb[0]), int(rgb[1]), int(rgb[2])


def _is_representative_poster_color(red: int, green: int, blue: int) -> bool:
    brightness = (red * 0.299) + (green * 0.587) + (blue * 0.114)
    return POSTER_COLOR_MIN_BRIGHTNESS <= brightness <= POSTER_COLOR_MAX_BRIGHTNESS


def _format_release_date_pt_br(raw_date: object) -> str:
    if raw_date is None:
        return "N/A"

    text = str(raw_date).strip()
    if not text or text.upper() == "N/A":
        return "N/A"

    if RELEASE_YEAR_PATTERN.fullmatch(text):
        return text

    if ISO_RELEASE_DATE_PATTERN.fullmatch(text) is None:
        return text

    try:
        parsed_date = date.fromisoformat(text)
    except ValueError:
        return "N/A"

    month_name = PT_BR_MONTHS[parsed_date.month]
    return f"{parsed_date.day} de {month_name} de {parsed_date.year}"


def validate_reaction_emoji(emoji_text: str) -> str | None:
    emoji = emoji_text.strip()
    if not emoji:
        return "o valor não pode ficar vazio."

    if len(emoji) > MAX_REACTION_EMOJI_LENGTH:
        return "o valor é grande demais para ser uma reação."

    if CUSTOM_EMOJI_PATTERN.fullmatch(emoji):
        return None

    if len(emoji) > MAX_UNICODE_REACTION_EMOJI_LENGTH:
        return "use apenas um emoji Unicode curto ou um custom emoji do Discord."

    if any(character.isspace() for character in emoji):
        return "emojis de reação não devem conter espaços."

    if _looks_like_unicode_emoji(emoji):
        return None

    return "use um emoji Unicode, como 👍, ou um custom emoji no formato `<:nome:id>`."


def _looks_like_unicode_emoji(emoji: str) -> bool:
    # Validação pragmática: Unicode não expõe uma API perfeita de emoji na stdlib.
    # Bloqueamos texto puro e aceitamos símbolos emoji comuns, variações e ZWJ.
    if any(character.isascii() and character.isalnum() for character in emoji):
        return False

    return any(_is_emoji_like_character(character) for character in emoji)


def _is_emoji_like_character(character: str) -> bool:
    codepoint = ord(character)
    if 0x1F000 <= codepoint <= 0x1FAFF:
        return True
    if 0x2600 <= codepoint <= 0x27BF:
        return True
    if 0xFE00 <= codepoint <= 0xFE0F:
        return True
    if codepoint == 0x200D:
        return True
    return unicodedata.category(character) == "So"


def _resolve_reaction_emojis(config: object) -> ReactionEmojiSet:
    return ReactionEmojiSet(
        like=_resolve_config_emoji(config, "like_emoji", DEFAULT_LIKE_EMOJI),
        dislike=_resolve_config_emoji(config, "dislike_emoji", DEFAULT_DISLIKE_EMOJI),
        never_watched=_resolve_config_emoji(
            config,
            "never_watched_emoji",
            DEFAULT_NEVER_WATCHED_EMOJI,
        ),
    )


def _resolve_config_emoji(config: object, key: str, default: str) -> str:
    value = _read_value(config, key)
    if value is None:
        return default

    emoji = str(value).strip()
    error = validate_reaction_emoji(emoji)
    if error is None:
        return emoji

    LOGGER.warning(
        "Emoji configurado invalido no MOTD; usando padrao key=%s value=%r error=%s.",
        key,
        emoji,
        error,
    )
    return default


def _build_post_prompt(reaction_emojis: ReactionEmojiSet) -> str:
    return (
        "E aí, já assistiu? O que achou?\n\n"
        f"{reaction_emojis.like} — Eu gosto desse filme\n"
        f"{reaction_emojis.dislike} — Não gosto desse filme\n"
        f"{reaction_emojis.never_watched} — Nunca assisti esse filme"
    )


def _build_content(role_id: int | None, reaction_emojis: ReactionEmojiSet) -> str:
    post_prompt = _build_post_prompt(reaction_emojis)
    if role_id is None:
        return post_prompt
    return f"<@&{role_id}>\n\n{post_prompt}"


async def _add_reaction_with_fallback(
    message: discord.Message,
    *,
    emoji_text: str,
    default_emoji: str,
    guild_id: int,
    label: str,
) -> None:
    try:
        await message.add_reaction(_coerce_reaction_emoji(emoji_text))
        return
    except (discord.HTTPException, discord.Forbidden, TypeError, ValueError):
        LOGGER.error(
            "Falha ao adicionar reacao customizada no Filme do Dia guild_id=%s label=%s emoji=%r.",
            guild_id,
            label,
            emoji_text,
            exc_info=True,
        )

    if emoji_text == default_emoji:
        return

    try:
        await message.add_reaction(default_emoji)
    except (discord.HTTPException, discord.Forbidden, TypeError):
        LOGGER.error(
            "Falha ao adicionar reacao fallback no Filme do Dia guild_id=%s label=%s emoji=%r.",
            guild_id,
            label,
            default_emoji,
            exc_info=True,
        )


def _coerce_reaction_emoji(emoji_text: str) -> str | discord.PartialEmoji:
    if CUSTOM_EMOJI_PATTERN.fullmatch(emoji_text):
        return discord.PartialEmoji.from_str(emoji_text)
    return emoji_text


def _is_valid_embed_image_url(url: str) -> bool:
    if not url or url == "N/A":
        return False

    parsed_url = urlparse(url)
    return parsed_url.scheme in {"http", "https"} and bool(parsed_url.netloc)


def _resolve_movie_id(movie: object) -> int:
    tmdb_id = _coerce_optional_int(_read_value(movie, "tmdb_id"))
    if tmdb_id is None:
        tmdb_id = _coerce_optional_int(_read_value(movie, "id"))
    if tmdb_id is None:
        raise ValueError("Filme selecionado sem identificador TMDB valido.")
    return tmdb_id


def _read_first_text(movie: object, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _read_text(movie, key)
        if value != "N/A":
            return value
    return "N/A"


def _read_text(movie: object, key: str, *, fallback: str = "N/A") -> str:
    value = _read_value(movie, key)
    if value is None:
        return fallback

    text = str(value).strip()
    return text or fallback


def _read_value(source: object, key: str) -> object | None:
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _coerce_optional_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None

    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None

    return parsed if parsed > 0 else None


async def send_motd_recap(
    bot: SupportsBot,
    guild_id: int,
    db_manager: Any,
    is_test: bool = False,
) -> bool:
    try:
        config = await db_manager.get_config(guild_id)
        if config is None:
            return False

        if not getattr(config, "recap_active", False) and not is_test:
            return False

        channel_id = _coerce_optional_int(_read_value(config, "channel_id"))
        if channel_id is None:
            return False

        channel = await _resolve_text_channel(bot, guild_id, channel_id)
        if channel is None:
            return False

        if not _has_required_permissions(channel):
            return False

        history = await db_manager.get_motd_history(guild_id)
        if not history:
            if is_test:
                await channel.send("Não há filmes registrados nesta semana para o recap.")
            return False

        from modules.movies.recap_logic import generate_recap_collage, build_recap_embed
        
        urls = [getattr(item, "poster_url", None) for item in history if getattr(item, "poster_url", None)]
        buffer = await generate_recap_collage(urls)
        
        items_dicts = [{"title": getattr(item, "movie_title", "Desconhecido")} for item in history]
        embed, emojis = build_recap_embed(items_dicts, system_type="MOTD")

        kwargs = {"embed": embed}
        if buffer:
            file = discord.File(fp=buffer, filename="recap.jpg")
            embed.set_image(url="attachment://recap.jpg")
            kwargs["file"] = file
            
        msg = await channel.send(**kwargs)
        
        for emoji in emojis:
            try:
                await msg.add_reaction(emoji)
            except discord.HTTPException:
                pass
                
        return True
    except discord.Forbidden:
        LOGGER.error(
            "Recap do MOTD bloqueado por permissao guild_id=%s.",
            guild_id,
            exc_info=True,
        )
        return False
    except Exception:
        LOGGER.error(
            "Falha inesperada no Recap do MOTD guild_id=%s.",
            guild_id,
            exc_info=True,
        )
        raise

__all__ = [
    "DEFAULT_LIKE_EMOJI",
    "DEFAULT_DISLIKE_EMOJI",
    "DEFAULT_NEVER_WATCHED_EMOJI",
    "EMOJI_GOSTO",
    "EMOJI_NAO_GOSTO",
    "EMOJI_NUNCA_ASSISTI",
    "ReactionEmojiSet",
    "post_movie_of_the_day",
    "send_motd_recap",
    "validate_reaction_emoji",
]
