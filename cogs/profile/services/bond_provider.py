from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

import discord
from discord.ext import commands


LOGGER = logging.getLogger("baphomet.profile.bond_provider")

DEFAULT_AFFINITY_LEVEL_2_DAYS = 7
DEFAULT_AFFINITY_LEVEL_3_DAYS = 60
BOND_RESONANCE_WINDOW = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class BondRenderStyle:
    style: str
    color: tuple[int, int, int, int]
    glow: tuple[int, int, int, int]
    label: str


@dataclass(frozen=True, slots=True)
class PrimaryBondSnapshot:
    vinculo_id: int
    partner_user_id: int
    partner_display_name: str
    partner_avatar_url: str | None
    bond_type: str
    bond_label: str
    affinity_level: int
    affinity_label: str
    resonance_active: bool
    partner_last_seen_at: str | None
    created_at: str
    style: BondRenderStyle
    medal_label: str | None

    @property
    def signature(self) -> tuple[object, ...]:
        return (
            self.vinculo_id,
            self.partner_user_id,
            self.partner_display_name,
            self.partner_avatar_url,
            self.bond_type,
            self.affinity_level,
            self.resonance_active,
            self.partner_last_seen_at,
            self.medal_label,
        )


@dataclass(frozen=True, slots=True)
class ProfileBondSnapshot:
    active_count: int = 0
    xp_multiplier: float = 1.0
    primary: PrimaryBondSnapshot | None = None
    provider_name: str = "none"
    available: bool = False
    unavailable_reason: str | None = None

    @property
    def signature(self) -> tuple[object, ...]:
        return (
            self.provider_name,
            self.available,
            self.active_count,
            round(self.xp_multiplier, 4),
            self.primary.signature if self.primary else None,
        )


class BondProvider(Protocol):
    async def get_bond_snapshot(self, guild: discord.Guild, member: discord.Member) -> ProfileBondSnapshot:
        ...


class NullBondProvider:
    provider_name = "none"

    async def get_bond_snapshot(self, guild: discord.Guild, member: discord.Member) -> ProfileBondSnapshot:
        return ProfileBondSnapshot(
            provider_name=self.provider_name,
            available=False,
            unavailable_reason="vinculos_runtime nao configurado",
        )


