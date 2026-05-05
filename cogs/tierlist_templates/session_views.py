from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import discord

from .messages import (
    SESSION_NOT_AVAILABLE_MESSAGE,
    SESSION_PERMISSION_DENIED_MESSAGE,
    inactive_session_message,
)
from .models import SessionStatus, TierSessionItem, TierTemplateItem
from .session_renderer import SessionRenderSnapshot

if TYPE_CHECKING:
    from .cog import TierTemplateCog


LOGGER = logging.getLogger("baphomet.tierlist_templates.session_views")

INVENTORY_VALUE = "__inventory__"
NO_ITEM_VALUE = "__no_item__"


def session_component_id(session_id: str, action: str) -> str:
    return f"tsess:{session_id}:{action}"


class SessionItemSelect(discord.ui.Select):
    def __init__(self, *, view_ref: "TierSessionView", snapshot: SessionRenderSnapshot, page_items: list[TierSessionItem], page_start: int) -> None:
        options: list[discord.SelectOption] = []
        if not page_items:
            options.append(discord.SelectOption(label="Nenhum item nesta página", value=NO_ITEM_VALUE))
        else:
            for offset, session_item in enumerate(page_items):
                template_item = snapshot.template_items_by_id.get(session_item.template_item_id)
                label = view_ref.cog.session_item_label(template_item, page_start + offset, session_item=session_item)
                options.append(
                    discord.SelectOption(
                        label=label[:100],
                        value=session_item.id,
                        description=view_ref.item_location_label(snapshot, session_item)[:100],
                        default=session_item.id == snapshot.session.selected_item_id,
                    )
                )
        super().__init__(
            placeholder="Escolha um item",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
            custom_id=session_component_id(snapshot.session.id, "item"),
        )
        self.view_ref = view_ref
        self.disabled = not page_items or snapshot.session.status != SessionStatus.ACTIVE

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == NO_ITEM_VALUE:
            await interaction.response.send_message("⚠️ Não há item nesta página.", ephemeral=True)
            return
        await self.view_ref.cog.select_session_item(interaction, self.view_ref.session_id, self.values[0])


class SessionTierSelect(discord.ui.Select):
    def __init__(self, *, view_ref: "TierSessionView", snapshot: SessionRenderSnapshot) -> None:
        options = [
            discord.SelectOption(
                label="Inventário / não usado",
                value=INVENTORY_VALUE,
                default=snapshot.session.selected_tier_id is None,
            )
        ]
        for tier in snapshot.tiers[:24]:
            tier_id = str(tier.get("id") or tier.get("label") or "?")
            label = str(tier.get("label") or tier_id)
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=tier_id,
                    default=snapshot.session.selected_tier_id == tier_id,
                )
            )
        super().__init__(
            placeholder="Escolha o destino",
            min_values=1,
            max_values=1,
            options=options,
            row=1,
            custom_id=session_component_id(snapshot.session.id, "tier"),
        )
        self.view_ref = view_ref
        self.disabled = snapshot.session.status != SessionStatus.ACTIVE

    async def callback(self, interaction: discord.Interaction) -> None:
        tier_id = None if self.values[0] == INVENTORY_VALUE else self.values[0]
        await self.view_ref.cog.select_session_tier(interaction, self.view_ref.session_id, tier_id)


