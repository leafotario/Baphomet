from __future__ import annotations

"""Comandos Públicos Do Sistema De XP."""

import discord
from discord import app_commands
from discord.ext import commands

from ..rendering import LeaderboardView, RankCardView, build_leaderboard_embed
from ..xp_runtime import XpRuntime


class XpUserCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, runtime: XpRuntime) -> None:
        self.bot = bot
        self.runtime = runtime

    @app_commands.command(name="rank", description="Exibe O Rank De Uma Alma ✨")
    @app_commands.guild_only()
    @app_commands.describe(member="Membro Que Você Quer Invocar No Rank")
    async def rank(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("🕯️ Este Comando Só Pode Ser Usado Dentro De Um Servidor.", ephemeral=True)
            return
        target = member or interaction.user
        if getattr(target, "bot", False):
            await interaction.response.send_message("🤖 Bots Não Participam Do Ritual De XP.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        snapshot = await self.runtime.service.get_rank_snapshot(interaction.guild, target)
        view = RankCardView(self.runtime.service, self.runtime.cards)
        try:
            image = await self.runtime.cards.render_rank_card(guild=interaction.guild, member=target, snapshot=snapshot)
            await interaction.edit_original_response(attachments=[discord.File(image, filename="rank.png")], view=view)
        except Exception:
            embed = discord.Embed(title="🔮 Rank De Prestígio", color=discord.Color.dark_purple())
            embed.description = (
                f"**{snapshot.display_name}**\n"
                f"Nível **{snapshot.level}**\n"
                f"XP Total **{snapshot.total_xp:,}**\n"
                f"Progresso **{snapshot.xp_into_level}/{snapshot.xp_for_next_level}**\n"
                f"Posição **{snapshot.position or 'Sem Posição'}**"
            ).replace(",", ".")
            await interaction.edit_original_response(embed=embed, view=view)
        view.message = await interaction.original_response()

    @app_commands.command(name="leaderboard", description="Exibe O Top 5 Da Glória 🏆")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("🕯️ Este Comando Só Pode Ser Usado Dentro De Um Servidor.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        view = LeaderboardView(self.runtime.service)
        entries = await self.runtime.service.get_leaderboard_entries(interaction.guild, 5)
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
            image = await self.runtime.cards.render_leaderboard_card(guild=interaction.guild, entries=resolved)
            await interaction.edit_original_response(attachments=[discord.File(image, filename="leaderboard.png")], view=view)
        except Exception:
            page = await self.runtime.service.get_leaderboard_page(interaction.guild, page=0, page_size=5)
            embed = build_leaderboard_embed(interaction.guild, page.entries, page.page, page.total_entries, page.page_size)
            await interaction.edit_original_response(embed=embed, view=view)
        view.message = await interaction.original_response()
