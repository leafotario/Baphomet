from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Iterable
from typing import Any

import aiohttp
import discord
from PIL import Image

from cogs.ficha.rendering import BondOverlayRenderData, ProfileRenderData

from ..models import ProfileFieldStatus
from ..schemas import ProfileSnapshot
from .bond_provider import BondProvider, NullBondProvider, PrimaryBondSnapshot, ProfileBondSnapshot
from .profile_badge_service import ProfileBadgeService, ResolvedProfileBadge
from .profile_service import ProfileService


LOGGER = logging.getLogger("baphomet.profile.render_data")

REMOVED_CONTENT = "[Conteúdo removido]"
AVATAR_FETCH_TIMEOUT_SECONDS = 4.0
MAX_AVATAR_BYTES = 4 * 1024 * 1024


class ProfileCardDataBuilder:
    """Monta `ProfileRenderData` a partir das fontes reais do bot.

    Esta classe é a camada impura: ela conversa com Discord assets, snapshots do
    perfil, XP/rank já agregados por `ProfileService` e runtime de vínculos. O
    renderer Pillow continua recebendo somente dados prontos.
    """

    def __init__(
        self,
        *,
        profile_service: ProfileService,
        bot: Any | None = None,
        badge_service: ProfileBadgeService | None = None,
        bond_provider: BondProvider | None = None,
        avatar_timeout_seconds: float = AVATAR_FETCH_TIMEOUT_SECONDS,
    ) -> None:
        self.profile_service = profile_service
        self.bot = bot
        self.badge_service = badge_service
        self.bond_provider = bond_provider or NullBondProvider()
        self.avatar_timeout_seconds = avatar_timeout_seconds

    async def build_profile_render_data(
        self,
        guild: discord.Guild,
        member: discord.Member,
    ) -> ProfileRenderData:
        guild_id = int(guild.id)
        user_id = int(member.id)
        LOGGER.info("profile_render_data_started guild_id=%s user_id=%s", guild_id, user_id)

        avatar_task = asyncio.create_task(self._fetch_avatar_bytes(guild_id, member))
        bond_task = asyncio.create_task(self._fetch_bond_snapshot(guild, member))
        badge_task = asyncio.create_task(self._fetch_badge(guild, member))

        snapshot: ProfileSnapshot | None
        try:
            snapshot = await self.profile_service.get_profile_snapshot(guild, member)
        except Exception as exc:
            LOGGER.exception(
                "profile_render_data_snapshot_failed guild_id=%s user_id=%s error=%s",
                guild_id,
                user_id,
                _summarize_error(exc),
            )
            snapshot = None

        avatar_bytes = await avatar_task
        bond_snapshot = await bond_task
        primary_bond = await self._build_bond_overlay(guild_id, bond_snapshot.primary)
        resolved_badge = await badge_task

        if snapshot is None:
            data = self._fallback_data_from_member(
                guild_id=guild_id,
                member=member,
                avatar_bytes=avatar_bytes,
                bond_snapshot=bond_snapshot,
                primary_bond=primary_bond,
                resolved_badge=resolved_badge,
            )
        else:
            self._warn_on_xp_badge_divergence(snapshot, resolved_badge)
            data = profile_render_data_from_snapshot(
                snapshot,
                avatar_bytes=avatar_bytes,
                bond_snapshot=bond_snapshot,
                primary_bond=primary_bond,
                badge_name=resolved_badge.badge.badge_name if resolved_badge else None,
                badge_image_bytes=resolved_badge.image_bytes if resolved_badge else None,
            )

        LOGGER.info("profile_render_data_finished guild_id=%s user_id=%s", guild_id, user_id)
        return data

    async def _fetch_avatar_bytes(self, guild_id: int, member: discord.Member) -> bytes | None:
        asset = getattr(member, "display_avatar", None)
        if asset is None:
            return None

        try:
            replacer = getattr(asset, "replace", None)
            if callable(replacer):
                asset = replacer(size=512, static_format="png")
            payload = await asyncio.wait_for(asset.read(), timeout=self.avatar_timeout_seconds)
        except Exception as exc:
            LOGGER.warning(
                "avatar_download_failed guild_id=%s user_id=%s error=%s",
                guild_id,
                getattr(member, "id", None),
                _summarize_error(exc),
            )
            return None

        if not isinstance(payload, bytes) or len(payload) > MAX_AVATAR_BYTES:
            LOGGER.warning(
                "avatar_download_failed guild_id=%s user_id=%s error=%s",
                guild_id,
                getattr(member, "id", None),
                "payload invalido ou grande demais",
            )
            return None
        return payload

    async def _fetch_bond_snapshot(self, guild: discord.Guild, member: discord.Member) -> ProfileBondSnapshot:
        try:
            return await self.bond_provider.get_bond_snapshot(guild, member)
        except Exception as exc:
            LOGGER.exception(
                "profile_bond_fetch_failed guild_id=%s user_id=%s error=%s",
                guild.id,
                member.id,
                _summarize_error(exc),
            )
            return await NullBondProvider().get_bond_snapshot(guild, member)

    async def _build_bond_overlay(
        self,
        guild_id: int,
        primary: PrimaryBondSnapshot | None,
    ) -> BondOverlayRenderData | None:
        if primary is None:
            return None
        partner_avatar_bytes = await self._fetch_avatar_url_bytes(
            guild_id=guild_id,
            user_id=primary.partner_user_id,
            avatar_url=primary.partner_avatar_url,
        )
        return BondOverlayRenderData(
            should_render=True,
            partner_user_id=primary.partner_user_id,
            partner_display_name=primary.partner_display_name,
            partner_avatar_bytes=partner_avatar_bytes,
            bond_type=primary.bond_type,
            bond_label=primary.bond_label,
            affinity_level=primary.affinity_level,
            affinity_label=primary.affinity_label,
            resonance_active=primary.resonance_active,
            medal_label=primary.medal_label,
            line_style=primary.style.style,
            line_color=primary.style.color,
            line_glow=primary.style.glow,
        )

    async def _fetch_avatar_url_bytes(self, *, guild_id: int, user_id: int, avatar_url: str | None) -> bytes | None:
        if not avatar_url:
            return None
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.avatar_timeout_seconds)) as session:
                async with session.get(avatar_url) as response:
                    if response.status >= 400:
                        return None
                    payload = await response.content.read(MAX_AVATAR_BYTES + 1)
        except Exception as exc:
            LOGGER.warning(
                "partner_avatar_download_failed guild_id=%s user_id=%s error=%s",
                guild_id,
                user_id,
                _summarize_error(exc),
            )
            return None
        if not isinstance(payload, bytes) or len(payload) > MAX_AVATAR_BYTES:
            return None
        return payload

    async def _fetch_badge(self, guild: discord.Guild, member: discord.Member) -> ResolvedProfileBadge | None:
        if self.badge_service is None:
            return None
        try:
            if not hasattr(member, "guild"):
                setattr(member, "guild", guild)
            return await self.badge_service.resolve_member_badge(member)
        except Exception as exc:
            LOGGER.exception(
                "profile_badge_fetch_failed guild_id=%s user_id=%s error=%s",
                getattr(getattr(member, "guild", guild), "id", None),
                member.id,
                _summarize_error(exc),
            )
            return None

    def _fallback_data_from_member(
        self,
        *,
        guild_id: int,
        member: discord.Member,
        avatar_bytes: bytes | None,
        bond_snapshot: ProfileBondSnapshot,
        primary_bond: BondOverlayRenderData | None,
        resolved_badge: ResolvedProfileBadge | None,
    ) -> ProfileRenderData:
        signature = _live_signature_from_parts(
            member=member,
            bond_snapshot=bond_snapshot,
            badge_name=resolved_badge.badge.badge_name if resolved_badge else None,
        )
        return ProfileRenderData(
            display_name=_display_name(member),
            username=_username(member),
            user_id=int(member.id),
            avatar_bytes=avatar_bytes,
            pronouns=None,
            rank_text="Sem rank",
            ask_me_about=[],
            basic_info=None,
            badge_name=resolved_badge.badge.badge_name if resolved_badge else "Sem insígnia",
            badge_image_bytes=resolved_badge.image_bytes if resolved_badge else None,
            bonds_count=max(0, int(bond_snapshot.active_count)),
            bonds_multiplier=max(0.0, float(bond_snapshot.xp_multiplier)),
            primary_bond=primary_bond,
            level=0,
            xp_current=0,
            xp_required=0,
            xp_total=0,
            xp_percent=0.0,
            render_revision=0,
            theme_key="fallback",
            live_signature=signature,
        )

    def _warn_on_xp_badge_divergence(
        self,
        snapshot: ProfileSnapshot,
        resolved_badge: ResolvedProfileBadge | None,
    ) -> None:
        xp_role_id = snapshot.level.badge_role_id
        if xp_role_id is None or resolved_badge is None or xp_role_id == resolved_badge.role_id:
            return
        LOGGER.warning(
            "profile_badge_xp_role_divergence guild_id=%s user_id=%s xp_role_id=%s badge_role_id=%s",
            snapshot.live.guild_id,
            snapshot.live.user_id,
            xp_role_id,
            resolved_badge.role_id,
        )


