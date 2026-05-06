from __future__ import annotations

import asyncio
import io
import logging
import math
import pathlib
import random
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from ..models import ProfileFieldStatus
from ..schemas import ProfileSnapshot


LOGGER = logging.getLogger("baphomet.profile.renderer")

ROOT_DIR = pathlib.Path(__file__).resolve().parents[3]
FONT_DIR = ROOT_DIR / "assets" / "fonts"


Color = tuple[int, int, int]
ColorA = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    @property
    def size(self) -> tuple[int, int]:
        return (self.w, self.h)

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    def inset(self, amount: int) -> Rect:
        return Rect(self.x + amount, self.y + amount, self.w - amount * 2, self.h - amount * 2)

    def inset_xy(self, x: int, y: int) -> Rect:
        return Rect(self.x + x, self.y + y, self.w - x * 2, self.h - y * 2)


@dataclass(frozen=True, slots=True)
class ProfileCardLayout:
    canvas: tuple[int, int] = (1500, 1000)
    safe_margin: int = 54
    frame_radius: int = 46
    inner_radius: int = 30
    panel_radius: int = 26
    avatar_medallion: Rect = Rect(80, 128, 330, 330)
    identity_plate: Rect = Rect(430, 86, 650, 198)
    badge_plate: Rect = Rect(1110, 86, 306, 198)
    basic_panel: Rect = Rect(430, 310, 484, 430)
    bio_panel: Rect = Rect(940, 310, 476, 430)
    xp_panel: Rect = Rect(80, 790, 1336, 142)
    user_plate: Rect = Rect(92, 482, 306, 114)
    charm_area: Rect = Rect(116, 616, 250, 126)
    panel_padding: int = 32
    text_line_gap: int = 8
    chip_gap: int = 12
    chip_radius: int = 17
    shadow_blur: int = 22
    template_noise_size: tuple[int, int] = (300, 200)


@dataclass(frozen=True, slots=True)
class ThemePreset:
    key: str
    name: str
    background_top: Color
    background_bottom: Color
    paper: Color
    paper_shadow: Color
    paper_highlight: Color
    metal_dark: Color
    metal_mid: Color
    metal_light: Color
    enamel: Color
    enamel_dark: Color
    accent: Color
    accent_2: Color
    text: Color
    text_muted: Color
    text_on_dark: Color
    line: Color
    chip_fill: Color
    chip_text: Color
    xp_start: Color
    xp_end: Color
    xp_track: Color
    fallback_slot: Color
    texture_strength: int


@dataclass(frozen=True, slots=True)
class FontSet:
    display_72: ImageFont.FreeTypeFont | ImageFont.ImageFont
    display_52: ImageFont.FreeTypeFont | ImageFont.ImageFont
    display_42: ImageFont.FreeTypeFont | ImageFont.ImageFont
    ui_34: ImageFont.FreeTypeFont | ImageFont.ImageFont
    ui_28: ImageFont.FreeTypeFont | ImageFont.ImageFont
    ui_24: ImageFont.FreeTypeFont | ImageFont.ImageFont
    ui_22: ImageFont.FreeTypeFont | ImageFont.ImageFont
    ui_20: ImageFont.FreeTypeFont | ImageFont.ImageFont
    ui_18: ImageFont.FreeTypeFont | ImageFont.ImageFont
    ui_16: ImageFont.FreeTypeFont | ImageFont.ImageFont
    bold_28: ImageFont.FreeTypeFont | ImageFont.ImageFont
    bold_24: ImageFont.FreeTypeFont | ImageFont.ImageFont
    bold_20: ImageFont.FreeTypeFont | ImageFont.ImageFont


@dataclass(frozen=True, slots=True)
class ProfileRenderResult:
    image: io.BytesIO | None
    filename: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProfileRenderCacheKey:
    guild_id: int
    user_id: int
    revision: int
    theme_key: str
    live_signature: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class ProfileRenderCacheEntry:
    payload: bytes
    created_at_monotonic: float


