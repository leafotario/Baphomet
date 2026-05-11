from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import discord
from PIL import Image, ImageOps, UnidentifiedImageError

from ..models import ProfileBadge
from ..repositories import ProfileRepository


LOGGER = logging.getLogger("baphomet.profile.badges")

MAX_BADGE_IMAGE_BYTES = 5 * 1024 * 1024
MIN_BADGE_DIMENSION = 128
MAX_BADGE_DIMENSION = 2048
NORMALIZED_BADGE_SIZE = 512
SUPPORTED_BADGE_FORMATS = {"PNG", "JPEG", "WEBP"}


class ProfileBadgeValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedProfileBadge:
    badge: ProfileBadge
    image_bytes: bytes | None
    role_id: int


class ProfileBadgeService:
    """Administra insígnias de ficha por cargo e resolve a insígnia atual de um membro."""

    def __init__(
        self,
        repository: ProfileRepository,
        *,
        storage_root: str | Path = "data/ficha/badges",
    ) -> None:
        self.repository = repository
        self.storage_root = Path(storage_root)
        self._image_cache: dict[tuple[int, int, str], bytes] = {}

    async def upsert_badge(
        self,
        *,
        guild_id: int,
        role: discord.Role,
        badge_name: str,
        image_bytes: bytes,
        created_by: int,
        priority: int = 0,
    ) -> tuple[ProfileBadge, bool]:
        self.validate_role(role)
        normalized_name = self._validate_badge_name(badge_name)
        processed = self._process_image(image_bytes)
        previous = await self.repository.get_badge(guild_id, role.id)
        image_path = self._image_path(guild_id, role.id)

        try:
            image_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = image_path.with_suffix(".tmp")
            tmp_path.write_bytes(processed)
            os.replace(tmp_path, image_path)
        except Exception as exc:
            LOGGER.exception(
                "profile_badge_image_save_failed guild_id=%s role_id=%s error=%s",
                guild_id,
                role.id,
                type(exc).__name__,
            )
            raise ProfileBadgeValidationError("Nao consegui salvar a imagem da insignia.") from exc

        badge = await self.repository.upsert_badge(
            guild_id=guild_id,
            role_id=role.id,
            badge_name=normalized_name,
            image_path=str(image_path),
            priority=int(priority),
            created_by=created_by,
        )
        self.invalidate(guild_id, role.id)
        return badge, previous is not None

    async def remove_badge(self, guild_id: int, role_id: int) -> ProfileBadge | None:
        badge = await self.repository.delete_badge(guild_id, role_id)
        if badge is None:
            return None

        self.invalidate(guild_id, role_id)
        try:
            Path(badge.image_path).unlink(missing_ok=True)
        except Exception:
            LOGGER.warning(
                "profile_badge_image_delete_failed guild_id=%s role_id=%s image_path=%s",
                guild_id,
                role_id,
                badge.image_path,
                exc_info=True,
            )
        return badge

    async def list_badges(self, guild_id: int) -> list[ProfileBadge]:
        return await self.repository.list_badges(guild_id)

    async def get_badge(self, guild_id: int, role_id: int) -> ProfileBadge | None:
        return await self.repository.get_badge(guild_id, role_id)

    async def update_priority(self, guild_id: int, role_id: int, priority: int) -> ProfileBadge | None:
        badge = await self.repository.update_badge_priority(guild_id, role_id, int(priority))
        if badge is not None:
            self.invalidate(guild_id, role_id)
        return badge

    async def preview_badge_image(self, badge: ProfileBadge) -> bytes | None:
        return self._load_badge_image(badge)

    async def resolve_member_badge(self, member: discord.Member) -> ResolvedProfileBadge | None:
        badges = await self.repository.list_badges(member.guild.id)
        if not badges:
            return None

        member_roles = {role.id: role for role in getattr(member, "roles", [])}
        candidates: list[tuple[int, int, int, ProfileBadge]] = []
        for badge in badges:
            role = member_roles.get(badge.role_id)
            if role is None:
                continue
            candidates.append((badge.priority, int(getattr(role, "position", 0)), badge.role_id, badge))

        if not candidates:
            return None

        _, _, _, selected = max(candidates, key=lambda item: item[:3])
        image_bytes = self._load_badge_image(selected)
        return ResolvedProfileBadge(badge=selected, image_bytes=image_bytes, role_id=selected.role_id)

    def invalidate(self, guild_id: int, role_id: int | None = None) -> None:
        stale = [
            key
            for key in self._image_cache
            if key[0] == guild_id and (role_id is None or key[1] == role_id)
        ]
        for key in stale:
            self._image_cache.pop(key, None)

    def role_status(self, guild: discord.Guild, badge: ProfileBadge) -> str:
        role = guild.get_role(badge.role_id)
        return "ok" if role is not None else "cargo removido"

    @staticmethod
    def validate_role(role: discord.Role) -> None:
        if getattr(role, "is_default", lambda: False)():
            raise ProfileBadgeValidationError("Nao configure insignia para @everyone.")

    def _load_badge_image(self, badge: ProfileBadge) -> bytes | None:
        cache_key = (badge.guild_id, badge.role_id, badge.updated_at)
        cached = self._image_cache.get(cache_key)
        if cached is not None:
            return cached

        path = Path(badge.image_path)
        try:
            payload = path.read_bytes()
            self._validate_processed_png(payload)
        except Exception:
            self.invalidate(badge.guild_id, badge.role_id)
            LOGGER.warning(
                "profile_badge_image_load_failed guild_id=%s role_id=%s image_path=%s",
                badge.guild_id,
                badge.role_id,
                badge.image_path,
                exc_info=True,
            )
            return None

        self._image_cache[cache_key] = payload
        return payload

    def _image_path(self, guild_id: int, role_id: int) -> Path:
        return self.storage_root / str(guild_id) / f"{role_id}.png"

    def _validate_badge_name(self, badge_name: str) -> str:
        normalized = " ".join(str(badge_name).strip().split())
        if not normalized:
            raise ProfileBadgeValidationError("Informe um nome para a insignia.")
        if len(normalized) > 80:
            raise ProfileBadgeValidationError("O nome da insignia deve ter no maximo 80 caracteres.")
        return normalized

    def _process_image(self, payload: bytes) -> bytes:
        if not payload or len(payload) > MAX_BADGE_IMAGE_BYTES:
            raise ProfileBadgeValidationError("A imagem deve ter no maximo 5 MB.")

        try:
            with Image.open(io.BytesIO(payload)) as image:
                if image.format not in SUPPORTED_BADGE_FORMATS:
                    raise ProfileBadgeValidationError("Use uma imagem PNG, JPEG ou WEBP.")
                if getattr(image, "is_animated", False):
                    raise ProfileBadgeValidationError("GIFs ou WEBPs animados nao sao aceitos nesta versao.")
                width, height = image.size
                if width < MIN_BADGE_DIMENSION or height < MIN_BADGE_DIMENSION:
                    raise ProfileBadgeValidationError("A imagem precisa ter pelo menos 128x128 px.")
                if width > MAX_BADGE_DIMENSION or height > MAX_BADGE_DIMENSION:
                    raise ProfileBadgeValidationError("A imagem deve ter no maximo 2048x2048 px.")

                normalized = ImageOps.exif_transpose(image).convert("RGBA")
                normalized.thumbnail(
                    (NORMALIZED_BADGE_SIZE, NORMALIZED_BADGE_SIZE),
                    Image.Resampling.LANCZOS,
                )
                canvas = Image.new("RGBA", (NORMALIZED_BADGE_SIZE, NORMALIZED_BADGE_SIZE), (0, 0, 0, 0))
                x = (NORMALIZED_BADGE_SIZE - normalized.width) // 2
                y = (NORMALIZED_BADGE_SIZE - normalized.height) // 2
                canvas.paste(normalized, (x, y), normalized)
        except ProfileBadgeValidationError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ProfileBadgeValidationError("A imagem enviada nao e valida. Use PNG, JPEG ou WEBP.") from exc

        output = io.BytesIO()
        canvas.save(output, format="PNG")
        return output.getvalue()

    def _validate_processed_png(self, payload: bytes) -> None:
        if len(payload) > MAX_BADGE_IMAGE_BYTES:
            raise ProfileBadgeValidationError("imagem processada grande demais")
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "PNG":
                raise ProfileBadgeValidationError("imagem processada nao esta em PNG")
            image.verify()