def profile_render_data_from_snapshot(
    snapshot: ProfileSnapshot,
    *,
    avatar_bytes: bytes | None,
    bond_snapshot: ProfileBondSnapshot | None = None,
    primary_bond: BondOverlayRenderData | None = None,
    badge_name: str | None = None,
    badge_image_bytes: bytes | None = None,
) -> ProfileRenderData:
    """Adapta o snapshot real da feature `/ficha` para o contrato do renderer."""

    level = snapshot.level
    if not level.available:
        LOGGER.warning(
            "rank_fetch_failed guild_id=%s user_id=%s error=%s",
            snapshot.live.guild_id,
            snapshot.live.user_id,
            level.unavailable_reason or "level provider indisponivel",
        )

    bond_snapshot = bond_snapshot or ProfileBondSnapshot()
    return ProfileRenderData(
        display_name=snapshot.live.display_name,
        username=_normalize_username(snapshot.live.username),
        user_id=snapshot.live.user_id,
        avatar_bytes=avatar_bytes,
        pronouns=_field_text(snapshot, "pronouns") or None,
        rank_text=f"#{level.position}" if level.position is not None else "Sem rank",
        ask_me_about=_ask_me_about(snapshot),
        basic_info=_field_text(snapshot, "basic_info") or None,
        badge_name=badge_name or "Sem insígnia",
        badge_image_bytes=badge_image_bytes,
        bonds_count=max(0, int(bond_snapshot.active_count)),
        bonds_multiplier=max(0.0, float(bond_snapshot.xp_multiplier)),
        primary_bond=primary_bond,
        level=max(0, int(level.level)),
        xp_current=max(0, int(level.xp_into_level)),
        xp_required=max(0, int(level.xp_for_next_level)),
        xp_total=max(0, int(level.total_xp)),
        xp_percent=max(0.0, min(1.0, float(level.progress_ratio))),
        render_revision=max(0, int(snapshot.profile.render_revision)),
        theme_key=_theme_key(snapshot),
        live_signature=_live_signature_from_snapshot(snapshot, bond_snapshot, badge_name),
    )


