from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands


def _format_uptime(delta: timedelta) -> str:
    total_seconds = max(0, int(delta.total_seconds()))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or parts:
        parts.append(f"{hours}h")
    if minutes or parts:
        parts.append(f"{minutes}min")
    parts.append(f"{seconds}s")
    return " ".join(parts)


class Ping(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="ping",
        description="Mostra desde quando o bot está ativo.",
    )
    async def ping(self, interaction: discord.Interaction) -> None:
        started_at = datetime.fromtimestamp(self.bot.start_time, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        uptime = _format_uptime(now - started_at)
        started_ts = int(started_at.timestamp())

        embed = discord.Embed(
            title="🏓 Ping do altar",
            description=(
                f"Ativo desde **{started_at.strftime('%d/%m/%Y %H:%M:%S UTC')}**\n"
                f"<t:{started_ts}:F> • <t:{started_ts}:R>\n"
                f"Uptime: **{uptime}**"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ping(bot))
