from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from .modals import TemplateImageSourceModal, TemplateTextItemModal

if TYPE_CHECKING:
    from .cog import TierTemplateCog


LOGGER = logging.getLogger("baphomet.tierlist_templates.views")


class TemplateEditorView(discord.ui.View):
    def __init__(
        self,
        *,
        cog: TierTemplateCog,
        template_id: str,
        version_id: str,
        creator_id: int,
        page: int = 0,
        total_pages: int = 1,
        is_locked: bool = False,
        item_count: int = 0,
    ) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.template_id = template_id
        self.version_id = version_id
        self.creator_id = creator_id
        self.page = page
        self.total_pages = max(1, total_pages)
        self.is_locked = is_locked
        self.item_count = item_count
        self._sync_disabled_state()

    def _sync_disabled_state(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id in {
                "tier_template:add_text",
                "tier_template:add_url",
                "tier_template:add_avatar",
                "tier_template:add_wikipedia",
                "tier_template:add_spotify",
                "tier_template:remove",
                "tier_template:reorder",
                "tier_template:publish",
            }:
                child.disabled = self.is_locked
        self.remove_item_button.disabled = self.is_locked or self.item_count == 0
        self.reorder_button.disabled = self.is_locked or self.item_count < 2
        self.preview_button.disabled = self.item_count == 0
        self.previous_page_button.disabled = self.page <= 0
        self.next_page_button.disabled = self.page >= self.total_pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await self.cog.user_can_edit(interaction, self.creator_id):
            return True
        await interaction.response.send_message(
            "⚠️ Esse painel não é seu, bestie. Crie ou edite o seu próprio template.",
            ephemeral=True,
        )
        return False

    async def _ensure_unlocked(self, interaction: discord.Interaction) -> bool:
        version = await self.cog.template_repository.get_template_version(self.version_id)
        if version is None:
            await interaction.response.send_message("⚠️ Essa versão de template não existe mais.", ephemeral=True)
            return False
        if version.is_locked:
            await interaction.response.send_message(
                "🔒 Esse template já foi publicado. Para editar, crie uma nova versão/clonagem.",
                ephemeral=True,
            )
            return False
        return True

    async def _send_source_modal(
        self,
        interaction: discord.Interaction,
        *,
        source_key: str,
        title: str,
        input_label: str,
        input_placeholder: str,
    ) -> None:
        if not await self._ensure_unlocked(interaction):
            return
        await interaction.response.send_modal(
            TemplateImageSourceModal(
                self.cog,
                template_id=self.template_id,
                version_id=self.version_id,
                creator_id=self.creator_id,
                panel_message=interaction.message,
                source_key=source_key,
                title=title,
                input_label=input_label,
                input_placeholder=input_placeholder,
            )
        )

    @discord.ui.button(label="Texto", emoji="➕", style=discord.ButtonStyle.primary, row=0, custom_id="tier_template:add_text")
    async def add_text_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._ensure_unlocked(interaction):
            return
        await interaction.response.send_modal(
            TemplateTextItemModal(
                self.cog,
                template_id=self.template_id,
                version_id=self.version_id,
                creator_id=self.creator_id,
                panel_message=interaction.message,
            )
        )

    @discord.ui.button(label="URL", emoji="🖼️", style=discord.ButtonStyle.secondary, row=0, custom_id="tier_template:add_url")
    async def add_url_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._send_source_modal(
            interaction,
            source_key="image_url",
            title="Adicionar Imagem por URL",
            input_label="URL da imagem",
            input_placeholder="https://exemplo.com/imagem.png",
        )

    @discord.ui.button(label="Avatar", emoji="👤", style=discord.ButtonStyle.secondary, row=0, custom_id="tier_template:add_avatar")
    async def add_avatar_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._send_source_modal(
            interaction,
            source_key="discord_user_id",
            title="Adicionar Avatar/PFP",
            input_label="ID do usuário",
            input_placeholder="123456789012345678",
        )

    @discord.ui.button(label="Wikipedia", emoji="🌐", style=discord.ButtonStyle.secondary, row=0, custom_id="tier_template:add_wikipedia")
    async def add_wikipedia_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._send_source_modal(
            interaction,
            source_key="wikipedia_query",
            title="Adicionar Imagem da Wikipedia",
            input_label="Termo de busca Wikipedia",
            input_placeholder="Ex: Blade Runner",
        )

    @discord.ui.button(label="Spotify", emoji="🎵", style=discord.ButtonStyle.secondary, row=0, custom_id="tier_template:add_spotify")
    async def add_spotify_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._send_source_modal(
            interaction,
            source_key="spotify_input",
            title="Adicionar Capa do Spotify",
            input_label="URL, URI ou busca Spotify",
            input_placeholder="Ex: spotify:album:... ou nome do álbum",
        )

    @discord.ui.button(label="Remover Item", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, custom_id="tier_template:remove")
    async def remove_item_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._ensure_unlocked(interaction):
            return
        view = TemplateItemRemoveView(
            cog=self.cog,
            template_id=self.template_id,
            version_id=self.version_id,
            creator_id=self.creator_id,
            panel_message=interaction.message,
        )
        embed = await view.build_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Reordenar", emoji="🔃", style=discord.ButtonStyle.secondary, row=1, custom_id="tier_template:reorder")
    async def reorder_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._ensure_unlocked(interaction):
            return
        view = TemplateItemReorderView(
            cog=self.cog,
            template_id=self.template_id,
            version_id=self.version_id,
            creator_id=self.creator_id,
            panel_message=interaction.message,
        )
        embed = await view.build_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Preview", emoji="👀", style=discord.ButtonStyle.secondary, row=1, custom_id="tier_template:preview")
    async def preview_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.send_template_preview(interaction, template_id=self.template_id, version_id=self.version_id)

    @discord.ui.button(label="Publicar", emoji="✅", style=discord.ButtonStyle.success, row=1, custom_id="tier_template:publish")
    async def publish_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.publish_template_from_panel(
            interaction,
            template_id=self.template_id,
            version_id=self.version_id,
            creator_id=self.creator_id,
        )

    @discord.ui.button(label="Fechar", emoji="❌", style=discord.ButtonStyle.secondary, row=1, custom_id="tier_template:close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Página anterior", emoji="◀️", style=discord.ButtonStyle.secondary, row=2, custom_id="tier_template:page_prev")
    async def previous_page_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed, view = await self.cog.build_editor_embed_and_view(
            template_id=self.template_id,
            version_id=self.version_id,
            creator_id=self.creator_id,
            page=max(0, self.page - 1),
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Próxima página", emoji="▶️", style=discord.ButtonStyle.secondary, row=2, custom_id="tier_template:page_next")
    async def next_page_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed, view = await self.cog.build_editor_embed_and_view(
            template_id=self.template_id,
            version_id=self.version_id,
            creator_id=self.creator_id,
            page=min(self.total_pages - 1, self.page + 1),
        )
        await interaction.response.edit_message(embed=embed, view=view)


class _PagedItemSelect(discord.ui.Select):
    def __init__(self, parent: "_BaseItemManageView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Escolha um item",
            min_values=1,
            max_values=1,
            options=parent.options_for_page(),
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.handle_selected(interaction, self.values[0])


class _BaseItemManageView(discord.ui.View):
    title = "Itens do template"

    def __init__(
        self,
        *,
        cog: TierTemplateCog,
        template_id: str,
        version_id: str,
        creator_id: int,
        panel_message: discord.Message | None,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.template_id = template_id
        self.version_id = version_id
        self.creator_id = creator_id
        self.panel_message = panel_message
        self.page = page
        self.items = []
        self.selected_item_id: str | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await self.cog.user_can_edit(interaction, self.creator_id):
            return True
        await interaction.response.send_message(
            "⚠️ Esse painel não é seu, bestie. Crie ou edite o seu próprio template.",
            ephemeral=True,
        )
        return False

    async def build_embed(self) -> discord.Embed:
        self.items = await self.cog.template_repository.list_template_items(self.version_id)
        self.page = max(0, min(self.page, self.max_page))
        self.clear_items()
        if self.items:
            self.add_item(_PagedItemSelect(self))
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.max_page
        self._sync_action_buttons()
        self.add_item(self.previous_button)
        self.add_item(self.next_button)
        self._add_action_buttons()

        embed = discord.Embed(
            title=self.title,
            description=f"Página {self.page + 1}/{self.max_page + 1} • {len(self.items)} itens",
            color=discord.Color.blurple(),
        )
        if self.selected_item_id:
            selected = next((item for item in self.items if item.id == self.selected_item_id), None)
            if selected:
                embed.add_field(name="Selecionado", value=self.cog.item_display_label(selected, self.items.index(selected)), inline=False)
        return embed

    @property
    def max_page(self) -> int:
        if not self.items:
            return 0
        return max(0, (len(self.items) - 1) // 25)

    def options_for_page(self) -> list[discord.SelectOption]:
        if not self.items:
            return [discord.SelectOption(label="Nenhum item", value="none")]
        start = self.page * 25
        page_items = self.items[start : start + 25]
        options = []
        for offset, item in enumerate(page_items):
            index = start + offset
            label = self.cog.item_display_label(item, index)[:100]
            options.append(discord.SelectOption(label=label, value=item.id, description=f"Posição {index + 1}"))
        return options

    async def refresh_message(self, interaction: discord.Interaction, *, content: str | None = None) -> None:
        embed = await self.build_embed()
        await interaction.response.edit_message(content=content, embed=embed, view=self)

    async def handle_selected(self, interaction: discord.Interaction, item_id: str) -> None:
        self.selected_item_id = item_id
        await self.refresh_message(interaction)

    def _sync_action_buttons(self) -> None:
        return None

    def _add_action_buttons(self) -> None:
        return None

    @discord.ui.button(label="Anterior", style=discord.ButtonStyle.secondary, row=3)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        await self.refresh_message(interaction)

    @discord.ui.button(label="Próxima", style=discord.ButtonStyle.secondary, row=3)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = min(self.max_page, self.page + 1)
        await self.refresh_message(interaction)


class TemplateItemRemoveView(_BaseItemManageView):
    title = "Remover item"

    def _add_action_buttons(self) -> None:
        self.remove_selected_button.disabled = self.selected_item_id is None
        self.add_item(self.remove_selected_button)

    @discord.ui.button(label="Remover selecionado", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)
    async def remove_selected_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.selected_item_id is None:
            await interaction.response.send_message("⚠️ Escolha um item primeiro.", ephemeral=True)
            return
        try:
            await self.cog.remove_template_item_from_panel(
                item_id=self.selected_item_id,
                template_id=self.template_id,
                version_id=self.version_id,
                creator_id=self.creator_id,
                panel_message=self.panel_message,
                actor=interaction.user,
                guild_id=interaction.guild_id,
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        self.selected_item_id = None
        await self.refresh_message(interaction, content="🗑️ Item removido.")


class TemplateItemReorderView(_BaseItemManageView):
    title = "Reordenar item"

    def _add_action_buttons(self) -> None:
        self.move_up_button.disabled = self.selected_item_id is None
        self.move_down_button.disabled = self.selected_item_id is None
        self.add_item(self.move_up_button)
        self.add_item(self.move_down_button)

    @discord.ui.button(label="Subir", emoji="⬆️", style=discord.ButtonStyle.primary, row=2)
    async def move_up_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._move(interaction, -1)

    @discord.ui.button(label="Descer", emoji="⬇️", style=discord.ButtonStyle.primary, row=2)
    async def move_down_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._move(interaction, 1)

    async def _move(self, interaction: discord.Interaction, direction: int) -> None:
        if self.selected_item_id is None:
            await interaction.response.send_message("⚠️ Escolha um item primeiro.", ephemeral=True)
            return
        try:
            await self.cog.reorder_template_item_from_panel(
                item_id=self.selected_item_id,
                direction=direction,
                template_id=self.template_id,
                version_id=self.version_id,
                creator_id=self.creator_id,
                panel_message=self.panel_message,
                actor=interaction.user,
                guild_id=interaction.guild_id,
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        await self.refresh_message(interaction, content="🔃 Ordem atualizada.")
