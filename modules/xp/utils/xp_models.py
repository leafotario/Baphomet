from __future__ import annotations

"""Modelos Do Sistema De XP Do Baphomet."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class XpDifficulty(str, Enum):
    VERY_EASY = "very_easy"
    EASY = "easy"
    NORMAL = "normal"
    HARD = "hard"
    INSANE = "insane"

    @property
    def label(self) -> str:
        return {
            XpDifficulty.VERY_EASY: "Muito Fácil",
            XpDifficulty.EASY: "Fácil",
            XpDifficulty.NORMAL: "Normal",
            XpDifficulty.HARD: "Difícil",
            XpDifficulty.INSANE: "Insano",
        }[self]


@dataclass(slots=True, frozen=True)
class CurveTuning:
    quadratic: int
    linear: int
    multiplier: float


@dataclass(slots=True)
class GuildXpConfig:
    guild_id: int
    difficulty: XpDifficulty = XpDifficulty.NORMAL
    cooldown_seconds: int = 60
    min_xp_per_message: int = 15
    max_xp_per_message: int = 25
    min_message_length: int = 8
    min_unique_words: int = 2
    anti_repeat_window_seconds: int = 180
    anti_repeat_similarity: float = 0.92
    ignore_bots: bool = True
    ignore_webhooks: bool = True
    levelup_channel_id: Optional[int] = None
    log_channel_id: Optional[int] = None  # NEW: Canal de log de auditoria
    ignored_channel_ids: set[int] = field(default_factory=set)
    ignored_category_ids: set[int] = field(default_factory=set)
    ignored_role_ids: set[int] = field(default_factory=set)
    level_roles: dict[int, int] = field(default_factory=dict)


@dataclass(slots=True)
class UserXpProfile:
    guild_id: int
    user_id: int
    total_xp: int = 0
    message_count: int = 0
    last_awarded_at: Optional[str] = None
    last_message_hash: Optional[str] = None
    last_message_at: Optional[str] = None
    last_known_name: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass(slots=True)
class ProgressSnapshot:
    total_xp: int
    level: int
    level_floor_xp: int
    next_level_total_xp: int
    xp_into_level: int
    xp_for_next_level: int
    remaining_to_next: int
    progress_ratio: float


@dataclass(slots=True)
class XpChangeResult:
    awarded: bool
    reason: Optional[str]
    old_total_xp: int
    new_total_xp: int
    old_level: int
    new_level: int
    levels_gained: int
    delta_xp: int = 0


@dataclass(slots=True, frozen=True)
class BondContribution:
    vinculo_id: int
    partner_id: int
    bond_type: str
    affinity_level: int
    bonus_rate: float
    resonance_active: bool
    partner_last_seen_at: Optional[str] = None
    resonance_window_seconds: int = 86_400
    allocated_bonus_xp: int = 0


@dataclass(slots=True, frozen=True)
class PenaltyContribution:
    penalty_id: int
    multiplier_delta: float
    reason: str
    expires_at: Optional[str] = None


@dataclass(slots=True, frozen=True)
class VinculoXpContext:
    base_xp: int
    final_xp: int
    final_multiplier: float
    positive_bonus_rate: float
    penalty_rate: float
    positive_bonus_pool: int
    penalty_pool: int
    bond_contributions: tuple[BondContribution, ...] = ()
    penalty_contributions: tuple[PenaltyContribution, ...] = ()
    source: str = "none"
    unallocated_bonus_xp: int = 0


@dataclass(slots=True)
class RankSnapshot:
    guild_id: int
    user_id: int
    display_name: str
    total_xp: int
    level: int
    xp_into_level: int
    xp_for_next_level: int
    remaining_to_next: int
    progress_ratio: float
    position: Optional[int]


@dataclass(slots=True, frozen=True)
class RankBadge:
    guild_id: int
    role_id: int
    image_path: str
    priority: int = 0
    label: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass(slots=True, frozen=True)
class RankBondSummary:
    count: int = 0
    multiplier: float = 1.0


@dataclass(slots=True, frozen=True)
class LevelRoleSyncResult:
    added_role_ids: tuple[int, ...] = ()
    removed_role_ids: tuple[int, ...] = ()
    skipped_role_ids: tuple[int, ...] = ()
    missing_role_ids: tuple[int, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.added_role_ids or self.removed_role_ids)


@dataclass(slots=True)
class LeaderboardEntry:
    position: int
    user_id: int
    display_name: str
    total_xp: int
    level: int
    remaining_to_next: int
    progress_ratio: float


@dataclass(slots=True)
class PageResult:
    entries: list[LeaderboardEntry]
    total_entries: int
    page: int
    page_size: int
