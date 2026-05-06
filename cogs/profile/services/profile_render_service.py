from __future__ import annotations

import io
import pathlib
import textwrap
from dataclasses import dataclass
from typing import Any

import discord
from PIL import Image, ImageDraw, ImageFont

from ..schemas import ProfileSnapshot


ROOT_DIR = pathlib.Path(__file__).resolve().parents[3]
FONT_DIR = ROOT_DIR / "assets" / "fonts"


@dataclass(frozen=True, slots=True)
class ProfileRenderResult:
    image: io.BytesIO | None
    filename: str
    is_stub: bool
    reason: str | None = None


class ProfileRenderService:
    async def render_profile(self, snapshot: ProfileSnapshot) -> ProfileRenderResult:
        image = self._render_image(snapshot)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return ProfileRenderResult(
            image=buffer,
            filename=f"ficha-{snapshot.live.user_id}.png",
            is_stub=False,
            reason=None,
        )

    def _render_image(self, snapshot: ProfileSnapshot) -> Image.Image:
        width, height = 1200, 700
        rendered = snapshot.rendered_fields()
        bg, panel, accent, text, muted = self._theme_colors(snapshot)
        image = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(image)

        draw.rounded_rectangle((36, 36, width - 36, height - 36), radius=28, fill=panel)
        draw.rectangle((36, 36, width - 36, 48), fill=accent)

        self._draw_avatar_placeholder(draw, snapshot.live.display_name, (82, 94), 148, accent, panel)

        title_font = self._font("Montserrat-Black.ttf", 54)
        headline_font = self._font("Poppins-Regular.ttf", 25)
        label_font = self._font("Poppins-Bold.ttf", 22)
        body_font = self._font("Poppins-Regular.ttf", 22)
        small_font = self._font("Poppins-Regular.ttf", 18)

        draw.text((260, 96), snapshot.live.display_name[:34], font=title_font, fill=text)
        headline = self._field_as_text(rendered.get("headline")) or "Ficha em construcao"
        draw.text((263, 160), self._fit_line(headline, 52), font=headline_font, fill=muted)

        pronouns = self._field_as_text(rendered.get("pronouns"))
        mood = self._field_as_text(rendered.get("mood"))
        badge = snapshot.level.badge_role_name or "sem insignia"
        stat_line = (
            f"@{snapshot.live.username}  |  ID {snapshot.live.user_id}  |  "
            f"Nivel {snapshot.level.level}  |  XP {snapshot.level.total_xp:,}  |  {badge}"
        ).replace(",", ".")
        if pronouns:
            stat_line = f"{pronouns}  |  {stat_line}"
        if mood:
            stat_line = f"{stat_line}  |  {mood}"
        draw.text((263, 202), self._fit_line(stat_line, 90), font=small_font, fill=muted)

        left_x, right_x = 82, 650
        y = 292
        y = self._draw_section(draw, "Bio", self._field_as_text(rendered.get("bio")), left_x, y, 500, label_font, body_font, accent, text, muted)
        y = self._draw_section(draw, "Info basica", self._field_as_text(rendered.get("basic_info")), left_x, y + 22, 500, label_font, body_font, accent, text, muted)

        ry = 292
        ry = self._draw_section(draw, "Me pergunte sobre", self._field_as_text(rendered.get("ask_me_about")), right_x, ry, 470, label_font, body_font, accent, text, muted)
        interests = self._field_as_text(rendered.get("interests"))
        ry = self._draw_section(draw, "Interesses", interests, right_x, ry + 22, 470, label_font, body_font, accent, text, muted)

        visual = self._visual_summary(snapshot).replace("\n", "  |  ")
        draw.text((82, height - 88), self._fit_line(visual, 100), font=small_font, fill=muted)
        return image

    def build_preview_embed(self, snapshot: ProfileSnapshot) -> discord.Embed:
        embed = discord.Embed(
            title=snapshot.live.display_name,
            description=self._field_as_text(snapshot.rendered_fields().get("headline")) or None,
            color=self._embed_color(snapshot),
        )
        if snapshot.live.avatar_url:
            embed.set_thumbnail(url=snapshot.live.avatar_url)

        embed.add_field(name="Usuario", value=f"{snapshot.live.username}\nID: {snapshot.live.user_id}", inline=True)
        embed.add_field(
            name="XP",
            value=self._xp_summary(snapshot),
            inline=True,
        )
        if snapshot.level.badge_role_name:
            embed.add_field(name="Cargo-insignia", value=snapshot.level.badge_role_name, inline=True)

        for key, value in snapshot.rendered_fields().items():
            if key in {"headline", "theme_preset", "accent_palette", "charm_preset"}:
                continue
            definition = snapshot.fields[key].definition
            embed.add_field(
                name=definition.label,
                value=self._field_as_text(value),
                inline=definition.key in {"pronouns", "mood"},
            )

        embed.add_field(name="Tema", value=self._visual_summary(snapshot), inline=False)
        embed.set_footer(text=f"Render revision {snapshot.profile.render_revision}")
        return embed

    def _xp_summary(self, snapshot: ProfileSnapshot) -> str:
        if not snapshot.level.available:
            return f"Indisponivel ({snapshot.level.unavailable_reason})"
        position = f"#{snapshot.level.position}" if snapshot.level.position is not None else "sem ranking"
        return (
            f"Nivel {snapshot.level.level}\n"
            f"XP {snapshot.level.total_xp:,}\n"
            f"{snapshot.level.xp_into_level}/{snapshot.level.xp_for_next_level} ({position})"
        ).replace(",", ".")

    def _visual_summary(self, snapshot: ProfileSnapshot) -> str:
        rendered = snapshot.rendered_fields()
        theme = rendered.get("theme_preset", "classic")
        palette = self._field_as_text(rendered.get("accent_palette", []))
        charm = rendered.get("charm_preset", "none")
        return f"Tema: {theme}\nPaleta: {palette or 'padrao'}\nCharm: {charm}"

    def _embed_color(self, snapshot: ProfileSnapshot) -> discord.Color:
        rendered = snapshot.rendered_fields()
        palette = rendered.get("accent_palette")
        if isinstance(palette, list) and palette:
            first = str(palette[0]).strip().lstrip("#")
            if len(first) == 6:
                try:
                    return discord.Color(int(first, 16))
                except ValueError:
                    pass
        if snapshot.level.badge_role_color is not None:
            return discord.Color(snapshot.level.badge_role_color)
        return discord.Color.dark_purple()

    def _theme_colors(self, snapshot: ProfileSnapshot) -> tuple[tuple[int, int, int], ...]:
        palette = snapshot.rendered_fields().get("accent_palette")
        accent = self._parse_hex_color(palette[0]) if isinstance(palette, list) and palette else None
        theme = str(snapshot.rendered_fields().get("theme_preset", "classic"))
        defaults = {
            "classic": ((24, 24, 30), (39, 38, 48), (139, 92, 246), (246, 244, 255), (190, 184, 210)),
            "minimal": ((246, 246, 242), (255, 255, 255), (40, 40, 40), (20, 20, 20), (95, 95, 95)),
            "neon": ((8, 8, 14), (18, 19, 31), (0, 209, 178), (240, 255, 252), (154, 245, 231)),
            "celestial": ((11, 18, 32), (24, 31, 51), (255, 209, 102), (248, 250, 252), (188, 200, 224)),
            "monochrome": ((18, 18, 18), (36, 36, 36), (210, 210, 210), (250, 250, 250), (180, 180, 180)),
        }
        bg, panel, default_accent, text, muted = defaults.get(theme, defaults["classic"])
        return bg, panel, accent or default_accent, text, muted

    def _parse_hex_color(self, value: object) -> tuple[int, int, int] | None:
        raw = str(value).strip().lstrip("#")
        if len(raw) != 6:
            return None
        try:
            return tuple(int(raw[index : index + 2], 16) for index in (0, 2, 4))
        except ValueError:
            return None

    def _font(self, filename: str, size: int) -> ImageFont.ImageFont:
        path = FONT_DIR / filename
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            return ImageFont.load_default()

    def _draw_avatar_placeholder(
        self,
        draw: ImageDraw.ImageDraw,
        display_name: str,
        top_left: tuple[int, int],
        size: int,
        accent: tuple[int, int, int],
        panel: tuple[int, int, int],
    ) -> None:
        x, y = top_left
        draw.ellipse((x, y, x + size, y + size), fill=accent)
        draw.ellipse((x + 7, y + 7, x + size - 7, y + size - 7), fill=panel)
        initials = "".join(part[0] for part in display_name.split()[:2]).upper() or "?"
        font = self._font("Montserrat-Black.ttf", 52)
        bbox = draw.textbbox((0, 0), initials, font=font)
        draw.text(
            (x + (size - (bbox[2] - bbox[0])) / 2, y + (size - (bbox[3] - bbox[1])) / 2 - 4),
            initials,
            font=font,
            fill=accent,
        )

    def _draw_section(
        self,
        draw: ImageDraw.ImageDraw,
        label: str,
        value: str,
        x: int,
        y: int,
        width_chars: int,
        label_font: ImageFont.ImageFont,
        body_font: ImageFont.ImageFont,
        accent: tuple[int, int, int],
        text: tuple[int, int, int],
        muted: tuple[int, int, int],
    ) -> int:
        draw.text((x, y), label, font=label_font, fill=accent)
        current_y = y + 34
        content = value or "Ainda nao preenchido."
        for paragraph in content.split("\n"):
            wrapped = textwrap.wrap(paragraph, width=max(24, width_chars // 11)) or [""]
            for line in wrapped[:5]:
                draw.text((x, current_y), line, font=body_font, fill=text if value else muted)
                current_y += 29
        return current_y

    def _fit_line(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 1)].rstrip() + "..."

    def _field_as_text(self, value: Any) -> str:
        if value in (None, "", (), []):
            return ""
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)