class TierSessionView(discord.ui.View):
    def __init__(
        self,
        *,
        cog: TierTemplateCog,
        snapshot: SessionRenderSnapshot,
        page_items: list[TierSessionItem],
        page_start: int,
        max_page: int,
        disabled: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.session_id = snapshot.session.id
        self.owner_id = snapshot.session.owner_id
        self.status = snapshot.session.status
        self.page = snapshot.session.current_inventory_page
        self.max_page = max_page
        self.disabled_all = disabled or snapshot.session.status != SessionStatus.ACTIVE
        self._apply_custom_ids()
        self.add_item(SessionItemSelect(view_ref=self, snapshot=snapshot, page_items=page_items, page_start=page_start))
        self.add_item(SessionTierSelect(view_ref=self, snapshot=snapshot))
        self._sync_buttons()

    def _apply_custom_ids(self) -> None:
        self.previous_button.custom_id = session_component_id(self.session_id, "prev")
        self.next_button.custom_id = session_component_id(self.session_id, "next")
        self.apply_button.custom_id = session_component_id(self.session_id, "apply")
        self.inventory_button.custom_id = session_component_id(self.session_id, "inventory")
        self.reset_button.custom_id = session_component_id(self.session_id, "reset")
        self.finalize_button.custom_id = session_component_id(self.session_id, "finalize")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        session = await self.cog.session_repository.get_session(self.session_id)
        if session is None:
            await interaction.response.send_message(SESSION_NOT_AVAILABLE_MESSAGE, ephemeral=True)
            return False
        if interaction.user.id != session.owner_id:
            LOGGER.info(
                "permission_denied surface=session_view user_id=%s owner_id=%s session_id=%s",
                interaction.user.id,
                session.owner_id,
                self.session_id,
            )
            await interaction.response.send_message(SESSION_PERMISSION_DENIED_MESSAGE, ephemeral=True)
            return False
        if session.status != SessionStatus.ACTIVE:
            await interaction.response.send_message(inactive_session_message(session.status), ephemeral=True)
            return False
        return True

    def _sync_buttons(self) -> None:
        self.previous_button.disabled = self.disabled_all or self.page <= 0
        self.next_button.disabled = self.disabled_all or self.page >= self.max_page
        self.apply_button.disabled = self.disabled_all
        self.inventory_button.disabled = self.disabled_all
        self.reset_button.disabled = self.disabled_all
        self.finalize_button.disabled = self.status != SessionStatus.ACTIVE
        for child in self.children:
            if self.disabled_all:
                child.disabled = True

    def item_location_label(self, snapshot: SessionRenderSnapshot, session_item: TierSessionItem) -> str:
        if session_item.is_unused or not session_item.current_tier_id:
            return "Inventário"
        label_by_id = {
            str(tier.get("id") or tier.get("label") or "?"): str(tier.get("label") or tier.get("id") or "?")
            for tier in snapshot.tiers
        }
        return f"Tier {label_by_id.get(session_item.current_tier_id, session_item.current_tier_id)}"

    @discord.ui.button(emoji="⬅️", label="Anterior", style=discord.ButtonStyle.secondary, row=2, custom_id="tier_session:previous")
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.change_session_page(interaction, self.session_id, self.page - 1)

    @discord.ui.button(emoji="➡️", label="Próxima", style=discord.ButtonStyle.secondary, row=2, custom_id="tier_session:next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.change_session_page(interaction, self.session_id, self.page + 1)

    @discord.ui.button(emoji="✅", label="Aplicar", style=discord.ButtonStyle.success, row=2, custom_id="tier_session:apply")
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.apply_session_selection(interaction, self.session_id)

    @discord.ui.button(emoji="↩️", label="Inventário", style=discord.ButtonStyle.secondary, row=2, custom_id="tier_session:inventory")
    async def inventory_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.move_selected_session_item_to_inventory(interaction, self.session_id)

    @discord.ui.button(emoji="🔄", label="Resetar", style=discord.ButtonStyle.danger, row=2, custom_id="tier_session:reset")
    async def reset_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = ConfirmResetSessionView(cog=self.cog, session_id=self.session_id, owner_id=self.owner_id, session_message=interaction.message)
        await interaction.response.send_message("Resetar todos os itens para o inventário?", view=view, ephemeral=True)

    @discord.ui.button(emoji="🏁", label="Finalizar", style=discord.ButtonStyle.primary, row=3, custom_id="tier_session:finalize")
    async def finalize_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.finalize_tier_session(interaction, self.session_id)


class ConfirmResetSessionView(discord.ui.View):
    def __init__(self, *, cog: TierTemplateCog, session_id: str, owner_id: int, session_message: discord.Message | None) -> None:
        super().__init__(timeout=60)
        self.cog = cog
        self.session_id = session_id
        self.owner_id = owner_id
        self.session_message = session_message
        self.confirm_button.custom_id = session_component_id(session_id, "confirm_reset")
        self.cancel_button.custom_id = session_component_id(session_id, "cancel_reset")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        LOGGER.info(
            "permission_denied surface=session_reset_confirm user_id=%s owner_id=%s session_id=%s",
            interaction.user.id,
            self.owner_id,
            self.session_id,
        )
        await interaction.response.send_message(SESSION_PERMISSION_DENIED_MESSAGE, ephemeral=True)
        return False

    @discord.ui.button(label="Confirmar reset", style=discord.ButtonStyle.danger, custom_id="tier_session:confirm_reset")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.reset_tier_session(interaction, self.session_id, session_message=self.session_message)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary, custom_id="tier_session:cancel_reset")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Reset cancelado.", view=self)
