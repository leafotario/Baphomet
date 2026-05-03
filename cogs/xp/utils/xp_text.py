from __future__ import annotations

"""Funções auxiliares de texto e data para o sistema de XP."""

import re
from datetime import datetime, timezone

from .xp_models import XpDifficulty

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


def normalize_difficulty(raw: str | XpDifficulty | None) -> XpDifficulty:
    if isinstance(raw, XpDifficulty):
        return raw
    if raw is None:
        return XpDifficulty.NORMAL
    return LEGACY_DIFFICULTY_MAP.get(str(raw).lower(), XpDifficulty.NORMAL)


def normalize_message_content(content: str) -> str:
    value = URL_RE.sub(" ", content.lower())
    value = NON_WORD_RE.sub(" ", value)
    value = WHITESPACE_RE.sub(" ", value).strip()
    return value
