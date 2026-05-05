from __future__ import annotations

import re
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from .cog import TierTemplateCog


SESSION_ACTION_RE = re.compile(
    r"^tsess:(?P<session_id>[0-9a-fA-F-]{36}):(?P<action>prev|next|apply|inventory|reset|finalize)$"
)


class TierSessionActionDynamicItem(discord.ui.DynamicItem[discord.ui.Button], template=SESSION_ACTION_RE):
    ACTION_META: dict[str, tuple[str, str, discord.ButtonStyle]] = {
        "prev": ("Anterior", "⬅️", discord.ButtonStyle.secondary),
        "next": ("Próxima", "➡️", discord.ButtonStyle.secondary),
        "apply": ("Aplicar", "✅", discord.ButtonStyle.success),
        "inventory": ("Inventário", "↩️", discord.ButtonStyle.secondary),
        "reset": ("Resetar", "🔄", discord.ButtonStyle.danger),
        "finalize": ("Finalizar", "🏁", discord.ButtonStyle.primary),
    }

    def __init__(self, *, session_id: str, action: str) -> None:
        label, emoji, style = self.ACTION_META.get(action, ("Ação", "⚙️", discord.ButtonStyle.secondary))
        self.session_id = session_id
        self.action = action
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=f"tsess:{session_id}:{action}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item,
        match: re.Match[str],
    ) -> TierSessionActionDynamicItem:
        return cls(session_id=match["session_id"], action=match["action"])

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("TierTemplateCog")
        if cog is None:
            await interaction.response.send_message("⚠️ O sistema de templates ainda está inicializando.", ephemeral=True)
            return
        typed_cog: TierTemplateCog = cog
        session = await typed_cog.session_repository.get_session(self.session_id)
        if session is None:
            await interaction.response.send_message("⚠️ Essa sessão não existe mais ou expirou.", ephemeral=True)
            return
        if interaction.user.id != session.owner_id:
            await interaction.response.send_message(
                "⚠️ Essa tierlist não é sua, fofoqueira. Crie sua própria sessão com /tier template usar.",
                ephemeral=True,
            )
            return
        if session.status.value != "ACTIVE":
            await interaction.response.send_message("⚠️ Essa sessão já foi finalizada ou expirou.", ephemeral=True)
            return

        if self.action == "prev":
            await typed_cog.change_session_page(interaction, self.session_id, session.current_inventory_page - 1)
        elif self.action == "next":
            await typed_cog.change_session_page(interaction, self.session_id, session.current_inventory_page + 1)
        elif self.action == "apply":
            await typed_cog.apply_session_selection(interaction, self.session_id)
        elif self.action == "inventory":
            await typed_cog.move_selected_session_item_to_inventory(interaction, self.session_id)
        elif self.action == "reset":
            from .session_views import ConfirmResetSessionView

            view = ConfirmResetSessionView(
                cog=typed_cog,
                session_id=self.session_id,
                owner_id=session.owner_id,
                session_message=interaction.message,
            )
            await interaction.response.send_message("Resetar todos os itens para o inventário?", view=view, ephemeral=True)
        elif self.action == "finalize":
            await typed_cog.finalize_tier_session(interaction, self.session_id)
