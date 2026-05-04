from __future__ import annotations

import io
import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any
from typing import OrderedDict as OrderedDictType

from .assets import TierListAssetStore
from .models import (
    SessionStatus,
    TemplateDraftSnapshot,
    TemplateSession,
    TemplateSessionSnapshot,
    TemplateVersion,
    TemplateVersionSnapshot,
    TemplateVisibility,
    TierListTemplate,
)
from .repository import TierListTemplateRepository
from .sources import (
    SOURCE_TEXT,
    TemplateSourceError,
    TemplateSourceResolver,
    conflicting_image_sources_message,
    get_filled_image_sources,
)


class TierListTemplateError(Exception):
    def __init__(self, user_message: str, *, code: str = "tierlist_template_error") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.code = code


class TierListTemplateService:
    MAX_TEMPLATE_TIERS = 25
    MAX_TEMPLATE_ITEMS = 200
    SESSION_TTL = timedelta(hours=24)

    def __init__(
        self,
        *,
        repository: TierListTemplateRepository,
        asset_store: TierListAssetStore,
        source_resolver: TemplateSourceResolver,
        renderer: Any | None = None,
    ) -> None:
        self.repository = repository
        self.asset_store = asset_store
        self.source_resolver = source_resolver
        self.renderer = renderer

    async def create_template(
        self,
        *,
        name: str,
        description: str,
        creator_id: int,
        guild_id: int | None,
        visibility: TemplateVisibility = TemplateVisibility.GUILD,
    ) -> TierListTemplate:
        clean_name = self.clean_text(name, max_length=80)
        if not clean_name:
            raise TierListTemplateError("Informe um nome para o template.", code="template_name_empty")
        clean_description = self.clean_text(description, max_length=300)
        return await self.repository.create_template(
            name=clean_name,
            description=clean_description,
            creator_id=creator_id,
            guild_id=guild_id if visibility != TemplateVisibility.PRIVATE else guild_id,
            visibility=visibility,
        )

    async def configure_draft_tiers(
        self,
        *,
        template_id: int,
        actor_id: int,
        raw_tiers: str,
    ) -> None:
        template = await self._require_template_owner(template_id, actor_id)
        tiers = self.parse_tiers(raw_tiers)
        if not tiers:
            raise TierListTemplateError("Informe pelo menos uma tier válida.", code="tiers_empty")
        await self.repository.set_draft_tiers(
            template_id=template.id,
            tiers=[(tier, None) for tier in tiers],
        )

    async def add_draft_item_from_fields(
        self,
        *,
        template_id: int,
        actor: Any,
        client: Any,
        guild_id: int | None,
        raw_name: str,
        image_url: str = "",
        avatar_user_id: str = "",
        wikipedia: str = "",
        spotify: str = "",
    ) -> None:
        await self._require_template_owner(template_id, actor.id)
        snapshot = await self.repository.get_draft_snapshot(template_id)
        if snapshot is None:
            raise TierListTemplateError("Template não encontrado.", code="template_not_found")
        if len(snapshot.items) >= self.MAX_TEMPLATE_ITEMS:
            raise TierListTemplateError(
                f"Esse template já chegou ao limite de {self.MAX_TEMPLATE_ITEMS} itens.",
                code="template_item_limit",
            )

        caption = self.normalize_caption(raw_name, max_length=80)
        sources = get_filled_image_sources(
            image_url=image_url,
            avatar_user_id=avatar_user_id,
            wikipedia=wikipedia,
            spotify=spotify,
        )
        if len(sources) > 1:
            raise TierListTemplateError(conflicting_image_sources_message(sources), code="conflicting_sources")
        if not sources and not caption:
            raise TierListTemplateError(
                "Preencha um nome ou escolha uma fonte de imagem para o item.",
                code="template_item_empty",
            )

        if not sources:
            await self.repository.add_draft_item(
                template_id=template_id,
                name=caption,
                source_type=SOURCE_TEXT,
                source_query=None,
                asset_sha256=None,
                metadata={},
            )
            return

        source = sources[0]
        try:
            resolved = await self.source_resolver.resolve(
                source_type=source.key,
                raw_value=source.value,
                client=client,
                guild_id=guild_id,
                user_id=actor.id,
            )
        except TemplateSourceError as exc:
            raise TierListTemplateError(exc.user_message, code=exc.code) from exc

        asset = self.asset_store.store_image_asset(resolved.image_bytes)
        await self.repository.upsert_asset(asset)
        await self.repository.add_draft_item(
            template_id=template_id,
            name=caption,
            source_type=resolved.source_type,
            source_query=resolved.source_query,
            asset_sha256=asset.sha256,
            metadata=resolved.metadata,
        )

    async def get_draft_snapshot(self, *, template_id: int, actor_id: int) -> TemplateDraftSnapshot:
        template = await self._require_template_owner(template_id, actor_id)
        snapshot = await self.repository.get_draft_snapshot(template.id)
        if snapshot is None:
            raise TierListTemplateError("Template não encontrado.", code="template_not_found")
        return snapshot

    async def publish_template(self, *, template_id: int, actor_id: int) -> TemplateVersion:
        template = await self._require_template_owner(template_id, actor_id)
        snapshot = await self.repository.get_draft_snapshot(template.id)
        if snapshot is None:
            raise TierListTemplateError("Template não encontrado.", code="template_not_found")
        if not snapshot.tiers:
            raise TierListTemplateError("Configure pelo menos uma tier antes de publicar.", code="tiers_empty")
        if not snapshot.items:
            raise TierListTemplateError("Adicione pelo menos um item antes de publicar.", code="items_empty")
        return await self.repository.publish_template(template_id=template.id, published_by=actor_id)

    async def list_visible_templates(
        self,
        *,
        guild_id: int | None,
        user_id: int,
        limit: int = 25,
    ) -> list[TierListTemplate]:
        return await self.repository.list_visible_templates(guild_id=guild_id, user_id=user_id, limit=limit)

    async def list_owned_templates(self, *, user_id: int, limit: int = 25) -> list[TierListTemplate]:
        return await self.repository.list_owned_templates(user_id=user_id, limit=limit)

    async def create_session_from_template(
        self,
        *,
        template_id: int,
        actor_id: int,
        guild_id: int | None,
        channel_id: int | None,
    ) -> TemplateSession:
        snapshot = await self.repository.get_current_version_snapshot(template_id)
        if snapshot is None:
            raise TierListTemplateError("Esse template ainda não tem versão publicada.", code="template_unpublished")
        if not self._can_use_template(snapshot.template, actor_id=actor_id, guild_id=guild_id):
            raise TierListTemplateError("Você não tem acesso a esse template.", code="template_forbidden")
        expires_at = (datetime.now(timezone.utc) + self.SESSION_TTL).replace(microsecond=0).isoformat()
        return await self.repository.create_session_from_version(
            version_id=snapshot.version.id,
            owner_id=actor_id,
            guild_id=guild_id,
            channel_id=channel_id,
            title=snapshot.template.name,
            expires_at=expires_at,
        )

    async def get_session_snapshot(self, *, session_id: int, actor_id: int) -> TemplateSessionSnapshot:
        snapshot = await self.repository.get_session_snapshot(session_id)
        if snapshot is None:
            raise TierListTemplateError("Sessão não encontrada.", code="session_not_found")
        if snapshot.session.owner_id != actor_id:
            raise TierListTemplateError("Essa sessão não é sua.", code="session_forbidden")
        return snapshot

    async def move_session_item(
        self,
        *,
        session_id: int,
        item_id: int,
        tier_name: str | None,
        actor_id: int,
    ) -> None:
        snapshot = await self.get_session_snapshot(session_id=session_id, actor_id=actor_id)
        if snapshot.session.status != SessionStatus.ACTIVE:
            raise TierListTemplateError("Essa sessão não está ativa.", code="session_not_active")
        await self.repository.move_session_item(session_id=session_id, item_id=item_id, tier_name=tier_name)

    async def abandon_session(self, *, session_id: int, actor_id: int) -> None:
        snapshot = await self.get_session_snapshot(session_id=session_id, actor_id=actor_id)
        if snapshot.session.status != SessionStatus.ACTIVE:
            raise TierListTemplateError("Essa sessão não está ativa.", code="session_not_active")
        await self.repository.update_session_status(session_id=session_id, status=SessionStatus.ABANDONED)

    async def finalize_session(self, *, session_id: int, actor_id: int) -> io.BytesIO:
        snapshot = await self.get_session_snapshot(session_id=session_id, actor_id=actor_id)
        if snapshot.session.status != SessionStatus.ACTIVE:
            raise TierListTemplateError("Essa sessão não está ativa.", code="session_not_active")
        if not any(item.tier_name for item in snapshot.items):
            raise TierListTemplateError("Mova pelo menos um item para uma tier antes de finalizar.", code="session_empty")
        image = self.render_session_snapshot(snapshot)
        await self.repository.update_session_status(session_id=session_id, status=SessionStatus.FINALIZED)
        return image

    def render_session_snapshot(
        self,
        snapshot: TemplateSessionSnapshot,
        *,
        author: object | None = None,
        guild_icon_bytes: bytes | None = None,
    ) -> io.BytesIO:
        if self.renderer is None:
            from cogs.tierlist import TierListRenderer

            self.renderer = TierListRenderer()
        tier_map = self.session_snapshot_to_tier_items(snapshot)
        return self.renderer.generate_tierlist_image(
            snapshot.session.title,
            tier_map,
            author=author,
            guild_icon_bytes=guild_icon_bytes,
            tier_colors=self._tier_colors(snapshot),
        )

    def session_snapshot_to_tier_items(
        self,
        snapshot: TemplateSessionSnapshot,
    ) -> OrderedDictType[str, list[Any]]:
        tiers: OrderedDictType[str, list[Any]] = OrderedDict((tier.name, []) for tier in snapshot.tiers)
        asset_cache: dict[str, bytes | None] = {}

        for item in sorted(snapshot.items, key=lambda value: (value.tier_name or "", value.position, value.id)):
            if item.tier_name not in tiers:
                continue
            image_bytes = None
            if item.asset_sha256:
                if item.asset_sha256 not in asset_cache:
                    asset_cache[item.asset_sha256] = self._load_asset_bytes(item.asset_sha256)
                image_bytes = asset_cache[item.asset_sha256]
            tiers[item.tier_name].append(self._session_item_to_tier_item(item, image_bytes=image_bytes))
        return tiers

    async def build_template_embed(self, template_id: int, actor_id: int) -> Any:
        import discord

        snapshot = await self.get_draft_snapshot(template_id=template_id, actor_id=actor_id)
        embed = discord.Embed(
            title=f"🧩 Template #{snapshot.template.id}: {discord.utils.escape_markdown(snapshot.template.name)}",
            description=discord.utils.escape_markdown(snapshot.template.description or "Sem descrição."),
            color=discord.Color.from_rgb(155, 93, 229),
        )
        embed.add_field(name="Visibilidade", value=f"`{snapshot.template.visibility.value}`", inline=True)
        embed.add_field(name="Versão atual", value=str(snapshot.template.current_version_id or "não publicado"), inline=True)
        embed.add_field(name="Itens no draft", value=str(len(snapshot.items)), inline=True)
        embed.add_field(
            name="Tiers",
            value=", ".join(tier.name for tier in snapshot.tiers) or "Nenhuma tier",
            inline=False,
        )
        preview = []
        for item in snapshot.items[:12]:
            icon = "📝" if item.source_type == SOURCE_TEXT else "🖼️"
            preview.append(f"{icon} {discord.utils.escape_markdown(item.name or 'item com imagem')}")
        embed.add_field(name="Itens", value="\n".join(preview) if preview else "Nenhum item", inline=False)
        embed.set_footer(text="Publicar cria uma nova versão congelada. Sessões existentes não mudam.")
        return embed

    def build_session_embed(self, snapshot: TemplateSessionSnapshot) -> Any:
        import discord

        unallocated = [item for item in snapshot.items if item.tier_name is None]
        placed = [item for item in snapshot.items if item.tier_name is not None]
        embed = discord.Embed(
            title=f"🧩 Sessão #{snapshot.session.id}: {discord.utils.escape_markdown(snapshot.session.title)}",
            description=(
                f"Template #{snapshot.template.id} v{snapshot.version.version_number}\n"
                f"Status: `{snapshot.session.status.value}`\n"
                f"Itens posicionados: **{len(placed)}** / **{len(snapshot.items)}**\n"
                f"Não alocados: **{len(unallocated)}**"
            ),
            color=discord.Color.from_rgb(107, 155, 242),
        )
        for tier in snapshot.tiers:
            items = [item for item in snapshot.items if item.tier_name == tier.name]
            preview = ", ".join(discord.utils.escape_markdown(item.name or "item com imagem") for item in items[:8])
            if len(items) > 8:
                preview += f" +{len(items) - 8}"
            embed.add_field(name=f"📌 {tier.name}", value=preview or "Sem itens", inline=False)
        if unallocated:
            preview = ", ".join(
                f"`{item.id}` {discord.utils.escape_markdown(item.name or 'item com imagem')}"
                for item in unallocated[:10]
            )
            if len(unallocated) > 10:
                preview += f" +{len(unallocated) - 10}"
            embed.add_field(name="Não alocados", value=preview, inline=False)
        embed.set_footer(text="Use os botões para mover itens e finalizar sua tierlist.")
        return embed

    def parse_tiers(self, raw: str) -> list[str]:
        tiers: list[str] = []
        seen: set[str] = set()
        for part in raw.split(","):
            clean = self.clean_text(part, max_length=20)
            if not clean:
                continue
            key = clean.casefold()
            if key in seen:
                continue
            seen.add(key)
            tiers.append(clean)
            if len(tiers) >= self.MAX_TEMPLATE_TIERS:
                break
        return tiers

    def clean_text(self, text: str, *, max_length: int) -> str:
        value = re.sub(r"\s+", " ", (text or "").strip())
        return value[:max_length].strip()

    def normalize_caption(self, value: object, *, max_length: int | None = None) -> str | None:
        if value is None:
            return None
        text = value if isinstance(value, str) else str(value)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None
        if text.casefold() in {"none", "null"}:
            return None
        if max_length is not None:
            text = text[:max_length].strip()
        return text or None

    async def _require_template_owner(self, template_id: int, actor_id: int) -> TierListTemplate:
        template = await self.repository.get_template(template_id)
        if template is None:
            raise TierListTemplateError("Template não encontrado.", code="template_not_found")
        if template.creator_id != actor_id:
            raise TierListTemplateError("Só quem criou o template pode editar/publicar.", code="template_forbidden")
        return template

    def _can_use_template(
        self,
        template: TierListTemplate,
        *,
        actor_id: int,
        guild_id: int | None,
    ) -> bool:
        if template.creator_id == actor_id:
            return True
        if template.visibility == TemplateVisibility.PUBLIC:
            return True
        if template.visibility == TemplateVisibility.GUILD:
            return guild_id is not None and template.guild_id == guild_id
        return False

    def _load_asset_bytes(self, sha256: str) -> bytes | None:
        # Este método é síncrono de propósito: o renderer também roda fora de rede,
        # e ler poucos assets locais antes do Pillow é barato.
        raise_missing = False
        try:
            asset_path = f"{sha256[:2]}/{sha256}.png"
            return self.asset_store.load_asset_bytes_by_path(asset_path)
        except Exception:
            if raise_missing:
                raise
            return None

    def _session_item_to_tier_item(self, item, *, image_bytes: bytes | None) -> Any:
        from cogs.tierlist import TierItem

        metadata = item.metadata or {}
        caption = self.normalize_caption(item.name, max_length=80)
        spotify_artists = metadata.get("spotify_artists") or ()
        if not isinstance(spotify_artists, (list, tuple)):
            spotify_artists = ()
        return TierItem(
            name=caption or "",
            image_url=None,
            image_bytes=image_bytes,
            source_type=item.source_type,
            caption=caption,
            user_caption=caption,
            render_caption=caption,
            has_visible_caption=caption is not None,
            internal_title=metadata.get("internal_title"),
            source_query=None,
            image_cache_key=metadata.get("image_cache_key"),
            spotify_type=metadata.get("spotify_type"),
            spotify_id=metadata.get("spotify_id"),
            spotify_url=metadata.get("spotify_url"),
            spotify_name=metadata.get("spotify_name"),
            spotify_artists=tuple(str(artist) for artist in spotify_artists),
            album_name=metadata.get("album_name"),
            track_name=metadata.get("track_name"),
            release_date=metadata.get("release_date"),
            attribution_text=metadata.get("attribution_text"),
            display_name=metadata.get("display_name"),
            image_url_used=None,
            wiki_language=metadata.get("wiki_language"),
            wikipedia_pageid=metadata.get("wikipedia_pageid"),
            wikipedia_title=metadata.get("wikipedia_title"),
            wikipedia_url=metadata.get("wikipedia_url"),
            wikimedia_file_title=metadata.get("wikimedia_file_title"),
            wikimedia_file_description_url=metadata.get("wikimedia_file_description_url"),
            image_mime=metadata.get("image_mime"),
            artist=metadata.get("artist"),
            credit=metadata.get("credit"),
            license_short_name=metadata.get("license_short_name"),
            license_url=metadata.get("license_url"),
            usage_terms=metadata.get("usage_terms"),
            attribution_required=metadata.get("attribution_required"),
            metadata_source=metadata.get("metadata_source"),
        )

    def _tier_colors(self, snapshot: TemplateSessionSnapshot) -> dict[str, tuple[int, int, int]]:
        colors: dict[str, tuple[int, int, int]] = {}
        for tier in snapshot.tiers:
            parsed = self._parse_hex_color(tier.color_hex or "")
            if parsed is not None:
                colors[tier.name] = parsed
        return colors

    def _parse_hex_color(self, raw: str) -> tuple[int, int, int] | None:
        match = re.fullmatch(r"#?([0-9a-fA-F]{6})", raw or "")
        if not match:
            return None
        value = match.group(1)
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


class TemplateSessionRenderer:
    def __init__(self, service: TierListTemplateService) -> None:
        self.service = service

    def render(self, snapshot: TemplateSessionSnapshot) -> io.BytesIO:
        return self.service.render_session_snapshot(snapshot)
