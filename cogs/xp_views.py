from __future__ import annotations

import math

import discord

from .xp_cards import XpCardRenderer
from .xp_models import LeaderboardEntry
from .xp_service import XpService


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
    def __init__(self, service: XpService, cards: XpCardRenderer, *, timeout: float = 180) -> None:
        super().__init__(timeout=timeout)
        self.service = service
        self.cards = cards
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
            resolved: list[tuple[LeaderboardEntry, discord.Member | discord.User | None]] = []
            for entry in entries:
                member = interaction.guild.get_member(entry.user_id)
                if member is None:
                    try:
                        member = await interaction.guild.fetch_member(entry.user_id)
                    except discord.HTTPException:
                        member = None
                resolved.append((entry, member))
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


# ══════════════════════════════════════════════════════════════════════════════
#  Paginador Visual do Leaderboard (Imagem Pillow + Botões)
# ══════════════════════════════════════════════════════════════════════════════

async def _resolve_entries(
    guild: discord.Guild,
    entries: list[LeaderboardEntry],
) -> list[tuple[LeaderboardEntry, discord.Member | discord.User | None]]:
    """Resolve os membros do Discord a partir das entries do banco de dados."""
    resolved: list[tuple[LeaderboardEntry, discord.Member | discord.User | None]] = []
    for entry in entries:
        member = guild.get_member(entry.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(entry.user_id)
            except discord.HTTPException:
                member = None
        resolved.append((entry, member))
    return resolved


class LeaderboardImagePaginator(discord.ui.View):
    """View paginada com imagem Pillow. Usa LIMIT/OFFSET no banco de dados."""

    PAGE_SIZE = 5

    def __init__(
        self,
        service: XpService,
        cards: XpCardRenderer,
        guild: discord.Guild,
        author_id: int,
        total_entries: int,
        *,
        timeout: float = 180,
    ) -> None:
        super().__init__(timeout=timeout)
        self.service = service
        self.cards = cards
        self.guild = guild
        self.author_id = author_id
        self.total_entries = total_entries
        self.current_page = 0
        self.total_pages = max(1, math.ceil(total_entries / self.PAGE_SIZE))
        self.message: discord.Message | None = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        """Atualiza o estado dos botões com base na página atual."""
        self.btn_prev.disabled = self.current_page <= 0
        self.btn_indicator.label = f"Página {self.current_page + 1}/{self.total_pages}"
        self.btn_next.disabled = self.current_page >= self.total_pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "⛔ Esse paginador pertence a quem abriu o leaderboard.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def _render_page(self) -> discord.File:
        """Busca os 5 usuários da página atual no BD e renderiza o card."""
        page_result = await self.service.get_leaderboard_page(
            self.guild, self.current_page, self.PAGE_SIZE
        )
        # Atualiza o total caso membros tenham entrado/saído entre cliques
        self.total_entries = page_result.total_entries
        self.total_pages = max(1, math.ceil(self.total_entries / self.PAGE_SIZE))

        resolved = await _resolve_entries(self.guild, page_result.entries)

        page_label = f"Página {self.current_page + 1}/{self.total_pages}"
        image = await self.cards.render_leaderboard_card(
            guild=self.guild, entries=resolved, page_label=page_label
        )
        return discord.File(image, filename="leaderboard.png")

    # ── Botões ──────────────────────────────────────────────────────

    @discord.ui.button(label="⬅️ Anterior", style=discord.ButtonStyle.secondary, custom_id="lb_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.current_page = max(0, self.current_page - 1)
        self._update_buttons()
        file = await self._render_page()
        await interaction.edit_original_response(attachments=[file], view=self)

    @discord.ui.button(label="Página 1/1", style=discord.ButtonStyle.primary, disabled=True, custom_id="lb_indicator")
    async def btn_indicator(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Botão decorativo — nunca clicável
        await interaction.response.defer()

    @discord.ui.button(label="Próxima ➡️", style=discord.ButtonStyle.secondary, custom_id="lb_next")
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        self._update_buttons()
        file = await self._render_page()
        await interaction.edit_original_response(attachments=[file], view=self)
