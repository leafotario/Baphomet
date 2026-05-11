from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ProfileRenderData:
    """Dados prontos para renderizar a ficha, sem acesso a Discord, banco ou XP real."""

    display_name: str
    username: str
    user_id: int
    avatar_bytes: bytes | None = None
    pronouns: str | None = None
    rank_text: str | None = None
    ask_me_about: list[str] = field(default_factory=list)
    basic_info: str | None = None
    badge_name: str | None = None
    badge_image_bytes: bytes | None = None
    bonds_count: int = 0
    bonds_multiplier: float = 1.0
    level: int = 0
    xp_current: int = 0
    xp_required: int = 0
    xp_total: int = 0
    xp_percent: float = 0.0

