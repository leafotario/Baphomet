from __future__ import annotations

from typing import Any


async def setup(bot: Any) -> None:
    from .cog import TierListTemplateCog

    await bot.add_cog(TierListTemplateCog(bot))
