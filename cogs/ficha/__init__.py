"""Pacote isolado para a nova ficha visual do usuário.

O loader do bot carrega diretórios em `cogs/` como extensões quando eles têm
`__init__.py`. Este pacote só expõe assets/rendering; o setup é intencionalmente
vazio para não registrar comandos paralelos.
"""

from __future__ import annotations

from discord.ext import commands


async def setup(bot: commands.Bot) -> None:
    return None
