from __future__ import annotations

import math

import discord

from ..xp_service import XpService
from ..utils import LeaderboardEntry
from ..rank_badges import RankBadgeService
from .xp_card_renderer import XpCardRenderer


def build_leaderboard_embed(guild: discord.Guild, entries: list[LeaderboardEntry], page: int, total_entries: int, page_size: int) -> discord.Embed:
    embed = discord.Embed(title=f"leaderboard • {guild.name}", color=discord.Color.dark_purple())
    if not entries:
        embed.description = "ninguém ganhou xp ainda neste servidor."
    else:
        lines = []
        for entry in entries:
            lines.append(
                f"**{entry.position}.** {discord.utils.escape_markdown(entry.display_name)} — nível **{entry.level}** — **{entry.total_xp:,} xp** — faltam **{entry.remaining_to_next:,} xp**".replace(",", ".")
            )
        embed.description = "\n".join(lines)
    total_pages = max(1, math.ceil(total_entries / page_size))
    embed.set_footer(text=f"página {page + 1}/{total_pages} • {total_entries} usuário(s) ranqueado(s)")
    return embed


class FullLeaderboardPaginator(discord.ui.View):
    def __init__(self, service: XpService, guild: discord.Guild, author_id: int, *, page_size: int = 10, timeout: float = 180) -> None:
        super().__init__(timeout=timeout)
        self.service = service
        self.guild = guild
        self.author_id = author_id
        self.page_size = page_size
        self.page = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("esse paginador pertence a quem abriu o leaderboard.", ephemeral=True)
            return False
        return True

    async def _embed(self) -> discord.Embed:
        page = await self.service.get_leaderboard_page(self.guild, self.page, self.page_size)
        return build_leaderboard_embed(self.guild, page.entries, page.page, page.total_entries, page.page_size)

    @discord.ui.button(label="anterior", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        await interaction.response.edit_message(embed=await self._embed(), view=self)

    @discord.ui.button(label="próxima", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        current = await self.service.get_leaderboard_page(self.guild, self.page, self.page_size)
        if not current.entries and self.page > 0:
            self.page -= 1
            current = await self.service.get_leaderboard_page(self.guild, self.page, self.page_size)
        await interaction.response.edit_message(
            embed=build_leaderboard_embed(self.guild, current.entries, current.page, current.total_entries, current.page_size),
            view=self,
        )


class RankCardView(discord.ui.View):
    def __init__(self, service: XpService, cards: XpCardRenderer, badges: RankBadgeService, *, timeout: float = 180) -> None:
        super().__init__(timeout=timeout)
        self.service = service
        self.cards = cards
        self.badges = badges
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="leaderboard", style=discord.ButtonStyle.primary)
    async def leaderboard_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("esse botão só funciona dentro de servidor.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            entries = await self.service.get_leaderboard_entries(interaction.guild, 5)
            resolved: list[tuple[LeaderboardEntry, discord.Member | discord.User | None, bytes | None]] = []
            for entry in entries:
                member = interaction.guild.get_member(entry.user_id)
                if member is None:
                    try:
                        member = await interaction.guild.fetch_member(entry.user_id)
                    except discord.HTTPException:
                        member = None
                
                badge_image_bytes = None
                if isinstance(member, discord.Member):
                    _badge, badge_image_bytes = await self.badges.resolve_member_badge_image(member)
                
                resolved.append((entry, member, badge_image_bytes))
            image = await self.cards.render_leaderboard_card(guild=interaction.guild, entries=resolved)
            await interaction.followup.send(file=discord.File(image, filename="leaderboard.png"), ephemeral=True)
        except Exception:
            page = await self.service.get_leaderboard_page(interaction.guild, page=0, page_size=5)
            embed = build_leaderboard_embed(interaction.guild, page.entries, page.page, page.total_entries, page.page_size)
            await interaction.followup.send(embed=embed, ephemeral=True)


class LeaderboardView(discord.ui.View):
    def __init__(self, service: XpService, *, timeout: float = 180) -> None:
        super().__init__(timeout=timeout)
        self.service = service
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="lista completa", style=discord.ButtonStyle.secondary)
    async def full_list_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("esse botão só funciona dentro de servidor.", ephemeral=True)
            return
        paginator = FullLeaderboardPaginator(self.service, interaction.guild, interaction.user.id)
        embed = await paginator._embed()
        await interaction.response.send_message(embed=embed, view=paginator, ephemeral=True)
