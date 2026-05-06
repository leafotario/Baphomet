from __future__ import annotations

import pathlib

from discord.ext import commands

from .cog import ProfileCog
from .db import ProfileDatabase
from .field_registry import PROFILE_FIELD_REGISTRY
from .repositories import ProfileRepository
from .runtime import ProfileRuntime
from .services import PresentationChannelService, ProfileModerationService, ProfileRenderService, ProfileService, XpRuntimeLevelProvider


DATA_DIR = pathlib.Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "baphomet_profiles.sqlite3"


async def setup(bot: commands.Bot) -> None:
    database = ProfileDatabase(DB_PATH)
    await database.run_migrations()

    repository = ProfileRepository(database)
    moderation = ProfileModerationService(PROFILE_FIELD_REGISTRY)
    service = ProfileService(
        repository=repository,
        field_registry=PROFILE_FIELD_REGISTRY,
        level_provider=XpRuntimeLevelProvider(bot),
        moderation_service=moderation,
    )
    renderer = ProfileRenderService()
    presentation = PresentationChannelService(service)

    bot.profile_runtime = ProfileRuntime(
        database=database,
        repository=repository,
        service=service,
        moderation=moderation,
        presentation=presentation,
        renderer=renderer,
    )
    await bot.add_cog(ProfileCog(bot, service, renderer, presentation))
