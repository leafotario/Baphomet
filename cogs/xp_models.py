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