THEMES: dict[str, ThemePreset] = {
    "classic": ThemePreset(
        key="classic",
        name="Obsidian dossier",
        background_top=(25, 21, 31),
        background_bottom=(66, 47, 83),
        paper=(245, 238, 220),
        paper_shadow=(179, 162, 130),
        paper_highlight=(255, 252, 241),
        metal_dark=(42, 39, 48),
        metal_mid=(105, 96, 115),
        metal_light=(211, 203, 219),
        enamel=(111, 70, 173),
        enamel_dark=(49, 34, 76),
        accent=(195, 161, 255),
        accent_2=(237, 184, 92),
        text=(42, 35, 41),
        text_muted=(102, 91, 99),
        text_on_dark=(253, 247, 255),
        line=(181, 161, 128),
        chip_fill=(72, 53, 88),
        chip_text=(252, 242, 255),
        xp_start=(111, 70, 173),
        xp_end=(237, 184, 92),
        xp_track=(65, 56, 69),
        fallback_slot=(205, 191, 166),
        texture_strength=28,
    ),
    "minimal": ThemePreset(
        key="minimal",
        name="Porcelain brass",
        background_top=(229, 226, 216),
        background_bottom=(190, 182, 163),
        paper=(254, 252, 244),
        paper_shadow=(176, 164, 133),
        paper_highlight=(255, 255, 250),
        metal_dark=(93, 78, 49),
        metal_mid=(178, 148, 85),
        metal_light=(246, 225, 162),
        enamel=(31, 79, 83),
        enamel_dark=(20, 48, 51),
        accent=(185, 141, 61),
        accent_2=(45, 107, 111),
        text=(34, 32, 28),
        text_muted=(105, 97, 82),
        text_on_dark=(253, 250, 239),
        line=(193, 176, 133),
        chip_fill=(238, 227, 197),
        chip_text=(65, 55, 40),
        xp_start=(45, 107, 111),
        xp_end=(220, 175, 82),
        xp_track=(207, 196, 168),
        fallback_slot=(217, 206, 177),
        texture_strength=20,
    ),
    "neon": ThemePreset(
        key="neon",
        name="Nocturne enamel",
        background_top=(8, 13, 22),
        background_bottom=(23, 18, 42),
        paper=(224, 230, 229),
        paper_shadow=(93, 107, 120),
        paper_highlight=(246, 255, 253),
        metal_dark=(16, 22, 32),
        metal_mid=(64, 75, 95),
        metal_light=(178, 204, 220),
        enamel=(0, 139, 152),
        enamel_dark=(9, 40, 52),
        accent=(0, 228, 202),
        accent_2=(255, 79, 163),
        text=(18, 28, 35),
        text_muted=(76, 94, 105),
        text_on_dark=(238, 255, 253),
        line=(96, 149, 157),
        chip_fill=(14, 47, 61),
        chip_text=(225, 255, 251),
        xp_start=(0, 228, 202),
        xp_end=(255, 79, 163),
        xp_track=(28, 45, 58),
        fallback_slot=(173, 196, 202),
        texture_strength=32,
    ),
    "celestial": ThemePreset(
        key="celestial",
        name="Celestial navy",
        background_top=(11, 25, 48),
        background_bottom=(40, 69, 104),
        paper=(239, 233, 210),
        paper_shadow=(147, 132, 99),
        paper_highlight=(253, 247, 226),
        metal_dark=(31, 43, 67),
        metal_mid=(76, 94, 129),
        metal_light=(200, 211, 230),
        enamel=(33, 57, 104),
        enamel_dark=(11, 21, 45),
        accent=(255, 205, 96),
        accent_2=(118, 178, 211),
        text=(31, 36, 48),
        text_muted=(88, 94, 107),
        text_on_dark=(247, 251, 255),
        line=(181, 157, 100),
        chip_fill=(31, 57, 104),
        chip_text=(247, 248, 236),
        xp_start=(255, 205, 96),
        xp_end=(118, 178, 211),
        xp_track=(41, 56, 82),
        fallback_slot=(199, 189, 156),
        texture_strength=24,
    ),
    "monochrome": ThemePreset(
        key="monochrome",
        name="Graphite archive",
        background_top=(22, 23, 24),
        background_bottom=(68, 68, 66),
        paper=(235, 233, 226),
        paper_shadow=(137, 135, 128),
        paper_highlight=(255, 254, 250),
        metal_dark=(37, 37, 38),
        metal_mid=(106, 106, 106),
        metal_light=(218, 218, 214),
        enamel=(66, 66, 67),
        enamel_dark=(20, 20, 21),
        accent=(204, 204, 198),
        accent_2=(111, 111, 110),
        text=(28, 28, 28),
        text_muted=(87, 87, 83),
        text_on_dark=(248, 248, 244),
        line=(166, 166, 158),
        chip_fill=(58, 58, 59),
        chip_text=(246, 246, 241),
        xp_start=(221, 221, 214),
        xp_end=(112, 112, 111),
        xp_track=(52, 52, 52),
        fallback_slot=(199, 198, 190),
        texture_strength=22,
    ),
}


LAYOUT = ProfileCardLayout()
ELLIPSIS = "..."
REMOVED_CONTENT = "[Conteúdo removido]"
EMPTY_CHIP = "sem interesses salvos"
AVATAR_FETCH_TIMEOUT_SECONDS = 4
MAX_AVATAR_BYTES = 4 * 1024 * 1024
RENDER_CACHE_TTL_SECONDS = 45.0
RENDER_CACHE_MAX_ITEMS = 96
REQUIRED_FONT_FILES = ("Poppins-Regular.ttf", "Poppins-Bold.ttf", "Montserrat-Black.ttf")


