from __future__ import annotations

from dataclasses import dataclass

from .db import XpRepository
from .rendering import XpCardRenderer
from .xp_service import XpService


@dataclass(slots=True)
class XpRuntime:
    repository: XpRepository
    service: XpService
    cards: XpCardRenderer
