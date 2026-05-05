from __future__ import annotations

import asyncio
import io
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from .asset_repository import TierAssetRepository
from .assets import TierTemplateAssetStore
from .database import DatabaseManager
from .downloads import SafeImageDownloader
from .item_resolver import TierTemplateItemResolver
from .migrations import DEFAULT_TIERS_JSON
from .models import SessionStatus, TemplateVisibility, TierSession, TierTemplate, TierTemplateItem, TierTemplateVersion
from .preview import TierTemplatePreviewRenderer
from .repository_utils import normalize_slug
from .session_renderer import SessionRenderSnapshot, TierSessionRenderer
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
    admin = app_commands.Group(name="admin", description="Administração de tier templates.", parent=tier)

    ITEMS_PER_EDITOR_PAGE = 8
    TEMPLATE_LIST_PAGE_SIZE = 10
    SESSION_EXPIRATION_HOURS = 24

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
        self.session_renderer = TierSessionRenderer(
            template_repository=self.template_repository,
            session_repository=self.session_repository,
            asset_repository=self.asset_repository,
            asset_store=self.asset_store,
        )
        self.session_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        await self.db.connect()
        self.register_dynamic_items()
        await self.expire_stale_active_sessions()
        await self.restore_persistent_session_views()
        LOGGER.info("Tier templates inicializados com banco, migrations e recovery.")

    def cog_unload(self) -> None:
        self.unregister_dynamic_items()
        asyncio.create_task(self.db.close())

    @template.command(name="criar", description="Cria um template reutilizável de tier list.")
    @app_commands.guild_only()
    async def criar_template(self, interaction: discord.Interaction) -> None:
        from .modals import TemplateCreateModal

        await interaction.response.send_modal(TemplateCreateModal(self))

    @template.command(name="usar", description="Usa um template publicado para montar sua tier list.")
    @app_commands.guild_only()
    @app_commands.describe(template="Slug do template publicado")
    async def usar_template(self, interaction: discord.Interaction, template: str) -> None:
        await interaction.response.defer(thinking=True)
        try:
            tier_template = await self.find_template_for_use(template, interaction=interaction)
            if tier_template is None:
                await interaction.followup.send("⚠️ Não encontrei um template publicado com esse slug.", ephemeral=True)
                return
            if not self.can_use_template(tier_template, interaction):
                await interaction.followup.send("⚠️ Você não tem acesso a esse template.", ephemeral=True)
                return
            if not tier_template.current_version_id:
                await interaction.followup.send("⚠️ Esse template ainda não possui versão publicada.", ephemeral=True)
                return
            version = await self.template_repository.get_template_version(tier_template.current_version_id)
            if version is None or not version.is_locked:
                await interaction.followup.send("⚠️ Esse template ainda não foi publicado.", ephemeral=True)
                return
            active_sessions = await self.session_repository.get_active_sessions_for_user(
                interaction.user.id,
                guild_id=interaction.guild_id,
                limit=6,
            )
            if len(active_sessions) >= 5:
                await interaction.followup.send(
                    "⚠️ Você já tem muitas sessões ativas. Finalize uma delas antes de criar outra.",
                    ephemeral=True,
                )
                return
            session = await self.session_repository.create_session(
                template_version_id=version.id,
                owner_id=interaction.user.id,
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
            )
            file = await self.render_session_file(session.id, author=interaction.user)
            view = await self.build_session_view(session.id)
            message = await interaction.followup.send(
                content=self.session_message_content(tier_template, session),
                file=file,
                view=view,
                wait=True,
            )
            await self.session_repository.update_message_id(
                session_id=session.id,
                message_id=message.id,
                channel_id=message.channel.id,
                owner_id=interaction.user.id,
            )
            LOGGER.info(
                "Sessão de template criada user_id=%s guild_id=%s template_id=%s version_id=%s session_id=%s message_id=%s",
                interaction.user.id,
                interaction.guild_id,
                tier_template.id,
                version.id,
                session.id,
                message.id,
            )
        except Exception:
            LOGGER.exception("Falha ao usar template template=%r user_id=%s guild_id=%s.", template, interaction.user.id, interaction.guild_id)
            await interaction.followup.send("❌ Não consegui criar a sessão desse template. O erro foi registrado.", ephemeral=True)

    @usar_template.autocomplete("template")
    async def usar_template_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        query = normalize_slug(current or "")
        templates: dict[str, TierTemplate] = {}
        if interaction.guild_id is not None:
            for template in await self.template_repository.list_templates_for_guild(interaction.guild_id, include_global=True, limit=25):
                templates[template.id] = template
        for template in await self.template_repository.list_templates_for_user(interaction.user.id, limit=25):
            templates[template.id] = template
        choices: list[app_commands.Choice[str]] = []
        for template in templates.values():
            if template.current_version_id is None:
                continue
            if query and query not in template.slug and query not in normalize_slug(template.name):
                continue
            if not self.can_use_template(template, interaction):
                continue
            choices.append(app_commands.Choice(name=f"{template.name[:60]} ({template.slug})"[:100], value=template.slug))
            if len(choices) >= 25:
                break
        return choices

    @template.command(name="listar", description="Lista seus templates e os templates disponíveis no servidor.")
    @app_commands.guild_only()
    @app_commands.describe(escopo="Use: todos, meus ou servidor", pagina="Página da listagem")
    async def listar_templates(self, interaction: discord.Interaction, escopo: str = "todos", pagina: int = 1) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        scope = (escopo or "todos").strip().casefold()
        if scope not in {"todos", "meus", "servidor"}:
            await interaction.followup.send("⚠️ Escopo inválido. Use `todos`, `meus` ou `servidor`.", ephemeral=True)
            return
        templates = await self.collect_templates_for_listing(interaction, scope=scope)
        await interaction.followup.send(embed=await self.build_template_list_embed(templates, page=max(1, pagina), title="Templates de Tier List"), ephemeral=True)

    @template.command(name="ver", description="Mostra detalhes de um template.")
    @app_commands.guild_only()
    @app_commands.describe(template="Slug ou nome do template")
    async def ver_template(self, interaction: discord.Interaction, template: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        tier_template = await self.find_template_for_view(template, interaction=interaction)
        if tier_template is None or not self.can_view_template(tier_template, interaction):
            await interaction.followup.send("⚠️ Não encontrei um template acessível com esse slug.", ephemeral=True)
            return
        await interaction.followup.send(embed=await self.build_template_detail_embed(tier_template), ephemeral=True)

    @ver_template.autocomplete("template")
    async def ver_template_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self.template_slug_autocomplete(interaction, current)

    @template.command(name="explorar", description="Busca templates publicados disponíveis neste servidor.")
    @app_commands.guild_only()
    @app_commands.describe(busca="Texto para buscar no nome, slug ou descrição")
    async def explorar_templates(self, interaction: discord.Interaction, busca: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        query = normalize_slug(busca or "")
        raw_query = str(busca or "").strip().casefold()
        templates = await self.collect_templates_for_listing(interaction, scope="servidor")
        results = []
        for template in templates:
            version = await self.template_repository.get_current_version(template.id)
            if (
                version
                and version.is_locked
                and template.visibility is not TemplateVisibility.PRIVATE
                and (
                not query
                or query in template.slug
                or query in normalize_slug(template.name)
                or raw_query in str(template.description or "").casefold()
            )
            ):
                results.append(template)
        await interaction.followup.send(
            embed=await self.build_template_list_embed(results, page=1, title=f"Explorar templates: {busca[:60] or 'todos'}"),
            ephemeral=True,
        )

    @template.command(name="editar", description="Abre o editor de um template. Templates publicados criam nova versão.")
    @app_commands.guild_only()
    @app_commands.describe(template="Slug ou nome do template")
    async def editar_template(self, interaction: discord.Interaction, template: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        tier_template = await self.find_template_for_view(template, interaction=interaction)
        if tier_template is None:
            await interaction.followup.send("🔎 Não encontrei esse template. Confere o nome/slug e tenta de novo.", ephemeral=True)
            return
        if not await self.can_manage_template(tier_template, interaction):
            await interaction.followup.send("⚠️ Você não tem permissão para editar esse template.", ephemeral=True)
            return
        try:
            version = await self.get_or_create_editable_version(tier_template, interaction=interaction)
        except ValueError as exc:
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            return
        await self.send_editor_panel(
            interaction,
            template_id=tier_template.id,
            version_id=version.id,
            creator_id=tier_template.creator_id,
            content="📝 Editor aberto. Se o template já estava publicado, esta é uma nova versão em rascunho.",
        )

    @editar_template.autocomplete("template")
    async def editar_template_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self.template_slug_autocomplete(interaction, current, manageable_only=True)

    @template.command(name="clonar", description="Clona um template para você editar sem alterar o original.")
    @app_commands.guild_only()
    @app_commands.describe(template="Slug ou nome do template")
    async def clonar_template(self, interaction: discord.Interaction, template: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        source = await self.find_template_for_view(template, interaction=interaction)
        if source is None or not self.can_view_template(source, interaction):
            await interaction.followup.send("⚠️ Não encontrei um template acessível com esse slug.", ephemeral=True)
            return
        source_version = await self.template_repository.get_current_version(source.id)
        if source_version is None:
            await interaction.followup.send("⚠️ Esse template não possui versão para clonar.", ephemeral=True)
            return
        clone_name = f"Cópia de {source.name}"[:80]
        clone_slug = await self._unique_slug(clone_name)
        try:
            cloned_template, cloned_version = await self.template_repository.clone_template(
                source_template_id=source.id,
                source_version_id=source_version.id,
                name=clone_name,
                description=source.description,
                slug=clone_slug,
                creator_id=interaction.user.id,
                guild_id=interaction.guild_id,
                visibility=TemplateVisibility.PRIVATE,
            )
        except ValueError as exc:
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            return
        LOGGER.info(
            "Template clonado user_id=%s guild_id=%s source_template_id=%s source_version_id=%s clone_template_id=%s clone_version_id=%s",
            interaction.user.id,
            interaction.guild_id,
            source.id,
            source_version.id,
            cloned_template.id,
            cloned_version.id,
        )
        await self.send_editor_panel(
            interaction,
            template_id=cloned_template.id,
            version_id=cloned_version.id,
            creator_id=cloned_template.creator_id,
            content=f"✅ Template clonado como `{cloned_template.slug}`.",
        )

    @clonar_template.autocomplete("template")
    async def clonar_template_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self.template_slug_autocomplete(interaction, current)

    @template.command(name="deletar", description="Remove um template com soft delete.")
    @app_commands.guild_only()
    @app_commands.describe(template="Slug ou nome do template")
    async def deletar_template(self, interaction: discord.Interaction, template: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        tier_template = await self.find_template_for_view(template, interaction=interaction)
        if tier_template is None:
            await interaction.followup.send("🔎 Não encontrei esse template. Confere o nome/slug e tenta de novo.", ephemeral=True)
            return
        if not await self.can_manage_template(tier_template, interaction):
            await interaction.followup.send("⚠️ Você não tem permissão para deletar esse template.", ephemeral=True)
            return
        deleted = await self.template_repository.soft_delete_template(tier_template.id)
        LOGGER.info(
            "Template removido por soft delete user_id=%s guild_id=%s template_id=%s slug=%s",
            interaction.user.id,
            interaction.guild_id,
            deleted.id,
            deleted.slug,
        )
        await interaction.followup.send(f"🗑️ Template `{deleted.slug}` removido. Os assets serão limpos por rotina futura.", ephemeral=True)

    @deletar_template.autocomplete("template")
    async def deletar_template_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self.template_slug_autocomplete(interaction, current, manageable_only=True)

    @admin.command(name="purge-assets", description="Marca ou remove assets órfãos de templates.")
    @app_commands.describe(dry_run="Se verdadeiro, apenas mostra o que seria feito")
    async def purge_assets(self, interaction: discord.Interaction, dry_run: bool = True) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await self.can_run_template_admin(interaction):
            await interaction.followup.send("⚠️ Apenas o dono técnico do bot pode rodar essa limpeza.", ephemeral=True)
            return
        summary = await self.purge_orphan_assets(dry_run=dry_run)
        LOGGER.info(
            "Purge de assets executado user_id=%s guild_id=%s dry_run=%s marked=%s deleted=%s",
            interaction.user.id,
            interaction.guild_id,
            dry_run,
            summary["marked"],
            summary["deleted"],
        )
        mode = "dry-run" if dry_run else "execução real"
        await interaction.followup.send(
            f"🧹 Purge assets ({mode})\n"
            f"Órfãos encontrados: `{summary['seen']}`\n"
            f"Seriam marcados/marcados: `{summary['marked']}`\n"
            f"Seriam deletados/deletados: `{summary['deleted']}`\n"
            f"Bytes candidatos: `{summary['bytes']}`",
            ephemeral=True,
        )

    async def template_slug_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
        *,
        manageable_only: bool = False,
    ) -> list[app_commands.Choice[str]]:
        query = normalize_slug(current or "")
        templates = await self.collect_templates_for_listing(interaction, scope="todos")
        choices: list[app_commands.Choice[str]] = []
        for template in templates:
            if query and query not in template.slug and query not in normalize_slug(template.name):
                continue
            if manageable_only and not await self.can_manage_template(template, interaction):
                continue
            choices.append(app_commands.Choice(name=f"{template.name[:60]} ({template.slug})"[:100], value=template.slug))
            if len(choices) >= 25:
                break
        return choices

    async def collect_templates_for_listing(self, interaction: discord.Interaction, *, scope: str) -> list[TierTemplate]:
        templates: dict[str, TierTemplate] = {}
        if scope in {"todos", "meus"}:
            for template in await self.template_repository.list_templates_for_user(interaction.user.id, limit=100):
                templates[template.id] = template
        if scope in {"todos", "servidor"} and interaction.guild_id is not None:
            for template in await self.template_repository.list_templates_for_guild(interaction.guild_id, include_global=True, limit=100):
                templates[template.id] = template
        visible = [template for template in templates.values() if self.can_view_template(template, interaction)]
        return sorted(visible, key=lambda template: template.updated_at, reverse=True)

    async def build_template_list_embed(self, templates: list[TierTemplate], *, page: int, title: str) -> discord.Embed:
        total_pages = max(1, (len(templates) + self.TEMPLATE_LIST_PAGE_SIZE - 1) // self.TEMPLATE_LIST_PAGE_SIZE)
        clean_page = max(1, min(page, total_pages))
        start = (clean_page - 1) * self.TEMPLATE_LIST_PAGE_SIZE
        page_items = templates[start : start + self.TEMPLATE_LIST_PAGE_SIZE]
        embed = discord.Embed(title=title, color=discord.Color.blurple())
        embed.set_footer(text=f"Página {clean_page}/{total_pages} • {len(templates)} templates")
        if not page_items:
            embed.description = "Nenhum template encontrado."
            return embed
        lines = []
        for template in page_items:
            version = await self.template_repository.get_current_version(template.id)
            if version is None:
                state = "sem versão"
            else:
                state = "publicado" if version.is_locked else "rascunho"
            lines.append(
                f"`{template.slug}` — **{discord.utils.escape_markdown(template.name)}** "
                f"({template.visibility.value.lower()}, {state})"
            )
        embed.description = "\n".join(lines)
        return embed

    async def build_template_detail_embed(self, template: TierTemplate) -> discord.Embed:
        version = await self.template_repository.get_current_version(template.id)
        items: list[TierTemplateItem] = []
        if version is not None:
            items = await self.template_repository.list_template_items(version.id)
        status = "sem versão"
        if version is not None:
            status = "publicado" if version.is_locked else "rascunho"
        embed = discord.Embed(
            title=template.name,
            description=template.description or "Sem descrição.",
            color=discord.Color.green() if version and version.is_locked else discord.Color.blurple(),
        )
        embed.add_field(name="Slug", value=f"`{template.slug}`", inline=True)
        embed.add_field(name="Criador", value=f"<@{template.creator_id}>", inline=True)
        embed.add_field(name="Visibilidade", value=template.visibility.value, inline=True)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Versão atual", value=str(version.version_number) if version else "-", inline=True)
        embed.add_field(name="Itens", value=str(len(items)), inline=True)
        embed.add_field(name="Guild", value=str(template.guild_id or "-"), inline=True)
        return embed

    async def find_template_for_use(self, raw: str, *, interaction: discord.Interaction) -> TierTemplate | None:
        slug = normalize_slug(raw)
        template = await self.template_repository.get_template_by_slug(slug)
        if template is not None:
            return template
        candidates: dict[str, TierTemplate] = {}
        if interaction.guild_id is not None:
            for candidate in await self.template_repository.list_templates_for_guild(interaction.guild_id, include_global=True, limit=100):
                candidates[candidate.id] = candidate
        for candidate in await self.template_repository.list_templates_for_user(interaction.user.id, limit=100):
            candidates[candidate.id] = candidate
        for candidate in candidates.values():
            if normalize_slug(candidate.name) == slug:
                return candidate
        return None

    async def find_template_for_view(self, raw: str, *, interaction: discord.Interaction) -> TierTemplate | None:
        slug = normalize_slug(raw)
        template = await self.template_repository.get_template_by_slug(slug)
        if template is not None:
            return template
        for candidate in await self.collect_templates_for_listing(interaction, scope="todos"):
            if normalize_slug(candidate.name) == slug:
                return candidate
        return None

    def can_use_template(self, template: TierTemplate, interaction: discord.Interaction) -> bool:
        if template.visibility is TemplateVisibility.PRIVATE:
            return template.creator_id == interaction.user.id
        if template.visibility is TemplateVisibility.GUILD:
            return template.guild_id is not None and template.guild_id == interaction.guild_id
        if template.visibility is TemplateVisibility.GLOBAL:
            return True
        return False

    def can_view_template(self, template: TierTemplate, interaction: discord.Interaction) -> bool:
        return self.can_use_template(template, interaction)

    async def user_can_edit(self, interaction: discord.Interaction, creator_id: int) -> bool:
        if interaction.user.id == creator_id:
            return True
        permissions = getattr(interaction.user, "guild_permissions", None)
        return bool(permissions and permissions.administrator)

    async def can_manage_template(self, template: TierTemplate, interaction: discord.Interaction) -> bool:
        if interaction.user.id == template.creator_id:
            return True
        try:
            if await self.bot.is_owner(interaction.user):
                return True
        except Exception:
            LOGGER.exception("Falha ao verificar owner do bot user_id=%s.", interaction.user.id)
        if template.visibility is TemplateVisibility.GUILD and template.guild_id == interaction.guild_id:
            permissions = getattr(interaction.user, "guild_permissions", None)
            return bool(permissions and permissions.administrator)
        return False

    async def can_run_template_admin(self, interaction: discord.Interaction) -> bool:
        try:
            return bool(await self.bot.is_owner(interaction.user))
        except Exception:
            LOGGER.exception("Falha ao verificar admin técnico user_id=%s.", interaction.user.id)
            return False

    async def get_or_create_editable_version(
        self,
        template: TierTemplate,
        *,
        interaction: discord.Interaction,
    ) -> TierTemplateVersion:
        current = await self.template_repository.get_current_version(template.id)
        if current is not None and not current.is_locked:
            return current
        draft = await self.template_repository.get_latest_draft_version(template.id)
        if draft is not None:
            return draft
        if current is None:
            return await self.template_repository.create_template_version(template_id=template.id, created_by=interaction.user.id)
        cloned = await self.template_repository.clone_version_for_editing(current.id, created_by=interaction.user.id)
        LOGGER.info(
            "Versão clonada para edição user_id=%s guild_id=%s template_id=%s source_version_id=%s draft_version_id=%s",
            interaction.user.id,
            interaction.guild_id,
            template.id,
            current.id,
            cloned.id,
        )
        return cloned

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
            "template_created user_id=%s guild_id=%s template_id=%s version_id=%s slug=%s visibility=%s",
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
            "template_item_added user_id=%s guild_id=%s template_id=%s version_id=%s item_id=%s source_type=%s",
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
            "template_item_removed user_id=%s guild_id=%s template_id=%s version_id=%s item_id=%s",
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
                "template_published user_id=%s guild_id=%s template_id=%s version_id=%s items=%s",
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
            raise ValueError("🔒 Esse template já foi publicado. Para editar, vou criar uma nova versão em rascunho.")
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

    def register_dynamic_items(self) -> None:
        if not hasattr(self.bot, "add_dynamic_items"):
            LOGGER.info("discord.py sem DynamicItem; usando apenas re-registro de persistent views.")
            return
        from .dynamic_items import TierSessionActionDynamicItem

        self.bot.add_dynamic_items(TierSessionActionDynamicItem)
        LOGGER.info("DynamicItem de sessão registrado para custom_id tsess:*.")

    def unregister_dynamic_items(self) -> None:
        if not hasattr(self.bot, "remove_dynamic_items"):
            return
        from .dynamic_items import TierSessionActionDynamicItem

        try:
            self.bot.remove_dynamic_items(TierSessionActionDynamicItem)
        except Exception:
            LOGGER.exception("Falha ao remover DynamicItem de sessão.")

    async def expire_stale_active_sessions(self) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=self.SESSION_EXPIRATION_HOURS)).replace(microsecond=0).isoformat()
        expired = await self.session_repository.expire_stale_sessions(updated_before=cutoff, limit=1000)
        if expired:
            LOGGER.info("Sessões antigas expiradas no startup: %s.", expired)
        return expired

    async def restore_persistent_session_views(self) -> None:
        restored = 0
        for session in await self.session_repository.list_active_sessions(limit=500):
            if session.message_id is None:
                continue
            try:
                view = await self.build_session_view(session.id)
                self.bot.add_view(view, message_id=session.message_id)
                restored += 1
            except Exception:
                LOGGER.exception("Falha ao restaurar view persistente session_id=%s.", session.id)
        LOGGER.info("Views persistentes de tier template restauradas: %s.", restored)

    async def purge_orphan_assets(self, *, dry_run: bool) -> dict[str, int]:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        cutoff = (now - timedelta(hours=72)).isoformat()
        assets = await self.asset_repository.list_unreferenced_assets(limit=1000)
        summary = {"seen": len(assets), "marked": 0, "deleted": 0, "bytes": 0}
        for asset in assets:
            summary["bytes"] += asset.size_bytes
            if asset.marked_orphan_at is None:
                summary["marked"] += 1
                if not dry_run:
                    await self.asset_repository.mark_orphan_candidate(asset.id, marked_at=now.isoformat())
                continue
            if asset.marked_orphan_at <= cutoff:
                summary["deleted"] += 1
                if dry_run:
                    continue
                try:
                    path = self.asset_store.asset_path(asset)
                    await asyncio.to_thread(path.unlink, missing_ok=True)
                    await self.asset_repository.soft_delete_asset(asset.id)
                except ValueError:
                    LOGGER.info("Asset órfão voltou a ser referenciado antes do purge asset_id=%s.", asset.id)
                except OSError:
                    LOGGER.exception("Falha ao remover arquivo físico de asset asset_id=%s storage_path=%s.", asset.id, asset.storage_path)
        return summary

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        session = await self.session_repository.get_session_by_message_id(payload.message_id)
        if session is None or session.status is not SessionStatus.ACTIVE:
            return
        try:
            await self.session_repository.abandon_session(session.id, owner_id=session.owner_id)
            LOGGER.info("Sessão marcada como abandonada por mensagem deletada session_id=%s message_id=%s.", session.id, payload.message_id)
        except ValueError:
            return

    def session_message_content(self, template: TierTemplate, session: TierSession) -> str:
        status = "finalizada" if session.status is SessionStatus.FINALIZED else "ativa"
        return f"**{discord.utils.escape_markdown(template.name)}** • sessão {status}"

    async def render_session_file(self, session_id: str, *, author: object | None = None) -> discord.File:
        buffer = await self.session_renderer.render_session(session_id, author=author)
        return discord.File(buffer, filename="tierlist.png")

    async def build_session_view(self, session_id: str, *, disabled: bool = False) -> discord.ui.View:
        from .session_views import TierSessionView

        snapshot = await self.session_renderer.build_snapshot(session_id)
        ordered_items = self.ordered_session_items(snapshot.session_items)
        max_page = max(0, (len(ordered_items) - 1) // 25) if ordered_items else 0
        page = max(0, min(snapshot.session.current_inventory_page, max_page))
        if page != snapshot.session.current_inventory_page and snapshot.session.status is SessionStatus.ACTIVE:
            snapshot_session = await self.session_repository.update_inventory_page(
                session_id=session_id,
                page=page,
                owner_id=snapshot.session.owner_id,
            )
            snapshot = SessionRenderSnapshot(
                template=snapshot.template,
                version=snapshot.version,
                session=snapshot_session,
                session_items=snapshot.session_items,
                template_items_by_id=snapshot.template_items_by_id,
                tiers=snapshot.tiers,
            )
        start = page * 25
        page_items = ordered_items[start : start + 25]
        return TierSessionView(
            cog=self,
            snapshot=snapshot,
            page_items=page_items,
            page_start=start,
            max_page=max_page,
            disabled=disabled,
        )

    def ordered_session_items(self, items: list[Any]) -> list[Any]:
        return sorted(
            items,
            key=lambda item: (
                0 if item.is_unused else 1,
                item.current_tier_id or "",
                item.position,
                item.created_at,
            ),
        )

    def session_item_label(
        self,
        template_item: TierTemplateItem | None,
        index: int,
        *,
        session_item: Any | None = None,
    ) -> str:
        if template_item is None:
            return f"Item #{index + 1}"
        label = template_item.render_caption or template_item.internal_title
        if label:
            prefix = ""
            if session_item is not None and not session_item.is_unused and session_item.current_tier_id:
                prefix = f"[{session_item.current_tier_id}] "
            return f"{prefix}{label}"
        if template_item.item_type.value == "IMAGE":
            return f"Item com imagem #{index + 1}"
        return f"Item #{index + 1}"

    async def select_session_item(self, interaction: discord.Interaction, session_id: str, session_item_id: str) -> None:
        await self.session_repository.set_selected_item(
            session_id=session_id,
            session_item_id=session_item_id,
            owner_id=interaction.user.id,
        )
        view = await self.build_session_view(session_id)
        await interaction.response.edit_message(view=view)

    async def select_session_tier(self, interaction: discord.Interaction, session_id: str, tier_id: str | None) -> None:
        await self.session_repository.set_selected_tier(
            session_id=session_id,
            tier_id=tier_id,
            owner_id=interaction.user.id,
        )
        view = await self.build_session_view(session_id)
        await interaction.response.edit_message(view=view)

    async def change_session_page(self, interaction: discord.Interaction, session_id: str, page: int) -> None:
        snapshot = await self.session_renderer.build_snapshot(session_id)
        ordered_items = self.ordered_session_items(snapshot.session_items)
        max_page = max(0, (len(ordered_items) - 1) // 25) if ordered_items else 0
        clean_page = max(0, min(page, max_page))
        await self.session_repository.update_inventory_page(
            session_id=session_id,
            page=clean_page,
            owner_id=interaction.user.id,
        )
        view = await self.build_session_view(session_id)
        await interaction.response.edit_message(view=view)

    async def apply_session_selection(self, interaction: discord.Interaction, session_id: str) -> None:
        lock = self._session_lock(session_id)
        if lock.locked():
            await interaction.response.send_message("⏳ Calma, diva. Ainda estou processando o clique anterior.", ephemeral=True)
            return
        async with lock:
            await interaction.response.defer()
            try:
                session = await self.session_repository.get_session(session_id)
                if session is None:
                    await interaction.followup.send("⚠️ Sessão não encontrada.", ephemeral=True)
                    return
                if not session.selected_item_id:
                    await interaction.followup.send("⚠️ Escolha um item antes de aplicar.", ephemeral=True)
                    return
                if session.selected_tier_id is None:
                    await self.session_repository.move_item_to_inventory(
                        session_id=session_id,
                        session_item_id=session.selected_item_id,
                        owner_id=interaction.user.id,
                    )
                else:
                    await self.session_repository.move_item_to_tier(
                        session_id=session_id,
                        session_item_id=session.selected_item_id,
                        tier_id=session.selected_tier_id,
                        owner_id=interaction.user.id,
                    )
                await self.edit_session_message(interaction.message, session_id=session_id, author=interaction.user)
            except ValueError as exc:
                await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            except Exception:
                LOGGER.exception("Falha ao aplicar movimento session_id=%s user_id=%s.", session_id, interaction.user.id)
                await interaction.followup.send("❌ Não consegui mover esse item. O erro foi registrado.", ephemeral=True)

    async def move_selected_session_item_to_inventory(self, interaction: discord.Interaction, session_id: str) -> None:
        lock = self._session_lock(session_id)
        if lock.locked():
            await interaction.response.send_message("⏳ Calma, diva. Ainda estou processando o clique anterior.", ephemeral=True)
            return
        async with lock:
            await interaction.response.defer()
            try:
                session = await self.session_repository.get_session(session_id)
                if session is None or not session.selected_item_id:
                    await interaction.followup.send("⚠️ Escolha um item antes.", ephemeral=True)
                    return
                await self.session_repository.move_item_to_inventory(
                    session_id=session_id,
                    session_item_id=session.selected_item_id,
                    owner_id=interaction.user.id,
                )
                await self.edit_session_message(interaction.message, session_id=session_id, author=interaction.user)
            except ValueError as exc:
                await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            except Exception:
                LOGGER.exception("Falha ao mover item para inventário session_id=%s.", session_id)
                await interaction.followup.send("❌ Não consegui devolver esse item ao inventário.", ephemeral=True)

    async def reset_tier_session(
        self,
        interaction: discord.Interaction,
        session_id: str,
        *,
        session_message: discord.Message | None,
    ) -> None:
        lock = self._session_lock(session_id)
        if lock.locked():
            await interaction.response.send_message("⏳ Calma, diva. Ainda estou processando o clique anterior.", ephemeral=True)
            return
        async with lock:
            await interaction.response.defer(ephemeral=True)
            try:
                await self.session_repository.reset_session(session_id, owner_id=interaction.user.id)
                await self.edit_session_message(session_message, session_id=session_id, author=interaction.user)
                await interaction.followup.send("🔄 Sessão resetada.", ephemeral=True)
            except ValueError as exc:
                await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            except Exception:
                LOGGER.exception("Falha ao resetar sessão session_id=%s.", session_id)
                await interaction.followup.send("❌ Não consegui resetar essa sessão.", ephemeral=True)

    async def finalize_tier_session(self, interaction: discord.Interaction, session_id: str) -> None:
        lock = self._session_lock(session_id)
        if lock.locked():
            await interaction.response.send_message("⏳ Calma, diva. Ainda estou processando o clique anterior.", ephemeral=True)
            return
        async with lock:
            await interaction.response.defer()
            try:
                await self.session_repository.finalize_session(session_id, owner_id=interaction.user.id)
                await self.edit_session_message(interaction.message, session_id=session_id, author=interaction.user, disabled=True)
                await interaction.followup.send("🏁 Tierlist finalizada.", ephemeral=True)
                LOGGER.info("Sessão finalizada user_id=%s guild_id=%s session_id=%s.", interaction.user.id, interaction.guild_id, session_id)
            except ValueError as exc:
                await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            except Exception:
                LOGGER.exception("Falha ao finalizar sessão session_id=%s.", session_id)
                await interaction.followup.send("❌ Não consegui finalizar essa sessão.", ephemeral=True)

    async def edit_session_message(
        self,
        message: discord.Message | None,
        *,
        session_id: str,
        author: object | None,
        disabled: bool = False,
    ) -> None:
        if message is None:
            return
        snapshot = await self.session_renderer.build_snapshot(session_id)
        file = await self.render_session_file(session_id, author=author)
        view = await self.build_session_view(session_id, disabled=disabled)
        await message.edit(
            content=self.session_message_content(snapshot.template, snapshot.session),
            attachments=[file],
            view=view,
        )

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self.session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self.session_locks[session_id] = lock
        return lock


async def setup(bot: commands.Bot) -> None:
    cog = TierTemplateCog(bot)
    await cog.start()
    await bot.add_cog(cog)
