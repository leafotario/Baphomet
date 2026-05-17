from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol

import discord


EMOJI_GOSTO = "👍"
EMOJI_NAO_GOSTO = "👎"
EMOJI_NUNCA_ASSISTI = "🤔"

DEFAULT_OVERVIEW = "A sinopse não foi providenciada em português pelo banco de dados."
POST_PROMPT = (
    f"E aí, já assistiu? O que achou?\n\n"
    f"{EMOJI_GOSTO} — Eu gosto desse filme\n"
    f"{EMOJI_NAO_GOSTO} — Não gosto desse filme\n"
    f"{EMOJI_NUNCA_ASSISTI} — Nunca assisti esse filme"
)


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
            logging.error(
                "Postagem do Filme do Dia abortada: configuracao ausente guild_id=%s.",
                guild_id,
            )
            return False

        channel_id = _coerce_optional_int(_read_value(config, "channel_id"))
        if channel_id is None:
            logging.error(
                "Postagem do Filme do Dia abortada: channel_id ausente guild_id=%s.",
                guild_id,
            )
            return False

        channel = await _resolve_text_channel(bot, guild_id, channel_id)
        if channel is None:
            return False

        if not _has_required_permissions(channel):
            return False

        movie = await tmdb_client.get_random_valid_movie(guild_id, db_manager)
        tmdb_id = _resolve_movie_id(movie)
        title = _read_text(movie, "title")
        overview = _read_text(movie, "overview", fallback=DEFAULT_OVERVIEW)
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
            description=overview,
            color=discord.Color.gold(),
        )
        if poster_url != "N/A":
            embed.set_thumbnail(url=poster_url)

        embed.add_field(name="Gênero", value=genres, inline=True)
        embed.add_field(name="Duração", value=runtime, inline=True)
        embed.add_field(name="Direção", value=director, inline=True)
        embed.add_field(name="Lançamento", value=release_date, inline=True)

        role_id = _coerce_optional_int(_read_value(config, "role_id"))
        content = _build_content(role_id)
        message = await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                users=False,
                roles=role_id is not None,
            ),
        )

        for emoji in (EMOJI_GOSTO, EMOJI_NAO_GOSTO, EMOJI_NUNCA_ASSISTI):
            try:
                await message.add_reaction(emoji)
            except (discord.HTTPException, discord.Forbidden, TypeError):
                logging.error(
                    "Falha ao adicionar reacao %s no Filme do Dia guild_id=%s.",
                    emoji,
                    guild_id,
                    exc_info=True,
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
        logging.error(
            "Postagem do Filme do Dia bloqueada por permissao guild_id=%s.",
            guild_id,
            exc_info=True,
        )
        return False
    except Exception:
        logging.error(
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
        logging.error(
            "Postagem do Filme do Dia abortada: guild nao encontrada guild_id=%s.",
            guild_id,
        )
        return None

    channel = guild.get_channel(channel_id) or bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            logging.error(
                "Postagem do Filme do Dia abortada: canal indisponivel channel_id=%s guild_id=%s.",
                channel_id,
                guild_id,
                exc_info=True,
            )
            return None

    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild_id:
        logging.error(
            "Postagem do Filme do Dia abortada: canal invalido channel_id=%s guild_id=%s.",
            channel_id,
            guild_id,
        )
        return None

    return channel


def _has_required_permissions(channel: discord.TextChannel) -> bool:
    me = channel.guild.me
    if me is None:
        logging.error(
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
        logging.error(
            "Postagem do Filme do Dia abortada: permissoes ausentes guild_id=%s channel_id=%s missing=%s.",
            channel.guild.id,
            channel.id,
            ", ".join(missing_permissions),
        )
        return False

    return True


def _build_content(role_id: int | None) -> str:
    if role_id is None:
        return POST_PROMPT
    return f"<@&{role_id}>\n\n{POST_PROMPT}"


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
    "EMOJI_GOSTO",
    "EMOJI_NAO_GOSTO",
    "EMOJI_NUNCA_ASSISTI",
    "post_movie_of_the_day",
]