class ProfileRenderService:
    def __init__(self, *, layout: ProfileCardLayout = LAYOUT) -> None:
        self.layout = layout
        self._validate_assets()
        self._fonts = self._load_fonts()
        self._template_cache: dict[str, Image.Image] = {}
        self._render_cache: OrderedDict[ProfileRenderCacheKey, ProfileRenderCacheEntry] = OrderedDict()
        self._render_locks: dict[tuple[int, int], asyncio.Lock] = {}

    async def render_profile(self, snapshot: ProfileSnapshot) -> ProfileRenderResult:
        start = time.monotonic()
        cache_key = self._cache_key(snapshot)
        lock = self._render_lock_for(snapshot.live.guild_id, snapshot.live.user_id)
        async with lock:
            cached = self._get_cached_render(cache_key)
            if cached is not None:
                LOGGER.info(
                    "profile_rendered guild_id=%s user_id=%s revision=%s cached=1 duration_ms=%s",
                    snapshot.live.guild_id,
                    snapshot.live.user_id,
                    snapshot.profile.render_revision,
                    int((time.monotonic() - start) * 1000),
                )
                buffer = io.BytesIO(cached)
                buffer.seek(0)
                return ProfileRenderResult(
                    image=buffer,
                    filename=f"ficha-{snapshot.live.user_id}.png",
                    reason=None,
                )

            try:
                avatar = await self._fetch_avatar(snapshot.live.avatar_url)
                image_bytes = self.render_profile_card(snapshot, avatar_image=avatar)
            except Exception:
                LOGGER.exception(
                    "profile_render_failed guild_id=%s user_id=%s revision=%s",
                    snapshot.live.guild_id,
                    snapshot.live.user_id,
                    snapshot.profile.render_revision,
                )
                raise
            self._store_render(cache_key, image_bytes)

        LOGGER.info(
            "profile_rendered guild_id=%s user_id=%s revision=%s cached=0 duration_ms=%s",
            snapshot.live.guild_id,
            snapshot.live.user_id,
            snapshot.profile.render_revision,
            int((time.monotonic() - start) * 1000),
        )
        buffer = io.BytesIO(image_bytes)
        buffer.seek(0)
        return ProfileRenderResult(
            image=buffer,
            filename=f"ficha-{snapshot.live.user_id}.png",
            reason=None,
        )

    def render_profile_card(self, snapshot: ProfileSnapshot, *, avatar_image: Image.Image | None = None) -> bytes:
        """Renderiza a ficha em PNG 1500x1000, pronta para envio no Discord."""
        image = self._compose(snapshot, avatar_image=avatar_image)
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()

    def invalidate_user(self, guild_id: int, user_id: int) -> None:
        stale_keys = [
            key
            for key in self._render_cache
            if key.guild_id == guild_id and key.user_id == user_id
        ]
        for key in stale_keys:
            self._render_cache.pop(key, None)

    def save_debug_preview(
        self,
        snapshot: ProfileSnapshot,
        path: str | pathlib.Path,
        *,
        avatar_image: Image.Image | None = None,
    ) -> pathlib.Path:
        output_path = pathlib.Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(self.render_profile_card(snapshot, avatar_image=avatar_image))
        return output_path

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

    def _compose(self, snapshot: ProfileSnapshot, *, avatar_image: Image.Image | None) -> Image.Image:
        theme = self._theme_for(snapshot)
        try:
            return self._compose_with_theme(snapshot, theme, avatar_image=avatar_image)
        except Exception:
            if theme.key == "classic":
                raise
            LOGGER.exception(
                "profile_theme_render_failed guild_id=%s user_id=%s revision=%s theme=%s fallback=classic",
                snapshot.live.guild_id,
                snapshot.live.user_id,
                snapshot.profile.render_revision,
                theme.key,
            )
            return self._compose_with_theme(snapshot, THEMES["classic"], avatar_image=avatar_image)

    def _compose_with_theme(
        self,
        snapshot: ProfileSnapshot,
        theme: ThemePreset,
        *,
        avatar_image: Image.Image | None,
    ) -> Image.Image:
        image = self._template_for(theme).copy()

        self._draw_avatar_medallion(image, snapshot, theme, avatar_image)
        self._draw_identity_plate(image, snapshot, theme)
        self._draw_badge_plate(image, snapshot, theme)
        self._draw_user_plate(image, snapshot, theme)
        self._draw_basic_panel(image, snapshot, theme)
        self._draw_bio_panel(image, snapshot, theme)
        self._draw_charm(image, snapshot, theme)
        self._draw_xp_panel(image, snapshot, theme)
        self._draw_edge_highlights(image, theme)
        return image

    def _template_for(self, theme: ThemePreset) -> Image.Image:
        cached = self._template_cache.get(theme.key)
        if cached is not None:
            return cached

        width, height = self.layout.canvas
        base = self._vertical_gradient((width, height), theme.background_top, theme.background_bottom)
        base = Image.alpha_composite(base, self._noise_overlay((width, height), theme.key, 22, 34))
        base = Image.alpha_composite(base, self._vignette((width, height), 84))

        frame = Rect(
            self.layout.safe_margin,
            self.layout.safe_margin,
            width - self.layout.safe_margin * 2,
            height - self.layout.safe_margin * 2,
        )
        self._draw_shadow(base, frame, radius=self.layout.frame_radius, blur=30, offset=(0, 16), opacity=120)
        metal = self._brushed_metal_layer(frame.size, theme)
        metal_mask = Image.new("L", frame.size, 0)
        ImageDraw.Draw(metal_mask).rounded_rectangle((0, 0, frame.w, frame.h), radius=self.layout.frame_radius, fill=255)
        base.paste(metal, frame.box[:2], metal_mask)

        draw = ImageDraw.Draw(base)
        draw.rounded_rectangle(frame.box, radius=self.layout.frame_radius, outline=with_alpha(theme.metal_light, 165), width=3)
        draw.rounded_rectangle(frame.inset(12).box, radius=self.layout.frame_radius - 10, outline=with_alpha(theme.metal_dark, 150), width=2)

        inner = frame.inset(28)
        self._draw_embossed_plate(base, inner, theme, radius=self.layout.inner_radius, fill=theme.paper, paper=True)
        self._draw_fasteners(draw, frame, theme)
        self._template_cache[theme.key] = base
        return base

    def _draw_identity_plate(self, image: Image.Image, snapshot: ProfileSnapshot, theme: ThemePreset) -> None:
        fonts = self._fonts
        rect = self.layout.identity_plate
        draw = ImageDraw.Draw(image)

        self._draw_embossed_plate(image, rect, theme, radius=28, fill=theme.enamel, paper=False)
        top_gloss = self._vertical_gradient(rect.size, (255, 255, 255), (255, 255, 255), top_alpha=58, bottom_alpha=0)
        mask = rounded_mask(rect.size, 28)
        image.paste(top_gloss, rect.box[:2], mask)

        name = self._fit_text_line(snapshot.live.display_name, fonts.display_72, rect.w - 48)
        draw.text((rect.x + 28, rect.y + 22), name, font=fonts.display_72, fill=theme.text_on_dark)

        username = self._fit_text_line(f"@{snapshot.live.username}", fonts.bold_24, 238)
        pronouns = self._field_text(snapshot, "pronouns") or "pronomes livres"
        pronouns = self._fit_text_line(pronouns, fonts.ui_22, 204)
        self._draw_pill(draw, Rect(rect.x + 30, rect.y + 112, 270, 42), username, fonts.bold_24, theme, dark=True)
        self._draw_pill(draw, Rect(rect.x + 316, rect.y + 112, 232, 42), pronouns, fonts.ui_22, theme, dark=True)

        headline = self._field_text(snapshot, "headline") or "Dossie social em branco, pronto para ganhar detalhes."
        headline_lines = self._wrap_text_pixels(headline, fonts.ui_22, rect.w - 60, max_lines=2)
        self._draw_lines(draw, headline_lines, rect.x + 30, rect.y + 160, fonts.ui_22, theme.text_on_dark, 4)

    def _draw_avatar_medallion(
        self,
        image: Image.Image,
        snapshot: ProfileSnapshot,
        theme: ThemePreset,
        avatar_image: Image.Image | None,
    ) -> None:
        rect = self.layout.avatar_medallion
        draw = ImageDraw.Draw(image)

        self._draw_shadow(image, rect, radius=rect.w // 2, blur=32, offset=(0, 18), opacity=135)
        draw.ellipse(rect.box, fill=theme.metal_dark, outline=theme.metal_light, width=4)

        for ring in range(18):
            progress = ring / 17
            color = mix(theme.metal_light, theme.metal_dark, progress)
            inset = 6 + ring * 3
            draw.ellipse((rect.x + inset, rect.y + inset, rect.x + rect.w - inset, rect.y + rect.h - inset), outline=color, width=3)

        enamel_rect = rect.inset(56)
        draw.ellipse(enamel_rect.box, fill=theme.enamel_dark)
        highlight_rect = Rect(enamel_rect.x + 14, enamel_rect.y + 10, enamel_rect.w - 28, enamel_rect.h // 2)
        draw.arc(highlight_rect.box, start=190, end=344, fill=with_alpha(theme.paper_highlight, 120), width=5)

        avatar_rect = rect.inset(66)
        avatar = self._prepare_avatar(snapshot, avatar_image, avatar_rect.w, theme)
        image.alpha_composite(avatar, avatar_rect.box[:2])

        draw.ellipse(avatar_rect.box, outline=with_alpha(theme.paper_highlight, 190), width=4)
        draw.ellipse(avatar_rect.inset(-8).box, outline=with_alpha(theme.accent, 180), width=3)

    def _draw_user_plate(self, image: Image.Image, snapshot: ProfileSnapshot, theme: ThemePreset) -> None:
        rect = self.layout.user_plate
        draw = ImageDraw.Draw(image)
        fonts = self._fonts

        self._draw_embossed_plate(image, rect, theme, radius=22, fill=theme.paper, paper=True)
        draw.text((rect.x + 22, rect.y + 22), "ID do usuario", font=fonts.bold_20, fill=theme.text_muted)
        draw.text((rect.x + 22, rect.y + 55), str(snapshot.live.user_id), font=fonts.ui_24, fill=theme.text)

    def _draw_badge_plate(self, image: Image.Image, snapshot: ProfileSnapshot, theme: ThemePreset) -> None:
        rect = self.layout.badge_plate
        draw = ImageDraw.Draw(image)
        fonts = self._fonts

        self._draw_embossed_plate(image, rect, theme, radius=28, fill=theme.paper, paper=True)
        draw.text((rect.x + 28, rect.y + 24), "Insignia atual", font=fonts.bold_20, fill=theme.text_muted)

        crest = Rect(rect.x + 24, rect.y + 58, 94, 100)
        badge_color = int_to_rgb(snapshot.level.badge_role_color) if snapshot.level.badge_role_color is not None else theme.fallback_slot
        self._draw_crest(draw, crest, badge_color, theme)

        badge = snapshot.level.badge_role_name or "Slot neutro"
        badge_lines = self._wrap_text_pixels(badge, fonts.bold_24, 144, max_lines=2)
        self._draw_lines(draw, badge_lines, rect.x + 136, rect.y + 70, fonts.bold_24, theme.text, 3)

        level_label = "Level indisponivel" if not snapshot.level.available else f"Level {snapshot.level.level}"
        self._draw_pill(draw, Rect(rect.x + 136, rect.y + 132, 140, 38), level_label, fonts.bold_20, theme, dark=False)

    def _draw_basic_panel(self, image: Image.Image, snapshot: ProfileSnapshot, theme: ThemePreset) -> None:
        rect = self.layout.basic_panel
        draw = ImageDraw.Draw(image)
        fonts = self._fonts

        self._draw_embossed_plate(image, rect, theme, radius=self.layout.panel_radius, fill=theme.paper, paper=True)
        self._draw_panel_title(draw, rect, "Informacoes basicas", theme)

        content = self._field_text(snapshot, "basic_info") or "Sem apresentacao salva ainda."
        lines = self._wrap_text_pixels(content, fonts.ui_24, rect.w - self.layout.panel_padding * 2, max_lines=10)
        text_color = theme.text if content != "Sem apresentacao salva ainda." else theme.text_muted
        self._draw_lines(
            draw,
            lines,
            rect.x + self.layout.panel_padding,
            rect.y + 82,
            fonts.ui_24,
            text_color,
            self.layout.text_line_gap,
        )

        mood = self._field_text(snapshot, "mood")
        if mood:
            mood_text = self._fit_text_line(f"Mood: {mood}", fonts.bold_20, rect.w - 70)
            self._draw_pill(draw, Rect(rect.x + 28, rect.y + rect.h - 62, rect.w - 56, 38), mood_text, fonts.bold_20, theme, dark=False)

    def _draw_bio_panel(self, image: Image.Image, snapshot: ProfileSnapshot, theme: ThemePreset) -> None:
        rect = self.layout.bio_panel
        draw = ImageDraw.Draw(image)
        fonts = self._fonts

        self._draw_embossed_plate(image, rect, theme, radius=self.layout.panel_radius, fill=theme.paper, paper=True)
        self._draw_panel_title(draw, rect, "Bio / Conexoes", theme)

        bio = self._field_text(snapshot, "bio") or "Bio ainda nao preenchida."
        bio_color = theme.text if bio != "Bio ainda nao preenchida." else theme.text_muted
        bio_lines = self._wrap_text_pixels(bio, fonts.ui_22, rect.w - 64, max_lines=6)
        self._draw_lines(draw, bio_lines, rect.x + 32, rect.y + 82, fonts.ui_22, bio_color, 7)

        ask = self._field_text(snapshot, "ask_me_about")
        ask_y = rect.y + 276
        draw.line((rect.x + 30, ask_y - 14, rect.x + rect.w - 30, ask_y - 14), fill=with_alpha(theme.line, 150), width=2)
        draw.text((rect.x + 32, ask_y), "Me pergunte sobre", font=fonts.bold_20, fill=theme.text_muted)
        ask_value = ask or "assuntos em aberto"
        ask_line = self._fit_text_line(ask_value, fonts.ui_20, rect.w - 64)
        draw.text((rect.x + 32, ask_y + 30), ask_line, font=fonts.ui_20, fill=theme.text if ask else theme.text_muted)

        interests = self._field_list(snapshot, "interests")
        chips = interests if interests else [EMPTY_CHIP]
        self._draw_chips(draw, chips, Rect(rect.x + 30, rect.y + 350, rect.w - 60, 58), theme)

    def _draw_xp_panel(self, image: Image.Image, snapshot: ProfileSnapshot, theme: ThemePreset) -> None:
        rect = self.layout.xp_panel
        draw = ImageDraw.Draw(image)
        fonts = self._fonts

        self._draw_embossed_plate(image, rect, theme, radius=28, fill=theme.enamel_dark, paper=False)
        draw.text((rect.x + 34, rect.y + 24), "Progresso de XP", font=fonts.bold_24, fill=theme.text_on_dark)

        if snapshot.level.available:
            ratio = clamp(snapshot.level.progress_ratio, 0.0, 1.0)
            percent = round(ratio * 100)
            left = f"XP total: {format_number(snapshot.level.total_xp)}"
            right = (
                f"Faltam {format_number(snapshot.level.remaining_to_next)} XP "
                f"para o proximo level - {percent}% completo"
            )
            bar_label = f"{format_number(snapshot.level.xp_into_level)} / {format_number(snapshot.level.xp_for_next_level)} XP"
        else:
            ratio = 0.0
            left = "XP indisponivel"
            reason = snapshot.level.unavailable_reason or "provedor nao conectado"
            right = self._fit_text_line(reason, fonts.ui_20, 540)
            bar_label = "sem dados de XP agora"

        draw.text((rect.x + 34, rect.y + 60), left, font=fonts.ui_20, fill=theme.text_on_dark)
        right_text = self._fit_text_line(right, fonts.ui_20, 660)
        right_width = self._text_width(right_text, fonts.ui_20)
        draw.text((rect.x + rect.w - right_width - 34, rect.y + 60), right_text, font=fonts.ui_20, fill=theme.text_on_dark)

        bar = Rect(rect.x + 34, rect.y + 96, rect.w - 68, 26)
        draw.rounded_rectangle(bar.box, radius=13, fill=theme.xp_track, outline=with_alpha(theme.metal_light, 90), width=2)
        if ratio > 0:
            fill_width = max(28, int(bar.w * ratio))
            fill_rect = Rect(bar.x, bar.y, fill_width, bar.h)
            fill = self._horizontal_gradient(fill_rect.size, theme.xp_start, theme.xp_end)
            mask = rounded_mask(fill_rect.size, 13)
            image.paste(fill, fill_rect.box[:2], mask)
            draw.rounded_rectangle(fill_rect.inset_xy(3, 3).box, radius=10, outline=with_alpha(theme.paper_highlight, 120), width=1)

        label = self._fit_text_line(bar_label, fonts.bold_20, bar.w - 20)
        label_width = self._text_width(label, fonts.bold_20)
        draw.text((bar.x + (bar.w - label_width) // 2, bar.y - 1), label, font=fonts.bold_20, fill=theme.text_on_dark)

    def _draw_charm(self, image: Image.Image, snapshot: ProfileSnapshot, theme: ThemePreset) -> None:
        rendered = snapshot.rendered_fields()
        charm = str(rendered.get("charm_preset") or "none")
        if charm == "none":
            return

        rect = self.layout.charm_area
        draw = ImageDraw.Draw(image)
        self._draw_embossed_plate(image, rect, theme, radius=22, fill=theme.paper, paper=True)
        draw.text((rect.x + 20, rect.y + 14), "Charm", font=self._fonts.bold_20, fill=theme.text_muted)

        if charm == "vinyl":
            center = (rect.x + rect.w - 58, rect.y + 70)
            for radius, color in ((44, theme.metal_dark), (32, theme.enamel), (14, theme.accent), (5, theme.paper_highlight)):
                draw.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), fill=color)
            for radius in (22, 36):
                draw.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), outline=with_alpha(theme.paper_highlight, 110), width=1)
            return

        if charm == "laurels":
            stem = (rect.x + 130, rect.y + 88)
            for side in (-1, 1):
                for index in range(6):
                    cx = stem[0] + side * (18 + index * 12)
                    cy = stem[1] - index * 9
                    draw.ellipse((cx - 8, cy - 5, cx + 8, cy + 5), fill=theme.accent)
            draw.arc((stem[0] - 84, stem[1] - 86, stem[0] + 84, stem[1] + 26), 28, 152, fill=theme.line, width=2)
            return

        if charm == "glitch":
            rng = random.Random(f"{theme.key}:glitch")
            for index in range(12):
                x = rect.x + 22 + rng.randrange(0, rect.w - 60)
                y = rect.y + 48 + rng.randrange(0, 55)
                color = theme.accent if index % 2 else theme.accent_2
                draw.rectangle((x, y, x + rng.randrange(20, 58), y + rng.randrange(3, 9)), fill=color)
            return

        if charm == "runes":
            for index, glyph in enumerate(("I", "V", "X", "II", "IV")):
                x = rect.x + 84 + index * 28
                y = rect.y + 58 + (index % 2) * 20
                draw.text((x, y), glyph, font=self._fonts.display_42, fill=theme.accent)
            return

        for index in range(5):
            cx = rect.x + 78 + index * 36
            cy = rect.y + 72 + (index % 2) * 14
            self._draw_star(draw, (cx, cy), 15 if index != 2 else 22, theme.accent)

    def _draw_panel_title(self, draw: ImageDraw.ImageDraw, rect: Rect, title: str, theme: ThemePreset) -> None:
        draw.rounded_rectangle((rect.x + 20, rect.y + 20, rect.x + rect.w - 20, rect.y + 56), radius=18, fill=theme.enamel)
        draw.line((rect.x + 34, rect.y + 60, rect.x + rect.w - 34, rect.y + 60), fill=with_alpha(theme.line, 165), width=2)
        draw.text((rect.x + 34, rect.y + 25), title, font=self._fonts.bold_24, fill=theme.text_on_dark)

    def _draw_embossed_plate(
        self,
        image: Image.Image,
        rect: Rect,
        theme: ThemePreset,
        *,
        radius: int,
        fill: Color,
        paper: bool,
    ) -> None:
        self._draw_shadow(image, rect, radius=radius, blur=self.layout.shadow_blur, offset=(0, 10), opacity=95)
        plate = Image.new("RGBA", rect.size, with_alpha(fill, 255))
        if paper:
            plate = Image.alpha_composite(plate, self._paper_texture(rect.size, theme))
        else:
            gloss = self._vertical_gradient(rect.size, (255, 255, 255), (0, 0, 0), top_alpha=42, bottom_alpha=38)
            plate = Image.alpha_composite(plate, gloss)
        mask = rounded_mask(rect.size, radius)
        image.paste(plate, rect.box[:2], mask)

        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(rect.box, radius=radius, outline=with_alpha(theme.paper_highlight, 150), width=2)
        dark_box = (rect.x + 2, rect.y + 2, rect.x + rect.w - 2, rect.y + rect.h - 2)
        draw.rounded_rectangle(dark_box, radius=max(1, radius - 2), outline=with_alpha(theme.paper_shadow, 135), width=2)

    def _draw_pill(
        self,
        draw: ImageDraw.ImageDraw,
        rect: Rect,
        text: str,
        font: ImageFont.ImageFont,
        theme: ThemePreset,
        *,
        dark: bool,
    ) -> None:
        fill = theme.enamel_dark if dark else theme.chip_fill
        text_color = theme.text_on_dark if dark else theme.chip_text
        draw.rounded_rectangle(rect.box, radius=rect.h // 2, fill=fill, outline=with_alpha(theme.metal_light, 120), width=1)
        draw.rounded_rectangle(rect.inset_xy(4, 4).box, radius=max(1, rect.h // 2 - 4), outline=with_alpha(theme.paper_highlight, 70), width=1)
        fitted = self._fit_text_line(text, font, rect.w - 24)
        text_width = self._text_width(fitted, font)
        draw.text((rect.x + (rect.w - text_width) // 2, rect.y + 7), fitted, font=font, fill=text_color)

    def _draw_chips(self, draw: ImageDraw.ImageDraw, chips: list[Any], rect: Rect, theme: ThemePreset) -> None:
        x = rect.x
        y = rect.y
        row_height = 36
        max_y = rect.y + rect.h
        for index, raw_chip in enumerate(chips[:12]):
            chip = self._fit_text_line(str(raw_chip), self._fonts.ui_18, 150)
            width = min(176, self._text_width(chip, self._fonts.ui_18) + 30)
            if x + width > rect.x + rect.w:
                x = rect.x
                y += row_height + self.layout.chip_gap
            if y + row_height > max_y:
                leftover = len(chips) - index
                if leftover > 0:
                    more = f"+{leftover}"
                    more_width = self._text_width(more, self._fonts.bold_20) + 28
                    draw.rounded_rectangle((x, y, x + more_width, y + row_height), radius=18, fill=theme.chip_fill)
                    draw.text((x + 14, y + 6), more, font=self._fonts.bold_20, fill=theme.chip_text)
                return
            draw.rounded_rectangle((x, y, x + width, y + row_height), radius=18, fill=theme.chip_fill, outline=with_alpha(theme.paper_highlight, 80), width=1)
            draw.line((x + 8, y + 5, x + width - 8, y + 5), fill=with_alpha(theme.paper_highlight, 55), width=1)
            draw.text((x + 15, y + 7), chip, font=self._fonts.ui_18, fill=theme.chip_text)
            x += width + self.layout.chip_gap

    def _draw_crest(self, draw: ImageDraw.ImageDraw, rect: Rect, badge_color: Color, theme: ThemePreset) -> None:
        points = [
            (rect.x + rect.w // 2, rect.y),
            (rect.x + rect.w, rect.y + 22),
            (rect.x + rect.w - 10, rect.y + rect.h - 25),
            (rect.x + rect.w // 2, rect.y + rect.h),
            (rect.x + 10, rect.y + rect.h - 25),
            (rect.x, rect.y + 22),
        ]
        draw.polygon(points, fill=badge_color, outline=theme.metal_dark)
        inner = [(x * 0.82 + rect.center[0] * 0.18, y * 0.82 + rect.center[1] * 0.18) for x, y in points]
        draw.polygon(inner, outline=with_alpha(theme.paper_highlight, 150))
        self._draw_star(draw, rect.center, 24, theme.paper_highlight)

    def _draw_fasteners(self, draw: ImageDraw.ImageDraw, frame: Rect, theme: ThemePreset) -> None:
        positions = (
            (frame.x + 34, frame.y + 34),
            (frame.x + frame.w - 34, frame.y + 34),
            (frame.x + 34, frame.y + frame.h - 34),
            (frame.x + frame.w - 34, frame.y + frame.h - 34),
        )
        for x, y in positions:
            draw.ellipse((x - 11, y - 11, x + 11, y + 11), fill=theme.metal_dark, outline=theme.metal_light, width=2)
            draw.line((x - 6, y, x + 6, y), fill=with_alpha(theme.metal_light, 160), width=2)

    def _draw_edge_highlights(self, image: Image.Image, theme: ThemePreset) -> None:
        width, height = self.layout.canvas
        draw = ImageDraw.Draw(image)
        draw.line((72, 76, width - 72, 76), fill=with_alpha(theme.paper_highlight, 90), width=2)
        draw.line((72, height - 78, width - 72, height - 78), fill=with_alpha(theme.metal_dark, 85), width=2)

    def _draw_lines(
        self,
        draw: ImageDraw.ImageDraw,
        lines: list[str],
        x: int,
        y: int,
        font: ImageFont.ImageFont,
        fill: Color | ColorA,
        gap: int,
    ) -> int:
        current_y = y
        line_height = self._line_height(font)
        for line in lines:
            draw.text((x, current_y), line, font=font, fill=fill)
            current_y += line_height + gap
        return current_y

    def _draw_shadow(
        self,
        image: Image.Image,
        rect: Rect,
        *,
        radius: int,
        blur: int,
        offset: tuple[int, int],
        opacity: int,
    ) -> None:
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        mask = Image.new("L", rect.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, rect.w, rect.h), radius=radius, fill=opacity)
        shadow.paste((0, 0, 0, opacity), (rect.x + offset[0], rect.y + offset[1]), mask)
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(blur)))

    def _draw_star(self, draw: ImageDraw.ImageDraw, center: tuple[int, int], radius: int, fill: Color | ColorA) -> None:
        cx, cy = center
        points: list[tuple[float, float]] = []
        for index in range(10):
            angle = -math.pi / 2 + index * math.pi / 5
            current_radius = radius if index % 2 == 0 else radius * 0.42
            points.append((cx + math.cos(angle) * current_radius, cy + math.sin(angle) * current_radius))
        draw.polygon(points, fill=fill)

    def _prepare_avatar(
        self,
        snapshot: ProfileSnapshot,
        avatar_image: Image.Image | None,
        size: int,
        theme: ThemePreset,
    ) -> Image.Image:
        if avatar_image is None:
            return self._avatar_placeholder(snapshot.live.display_name, size, theme)

        avatar = ImageOps.fit(avatar_image.convert("RGBA"), (size, size), method=Image.Resampling.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
        avatar.putalpha(mask)
        return avatar

    def _avatar_placeholder(self, display_name: str, size: int, theme: ThemePreset) -> Image.Image:
        avatar = self._vertical_gradient((size, size), theme.enamel, theme.enamel_dark)
        draw = ImageDraw.Draw(avatar)
        initials = "".join(part[0] for part in display_name.split()[:2]).upper() or "?"
        initials = initials[:2]
        font = self._font("Montserrat-Black.ttf", 72)
        text_width = self._text_width(initials, font)
        bbox = draw.textbbox((0, 0), initials, font=font)
        draw.text(
            ((size - text_width) / 2, (size - (bbox[3] - bbox[1])) / 2 - 8),
            initials,
            font=font,
            fill=theme.text_on_dark,
        )
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
        avatar.putalpha(mask)
        return avatar

    async def _fetch_avatar(self, avatar_url: str | None) -> Image.Image | None:
        if not avatar_url:
            return None
        timeout = aiohttp.ClientTimeout(total=AVATAR_FETCH_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(avatar_url) as response:
                    if response.status >= 400:
                        return None
                    payload = await response.content.read(MAX_AVATAR_BYTES + 1)
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            LOGGER.debug("profile_avatar_fetch_failed url=%s error=%s", avatar_url, exc)
            return None

        if len(payload) > MAX_AVATAR_BYTES:
            LOGGER.debug("profile_avatar_too_large url=%s", avatar_url)
            return None
        try:
            return Image.open(io.BytesIO(payload)).convert("RGBA")
        except OSError:
            return None

    def _wrap_text_pixels(
        self,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
        *,
        max_lines: int,
    ) -> list[str]:
        cleaned = " ".join(str(text).replace("\r", "\n").split())
        if not cleaned:
            return []

        lines: list[str] = []
        current = ""
        for word in cleaned.split(" "):
            candidate = f"{current} {word}".strip()
            if self._text_width(candidate, font) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            if self._text_width(word, font) > max_width:
                lines.append(self._fit_text_line(word, font, max_width))
                current = ""
            else:
                current = word
            if len(lines) >= max_lines:
                break

        if current and len(lines) < max_lines:
            lines.append(current)

        if len(lines) > max_lines:
            lines = lines[:max_lines]
        if lines and len(lines) == max_lines:
            consumed_text = " ".join(lines)
            if len(consumed_text) < len(cleaned):
                lines[-1] = self._fit_text_line(f"{lines[-1]} {ELLIPSIS}", font, max_width)
        return lines

    def _fit_text_line(self, value: str, font: ImageFont.ImageFont, max_width: int) -> str:
        text = str(value).strip()
        if self._text_width(text, font) <= max_width:
            return text

        ellipsis_width = self._text_width(ELLIPSIS, font)
        if ellipsis_width >= max_width:
            return ELLIPSIS

        low = 0
        high = len(text)
        while low < high:
            mid = (low + high + 1) // 2
            candidate = text[:mid].rstrip()
            if self._text_width(candidate, font) + ellipsis_width <= max_width:
                low = mid
            else:
                high = mid - 1
        return text[:low].rstrip() + ELLIPSIS

    def _text_width(self, value: str, font: ImageFont.ImageFont) -> int:
        if not value:
            return 0
        bbox = font.getbbox(value)
        return int(math.ceil(bbox[2] - bbox[0]))

    def _line_height(self, font: ImageFont.ImageFont) -> int:
        bbox = font.getbbox("Ag")
        return int(math.ceil(bbox[3] - bbox[1]))

    def _theme_for(self, snapshot: ProfileSnapshot) -> ThemePreset:
        rendered = snapshot.rendered_fields()
        key = str(rendered.get("theme_preset") or "classic")
        theme = THEMES.get(key, THEMES["classic"])
        palette = rendered.get("accent_palette")
        if not isinstance(palette, list) or not palette:
            return theme

        colors = [color for color in (self._parse_hex_color(value) for value in palette[:3]) if color is not None]
        if not colors:
            return theme
        accent = colors[0]
        accent_2 = colors[1] if len(colors) > 1 else theme.accent_2
        enamel = mix(colors[2], theme.enamel_dark, 0.2) if len(colors) > 2 else mix(accent, theme.enamel_dark, 0.35)
        return replace(
            theme,
            accent=accent,
            accent_2=accent_2,
            enamel=enamel,
            chip_fill=mix(enamel, theme.metal_dark, 0.22),
            xp_start=accent,
            xp_end=accent_2,
        )

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
        theme_name = THEMES.get(str(theme), THEMES["classic"]).name
        return f"Tema: {theme_name}\nPaleta: {palette or 'padrao'}\nCharm: {charm}"

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

    def _parse_hex_color(self, value: object) -> Color | None:
        raw = str(value).strip().lstrip("#")
        if len(raw) != 6:
            return None
        try:
            return tuple(int(raw[index : index + 2], 16) for index in (0, 2, 4))
        except ValueError:
            return None

    def _field_as_text(self, value: Any) -> str:
        if value in (None, "", (), []):
            return ""
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    def _field_text(self, snapshot: ProfileSnapshot, field_key: str) -> str:
        field = snapshot.fields.get(field_key)
        if field is not None and field.status is ProfileFieldStatus.REMOVED_BY_MOD:
            return REMOVED_CONTENT
        if field is None:
            return ""
        return self._field_as_text(field.value)

    def _field_list(self, snapshot: ProfileSnapshot, field_key: str) -> list[str]:
        field = snapshot.fields.get(field_key)
        if field is not None and field.status is ProfileFieldStatus.REMOVED_BY_MOD:
            return [REMOVED_CONTENT]
        if field is None or not isinstance(field.value, list):
            return []
        return [str(item) for item in field.value]

    def _cache_key(self, snapshot: ProfileSnapshot) -> ProfileRenderCacheKey:
        level = snapshot.level
        live_signature = (
            snapshot.live.display_name,
            snapshot.live.username,
            snapshot.live.avatar_url,
            level.available,
            level.total_xp,
            level.level,
            level.xp_into_level,
            level.xp_for_next_level,
            level.remaining_to_next,
            round(level.progress_ratio, 4),
            level.badge_role_id,
            level.badge_role_name,
            level.badge_role_color,
        )
        return ProfileRenderCacheKey(
            guild_id=snapshot.live.guild_id,
            user_id=snapshot.live.user_id,
            revision=snapshot.profile.render_revision,
            theme_key=self._theme_for(snapshot).key,
            live_signature=live_signature,
        )

    def _render_lock_for(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return self._render_locks.setdefault((guild_id, user_id), asyncio.Lock())

    def _get_cached_render(self, key: ProfileRenderCacheKey) -> bytes | None:
        entry = self._render_cache.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry.created_at_monotonic > RENDER_CACHE_TTL_SECONDS:
            self._render_cache.pop(key, None)
            return None
        self._render_cache.move_to_end(key)
        return entry.payload

    def _store_render(self, key: ProfileRenderCacheKey, payload: bytes) -> None:
        self._render_cache[key] = ProfileRenderCacheEntry(
            payload=payload,
            created_at_monotonic=time.monotonic(),
        )
        self._render_cache.move_to_end(key)
        while len(self._render_cache) > RENDER_CACHE_MAX_ITEMS:
            self._render_cache.popitem(last=False)

    def _load_fonts(self) -> FontSet:
        return FontSet(
            display_72=self._font("Montserrat-Black.ttf", 72),
            display_52=self._font("Montserrat-Black.ttf", 52),
            display_42=self._font("Montserrat-Black.ttf", 42),
            ui_34=self._font("Poppins-Regular.ttf", 34),
            ui_28=self._font("Poppins-Regular.ttf", 28),
            ui_24=self._font("Poppins-Regular.ttf", 24),
            ui_22=self._font("Poppins-Regular.ttf", 22),
            ui_20=self._font("Poppins-Regular.ttf", 20),
            ui_18=self._font("Poppins-Regular.ttf", 18),
            ui_16=self._font("Poppins-Regular.ttf", 16),
            bold_28=self._font("Poppins-Bold.ttf", 28),
            bold_24=self._font("Poppins-Bold.ttf", 24),
            bold_20=self._font("Poppins-Bold.ttf", 20),
        )

    def _font(self, filename: str, size: int) -> ImageFont.ImageFont:
        path = FONT_DIR / filename
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            LOGGER.warning("profile_font_missing path=%s", path)
            return ImageFont.load_default()

    def _validate_assets(self) -> None:
        missing = [str(FONT_DIR / filename) for filename in REQUIRED_FONT_FILES if not (FONT_DIR / filename).exists()]
        if missing:
            LOGGER.warning(
                "profile_assets_missing guild_id=0 user_id=0 revision=startup missing_fonts=%s fallback=pil_default",
                ",".join(missing),
            )

    def _vertical_gradient(
        self,
        size: tuple[int, int],
        top: Color,
        bottom: Color,
        *,
        top_alpha: int = 255,
        bottom_alpha: int = 255,
    ) -> Image.Image:
        width, height = size
        image = Image.new("RGBA", size)
        draw = ImageDraw.Draw(image)
        for y in range(height):
            ratio = y / max(1, height - 1)
            color = mix(top, bottom, ratio)
            alpha = int(top_alpha + (bottom_alpha - top_alpha) * ratio)
            draw.line((0, y, width, y), fill=with_alpha(color, alpha))
        return image

    def _horizontal_gradient(self, size: tuple[int, int], left: Color, right: Color) -> Image.Image:
        width, height = size
        image = Image.new("RGBA", size)
        draw = ImageDraw.Draw(image)
        for x in range(width):
            ratio = x / max(1, width - 1)
            draw.line((x, 0, x, height), fill=with_alpha(mix(left, right, ratio), 255))
        return image

    def _paper_texture(self, size: tuple[int, int], theme: ThemePreset) -> Image.Image:
        overlay = self._noise_overlay(size, f"{theme.key}:paper", theme.texture_strength, 34)
        fibers = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(fibers)
        rng = random.Random(f"{theme.key}:fibers:{size}")
        for _index in range(max(20, size[1] // 3)):
            y = rng.randrange(0, max(1, size[1]))
            alpha = rng.randrange(10, 34)
            draw.line((0, y, size[0], y + rng.randrange(-2, 3)), fill=with_alpha(theme.paper_shadow, alpha), width=1)
        return Image.alpha_composite(overlay, fibers)

    def _noise_overlay(self, size: tuple[int, int], seed: str, strength: int, alpha: int) -> Image.Image:
        small_w, small_h = self.layout.template_noise_size
        rng = random.Random(seed)
        values = [128 + rng.randrange(-strength, strength + 1) for _ in range(small_w * small_h)]
        noise = Image.new("L", (small_w, small_h))
        noise.putdata(values)
        noise = noise.resize(size, Image.Resampling.BICUBIC)
        bright = Image.new("RGBA", size, (255, 255, 255, 0))
        dark = Image.new("RGBA", size, (0, 0, 0, 0))
        bright.putalpha(noise.point(lambda value: max(0, value - 128) * alpha // max(1, strength)))
        dark.putalpha(noise.point(lambda value: max(0, 128 - value) * alpha // max(1, strength)))
        return Image.alpha_composite(dark, bright)

    def _vignette(self, size: tuple[int, int], opacity: int) -> Image.Image:
        width, height = size
        small_size = (max(1, width // 6), max(1, height // 6))
        small_width, small_height = small_size
        mask = Image.new("L", small_size, 0)
        pixels: list[int] = []
        cx, cy = small_width / 2, small_height / 2
        max_dist = math.sqrt(cx * cx + cy * cy)
        for y in range(small_height):
            for x in range(small_width):
                dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
                pixels.append(int(clamp((dist / max_dist - 0.35) / 0.65, 0, 1) * opacity))
        mask.putdata(pixels)
        mask = mask.resize(size, Image.Resampling.BICUBIC)
        vignette = Image.new("RGBA", size, (0, 0, 0, 0))
        vignette.putalpha(mask)
        return vignette

    def _brushed_metal_layer(self, size: tuple[int, int], theme: ThemePreset) -> Image.Image:
        width, height = size
        layer = self._vertical_gradient(size, theme.metal_light, theme.metal_dark)
        draw = ImageDraw.Draw(layer)
        rng = random.Random(f"{theme.key}:metal")
        for x in range(0, width, 3):
            alpha = rng.randrange(12, 38)
            color = theme.metal_light if x % 2 else theme.metal_dark
            draw.line((x, 0, x, height), fill=with_alpha(color, alpha), width=1)
        for y in range(0, height, 10):
            draw.line((0, y, width, y), fill=with_alpha(theme.paper_highlight, 10), width=1)
        return layer.filter(ImageFilter.GaussianBlur(0.45))


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    return mask


def with_alpha(color: Color, alpha: int) -> ColorA:
    return (color[0], color[1], color[2], alpha)


def mix(left: Color, right: Color, ratio: float) -> Color:
    ratio = clamp(ratio, 0.0, 1.0)
    return (
        int(left[0] + (right[0] - left[0]) * ratio),
        int(left[1] + (right[1] - left[1]) * ratio),
        int(left[2] + (right[2] - left[2]) * ratio),
    )


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def int_to_rgb(value: int) -> Color:
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def format_number(value: int) -> str:
    return f"{value:,}".replace(",", ".")
