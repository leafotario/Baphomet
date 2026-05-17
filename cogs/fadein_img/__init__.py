from __future__ import annotations

from typing import Any


async def setup(bot: Any) -> None:
    from .commands import setup as setup_cog

    await setup_cog(bot)


__all__ = ["setup"]
