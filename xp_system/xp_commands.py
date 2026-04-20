from __future__ import annotations

import logging
import pathlib

import discord
from discord import app_commands
from discord.ext import commands

from .xp_cards import XpCardRenderer
from .xp_models import XpDifficulty
from .xp_repository import XpRepository
from .xp_service import XpService
from .xp_views import LeaderboardView, RankCardView, build_leaderboard_embed

LOGGER = logging.getLogger("baphomet.xp")
DATA_DIR = pathlib.Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "baphomet_xp.sqlite3"

DIFFICULTY_CHOICES = [
    app_commands.Choice(name="muito fácil", value=XpDifficulty.VERY_EASY.value),
    app_commands.Choice(name="fácil", value=XpDifficulty.EASY.value),
    app_commands.Choice(name="normal", value=XpDifficulty.NORMAL.value),
    app_commands.Choice(name="difícil", value=XpDifficulty.HARD.value),
    app_commands.Choice(name="insano", value=XpDifficulty.INSANE.value),
]

GuildChannelParam = discord.TextChannel | discord.VoiceChannel | discord.StageChannel | discord.ForumChannel


async def ensure_xp_runtime(bot: commands.Bot) -> None:
    if getattr(bot, "xp_service", None) is not None:
        return
    repository = XpRepository(str(DB_PATH))
    await repository.connect()
    bot.xp_repository = repository
    bot.xp_service = XpService(repository, logger=LOGGER)
    bot.xp_cards = XpCardRenderer()


class XpPublicCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.service: XpService = bot.xp_service
        self.cards: XpCardRenderer = bot.xp_cards
        self.logger = LOGGER

    async def cog_unload(self) -> None:
        pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        try:
            result = await self.service.process_message(message)
            if (
                result is None
                or message.guild is None
                or not isinstance(message.author, discord.Member)
                or result.new_level <= result.old_level
            ):
                return
            config = await self.service.get_guild_config(message.guild.id)
            await self.service.grant_level_rewards(message.author, result.new_level)
            channel = message.guild.get_channel(config.levelup_channel_id) if config.levelup_channel_id else message.channel
            if channel is None:
                return
            embed = discord.Embed(
                description=f"🎉 {message.author.mention} subiu para o **nível {result.new_level}**!",
                color=discord.Color.from_rgb(120, 60, 240),
            )
            try:
                await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                return
        except Exception:
            self.logger.exception("falha ao processar ganho de xp", exc_info=True)

    @app_commands.command(name="rank", description="mostra o rank individual de um usuário")
    @app_commands.guild_only()
    @app_commands.describe(member="membro que você quer consultar")
    async def rank(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("esse comando só funciona dentro de servidor.", ephemeral=True)
            return
        target = member or interaction.user
        if getattr(target, "bot", False):
            await interaction.response.send_message("bots não entram no sistema de xp.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        snapshot = await self.service.get_rank_snapshot(interaction.guild, target)
        view = RankCardView(self.service, self.cards)
        try:
            image = await self.cards.render_rank_card(guild=interaction.guild, member=target, snapshot=snapshot)
            await interaction.edit_original_response(attachments=[discord.File(image, filename="rank.png")], view=view)
        except Exception:
            embed = discord.Embed(title="rank", color=discord.Color.dark_purple())
            embed.description = (
                f"**{snapshot.display_name}**\n"
                f"nível **{snapshot.level}**\n"
                f"xp total **{snapshot.total_xp:,}**\n"
                f"progresso **{snapshot.xp_into_level}/{snapshot.xp_for_next_level}**\n"
                f"posição **{snapshot.position or 'sem posição'}**"
            ).replace(",", ".")
            await interaction.edit_original_response(embed=embed, view=view)
        view.message = await interaction.original_response()

    @app_commands.command(name="leaderboard", description="mostra o top 5 do servidor em imagem")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("esse comando só funciona dentro de servidor.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        view = LeaderboardView(self.service)
        entries = await self.service.get_leaderboard_entries(interaction.guild, 5)
        resolved: list[tuple] = []
        for entry in entries:
            member = interaction.guild.get_member(entry.user_id)
            if member is None:
                try:
                    member = await interaction.guild.fetch_member(entry.user_id)
                except discord.HTTPException:
                    member = None
            resolved.append((entry, member))
        try:
            image = await self.cards.render_leaderboard_card(guild=interaction.guild, entries=resolved)
            await interaction.edit_original_response(attachments=[discord.File(image, filename="leaderboard.png")], view=view)
        except Exception:
            page = await self.service.get_leaderboard_page(interaction.guild, page=0, page_size=5)
            embed = build_leaderboard_embed(interaction.guild, page.entries, page.page, page.total_entries, page.page_size)
            await interaction.edit_original_response(embed=embed, view=view)
        view.message = await interaction.original_response()


class XpAdminCommands(commands.GroupCog, group_name="xp", group_description="configurações e administração do sistema de xp"):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot
        self.service: XpService = bot.xp_service

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message("esse comando só funciona dentro de servidor.", ephemeral=True)
            return False
        return True

    @app_commands.command(name="difficulty", description="altera a dificuldade da curva de progressão")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(difficulty=DIFFICULTY_CHOICES)
    async def difficulty(self, interaction: discord.Interaction, difficulty: app_commands.Choice[str]) -> None:
        config = await self.service.update_guild_config(interaction.guild.id, difficulty=XpDifficulty(difficulty.value))
        await interaction.response.send_message(f"dificuldade atualizada para **{config.difficulty.label}**.", ephemeral=True)

    @app_commands.command(name="cooldown", description="altera o cooldown de ganho de xp por usuário")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cooldown(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 3600]) -> None:
        config = await self.service.update_guild_config(interaction.guild.id, cooldown_seconds=seconds)
        await interaction.response.send_message(f"cooldown atualizado para **{config.cooldown_seconds}s**.", ephemeral=True)

    @app_commands.command(name="xp-range", description="altera a faixa de xp recebida por mensagem")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def xp_range(self, interaction: discord.Interaction, min_xp: app_commands.Range[int, 1, 1000], max_xp: app_commands.Range[int, 1, 1000]) -> None:
        if min_xp > max_xp:
            await interaction.response.send_message("o mínimo não pode ser maior que o máximo.", ephemeral=True)
            return
        config = await self.service.update_guild_config(interaction.guild.id, min_xp_per_message=min_xp, max_xp_per_message=max_xp)
        await interaction.response.send_message(f"faixa de xp atualizada para **{config.min_xp_per_message}-{config.max_xp_per_message}**.", ephemeral=True)

    @app_commands.command(name="ignore-channel", description="ativa ou desativa ignore de um canal")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ignore_channel(self, interaction: discord.Interaction, channel: GuildChannelParam, enabled: bool) -> None:
        await self.service.set_ignored_channel(interaction.guild.id, channel.id, enabled)
        status = "ignorado" if enabled else "removido da lista de ignore"
        await interaction.response.send_message(f"canal **{channel.name}**: **{status}**.", ephemeral=True)

    @app_commands.command(name="ignore-category", description="ativa ou desativa ignore de uma categoria")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ignore_category(self, interaction: discord.Interaction, category: discord.CategoryChannel, enabled: bool) -> None:
        await self.service.set_ignored_category(interaction.guild.id, category.id, enabled)
        status = "ignorada" if enabled else "removida da lista de ignore"
        await interaction.response.send_message(f"categoria **{category.name}**: **{status}**.", ephemeral=True)

    @app_commands.command(name="ignore-role", description="ativa ou desativa ignore de um cargo")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ignore_role(self, interaction: discord.Interaction, role: discord.Role, enabled: bool) -> None:
        await self.service.set_ignored_role(interaction.guild.id, role.id, enabled)
        status = "ignorado" if enabled else "removido da lista de ignore"
        await interaction.response.send_message(f"cargo {role.mention}: **{status}**.", ephemeral=True)

    @app_commands.command(name="give", description="adiciona xp manualmente para um membro")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def give(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000], reason: str | None = None) -> None:
        result = await self.service.give_xp(interaction.guild, member, amount, interaction.user.id, reason)
        await self.service.grant_level_rewards(member, result.new_level)
        await interaction.response.send_message(
            f"{amount:,} xp adicionados para **{member.display_name}**. nível: **{result.old_level} → {result.new_level}**.".replace(",", "."),
            ephemeral=True,
        )

    @app_commands.command(name="remove", description="remove xp manualmente de um membro")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000], reason: str | None = None) -> None:
        result = await self.service.remove_xp(interaction.guild, member, amount, interaction.user.id, reason)
        await interaction.response.send_message(
            f"{amount:,} xp removidos de **{member.display_name}**. nível: **{result.old_level} → {result.new_level}**.".replace(",", "."),
            ephemeral=True,
        )

    @app_commands.command(name="reset", description="zera o xp de um membro")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reset(self, interaction: discord.Interaction, member: discord.Member, reason: str | None = None) -> None:
        result = await self.service.reset_xp(interaction.guild, member, interaction.user.id, reason)
        await interaction.response.send_message(
            f"xp de **{member.display_name}** resetado. total removido: **{abs(result.delta_xp):,} xp**.".replace(",", "."),
            ephemeral=True,
        )

    @app_commands.command(name="config", description="mostra a configuração atual do sistema de xp")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config(self, interaction: discord.Interaction) -> None:
        config = await self.service.get_guild_config(interaction.guild.id)
        levelup = f"<#{config.levelup_channel_id}>" if config.levelup_channel_id else "mesmo canal da mensagem"
        level_roles = "\n".join(f"nível {level} → <@&{role_id}>" for level, role_id in sorted(config.level_roles.items())) or "nenhum"
        embed = discord.Embed(title="configuração de xp", color=discord.Color.dark_purple())
        embed.description = (
            f"**dificuldade:** {config.difficulty.label}\n"
            f"**cooldown:** {config.cooldown_seconds}s\n"
            f"**faixa de xp:** {config.min_xp_per_message}-{config.max_xp_per_message}\n"
            f"**mín. de caracteres:** {config.min_message_length}\n"
            f"**mín. de palavras únicas:** {config.min_unique_words}\n"
            f"**janela anti-repeat:** {config.anti_repeat_window_seconds}s\n"
            f"**similaridade anti-repeat:** {config.anti_repeat_similarity:.2f}\n"
            f"**canal de level up:** {levelup}\n"
            f"**canais ignorados:** {len(config.ignored_channel_ids)}\n"
            f"**categorias ignoradas:** {len(config.ignored_category_ids)}\n"
            f"**cargos ignorados:** {len(config.ignored_role_ids)}\n"
            f"**cargos por nível:**\n{level_roles}"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="levelup-channel", description="define o canal dos anúncios de level up")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def levelup_channel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        config = await self.service.update_guild_config(interaction.guild.id, levelup_channel_id=channel.id if channel else None)
        if config.levelup_channel_id:
            await interaction.response.send_message(f"canal de level up definido para <#{config.levelup_channel_id}>.", ephemeral=True)
        else:
            await interaction.response.send_message("anúncios de level up voltarão para o mesmo canal da mensagem.", ephemeral=True)

    @app_commands.command(name="level-role-add", description="configura um cargo automático para um nível")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def level_role_add(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000], role: discord.Role) -> None:
        await self.service.set_level_role(interaction.guild.id, level, role.id)
        await interaction.response.send_message(f"cargo {role.mention} configurado para o nível **{level}**.", ephemeral=True)

    @app_commands.command(name="level-role-remove", description="remove o cargo automático de um nível")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def level_role_remove(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000]) -> None:
        _config, removed = await self.service.remove_level_role(interaction.guild.id, level)
        if not removed:
            await interaction.response.send_message(f"não havia cargo configurado para o nível **{level}**.", ephemeral=True)
            return
        await interaction.response.send_message(f"cargo automático do nível **{level}** removido.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await ensure_xp_runtime(bot)
    await bot.add_cog(XpPublicCommands(bot))
    await bot.add_cog(XpAdminCommands(bot))
