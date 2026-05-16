from __future__ import annotations

import pathlib

from discord.ext import commands

from .commands import RankAdminCommands, XpAdminCommands, XpEvents, XpUserCommands
from .db import XpRepository
from .rank_badges import RankBadgeService
from .rendering import XpCardRenderer
from .xp_runtime import XpRuntime
from .xp_service import XpService

DATA_DIR = pathlib.Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "baphomet_xp.sqlite3"


async def setup(bot: commands.Bot) -> None:
    # 1. Setup DB
    repository = XpRepository(str(DB_PATH))
    await repository.connect()

    # 2. Setup Services
    vinculos_runtime = getattr(bot, "vinculos_runtime", None)
    vinculos_provider = getattr(vinculos_runtime, "repository", None)
    service = XpService(repository, vinculos_provider=vinculos_provider)
    cards = XpCardRenderer()
    badges = RankBadgeService(repository)

    # 3. Create Runtime and attach to bot
    runtime = XpRuntime(repository=repository, service=service, cards=cards, badges=badges)
    bot.xp_runtime = runtime

    # 4. Add Cogs
    await bot.add_cog(XpEvents(bot, runtime))
    await bot.add_cog(XpUserCommands(bot, runtime))
    await bot.add_cog(XpAdminCommands(bot, runtime))
    await bot.add_cog(RankAdminCommands(bot, runtime))
