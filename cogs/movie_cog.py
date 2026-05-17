from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from discord import app_commands
from discord.ext import commands

from database import DEFAULT_SCHEDULE_TIME, DatabaseManager
from movie_logic import post_movie_of_the_day
from tmdb_api import TMDBClient


LOGGER = logging.getLogger("baphomet.movie_cog")

SCHEDULER_TIMEZONE: Final[str] = "America/Sao_Paulo"
TIME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:[01]\d|2[0-3]):[0-5]\d$"
)
BLACKLIST_PAGE_SIZE: Final[int] = 10
JOB_ID_PREFIX: Final[str] = "movie_job_"
UNSET: Final[object] = object()


@dataclass(frozen=True, slots=True)
class MovieGuildConfig:
    guild_id: int
    channel_id: int | None
    role_id: int | None
    schedule_time: str


class MovieCog(commands.Cog):
    motd = app_commands.Group(
        name="motd",
        description="Configuração e operação do Filme do Dia.",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db_manager = DatabaseManager()
        self.tmdb_client = TMDBClient()
        self.scheduler = AsyncIOScheduler(timezone=SCHEDULER_TIMEZONE)
        self._initial_reschedule_done = False

    async def cog_load(self) -> None:
        await self.db_manager.init_db()
        if not self.scheduler.running:
            self.scheduler.start()
        LOGGER.info("MovieCog inicializado com APScheduler em %s.", SCHEDULER_TIMEZONE)

    async def cog_unload(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        await self.tmdb_client.close()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._initial_reschedule_done:
            return

        self._initial_reschedule_done = True
        for guild in self.bot.guilds:
            config = await self.db_manager.get_config(guild.id)
            if config is None:
                continue

            schedule_time = str(
                getattr(config, "schedule_time", DEFAULT_SCHEDULE_TIME)
                or DEFAULT_SCHEDULE_TIME
            )
            try:
                self._reschedule_guild(guild.id, schedule_time)
            except ValueError:
                LOGGER.error(
                    "Horario invalido ao reagendar guild_id=%s schedule_time=%s.",
                    guild.id,
                    schedule_time,
                    exc_info=True,
                )

    def _reschedule_guild(self, guild_id: int, time_str: str) -> None:
        hour, minute = self._parse_schedule_time(time_str)
        trigger = CronTrigger(hour=hour, minute=minute)
        self.scheduler.add_job(
            post_movie_of_the_day,
            trigger=trigger,
            id=f"{JOB_ID_PREFIX}{guild_id}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            kwargs={
                "bot": self.bot,
                "guild_id": guild_id,
                "db_manager": self.db_manager,
                "tmdb_client": self.tmdb_client,
                "is_test": False,
            },
        )
        LOGGER.info(
            "Agendamento do Filme do Dia atualizado guild_id=%s horario=%s.",
            guild_id,
            time_str,
        )

    @motd.command(
        name="set_time",
        description="Define o horário diário do Filme do Dia no formato HH:MM.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(horario="Horário em formato HH:MM, usando America/Sao_Paulo.")
    async def set_time(self, interaction: discord.Interaction, horario: str) -> None:
        guild_id = self._interaction_guild_id(interaction)
        if guild_id is None:
            return

        if TIME_PATTERN.fullmatch(horario) is None:
            await interaction.response.send_message(
                "Formato inválido. Use `HH:MM`, por exemplo `18:00`.",
                ephemeral=True,
            )
            return

        config = await self._save_config(guild_id, schedule_time=horario)
        self._reschedule_guild(guild_id, config.schedule_time)
        await interaction.response.send_message(
            f"Horário do Filme do Dia configurado para `{config.schedule_time}`.",
            ephemeral=True,
        )

    @motd.command(
        name="set_channel",
        description="Define o canal onde o Filme do Dia será publicado.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(canal="Canal textual de publicação.")
    async def set_channel(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
    ) -> None:
        guild_id = self._interaction_guild_id(interaction)
        if guild_id is None:
            return

        config = await self._save_config(guild_id, channel_id=canal.id)
        self._reschedule_guild(guild_id, config.schedule_time)
        await interaction.response.send_message(
            f"Canal do Filme do Dia configurado para {canal.mention}.",
            ephemeral=True,
        )

    @motd.command(
        name="set_role",
        description="Define o cargo mencionado nas publicações do Filme do Dia.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(cargo="Cargo mencionado junto ao Filme do Dia.")
    async def set_role(
        self,
        interaction: discord.Interaction,
        cargo: discord.Role,
    ) -> None:
        guild_id = self._interaction_guild_id(interaction)
        if guild_id is None:
            return

        config = await self._save_config(guild_id, role_id=cargo.id)
        self._reschedule_guild(guild_id, config.schedule_time)
        await interaction.response.send_message(
            f"Cargo do Filme do Dia configurado para {cargo.mention}.",
            ephemeral=True,
        )

    @motd.command(
        name="blacklist_remove",
        description="Remove um filme da blacklist pelo ID do TMDB.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(id="ID numérico do filme no TMDB.")
    async def blacklist_remove(self, interaction: discord.Interaction, id: int) -> None:
        guild_id = self._interaction_guild_id(interaction)
        if guild_id is None:
            return

        removed = await self.db_manager.remove_from_blacklist(guild_id, id)
        if removed:
            message = f"Filme `{id}` removido da blacklist."
        else:
            message = f"Nenhum registro com TMDB ID `{id}` foi encontrado."

        await interaction.response.send_message(message, ephemeral=True)

    @motd.command(
        name="blacklist_view",
        description="Lista os filmes registrados na blacklist deste servidor.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def blacklist_view(self, interaction: discord.Interaction) -> None:
        guild_id = self._interaction_guild_id(interaction)
        if guild_id is None:
            return

        await interaction.response.defer(ephemeral=True)
        entries = await self.db_manager.get_blacklist(guild_id)
        if not entries:
            await interaction.followup.send(
                "A blacklist deste servidor ainda está vazia.",
                ephemeral=True,
            )
            return

        total_pages = (len(entries) + BLACKLIST_PAGE_SIZE - 1) // BLACKLIST_PAGE_SIZE
        for page_index, start in enumerate(
            range(0, len(entries), BLACKLIST_PAGE_SIZE),
            start=1,
        ):
            chunk = entries[start : start + BLACKLIST_PAGE_SIZE]
            embed = self._build_blacklist_embed(chunk, page_index, total_pages)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @motd.command(
        name="test_movie",
        description="Publica um Filme do Dia de teste sem gravar na blacklist.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def test_movie(self, interaction: discord.Interaction) -> None:
        guild_id = self._interaction_guild_id(interaction)
        if guild_id is None:
            return

        await interaction.response.defer(ephemeral=True)
        try:
            posted = await post_movie_of_the_day(
                self.bot,
                guild_id,
                self.db_manager,
                self.tmdb_client,
                is_test=True,
            )
        except Exception:
            LOGGER.error(
                "Falha ao executar /test_movie guild_id=%s.",
                guild_id,
                exc_info=True,
            )
            await interaction.followup.send(
                "Não consegui publicar o filme de teste agora.",
                ephemeral=True,
            )
            return

        if posted:
            message = "Filme de teste publicado sem alterar a blacklist."
        else:
            message = "O filme de teste não foi publicado. Verifique a configuração e permissões."

        await interaction.followup.send(message, ephemeral=True)

    @set_time.error
    @set_channel.error
    @set_role.error
    @blacklist_remove.error
    @blacklist_view.error
    @test_movie.error
    async def movie_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "Você precisa ser administrador(a) para usar esse comando."
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "Esse comando só pode ser usado dentro de um servidor."
        else:
            LOGGER.error(
                "Erro em comando do MovieCog command=%s.",
                interaction.command.name if interaction.command else "?",
                exc_info=True,
            )
            message = "Ocorreu um erro ao executar o comando do Filme do Dia."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def _save_config(
        self,
        guild_id: int,
        *,
        channel_id: int | None | object = UNSET,
        role_id: int | None | object = UNSET,
        schedule_time: str | object = UNSET,
    ) -> MovieGuildConfig:
        current = await self.db_manager.get_config(guild_id)
        config = self._normalize_config(guild_id, current)

        next_channel_id = (
            config.channel_id if channel_id is UNSET else self._coerce_optional_int(channel_id)
        )
        next_role_id = (
            config.role_id if role_id is UNSET else self._coerce_optional_int(role_id)
        )
        next_schedule_time = (
            config.schedule_time if schedule_time is UNSET else str(schedule_time)
        )

        await self.db_manager.set_config(
            guild_id,
            next_channel_id,
            next_role_id,
            next_schedule_time,
        )

        return MovieGuildConfig(
            guild_id=guild_id,
            channel_id=next_channel_id,
            role_id=next_role_id,
            schedule_time=next_schedule_time,
        )

    @staticmethod
    def _normalize_config(guild_id: int, config: object | None) -> MovieGuildConfig:
        if config is None:
            return MovieGuildConfig(
                guild_id=guild_id,
                channel_id=None,
                role_id=None,
                schedule_time=DEFAULT_SCHEDULE_TIME,
            )

        return MovieGuildConfig(
            guild_id=guild_id,
            channel_id=MovieCog._coerce_optional_int(getattr(config, "channel_id", None)),
            role_id=MovieCog._coerce_optional_int(getattr(config, "role_id", None)),
            schedule_time=str(
                getattr(config, "schedule_time", DEFAULT_SCHEDULE_TIME)
                or DEFAULT_SCHEDULE_TIME
            ),
        )

    @staticmethod
    def _parse_schedule_time(time_str: str) -> tuple[int, int]:
        if TIME_PATTERN.fullmatch(time_str) is None:
            raise ValueError(f"Horario invalido: {time_str}")

        hour_text, minute_text = time_str.split(":", maxsplit=1)
        return int(hour_text), int(minute_text)

    @staticmethod
    def _interaction_guild_id(interaction: discord.Interaction) -> int | None:
        if interaction.guild_id is None:
            return None
        return int(interaction.guild_id)

    @staticmethod
    def _coerce_optional_int(value: object) -> int | None:
        if value is None or isinstance(value, bool):
            return None

        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

        return parsed if parsed > 0 else None

    @staticmethod
    def _build_blacklist_embed(
        entries: list[object],
        page_index: int,
        total_pages: int,
    ) -> discord.Embed:
        lines = ["TMDB ID    | Título                         | Data"]
        lines.append("-" * 60)

        for entry in entries:
            tmdb_id = str(getattr(entry, "tmdb_id", "N/A"))
            title = str(getattr(entry, "movie_title", None) or "N/A")
            date_added = str(getattr(entry, "date_added", "N/A"))
            lines.append(
                f"{tmdb_id[:10]:<10} | {title[:30]:<30} | {date_added[:16]}"
            )

        embed = discord.Embed(
            title=f"Blacklist de Filmes ({page_index}/{total_pages})",
            description=f"```text\n{chr(10).join(lines)}\n```",
            color=discord.Color.dark_gold(),
        )
        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MovieCog(bot))


__all__ = [
    "MovieCog",
    "setup",
]
