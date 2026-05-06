from __future__ import annotations

from dataclasses import dataclass

from .db import ProfileDatabase
from .repositories import ProfileRepository
from .services import PresentationChannelService, ProfileModerationService, ProfileRenderService, ProfileService


@dataclass(frozen=True, slots=True)
class ProfileRuntime:
    database: ProfileDatabase
    repository: ProfileRepository
    service: ProfileService
    moderation: ProfileModerationService
    presentation: PresentationChannelService
    renderer: ProfileRenderService
