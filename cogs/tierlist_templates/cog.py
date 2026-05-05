from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from .asset_repository import TierAssetRepository
from .assets import TierTemplateAssetStore
from .database import DatabaseManager
from .downloads import SafeImageDownloader
from .item_resolver import ResolvedTemplateItem, TierTemplateItemResolver
from .migrations import DEFAULT_TIERS_JSON
from .models import TemplateVisibility, TierTemplate, TierTemplateItem, TierTemplateVersion
from .preview import TierTemplatePreviewRenderer
from .repository_utils import normalize_slug
from .session_repository import TierSessionRepository
from .template_repository import TierTemplateRepository


LOGGER = logging.getLogger("baphomet.tierlist_templates.cog")


@dataclass(frozen=True)
class EditorState:
    template: TierTemplate
    version: TierTemplateVersion
    items: list[TierTemplateItem]
    page: int
    total_pages: int


class TierTemplateCog(commands.Cog):
    tier = app_commands.Group(name="tier", description="Ferramentas de tier list.")
    template = app_commands.Group(name="template", description="Templates reutilizáveis de tier list.", parent=tier)

    ITEMS_PER_EDITOR_PAGE = 8

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = DatabaseManager()
        self.template_repository = TierTemplateRepository(self.db)
        self.asset_repository = TierAssetRepository(self.db)
        self.session_repository = TierSessionRepository(self.db)
        self.asset_store = TierTemplateAssetStore(repository=self.asset_repository)
        self.item_resolver = TierTemplateItemResolver(
            asset_store=self.asset_store,
            downloader=SafeImageDownloader(),
        )
        self.preview_renderer = TierTemplatePreviewRenderer(
            asset_repository=self.asset_repository,
            asset_store=self.asset_store,
        )

    async def start(self) -> None:
        await self.db.connect()

    def cog_unload(self) -> None:
        asyncio.create_task(self.db.close())

    @template.command(name="criar", description="Cria um template reutilizável de tier list.")
    @app_commands.guild_only()
    async def criar_template(self, interaction: discord.Interaction) -> None:
        from .modals import TemplateCreateModal

        await interaction.response.send_modal(TemplateCreateModal(self))

    async def user_can_edit(self, interaction: discord.Interaction, creator_id: int) -> bool:
        if interaction.user.id == creator_id:
            return True
        permissions = getattr(interaction.user, "guild_permissions", None)
        return bool(permissions and permissions.administrator)

    async def create_template_from_user(
        self,
        *,
        name: str,
        description: str,
        visibility: TemplateVisibility,
        interaction: discord.Interaction,
    ) -> tuple[TierTemplate, TierTemplateVersion]:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("Nome do template é obrigatório.")
        if len(clean_name) > 80:
            raise ValueError("Nome do template deve ter no máximo 80 caracteres.")
        clean_description = str(description or "").strip() or None
        if clean_description and len(clean_description) > 300:
            raise ValueError("Descrição deve ter no máximo 300 caracteres.")

        guild_id = None if visibility is TemplateVisibility.GLOBAL else interaction.guild_id
        template: TierTemplate | None = None
        version: TierTemplateVersion | None = None
        for attempt in range(3):
            slug = await self._unique_slug(clean_name if attempt == 0 else f"{clean_name}-{uuid.uuid4().hex[:6]}")
            try:
                template, version = await self.template_repository.create_template(
                    name=clean_name,
                    description=clean_description,
                    creator_id=interaction.user.id,
                    guild_id=guild_id,
                    visibility=visibility,
                    slug=slug,
                    default_tiers_json=DEFAULT_TIERS_JSON,
                )
                break
            except ValueError as exc:
                if "slug" not in str(exc).casefold() and "existe" not in str(exc).casefold():
                    raise
        if template is None or version is None:
            raise ValueError("Não consegui gerar um slug único para esse template. Tente outro nome.")
        LOGGER.info(
            "Template criado user_id=%s guild_id=%s template_id=%s version_id=%s slug=%s visibility=%s",
            interaction.user.id,
            interaction.guild_id,
            template.id,
            version.id,
            template.slug,
            template.visibility.value,
        )
        return template, version

    async def _unique_slug(self, name: str) -> str:
        base = normalize_slug(name)[:54].strip("-") or "template"
        for suffix in ["", *[f"-{index}" for index in range(2, 30)]]:
            candidate = f"{base}{suffix}"[:60].strip("-")
            if await self.template_repository.get_template_by_slug(candidate) is None:
                return candidate
        candidate = f"{base[:43]}-{uuid.uuid4().hex[:8]}".strip("-")
        return candidate

    async def add_resolved_item_to_template(
        self,
        *,
        template_id: str,
        version_id: str,
        creator_id: int,
        interaction: discord.Interaction,
        **resolver_kwargs: Any,
    ) -> TierTemplateItem:
        await self._require_editable_version(version_id)
        resolved = await self.item_resolver.resolve_item(
            interaction=interaction,
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            **resolver_kwargs,
        )
        item = await self.template_repository.add_template_item(
            template_version_id=version_id,
            **resolved.to_repository_kwargs(),
        )
        LOGGER.info(
            "Item adicionado user_id=%s guild_id=%s template_id=%s version_id=%s item_id=%s source_type=%s",
            interaction.user.id,
            interaction.guild_id,
            template_id,
            version_id,
            item.id,
            resolved.source_type,
        )
        return item

    async def remove_template_item_from_panel(
        self,
        *,
        item_id: str,
        template_id: str,
        version_id: str,
        creator_id: int,
        panel_message: discord.Message | None,
        actor: discord.abc.User,
        guild_id: int | None,
    ) -> None:
        await self._require_editable_version(version_id)
        await self.template_repository.remove_template_item(item_id)
        LOGGER.info(
            "Item removido user_id=%s guild_id=%s template_id=%s version_id=%s item_id=%s",
            actor.id,
            guild_id,
            template_id,
            version_id,
            item_id,
        )
        await self.refresh_editor_panel_message(
            panel_message,
            template_id=template_id,
            version_id=version_id,
            creator_id=creator_id,
        )

    async def reorder_template_item_from_panel(
        self,
        *,
        item_id: str,
        direction: int,
        template_id: str,
        version_id: str,
        creator_id: int,
        panel_message: discord.Message | None,
        actor: discord.abc.User,
        guild_id: int | None,
    ) -> None:
        await self._require_editable_version(version_id)
        await self.template_repository.move_template_item(item_id, direction=direction)
        LOGGER.info(
            "Item reordenado user_id=%s guild_id=%s template_id=%s version_id=%s item_id=%s direction=%s",
            actor.id,
            guild_id,
            template_id,
            version_id,
            item_id,
            direction,
        )
        await self.refresh_editor_panel_message(
            panel_message,
            template_id=template_id,
            version_id=version_id,
            creator_id=creator_id,
        )

    async def publish_template_from_panel(
        self,
        interaction: discord.Interaction,
        *,
        template_id: str,
        version_id: str,
        creator_id: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            version = await self._require_editable_version(version_id)
            items = await self.template_repository.list_template_items(version_id)
            if not items:
                await interaction.followup.send("⚠️ Publique só depois de adicionar pelo menos 1 item.", ephemeral=True)
                return
            self._validate_default_tiers_json(version.default_tiers_json)
            published = await self.template_repository.lock_version(version_id, published_by=interaction.user.id)
            LOGGER.info(
                "Template publicado user_id=%s guild_id=%s template_id=%s version_id=%s items=%s",
                interaction.user.id,
                interaction.guild_id,
                template_id,
                version_id,
                len(items),
            )
            await self.refresh_editor_panel_message(
                interaction.message,
                template_id=template_id,
                version_id=published.id,
                creator_id=creator_id,
            )
            await interaction.followup.send("✅ Template publicado e congelado.", ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
        except Exception:
            LOGGER.exception("Falha inesperada ao publicar template_id=%s version_id=%s.", template_id, version_id)
            await interaction.followup.send("❌ Não consegui publicar esse template. O erro foi registrado.", ephemeral=True)

    async def send_template_preview(
        self,
        interaction: discord.Interaction,
        *,
        template_id: str,
        version_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            template = await self.template_repository.get_template_by_id(template_id)
            version = await self.template_repository.get_template_version(version_id)
            if template is None or version is None:
                await interaction.followup.send("⚠️ Template ou versão não encontrada.", ephemeral=True)
                return
            items = await self.template_repository.list_template_items(version_id)
            if not items:
                await interaction.followup.send("⚠️ Adicione itens antes de gerar o preview.", ephemeral=True)
                return
            output = await self.preview_renderer.render_preview(template=template, version=version, items=items)
            file = discord.File(output, filename="tier_template_preview.png")
            await interaction.followup.send(
                content=f"👀 Preview de **{discord.utils.escape_markdown(template.name)}** com {len(items)} itens.",
                file=file,
                ephemeral=True,
            )
        except Exception:
            LOGGER.exception("Falha ao renderizar preview template_id=%s version_id=%s.", template_id, version_id)
            await interaction.followup.send("❌ Não consegui gerar o preview agora. O erro foi registrado.", ephemeral=True)

    async def send_editor_panel(
        self,
        interaction: discord.Interaction,
        *,
        template_id: str,
        version_id: str,
        creator_id: int,
        page: int = 0,
        content: str | None = None,
    ) -> None:
        embed, view = await self.build_editor_embed_and_view(
            template_id=template_id,
            version_id=version_id,
            creator_id=creator_id,
            page=page,
        )
        await interaction.followup.send(content=content, embed=embed, view=view, ephemeral=True)

    async def refresh_editor_panel_message(
        self,
        message: discord.Message | None,
        *,
        template_id: str,
        version_id: str,
        creator_id: int,
        page: int = 0,
    ) -> None:
        if message is None:
            return
        try:
            embed, view = await self.build_editor_embed_and_view(
                template_id=template_id,
                version_id=version_id,
                creator_id=creator_id,
                page=page,
            )
            await message.edit(embed=embed, view=view)
        except discord.HTTPException:
            LOGGER.warning("Não consegui editar painel de template_id=%s.", template_id)

    async def build_editor_embed_and_view(
        self,
        *,
        template_id: str,
        version_id: str,
        creator_id: int,
        page: int,
    ) -> tuple[discord.Embed, discord.ui.View]:
        from .views import TemplateEditorView

        state = await self.get_editor_state(template_id=template_id, version_id=version_id, page=page)
        embed = self.build_editor_embed(state)
        view = TemplateEditorView(
            cog=self,
            template_id=template_id,
            version_id=version_id,
            creator_id=creator_id,
            page=state.page,
            total_pages=state.total_pages,
            is_locked=state.version.is_locked,
            item_count=len(state.items),
        )
        return embed, view

    async def get_editor_state(self, *, template_id: str, version_id: str, page: int) -> EditorState:
        template = await self.template_repository.get_template_by_id(template_id)
        version = await self.template_repository.get_template_version(version_id)
        if template is None or version is None:
            raise ValueError("Template ou versão não encontrada.")
        items = await self.template_repository.list_template_items(version_id)
        total_pages = max(1, (len(items) + self.ITEMS_PER_EDITOR_PAGE - 1) // self.ITEMS_PER_EDITOR_PAGE)
        clean_page = max(0, min(page, total_pages - 1))
        return EditorState(template=template, version=version, items=items, page=clean_page, total_pages=total_pages)

    def build_editor_embed(self, state: EditorState) -> discord.Embed:
        status = "🔒 publicado" if state.version.is_locked else "📝 rascunho"
        embed = discord.Embed(
            title=f"Template: {state.template.name}",
            description=state.template.description or "Sem descrição.",
            color=discord.Color.green() if state.version.is_locked else discord.Color.blurple(),
        )
        embed.add_field(name="Slug", value=f"`{state.template.slug}`", inline=True)
        embed.add_field(name="Visibilidade", value=state.template.visibility.value, inline=True)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Versão", value=str(state.version.version_number), inline=True)
        embed.add_field(name="Itens", value=str(len(state.items)), inline=True)
        embed.add_field(name="Página", value=f"{state.page + 1}/{state.total_pages}", inline=True)
        embed.add_field(name="Tiers padrão", value=self._tiers_summary(state.version.default_tiers_json), inline=False)

        start = state.page * self.ITEMS_PER_EDITOR_PAGE
        page_items = state.items[start : start + self.ITEMS_PER_EDITOR_PAGE]
        if page_items:
            lines = [
                f"`{start + index + 1:02d}.` {discord.utils.escape_markdown(self.item_display_label(item, start + index))}"
                for index, item in enumerate(page_items)
            ]
            embed.add_field(name="Itens nesta página", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Itens nesta página", value="Nenhum item adicionado ainda.", inline=False)

        embed.set_footer(text="Adicione itens, confira o preview e publique para congelar esta versão.")
        return embed

    def item_display_label(self, item: TierTemplateItem, index: int) -> str:
        label = item.render_caption or item.internal_title or f"Item sem nome #{index + 1}"
        if item.item_type.value == "TEXT_ONLY":
            return f"Texto: {label}"
        visible = "com legenda" if item.has_visible_caption else "sem legenda"
        return f"Imagem {visible}: {label}"

    async def _require_editable_version(self, version_id: str) -> TierTemplateVersion:
        version = await self.template_repository.get_template_version(version_id)
        if version is None:
            raise ValueError("Versão de template não encontrada.")
        if version.is_locked:
            raise ValueError("🔒 Esse template já foi publicado. Para editar, crie uma nova versão/clonagem.")
        return version

    def _validate_default_tiers_json(self, raw: str) -> None:
        try:
            tiers = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("As tiers padrão desta versão estão inválidas.") from exc
        if not isinstance(tiers, list) or not tiers:
            raise ValueError("As tiers padrão desta versão estão vazias.")
        for tier in tiers:
            if not isinstance(tier, dict) or not str(tier.get("id") or "").strip():
                raise ValueError("As tiers padrão desta versão possuem item inválido.")

    def _tiers_summary(self, raw: str) -> str:
        try:
            tiers = json.loads(raw)
        except json.JSONDecodeError:
            return "Tiers inválidas."
        labels = [str(tier.get("label") or tier.get("id") or "?") for tier in tiers if isinstance(tier, dict)]
        return ", ".join(labels[:12]) or "Nenhuma tier."


async def setup(bot: commands.Bot) -> None:
    cog = TierTemplateCog(bot)
    await cog.start()
    await bot.add_cog(cog)
