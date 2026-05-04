from __future__ import annotations

import discord

from .models import TemplateVisibility
from .services import TierListTemplateError, TierListTemplateService


class TemplateCreateModal(discord.ui.Modal):
    def __init__(self, service: TierListTemplateService) -> None:
        super().__init__(title="Criar Template De Tier List")
        self.service = service
        self.template_name = discord.ui.TextInput(
            label="Nome",
            placeholder="Ex: Álbuns favoritos, personagens, jogos...",
            min_length=1,
            max_length=80,
            required=True,
        )
        self.description = discord.ui.TextInput(
            label="Descrição",
            placeholder="Opcional",
            style=discord.TextStyle.paragraph,
            min_length=0,
            max_length=300,
            required=False,
        )
        self.add_item(self.template_name)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            template = await self.service.create_template(
                name=str(self.template_name.value),
                description=str(self.description.value),
                creator_id=interaction.user.id,
                guild_id=interaction.guild_id,
                visibility=TemplateVisibility.GUILD,
            )
            embed = await self.service.build_template_embed(template.id, interaction.user.id)
        except TierListTemplateError as exc:
            await interaction.response.send_message(f"❌ {exc.user_message}", ephemeral=True)
            return

        await interaction.response.send_message(embed=embed, ephemeral=True)


class TemplateItemModal(discord.ui.Modal):
    def __init__(self, service: TierListTemplateService, *, template_id: int) -> None:
        super().__init__(title=f"Adicionar Item Ao Template #{template_id}")
        self.service = service
        self.template_id = template_id
        self.item_name = discord.ui.TextInput(
            label="Nome",
            placeholder="Opcional. Só esse texto aparece no card.",
            min_length=0,
            max_length=80,
            required=False,
        )
        self.image_url = discord.ui.TextInput(
            label="URL da imagem",
            placeholder="Opcional",
            min_length=0,
            max_length=500,
            required=False,
        )
        self.user_id_input = discord.ui.TextInput(
            label="Foto de usuário (ID)",
            placeholder="Opcional",
            min_length=0,
            max_length=25,
            required=False,
        )
        self.wikipedia_input = discord.ui.TextInput(
            label="Wikipedia / termo de busca",
            placeholder="Opcional",
            min_length=0,
            max_length=100,
            required=False,
        )
        self.spotify_input = discord.ui.TextInput(
            label="Spotify URL/URI ou busca",
            placeholder="Opcional",
            min_length=0,
            max_length=200,
            required=False,
        )
        self.add_item(self.item_name)
        self.add_item(self.image_url)
        self.add_item(self.user_id_input)
        self.add_item(self.wikipedia_input)
        self.add_item(self.spotify_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.service.add_draft_item_from_fields(
                template_id=self.template_id,
                actor=interaction.user,
                client=interaction.client,
                guild_id=interaction.guild_id,
                raw_name=str(self.item_name.value),
                image_url=str(self.image_url.value),
                avatar_user_id=str(self.user_id_input.value),
                wikipedia=str(self.wikipedia_input.value),
                spotify=str(self.spotify_input.value),
            )
            embed = await self.service.build_template_embed(self.template_id, interaction.user.id)
        except TierListTemplateError as exc:
            await interaction.followup.send(f"❌ {exc.user_message}", ephemeral=True)
            return

        await interaction.followup.send("✅ Item adicionado ao draft do template.", embed=embed, ephemeral=True)
