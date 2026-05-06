from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from .models import IcebergProject, ItemConfig, ItemSourceType
from .sources.providers import IcebergUserError

if TYPE_CHECKING:
    from .commands import IcebergCog


LOGGER = logging.getLogger("baphomet.iceberg.views")
NO_ITEM_VALUE = "__no_item__"


def iceberg_component_id(project_id: str, action: str) -> str:
    return f"ice:{project_id}:{action}"


class IcebergEditorView(discord.ui.View):
    def __init__(self, *, cog: IcebergCog, project: IcebergProject) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.project_id = project.id
        self.owner_id = project.owner_id
        self._apply_custom_ids()

    def _apply_custom_ids(self) -> None:
        self.general_button.custom_id = iceberg_component_id(self.project_id, "general")
        self.layers_button.custom_id = iceberg_component_id(self.project_id, "layers")
        self.add_item_button.custom_id = iceberg_component_id(self.project_id, "add")
        self.manage_items_button.custom_id = iceberg_component_id(self.project_id, "items")
        self.preview_button.custom_id = iceberg_component_id(self.project_id, "preview")
        self.render_button.custom_id = iceberg_component_id(self.project_id, "render")
        self.export_button.custom_id = iceberg_component_id(self.project_id, "export")
        self.close_button.custom_id = iceberg_component_id(self.project_id, "close")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("⚠️ Esse painel de iceberg não é seu.", ephemeral=True)
        return False

    @discord.ui.button(label="Geral", emoji="✏️", style=discord.ButtonStyle.secondary, row=0, custom_id="iceberg:general")
    async def general_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .modals import IcebergGeneralModal

        project = await self.cog.service.get_project_for_user(self.project_id, owner_id=self.owner_id)
        await interaction.response.send_modal(
            IcebergGeneralModal(
                self.cog,
                project_id=project.id,
                owner_id=project.owner_id,
                current_name=project.name,
                current_theme=project.theme_id,
                current_style=project.default_item_style.value,
                panel_message=interaction.message,
            )
        )

    @discord.ui.button(label="Camadas", emoji="🧊", style=discord.ButtonStyle.primary, row=0, custom_id="iceberg:layers")
    async def layers_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .modals import IcebergLayersModal

        project = await self.cog.service.get_project_for_user(self.project_id, owner_id=self.owner_id)
        layer_lines = "\n".join(layer.name for layer in project.ordered_layers())
        weights = ", ".join(f"{layer.height_weight:g}" for layer in project.ordered_layers())
        await interaction.response.send_modal(
            IcebergLayersModal(
                self.cog,
                project_id=project.id,
                owner_id=project.owner_id,
                layer_lines=layer_lines,
                weight_line=weights,
                panel_message=interaction.message,
            )
        )

    @discord.ui.button(label="Adicionar", emoji="➕", style=discord.ButtonStyle.success, row=0, custom_id="iceberg:add")
    async def add_item_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = IcebergAddItemView(cog=self.cog, project_id=self.project_id, owner_id=self.owner_id, panel_message=interaction.message)
        await interaction.response.send_message("Escolha a fonte do item.", view=view, ephemeral=True)

    @discord.ui.button(label="Itens", emoji="🧩", style=discord.ButtonStyle.secondary, row=0, custom_id="iceberg:items")
    async def manage_items_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        project = await self.cog.service.get_project_for_user(self.project_id, owner_id=self.owner_id)
        view = IcebergManageItemsView(cog=self.cog, project=project, panel_message=interaction.message)
        embed = view.build_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Preview", emoji="👀", style=discord.ButtonStyle.secondary, row=1, custom_id="iceberg:preview")
    async def preview_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.send_rendered_iceberg(interaction, project_id=self.project_id, final=False)

    @discord.ui.button(label="Render Final", emoji="🖼️", style=discord.ButtonStyle.primary, row=1, custom_id="iceberg:render")
    async def render_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.send_rendered_iceberg(interaction, project_id=self.project_id, final=True)

    @discord.ui.button(label="Exportar JSON", emoji="📦", style=discord.ButtonStyle.secondary, row=1, custom_id="iceberg:export")
    async def export_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.send_project_export(interaction, project_id=self.project_id)

    @discord.ui.button(label="Fechar", emoji="❌", style=discord.ButtonStyle.danger, row=1, custom_id="iceberg:close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)


class IcebergAddItemView(discord.ui.View):
    def __init__(self, *, cog: IcebergCog, project_id: str, owner_id: int, panel_message: discord.Message | None) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.project_id = project_id
        self.owner_id = owner_id
        self.panel_message = panel_message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("⚠️ Esse seletor não é seu.", ephemeral=True)
        return False

    async def _send_modal(self, interaction: discord.Interaction, source_type: ItemSourceType) -> None:
        from .modals import IcebergAddItemModal

        await interaction.response.send_modal(
            IcebergAddItemModal(
                self.cog,
                project_id=self.project_id,
                owner_id=self.owner_id,
                source_type=source_type,
                panel_message=self.panel_message,
            )
        )

    @discord.ui.button(label="Texto", emoji="📝", style=discord.ButtonStyle.primary)
    async def text_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._send_modal(interaction, ItemSourceType.TEXT)

    @discord.ui.button(label="URL", emoji="🖼️", style=discord.ButtonStyle.secondary)
    async def url_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._send_modal(interaction, ItemSourceType.IMAGE_URL)

    @discord.ui.button(label="Avatar", emoji="👤", style=discord.ButtonStyle.secondary)
    async def avatar_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._send_modal(interaction, ItemSourceType.DISCORD_AVATAR)

    @discord.ui.button(label="Wikipedia", emoji="🌐", style=discord.ButtonStyle.secondary)
    async def wiki_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._send_modal(interaction, ItemSourceType.WIKIPEDIA)


class IcebergItemSelect(discord.ui.Select):
    def __init__(self, parent: "IcebergManageItemsView") -> None:
        options = parent.options()
        super().__init__(
            placeholder="Escolha um item",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not parent.project.items,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == NO_ITEM_VALUE:
            await interaction.response.send_message("⚠️ Não há itens nesse iceberg.", ephemeral=True)
            return
        self.parent_view.selected_item_id = self.values[0]
        embed = self.parent_view.build_embed()
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class IcebergManageItemsView(discord.ui.View):
    def __init__(self, *, cog: IcebergCog, project: IcebergProject, panel_message: discord.Message | None) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.project = project
        self.project_id = project.id
        self.owner_id = project.owner_id
        self.panel_message = panel_message
        self.selected_item_id: str | None = None
        self.add_item(IcebergItemSelect(self))
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("⚠️ Esse gerenciador não é seu.", ephemeral=True)
        return False

    def options(self) -> list[discord.SelectOption]:
        if not self.project.items:
            return [discord.SelectOption(label="Nenhum item", value=NO_ITEM_VALUE)]
        layer_names = {layer.id: layer.name for layer in self.project.layers}
        options: list[discord.SelectOption] = []
        ordered = sorted(self.project.items, key=lambda item: (self.project.layer_by_id(item.layer_id).order if self.project.layer_by_id(item.layer_id) else 999, item.sort_order))
        for item in ordered[:25]:
            options.append(
                discord.SelectOption(
                    label=item.title[:100],
                    value=item.id,
                    description=f"{layer_names.get(item.layer_id, 'Camada')} • {item.source.type.value}"[:100],
                    default=item.id == self.selected_item_id,
                )
            )
        return options

    def build_embed(self) -> discord.Embed:
        selected = self._selected_item()
        embed = discord.Embed(
            title="Itens do iceberg",
            description=f"{len(self.project.items)} itens no projeto.",
            color=discord.Color.blurple(),
        )
        if selected:
            layer = self.project.layer_by_id(selected.layer_id)
            embed.add_field(name="Selecionado", value=f"**{discord.utils.escape_markdown(selected.title)}**\nCamada: {layer.name if layer else selected.layer_id}\nEstilo: {selected.display_style.value}", inline=False)
        else:
            embed.add_field(name="Selecionado", value="Escolha um item no menu.", inline=False)
        return embed

    def _sync_buttons(self) -> None:
        disabled = self.selected_item_id is None
        self.edit_button.disabled = disabled
        self.remove_button.disabled = disabled
        self.up_button.disabled = disabled
        self.down_button.disabled = disabled

    def _selected_item(self) -> ItemConfig | None:
        if self.selected_item_id is None:
            return None
        return next((item for item in self.project.items if item.id == self.selected_item_id), None)

    async def _reload(self) -> None:
        self.project = await self.cog.service.get_project_for_user(self.project_id, owner_id=self.owner_id)
        self.clear_items()
        self.add_item(IcebergItemSelect(self))
        self._sync_buttons()
        self.add_item(self.edit_button)
        self.add_item(self.remove_button)
        self.add_item(self.up_button)
        self.add_item(self.down_button)

    @discord.ui.button(label="Editar", emoji="✏️", style=discord.ButtonStyle.primary, row=1)
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .modals import IcebergEditItemModal

        selected = self._selected_item()
        if selected is None:
            await interaction.response.send_message("⚠️ Escolha um item primeiro.", ephemeral=True)
            return
        layer = self.project.layer_by_id(selected.layer_id)
        await interaction.response.send_modal(
            IcebergEditItemModal(
                self.cog,
                project_id=self.project_id,
                owner_id=self.owner_id,
                item_id=selected.id,
                current_title=selected.title,
                current_layer=layer.name if layer else selected.layer_id,
                current_style=selected.display_style.value,
                current_placement=selected.placement,
                panel_message=self.panel_message,
            )
        )

    @discord.ui.button(label="Remover", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.selected_item_id is None:
            await interaction.response.send_message("⚠️ Escolha um item primeiro.", ephemeral=True)
            return
        try:
            project = await self.cog.service.remove_item(self.project_id, owner_id=self.owner_id, item_id=self.selected_item_id)
        except IcebergUserError as exc:
            await interaction.response.send_message(exc.user_message, ephemeral=True)
            return
        self.selected_item_id = None
        await self.cog.refresh_panel_message(self.panel_message, project)
        self.project = project
        await self._reload()
        await interaction.response.edit_message(content="🗑️ Item removido.", embed=self.build_embed(), view=self)

    @discord.ui.button(label="Subir", emoji="⬆️", style=discord.ButtonStyle.secondary, row=1)
    async def up_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._move(interaction, -1)

    @discord.ui.button(label="Descer", emoji="⬇️", style=discord.ButtonStyle.secondary, row=1)
    async def down_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._move(interaction, 1)

    async def _move(self, interaction: discord.Interaction, direction: int) -> None:
        if self.selected_item_id is None:
            await interaction.response.send_message("⚠️ Escolha um item primeiro.", ephemeral=True)
            return
        try:
            project = await self.cog.service.move_item(self.project_id, owner_id=self.owner_id, item_id=self.selected_item_id, direction=direction)
        except IcebergUserError as exc:
            await interaction.response.send_message(exc.user_message, ephemeral=True)
            return
        await self.cog.refresh_panel_message(self.panel_message, project)
        self.project = project
        await self._reload()
        await interaction.response.edit_message(content="🔃 Ordem atualizada.", embed=self.build_embed(), view=self)
