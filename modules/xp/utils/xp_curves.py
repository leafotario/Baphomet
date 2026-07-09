from __future__ import annotations

"""Curvas De Progressão Do Sistema De XP."""

from .xp_models import CurveTuning, ProgressSnapshot, XpDifficulty

CURVE_BY_DIFFICULTY: dict[XpDifficulty, CurveTuning] = {
    XpDifficulty.VERY_EASY: CurveTuning(quadratic=70, linear=50, multiplier=0.65),
    XpDifficulty.EASY: CurveTuning(quadratic=70, linear=50, multiplier=0.85),
    XpDifficulty.NORMAL: CurveTuning(quadratic=70, linear=50, multiplier=1.00),
    XpDifficulty.HARD: CurveTuning(quadratic=70, linear=50, multiplier=1.25),
    XpDifficulty.INSANE: CurveTuning(quadratic=70, linear=50, multiplier=1.60),
}


def curve_for(difficulty: XpDifficulty) -> CurveTuning:
    return CURVE_BY_DIFFICULTY[difficulty]


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
