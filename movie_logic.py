from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol
from urllib.parse import urlparse

import discord


LOGGER = logging.getLogger("baphomet.movie_logic")

DEFAULT_LIKE_EMOJI: Final[str] = "👍"
DEFAULT_DISLIKE_EMOJI: Final[str] = "👎"
DEFAULT_NEVER_WATCHED_EMOJI: Final[str] = "🤔"
EMOJI_GOSTO: Final[str] = DEFAULT_LIKE_EMOJI
EMOJI_NAO_GOSTO: Final[str] = DEFAULT_DISLIKE_EMOJI
EMOJI_NUNCA_ASSISTI: Final[str] = DEFAULT_NEVER_WATCHED_EMOJI
MAX_REACTION_EMOJI_LENGTH: Final[int] = 100
MAX_UNICODE_REACTION_EMOJI_LENGTH: Final[int] = 16
CUSTOM_EMOJI_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^<a?:[A-Za-z0-9_]{2,32}:\d{15,25}>$"
)


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


class SupportsTMDBClient(Protocol):
    async def get_random_valid_movie(
        self,
        guild_id: int,
        db_manager: SupportsDatabaseManager,
    ) -> Any:
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

        try:
            movie = await tmdb_client.get_random_valid_movie(guild_id, db_manager)
        except Exception:
            LOGGER.error(
                "Postagem do Filme do Dia abortada: nao foi possivel selecionar filme guild_id=%s.",
                guild_id,
                exc_info=True,
            )
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
        poster_url = _read_text(movie, "poster_url")

        embed = discord.Embed(
            title=title,
            color=discord.Color.gold(),
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
            await message.create_thread(
                name=f"Filme do Dia: {title}"[:100],
                auto_archive_duration=10080,
            )
        except discord.Forbidden:
            pass

        if not is_test:
            await db_manager.add_to_blacklist(guild_id, tmdb_id, title)

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


__all__ = [
    "DEFAULT_LIKE_EMOJI",
    "DEFAULT_DISLIKE_EMOJI",
    "DEFAULT_NEVER_WATCHED_EMOJI",
    "EMOJI_GOSTO",
    "EMOJI_NAO_GOSTO",
    "EMOJI_NUNCA_ASSISTI",
    "ReactionEmojiSet",
    "post_movie_of_the_day",
    "validate_reaction_emoji",
]
