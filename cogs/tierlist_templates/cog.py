from __future__ import annotations

import asyncio
import logging
import pathlib
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from .assets import TierListAssetStore
from .modals import TemplateCreateModal, TemplateItemModal
from .models import SessionStatus, TemplateVisibility
from .repository import TierListTemplateRepository
from .services import TierListTemplateError, TierListTemplateService
from .sources import TemplateSourceResolver
from .views import (
    TemplatePublishConfirmView,
    TemplateSessionControlView,
    read_guild_icon_bytes,
)


LOGGER = logging.getLogger("baphomet.tierlist.templates")
DATA_DIR = pathlib.Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "tierlist_templates.sqlite3"
ASSET_DIR = DATA_DIR / "tierlist_assets"


class TierListTemplateCog(
    commands.GroupCog,
    group_name="tierlist-template",
    group_description="Templates reutilizáveis de Tier List",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.repository = TierListTemplateRepository(str(DB_PATH))
        self.asset_store = TierListAssetStore(ASSET_DIR)
        self.source_resolver = TemplateSourceResolver()
        self.service = TierListTemplateService(
            repository=self.repository,
            asset_store=self.asset_store,
            source_resolver=self.source_resolver,
        )

    async def cog_load(self) -> None:
        await self.repository.connect()
        expired = await self.repository.expire_stale_sessions()
        LOGGER.info("TierListTemplateCog carregado. Sessões expiradas: %d.", expired)

    async def cog_unload(self) -> None:
        await self.repository.close()

    @app_commands.command(name="novo", description="Abre um modal para criar um template de tier list.")
    @app_commands.guild_only()
    async def new_template_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TemplateCreateModal(self.service))

    @app_commands.command(name="criar", description="Cria um template de tier list.")
    @app_commands.guild_only()
    @app_commands.describe(
        nome="Nome do template",
        descricao="Descrição opcional",
        visibilidade="Quem pode usar depois de publicado",
    )
    async def create_template(
        self,
        interaction: discord.Interaction,
        nome: app_commands.Range[str, 1, 80],
        descricao: app_commands.Range[str, 0, 300] = "",
        visibilidade: Literal["guild", "private", "public"] = "guild",
    ) -> None:
        try:
            template = await self.service.create_template(
                name=str(nome),
                description=str(descricao or ""),
                creator_id=interaction.user.id,
                guild_id=interaction.guild_id,
                visibility=TemplateVisibility(visibilidade),
            )
            embed = await self.service.build_template_embed(template.id, interaction.user.id)
        except TierListTemplateError as exc:
            await interaction.response.send_message(f"❌ {exc.user_message}", ephemeral=True)
            return

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="tiers", description="Configura as tiers do draft de um template.")
    @app_commands.guild_only()
    @app_commands.describe(template_id="ID do template", tiers="Tiers separadas por vírgula. Ex: S, A, B, C, D")
    async def configure_tiers(
        self,
        interaction: discord.Interaction,
        template_id: int,
        tiers: app_commands.Range[str, 1, 250],
    ) -> None:
        try:
            await self.service.configure_draft_tiers(
                template_id=template_id,
                actor_id=interaction.user.id,
                raw_tiers=str(tiers),
            )
            embed = await self.service.build_template_embed(template_id, interaction.user.id)
        except TierListTemplateError as exc:
            await interaction.response.send_message(f"❌ {exc.user_message}", ephemeral=True)
            return

        await interaction.response.send_message("✅ Tiers atualizadas no draft.", embed=embed, ephemeral=True)

    @app_commands.command(name="adicionar-modal", description="Abre um modal para adicionar item ao template.")
    @app_commands.guild_only()
    async def add_item_modal(self, interaction: discord.Interaction, template_id: int) -> None:
        await interaction.response.send_modal(TemplateItemModal(self.service, template_id=template_id))

    @app_commands.command(name="adicionar-item", description="Adiciona item de texto ou imagem ao draft do template.")
    @app_commands.guild_only()
    @app_commands.describe(
        template_id="ID do template",
        nome="Opcional. Só esse texto aparece no card.",
        url="URL direta de imagem",
        avatar_user_id="ID de usuário para usar o avatar",
        wikipedia="Termo ou URL da Wikipedia/Wikimedia",
        spotify="URL/URI/busca do Spotify",
    )
    async def add_item(
        self,
        interaction: discord.Interaction,
        template_id: int,
        nome: str | None = None,
        url: str | None = None,
        avatar_user_id: str | None = None,
        wikipedia: str | None = None,
        spotify: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.service.add_draft_item_from_fields(
                template_id=template_id,
                actor=interaction.user,
                client=interaction.client,
                guild_id=interaction.guild_id,
                raw_name=nome or "",
                image_url=url or "",
                avatar_user_id=avatar_user_id or "",
                wikipedia=wikipedia or "",
                spotify=spotify or "",
            )
            embed = await self.service.build_template_embed(template_id, interaction.user.id)
        except TierListTemplateError as exc:
            await interaction.followup.send(f"❌ {exc.user_message}", ephemeral=True)
            return

        await interaction.followup.send("✅ Item adicionado ao draft.", embed=embed, ephemeral=True)

    @app_commands.command(name="publicar", description="Publica uma nova versão congelada do template.")
    @app_commands.guild_only()
    async def publish_template(self, interaction: discord.Interaction, template_id: int) -> None:
        try:
            snapshot = await self.service.get_draft_snapshot(
                template_id=template_id,
                actor_id=interaction.user.id,
            )
        except TierListTemplateError as exc:
            await interaction.response.send_message(f"❌ {exc.user_message}", ephemeral=True)
            return

        await interaction.response.send_message(
            (
                f"📌 Publicar **{discord.utils.escape_markdown(snapshot.template.name)}** "
                "vai criar uma nova versão congelada. Sessões antigas continuarão usando a versão delas."
            ),
            view=TemplatePublishConfirmView(
                self.service,
                template_id=template_id,
                owner_id=interaction.user.id,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="listar", description="Lista templates publicados que você pode usar.")
    @app_commands.guild_only()
    async def list_templates(self, interaction: discord.Interaction) -> None:
        templates = await self.service.list_visible_templates(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            limit=25,
        )
        embed = discord.Embed(
            title="🧩 Templates publicados",
            color=discord.Color.from_rgb(107, 155, 242),
        )
        if not templates:
            embed.description = "Nenhum template publicado disponível por enquanto."
        else:
            lines = []
            for template in templates:
                scope = template.visibility.value
                version_label = f"versão #{template.current_version_id}"
                lines.append(
                    f"`{template.id}` **{discord.utils.escape_markdown(template.name)}** "
                    f"{version_label} · `{scope}`"
                )
            embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="meus", description="Lista seus templates, incluindo drafts não publicados.")
    @app_commands.guild_only()
    async def list_owned_templates(self, interaction: discord.Interaction) -> None:
        templates = await self.service.list_owned_templates(user_id=interaction.user.id, limit=25)
        embed = discord.Embed(
            title="🧩 Meus templates",
            color=discord.Color.from_rgb(155, 93, 229),
        )
        if not templates:
            embed.description = "Você ainda não criou templates."
        else:
            lines = []
            for template in templates:
                published = f"versão #{template.current_version_id}" if template.current_version_id else "draft"
                lines.append(
                    f"`{template.id}` **{discord.utils.escape_markdown(template.name)}** · "
                    f"`{template.visibility.value}` · {published}"
                )
            embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ver", description="Mostra o draft de um template seu.")
    @app_commands.guild_only()
    async def view_template(self, interaction: discord.Interaction, template_id: int) -> None:
        try:
            embed = await self.service.build_template_embed(template_id, interaction.user.id)
        except TierListTemplateError as exc:
            await interaction.response.send_message(f"❌ {exc.user_message}", ephemeral=True)
            return
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="usar", description="Cria uma sessão sua baseada na versão publicada do template.")
    @app_commands.guild_only()
    async def use_template(self, interaction: discord.Interaction, template_id: int) -> None:
        try:
            session = await self.service.create_session_from_template(
                template_id=template_id,
                actor_id=interaction.user.id,
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
            )
            snapshot = await self.service.get_session_snapshot(
                session_id=session.id,
                actor_id=interaction.user.id,
            )
        except TierListTemplateError as exc:
            await interaction.response.send_message(f"❌ {exc.user_message}", ephemeral=True)
            return

        view = TemplateSessionControlView(
            self.service,
            session_id=session.id,
            owner_id=interaction.user.id,
        )
        await interaction.response.send_message(embed=self.service.build_session_embed(snapshot), view=view)
        message = await interaction.original_response()
        await self.repository.set_session_message(
            session_id=session.id,
            channel_id=message.channel.id,
            message_id=message.id,
        )

    @app_commands.command(name="abrir-sessao", description="Reabre o painel de uma sessão ativa sua.")
    @app_commands.guild_only()
    async def reopen_session(self, interaction: discord.Interaction, session_id: int) -> None:
        try:
            snapshot = await self.service.get_session_snapshot(
                session_id=session_id,
                actor_id=interaction.user.id,
            )
        except TierListTemplateError as exc:
            await interaction.response.send_message(f"❌ {exc.user_message}", ephemeral=True)
            return
        if snapshot.session.status != SessionStatus.ACTIVE:
            await interaction.response.send_message("❌ Essa sessão não está ativa.", ephemeral=True)
            return

        view = TemplateSessionControlView(self.service, session_id=session_id, owner_id=interaction.user.id)
        await interaction.response.send_message(embed=self.service.build_session_embed(snapshot), view=view)
        message = await interaction.original_response()
        await self.repository.set_session_message(
            session_id=session_id,
            channel_id=message.channel.id,
            message_id=message.id,
        )

    @app_commands.command(name="renderizar", description="Gera uma prévia da sessão sem finalizar.")
    @app_commands.guild_only()
    async def render_session(self, interaction: discord.Interaction, session_id: int) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            snapshot = await self.service.get_session_snapshot(
                session_id=session_id,
                actor_id=interaction.user.id,
            )
            guild_icon_bytes = await read_guild_icon_bytes(interaction)
            image_buffer = await asyncio.to_thread(
                self.service.render_session_snapshot,
                snapshot,
                author=interaction.user,
                guild_icon_bytes=guild_icon_bytes,
            )
        except TierListTemplateError as exc:
            await interaction.followup.send(f"❌ {exc.user_message}", ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Falha ao renderizar sessão de template.")
            await interaction.followup.send("❌ Não consegui renderizar essa sessão agora.", ephemeral=True)
            return

        await interaction.followup.send(
            file=discord.File(image_buffer, filename="tierlist_template_preview.png"),
            ephemeral=True,
        )

    @app_commands.command(name="abandonar", description="Marca uma sessão ativa sua como abandonada.")
    @app_commands.guild_only()
    async def abandon_session(self, interaction: discord.Interaction, session_id: int) -> None:
        try:
            await self.service.abandon_session(session_id=session_id, actor_id=interaction.user.id)
        except TierListTemplateError as exc:
            await interaction.response.send_message(f"❌ {exc.user_message}", ephemeral=True)
            return
        await interaction.response.send_message("✅ Sessão abandonada.", ephemeral=True)
