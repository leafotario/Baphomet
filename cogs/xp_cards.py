from __future__ import annotations

"""Cards Visuais Do Sistema De XP Do Baphomet."""

import io
from typing import Iterable

import discord
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .xp_models import LeaderboardEntry, RankSnapshot

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


class XpCardRenderer:
    def __init__(self, *, font_regular_path: str | None = FONT_REG, font_bold_path: str | None = FONT_BOLD) -> None:
        self.font_regular_path = font_regular_path
        self.font_bold_path = font_bold_path or font_regular_path

    def _font(self, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        path = self.font_bold_path if bold else self.font_regular_path
        if path:
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                pass
        return ImageFont.load_default()

    async def _read_asset(self, asset: discord.Asset | None) -> bytes | None:
        if asset is None:
            return None
        try:
            return await asset.read()
        except Exception:
            return None

    def _circle_avatar(self, avatar_bytes: bytes | None, size: int) -> Image.Image:
        if avatar_bytes:
            try:
                avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
                avatar = ImageOps.fit(avatar, (size, size), method=Image.Resampling.LANCZOS)
            except Exception:
                avatar = Image.new("RGBA", (size, size), (86, 64, 134, 255))
        else:
            avatar = Image.new("RGBA", (size, size), (86, 64, 134, 255))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
        output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        output.paste(avatar, (0, 0), mask)
        return output

    def _truncate(self, value: str, max_chars: int) -> str:
        return value if len(value) <= max_chars else value[: max_chars - 1].rstrip() + "…"

    def _progress_bar(self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], ratio: float) -> None:
        x0, y0, x1, y1 = box
        draw.rounded_rectangle(box, radius=14, fill=(52, 43, 74, 255))
        filled = x0 + int((x1 - x0) * ratio)
        if filled > x0:
            draw.rounded_rectangle((x0, y0, filled, y1), radius=14, fill=(163, 112, 255, 255))

    async def render_rank_card(self, *, guild: discord.Guild, member: discord.Member | discord.User, snapshot: RankSnapshot) -> io.BytesIO:
        width, height = 1040, 320
        canvas = Image.new("RGBA", (width, height), (16, 13, 25, 255))
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((24, 24, width - 24, height - 24), radius=28, fill=(27, 23, 41, 255), outline=(110, 86, 171, 255), width=2)
        draw.rounded_rectangle((40, 40, 250, height - 40), radius=24, fill=(39, 31, 63, 255))

        avatar = self._circle_avatar(await self._read_asset(member.display_avatar), 150)
        canvas.paste(avatar, (70, 85), avatar)

        guild_name = self._truncate(guild.name, 28)
        user_name = self._truncate(snapshot.display_name, 26)
        rank_text = f"#{snapshot.position}" if snapshot.position is not None else "Sem Posição"
        progress_text = f"Progresso {snapshot.xp_into_level}/{snapshot.xp_for_next_level} XP"

        draw.text((285, 55), "Não Sei O Que", font=self._font(28, bold=True), fill=(220, 208, 255, 255
        draw.text((285, 138), user_name, font=self._font(34, bold=True), fill=(245, 242, 255, 255))
        draw.text((285, 190), f"Nível {snapshot.level}", font=self._font(24, bold=True), fill=(189, 255, 220, 255))
        draw.text((435, 190), f"XP Total {snapshot.total_xp:,}".replace(",", "."), font=self._font(24), fill=(234, 228, 255, 255))
        draw.text((760, 190), rank_text, font=self._font(30, bold=True), fill=(255, 220, 145, 255))
        self._progress_bar(draw, (285, 235, 930, 260), snapshot.progress_ratio)
        draw.text((285, 268), progress_text, font=self._font(18), fill=(201, 193, 228, 255))
        draw.text((735, 268), f"Faltam {snapshot.remaining_to_next:,} XP".replace(",", "."), font=self._font(18), fill=(201, 193, 228, 255))

        output = io.BytesIO()
        canvas.save(output, format="PNG")
        output.seek(0)
        return output

    async def render_leaderboard_card(
        self,
        *,
        guild: discord.Guild,
        entries: Iterable[tuple[LeaderboardEntry, discord.Member | discord.User | None]],
    ) -> io.BytesIO:
        rows = list(entries)[:5]
        width = 1180
        row_height = 128
        top_padding = 150
        height = max(300, top_padding + (row_height * max(1, len(rows))) + 40)
        canvas = Image.new("RGBA", (width, height), (16, 13, 25, 255))
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((22, 22, width - 22, height - 22), radius=30, fill=(26, 21, 40, 255), outline=(110, 86, 171, 255), width=2)
        draw.text((55, 48), "Hall Da Glória", font=self._font(34, bold=True), fill=(245, 240, 255, 255))
        draw.text((55, 92), self._truncate(guild.name, 34), font=self._font(20), fill=(182, 173, 212, 255))

        medal_colors = {1: (255, 215, 120, 255), 2: (198, 207, 225, 255), 3: (222, 166, 120, 255)}
        if not rows:
            draw.text((55, 180), "Ainda Não Há Almas Ranqueadas Neste Servidor.", font=self._font(24), fill=(220, 210, 246, 255))
        else:
            for index, (entry, member) in enumerate(rows, start=1):
                y = top_padding + ((index - 1) * row_height)
                bg_color = (42, 33, 65, 255) if index <= 3 else (33, 28, 52, 255)
                draw.rounded_rectangle((45, y, width - 45, y + 102), radius=24, fill=bg_color)
                badge_color = medal_colors.get(index, (115, 106, 146, 255))
                draw.rounded_rectangle((65, y + 18, 140, y + 84), radius=18, fill=badge_color)
                draw.text((88, y + 35), str(index), font=self._font(26, bold=True), fill=(20, 16, 30, 255))
                avatar_bytes = await self._read_asset(member.display_avatar) if member else None
                avatar = self._circle_avatar(avatar_bytes, 72)
                canvas.paste(avatar, (160, y + 15), avatar)
                display_name = self._truncate(entry.display_name, 24)
                draw.text((255, y + 24), display_name, font=self._font(24, bold=True), fill=(246, 242, 255, 255))
                draw.text((255, y + 58), f"Nível {entry.level} • {entry.total_xp:,} XP".replace(",", "."), font=self._font(18), fill=(206, 199, 229, 255))
                self._progress_bar(draw, (700, y + 34, 1055, y + 58), entry.progress_ratio)
                draw.text((700, y + 66), f"Faltam {entry.remaining_to_next:,} XP".replace(",", "."), font=self._font(16), fill=(198, 190, 223, 255))

        output = io.BytesIO()
        canvas.save(output, format="PNG")
        output.seek(0)
        return output