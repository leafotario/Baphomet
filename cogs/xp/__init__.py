from __future__ import annotations

import pathlib

from discord.ext import commands

from .commands import RankConfig, XpAdminCommands, XpEvents, XpUserCommands
from .db import XpRepository
from .rank_badges import RankBadgeService
from .rendering import XpCardRenderer
from .xp_runtime import XpRuntime
from .xp_service import XpService

DATA_DIR = pathlib.Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "baphomet_xp.sqlite3"


import logging
import traceback
from core_logger import log_exception


logger = logging.getLogger("baphomet.xp.setup")

async def setup(bot: commands.Bot) -> None:
    try:
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
        await bot.add_cog(RankConfig(bot, runtime))
    except Exception as e:
        log_exception(e)
        tb_str = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        logger.error(
            f"❌ [XP SETUP FORENSE] Falha Crítica ao inicializar o cogs.xp\n"
            f"➤ Erro: {type(e).__name__}: {e}\n"
            f"➤ Traceback Integral:\n{tb_str}"
        )
        raise e  # Reraise para falhar explicitamente o carregamento no discord.py
