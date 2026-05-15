from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class BondOverlayRenderData:
    should_render: bool
    partner_user_id: int
    partner_display_name: str
    partner_avatar_bytes: bytes | None = None
    bond_type: str = "pacto_sangue"
    bond_label: str = "Pacto de sangue"
    affinity_level: int = 1
    affinity_label: str = "fio fino"
    resonance_active: bool = False
    medal_label: str | None = None
    line_style: str = "organic"
    line_color: tuple[int, int, int, int] = (188, 44, 70, 235)
    line_glow: tuple[int, int, int, int] = (188, 44, 70, 86)


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
    primary_bond: BondOverlayRenderData | None = None
    level: int = 0
    xp_current: int = 0
    xp_required: int = 0
    xp_total: int = 0
    xp_percent: float = 0.0
    render_revision: int = 0
    theme_key: str = "classic"
    live_signature: tuple[object, ...] = field(default_factory=tuple)