class VinculosRuntimeBondProvider:
    provider_name = "vinculos_runtime"

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def get_bond_snapshot(self, guild: discord.Guild, member: discord.Member) -> ProfileBondSnapshot:
        runtime = getattr(self.bot, "vinculos_runtime", None)
        repository = getattr(runtime, "repository", None)
        if repository is None:
            return await NullBondProvider().get_bond_snapshot(guild, member)

        try:
            active_vinculos = list(await repository.list_active_vinculos_for_user(guild.id, member.id))
            multiplier = float(await repository.get_xp_multiplier(guild.id, member.id))
            if not active_vinculos:
                return ProfileBondSnapshot(
                    active_count=0,
                    xp_multiplier=max(0.0, multiplier),
                    primary=None,
                    provider_name=self.provider_name,
                    available=True,
                )

            settings = await self._get_settings(repository, guild.id)
            partner_ids = [int(vinculo.partner_id_for(member.id) or 0) for vinculo in active_vinculos]
            presence = await self._presence_by_user(repository, guild.id, [pid for pid in partner_ids if pid])
            now = datetime.now(timezone.utc)

            candidates: list[tuple[tuple[object, ...], PrimaryBondSnapshot]] = []
            for vinculo in active_vinculos:
                partner_id = int(vinculo.partner_id_for(member.id) or 0)
                if partner_id <= 0:
                    continue
                created_at = _parse_iso(getattr(vinculo, "created_at", None)) or now
                affinity_level = _affinity_level(
                    created_at,
                    now,
                    int(getattr(settings, "affinity_level_2_days", DEFAULT_AFFINITY_LEVEL_2_DAYS)),
                    int(getattr(settings, "affinity_level_3_days", DEFAULT_AFFINITY_LEVEL_3_DAYS)),
                )
                last_seen_raw = presence.get(partner_id)
                last_seen = _parse_iso(last_seen_raw)
                resonance_active = bool(last_seen and now - last_seen <= BOND_RESONANCE_WINDOW)
                partner = guild.get_member(partner_id)
                bond_type_value = _bond_type_value(getattr(vinculo, "bond_type", "pacto_sangue"))
                style = _style_for_bond_type(bond_type_value)
                snapshot = PrimaryBondSnapshot(
                    vinculo_id=int(getattr(vinculo, "id")),
                    partner_user_id=partner_id,
                    partner_display_name=_partner_display_name(partner, partner_id),
                    partner_avatar_url=_partner_avatar_url(partner),
                    bond_type=bond_type_value,
                    bond_label=style.label,
                    affinity_level=affinity_level,
                    affinity_label=_affinity_label(affinity_level),
                    resonance_active=resonance_active,
                    partner_last_seen_at=last_seen_raw,
                    created_at=getattr(vinculo, "created_at", "") or "",
                    style=style,
                    medal_label="Laço da alma" if affinity_level >= 3 else None,
                )
                # Regra deterministica do vínculo primário:
                # 1. maior afinidade; 2. ressonância mais recente;
                # 3. pacto mais antigo; 4. menor id técnico.
                sort_key = (
                    -affinity_level,
                    -(last_seen.timestamp() if last_seen else 0.0),
                    created_at.timestamp(),
                    snapshot.vinculo_id,
                )
                candidates.append((sort_key, snapshot))

            primary = min(candidates, key=lambda item: item[0])[1] if candidates else None
            return ProfileBondSnapshot(
                active_count=max(0, len(active_vinculos)),
                xp_multiplier=max(0.0, multiplier),
                primary=primary,
                provider_name=self.provider_name,
                available=True,
            )
        except Exception as exc:
            LOGGER.exception(
                "profile_bond_snapshot_failed guild_id=%s user_id=%s error=%s",
                guild.id,
                member.id,
                _summarize_error(exc),
            )
            return ProfileBondSnapshot(
                provider_name=self.provider_name,
                available=False,
                unavailable_reason="vinculos_runtime falhou ao montar snapshot",
            )

    async def _get_settings(self, repository: object, guild_id: int) -> object:
        getter = getattr(repository, "get_guild_settings", None)
        if callable(getter):
            return await getter(guild_id)
        return object()

    async def _presence_by_user(self, repository: object, guild_id: int, user_ids: list[int]) -> dict[int, str]:
        if not user_ids:
            return {}
        connection = getattr(repository, "connection", None)
        if connection is None:
            return {}
        placeholders = ", ".join("?" for _ in user_ids)
        rows = await connection.execute_fetchall(
            f"""
            SELECT user_id, last_seen_at
            FROM vinculo_presence
            WHERE guild_id = ?
              AND user_id IN ({placeholders})
            """,
            (guild_id, *user_ids),
        )
        return {int(row["user_id"]): str(row["last_seen_at"]) for row in rows}


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _affinity_level(created_at: datetime, now: datetime, level_2_days: int, level_3_days: int) -> int:
    level_2_days = max(1, int(level_2_days))
    level_3_days = max(level_2_days + 1, int(level_3_days))
    if now >= created_at + timedelta(days=level_3_days):
        return 3
    if now >= created_at + timedelta(days=level_2_days):
        return 2
    return 1


def _affinity_label(level: int) -> str:
    return {
        1: "fio fino",
        2: "fio de sangue",
        3: "laço da alma",
    }.get(level, "fio fino")


def _style_for_bond_type(bond_type: str) -> BondRenderStyle:
    normalized = str(bond_type or "pacto_sangue")
    if normalized == "fio_rivalidade":
        return BondRenderStyle(
            style="jagged",
            color=(222, 139, 46, 235),
            glow=(255, 91, 58, 80),
            label="Fio de rivalidade",
        )
    if normalized == "pacto_servidao":
        return BondRenderStyle(
            style="chain",
            color=(139, 86, 206, 235),
            glow=(102, 63, 172, 90),
            label="Pacto de servidão",
        )
    return BondRenderStyle(
        style="organic",
        color=(188, 44, 70, 235),
        glow=(188, 44, 70, 86),
        label="Pacto de sangue",
    )


def _bond_type_value(raw: object) -> str:
    value = getattr(raw, "value", raw)
    text = str(value or "pacto_sangue")
    if text in {"pacto_sangue", "fio_rivalidade", "pacto_servidao"}:
        return text
    return "pacto_sangue"


def _partner_display_name(member: discord.Member | None, user_id: int) -> str:
    return str(getattr(member, "display_name", None) or getattr(member, "name", None) or f"Alma {user_id}")


def _partner_avatar_url(member: discord.Member | None) -> str | None:
    if member is None:
        return None
    avatar = getattr(member, "display_avatar", None)
    return str(getattr(avatar, "url", "") or "") or None


def _summarize_error(exc: BaseException) -> str:
    message = str(exc).strip()
    if len(message) > 120:
        message = message[:117].rstrip() + "..."
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
