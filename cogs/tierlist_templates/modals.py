from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

from .exceptions import TemplateItemResolveError
from .item_resolver import normalize_caption
from .models import TemplateVisibility

if TYPE_CHECKING:
    from .cog import TierTemplateCog


LOGGER = logging.getLogger("baphomet.tierlist_templates.modals")


class TemplateCreateModal(discord.ui.Modal, title="Criar Template de Tier List"):
    def __init__(self, cog: TierTemplateCog) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.name_input = discord.ui.TextInput(
            label="Nome do template",
            placeholder="Ex: Álbuns favoritos de 2026",
            min_length=1,
            max_length=80,
            required=True,
        )
        self.description_input = discord.ui.TextInput(
            label="Descrição opcional",
            placeholder="Uma frase curta para identificar o template",
            style=discord.TextStyle.paragraph,
            max_length=300,
            required=False,
        )
        self.visibility_input = discord.ui.TextInput(
            label="Visibilidade",
            placeholder="PRIVATE, GUILD ou GLOBAL",
            default="GUILD",
            min_length=1,
            max_length=12,
            required=False,
        )
        self.add_item(self.name_input)
        self.add_item(self.description_input)
        self.add_item(self.visibility_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            visibility_raw = normalize_caption(self.visibility_input.value) or TemplateVisibility.GUILD.value
            visibility = TemplateVisibility(visibility_raw.strip().upper())
        except ValueError:
            await interaction.followup.send("⚠️ Visibilidade inválida. Use PRIVATE, GUILD ou GLOBAL.", ephemeral=True)
            return

        try:
            template, version = await self.cog.create_template_from_user(
                name=str(self.name_input.value),
                description=str(self.description_input.value or ""),
                visibility=visibility,
                interaction=interaction,
            )
        except ValueError as exc:
            await interaction.followup.send(self.cog.warning_message(exc), ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Falha inesperada ao criar template.")
            await interaction.followup.send("❌ Não consegui criar esse template agora. O erro foi registrado.", ephemeral=True)
            return

        await self.cog.send_editor_panel(
            interaction,
            template_id=template.id,
            version_id=version.id,
            creator_id=template.creator_id,
            content="✅ Template criado. Use o painel abaixo para adicionar itens e publicar.",
        )


class TemplateTextItemModal(discord.ui.Modal, title="Adicionar Item de Texto"):
    def __init__(self, cog: TierTemplateCog, *, template_id: str, version_id: str, creator_id: int, panel_message: discord.Message | None) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.template_id = template_id
        self.version_id = version_id
        self.creator_id = creator_id
        self.panel_message = panel_message
        self.name_input = discord.ui.TextInput(
            label="Nome/texto do item",
            placeholder="Texto visível no card",
            min_length=1,
            max_length=80,
            required=True,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._handle_submit(interaction, user_caption_raw=str(self.name_input.value))

    async def _handle_submit(self, interaction: discord.Interaction, **resolver_kwargs: Any) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            item = await self.cog.add_resolved_item_to_template(
                template_id=self.template_id,
                version_id=self.version_id,
                creator_id=self.creator_id,
                interaction=interaction,
                **resolver_kwargs,
            )
        except TemplateItemResolveError as exc:
            await interaction.followup.send(self.cog.warning_message(exc.user_message), ephemeral=True)
            return
        except ValueError as exc:
            await interaction.followup.send(self.cog.warning_message(exc), ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Falha inesperada ao adicionar item ao template %s.", self.template_id)
            await interaction.followup.send("❌ Não consegui adicionar esse item. O erro foi registrado.", ephemeral=True)
            return

        await self.cog.refresh_editor_panel_message(
            self.panel_message,
            template_id=self.template_id,
            version_id=self.version_id,
            creator_id=self.creator_id,
        )
        label = item.render_caption or ("item com imagem sem legenda" if item.item_type.value == "IMAGE" else "item sem texto")
        await interaction.followup.send(f"✅ Item adicionado: **{discord.utils.escape_markdown(label[:80])}**", ephemeral=True)


class TemplateImageSourceModal(TemplateTextItemModal):
    def __init__(
        self,
        cog: TierTemplateCog,
        *,
        template_id: str,
        version_id: str,
        creator_id: int,
        panel_message: discord.Message | None,
        source_key: str,
        title: str,
        input_label: str,
        input_placeholder: str,
    ) -> None:
        discord.ui.Modal.__init__(self, title=title, timeout=300)
        self.cog = cog
        self.template_id = template_id
        self.version_id = version_id
        self.creator_id = creator_id
        self.panel_message = panel_message
        self.source_key = source_key
        self.caption_input = discord.ui.TextInput(
            label="Nome opcional",
            placeholder="Se vazio, a imagem fica sem legenda",
            min_length=0,
            max_length=80,
            required=False,
        )
        self.source_input = discord.ui.TextInput(
            label=input_label,
            placeholder=input_placeholder,
            min_length=1,
            max_length=300,
            required=True,
        )
        self.add_item(self.caption_input)
        self.add_item(self.source_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        kwargs: dict[str, Any] = {"user_caption_raw": str(self.caption_input.value or "")}
        kwargs[self.source_key] = str(self.source_input.value)
        await self._handle_submit(interaction, **kwargs)
