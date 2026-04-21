from __future__ import annotations

"""Configurações E Curva De Progressão Do Sistema De XP."""

import re
from datetime import datetime, timezone

from .xp_models import CurveTuning, GuildXpConfig, ProgressSnapshot, XpDifficulty

CURVE_BY_DIFFICULTY: dict[XpDifficulty, CurveTuning] = {
    XpDifficulty.VERY_EASY: CurveTuning(quadratic=70, linear=50, multiplier=0.65),
    XpDifficulty.EASY: CurveTuning(quadratic=70, linear=50, multiplier=0.85),
    XpDifficulty.NORMAL: CurveTuning(quadratic=70, linear=50, multiplier=1.00),
    XpDifficulty.HARD: CurveTuning(quadratic=70, linear=50, multiplier=1.25),
    XpDifficulty.INSANE: CurveTuning(quadratic=70, linear=50, multiplier=1.60),
}

LEGACY_DIFFICULTY_MAP: dict[str, XpDifficulty] = {
    "muito_facil": XpDifficulty.VERY_EASY,
    "facil": XpDifficulty.EASY,
    "easy": XpDifficulty.EASY,
    "normal": XpDifficulty.NORMAL,
    "dificil": XpDifficulty.HARD,
    "hard": XpDifficulty.HARD,
    "expert": XpDifficulty.INSANE,
    "insano": XpDifficulty.INSANE,
    "insane": XpDifficulty.INSANE,
}

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
WHITESPACE_RE = re.compile(r"\s+")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def build_default_guild_config(guild_id: int) -> GuildXpConfig:
    return GuildXpConfig(guild_id=guild_id)


def curve_for(difficulty: XpDifficulty) -> CurveTuning:
    return CURVE_BY_DIFFICULTY[difficulty]


def normalize_difficulty(raw: str | XpDifficulty | None) -> XpDifficulty:
    if isinstance(raw, XpDifficulty):
        return raw
    if raw is None:
        return XpDifficulty.NORMAL
    return LEGACY_DIFFICULTY_MAP.get(str(raw).lower(), XpDifficulty.NORMAL)


def xp_to_reach_level(level: int, difficulty: XpDifficulty) -> int:
    if level <= 0:
        return 0
    curve = curve_for(difficulty)
    base = (curve.quadratic * (level ** 2)) + (curve.linear * level)
    return int(round(base * curve.multiplier))


def level_from_total_xp(total_xp: int, difficulty: XpDifficulty) -> int:
    if total_xp <= 0:
        return 0

    low = 0
    high = 1
    while xp_to_reach_level(high, difficulty) <= total_xp:
        high *= 2

    while low + 1 < high:
        mid = (low + high) // 2
        if xp_to_reach_level(mid, difficulty) <= total_xp:
            low = mid
        else:
            high = mid

    return low


def build_progress_snapshot(total_xp: int, difficulty: XpDifficulty) -> ProgressSnapshot:
    level = level_from_total_xp(total_xp, difficulty)
    level_floor_xp = xp_to_reach_level(level, difficulty)
    next_level_total_xp = xp_to_reach_level(level + 1, difficulty)
    xp_into_level = max(0, total_xp - level_floor_xp)
    xp_for_next_level = max(1, next_level_total_xp - level_floor_xp)
    remaining_to_next = max(0, next_level_total_xp - total_xp)
    progress_ratio = min(1.0, max(0.0, xp_into_level / xp_for_next_level))

    return ProgressSnapshot(
        total_xp=total_xp,
        level=level,
        level_floor_xp=level_floor_xp,
        next_level_total_xp=next_level_total_xp,
        xp_into_level=xp_into_level,
        xp_for_next_level=xp_for_next_level,
        remaining_to_next=remaining_to_next,
        progress_ratio=progress_ratio,
    )


def normalize_message_content(content: str) -> str:
    value = URL_RE.sub(" ", content.lower())
    value = NON_WORD_RE.sub(" ", value)
    value = WHITESPACE_RE.sub(" ", value).strip()
    return value