def image_to_png_bytes(image: Image.Image | None) -> bytes | None:
    if image is None:
        return None
    output = io.BytesIO()
    image.convert("RGBA").save(output, format="PNG")
    return output.getvalue()


def _ask_me_about(snapshot: ProfileSnapshot) -> list[str]:
    field = snapshot.fields.get("ask_me_about")
    if field is not None and field.status is ProfileFieldStatus.REMOVED_BY_MOD:
        return [REMOVED_CONTENT]
    if field is None or field.value in (None, "", [], ()):
        return []
    return _split_topics(field.value)


def _split_topics(value: Any) -> list[str]:
    if value == REMOVED_CONTENT:
        return [REMOVED_CONTENT]
    if isinstance(value, str):
        raw_items = value.replace("\n", ",").split(",")
    elif isinstance(value, Iterable):
        raw_items = [str(item) for item in value]
    else:
        raw_items = [str(value)]
    items: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        normalized = str(item).strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(normalized)
    return items


def _field_text(snapshot: ProfileSnapshot, field_key: str) -> str:
    field = snapshot.fields.get(field_key)
    if field is not None and field.status is ProfileFieldStatus.REMOVED_BY_MOD:
        return REMOVED_CONTENT
    if field is None or field.value in (None, "", [], ()):
        return ""
    if isinstance(field.value, list):
        return ", ".join(str(item) for item in field.value)
    return str(field.value)


def _display_name(member: discord.Member) -> str:
    return str(getattr(member, "display_name", None) or getattr(member, "name", None) or member.id)


def _username(member: discord.Member) -> str:
    return _normalize_username(str(getattr(member, "name", None) or getattr(member, "global_name", None) or member.id))


def _normalize_username(username: str) -> str:
    username = username.strip()
    return username if username.startswith("@") else f"@{username}"


def _theme_key(snapshot: ProfileSnapshot) -> str:
    field = snapshot.fields.get("theme_preset")
    if field is None or field.value in (None, ""):
        return "classic"
    return str(field.value)


def _live_signature_from_snapshot(
    snapshot: ProfileSnapshot,
    bond_snapshot: ProfileBondSnapshot,
    badge_name: str | None,
) -> tuple[object, ...]:
    level = snapshot.level
    return (
        snapshot.live.display_name,
        snapshot.live.username,
        snapshot.live.avatar_url,
        level.available,
        level.total_xp,
        level.level,
        level.xp_into_level,
        level.xp_for_next_level,
        level.remaining_to_next,
        round(level.progress_ratio, 4),
        level.badge_role_id,
        level.badge_role_name,
        level.badge_role_color,
        badge_name,
        bond_snapshot.signature,
    )


def _live_signature_from_parts(
    *,
    member: discord.Member,
    bond_snapshot: ProfileBondSnapshot,
    badge_name: str | None,
) -> tuple[object, ...]:
    avatar = getattr(member, "display_avatar", None)
    return (
        _display_name(member),
        _username(member),
        str(getattr(avatar, "url", "") or ""),
        badge_name,
        bond_snapshot.signature,
    )


def _summarize_error(exc: BaseException) -> str:
    message = str(exc).strip()
    if len(message) > 120:
        message = message[:117].rstrip() + "..."
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
