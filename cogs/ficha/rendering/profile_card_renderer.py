from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .drawing import (
    ColorA,
    Rect,
    add_noise_overlay,
    circular_crop,
    clamp,
    clamp_lines_with_ellipsis,
    create_avatar_placeholder,
    create_badge_placeholder,
    draw_bevel_border,
    draw_chip,
    draw_inner_highlight,
    draw_soft_shadow,
    draw_text_shadow,
    draw_xp_bar,
    fit_font_to_width,
    load_rgba_from_bytes,
    paste_centered,
    paste_rounded,
    text_height,
    text_width,
    truncate_text,
    vertical_gradient,
    wrap_text_pixels,
)
from .fonts import FontManager
from .profile_card_types import ProfileRenderData


REMOVED_CONTENT = "[Conteúdo removido]"


@dataclass(frozen=True, slots=True)
class ProfileCardTheme:
    background_top: ColorA = (10, 10, 12, 255)
    background_bottom: ColorA = (31, 23, 28, 255)
    smoke: ColorA = (78, 68, 74, 42)
    outer_top: ColorA = (98, 95, 93, 255)
    outer_bottom: ColorA = (48, 45, 46, 255)
    inner_top: ColorA = (66, 63, 63, 255)
    inner_bottom: ColorA = (38, 36, 38, 255)
    panel_top: ColorA = (88, 82, 82, 246)
    panel_bottom: ColorA = (52, 49, 50, 248)
    panel_outline: ColorA = (119, 108, 94, 210)
    panel_highlight: ColorA = (232, 221, 196, 62)
    panel_shadow: ColorA = (0, 0, 0, 125)
    text: ColorA = (239, 229, 207, 255)
    text_soft: ColorA = (199, 187, 164, 255)
    text_muted: ColorA = (157, 145, 126, 255)
    text_dark: ColorA = (30, 26, 24, 255)
    accent_dark: ColorA = (74, 14, 29, 255)
    accent: ColorA = (130, 29, 48, 255)
    accent_light: ColorA = (191, 139, 102, 255)
    silver_dark: ColorA = (76, 73, 72, 255)
    silver_mid: ColorA = (151, 145, 135, 255)
    silver_light: ColorA = (219, 211, 192, 255)
    gold: ColorA = (167, 137, 78, 255)
    chip_fill: ColorA = (85, 28, 40, 248)
    chip_outline: ColorA = (175, 140, 90, 160)
    chip_highlight: ColorA = (255, 230, 180, 70)
    xp_track: ColorA = (23, 22, 24, 225)
    xp_start: ColorA = (96, 17, 36, 255)
    xp_end: ColorA = (202, 154, 84, 255)
    recessed: ColorA = (24, 22, 23, 210)


@dataclass(frozen=True, slots=True)
class ProfileCardLayout:
    canvas: tuple[int, int] = (1500, 1000)
    outer_card: Rect = Rect(74, 58, 1352, 884)
    inner_card: Rect = Rect(116, 96, 1268, 808)
    avatar_medallion: Rect = Rect(156, 122, 308, 308)
    identity_panel: Rect = Rect(156, 448, 308, 416)
    ask_panel: Rect = Rect(488, 122, 512, 220)
    basic_panel: Rect = Rect(488, 366, 512, 282)
    badge_panel: Rect = Rect(1024, 122, 306, 290)
    bonds_panel: Rect = Rect(1024, 438, 306, 210)
    xp_panel: Rect = Rect(488, 674, 842, 190)
    outer_radius: int = 94
    inner_radius: int = 58
    panel_radius: int = 28
    section_pad: int = 28


class ProfileCardRenderer:
    """Renderer Pillow puro para gerar a ficha PNG em 3:2."""

    def __init__(
        self,
        *,
        layout: ProfileCardLayout | None = None,
        theme: ProfileCardTheme | None = None,
        fonts: FontManager | None = None,
    ) -> None:
        self.layout = layout or ProfileCardLayout()
        self.theme = theme or ProfileCardTheme()
        self.fonts = fonts or FontManager()

    def render(self, profile: ProfileRenderData) -> bytes:
        """Renderiza a ficha completa e retorna bytes PNG prontos para Discord."""

        canvas = self._create_background()
        self._draw_main_frame(canvas)
        self._draw_avatar(canvas, profile)
        self._draw_identity(canvas, profile)
        self._draw_ask_me_about(canvas, profile)
        self._draw_basic_info(canvas, profile)
        self._draw_badge(canvas, profile)
        self._draw_bonds(canvas, profile)
        self._draw_xp_progress(canvas, profile)
        self._apply_finishing_patina(canvas)

        output = io.BytesIO()
        canvas.convert("RGBA").save(output, format="PNG")
        return output.getvalue()

    def _create_background(self) -> Image.Image:
        width, height = self.layout.canvas
        canvas = vertical_gradient(self.layout.canvas, self.theme.background_top, self.theme.background_bottom)
        add_noise_overlay(canvas, opacity=20, seed=404, scale=4)

        smoke = Image.new("RGBA", self.layout.canvas, (0, 0, 0, 0))
        smoke_draw = ImageDraw.Draw(smoke)
        rng = random.Random(667)
        for _ in range(18):
            cx = rng.randint(-120, width + 120)
            cy = rng.randint(-80, height + 80)
            rx = rng.randint(130, 360)
            ry = rng.randint(80, 230)
            color = (
                self.theme.smoke[0],
                self.theme.smoke[1],
                self.theme.smoke[2],
                rng.randint(14, self.theme.smoke[3]),
            )
            smoke_draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=color)
        smoke = smoke.filter(ImageFilter.GaussianBlur(38))
        canvas.alpha_composite(smoke)
        return canvas

    def _apply_finishing_patina(self, canvas: Image.Image) -> None:
        patina = Image.new("RGBA", self.layout.canvas, (172, 151, 118, 24))
        canvas.alpha_composite(patina)

    def _draw_main_frame(self, canvas: Image.Image) -> None:
        layout = self.layout
        theme = self.theme

        draw_soft_shadow(
            canvas,
            layout.outer_card,
            layout.outer_radius,
            offset=(0, 28),
            blur=44,
            color=(0, 0, 0, 190),
            spread=8,
        )
        outer = vertical_gradient(layout.outer_card.size, theme.outer_top, theme.outer_bottom)
        add_noise_overlay(outer, opacity=12, seed=8, scale=3)
        paste_rounded(canvas, outer, layout.outer_card, layout.outer_radius)
        draw_bevel_border(
            canvas,
            layout.outer_card,
            layout.outer_radius,
            highlight=(255, 255, 255, 52),
            shadow=(0, 0, 0, 130),
            outline=(20, 19, 20, 210),
            width=4,
        )

        inner = vertical_gradient(layout.inner_card.size, theme.inner_top, theme.inner_bottom)
        add_noise_overlay(inner, opacity=16, seed=11, scale=3)
        paste_rounded(canvas, inner, layout.inner_card, layout.inner_radius)
        draw_bevel_border(
            canvas,
            layout.inner_card,
            layout.inner_radius,
            highlight=(255, 246, 220, 74),
            shadow=(0, 0, 0, 150),
            outline=(126, 118, 104, 150),
            width=3,
        )
        self._draw_corner_rivets(canvas)

    def _draw_corner_rivets(self, canvas: Image.Image) -> None:
        draw = ImageDraw.Draw(canvas)
        rect = self.layout.inner_card
        for x, y in (
            (rect.x + 44, rect.y + 42),
            (rect.right - 44, rect.y + 42),
            (rect.x + 44, rect.bottom - 42),
            (rect.right - 44, rect.bottom - 42),
        ):
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=(28, 25, 24, 210))
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=self.theme.silver_mid)
            draw.ellipse((x - 3, y - 4, x + 2, y + 1), fill=(235, 225, 200, 90))

    def _draw_panel(self, canvas: Image.Image, rect: Rect, *, radius: int | None = None) -> None:
        theme = self.theme
        radius = radius or self.layout.panel_radius
        draw_soft_shadow(canvas, rect, radius, offset=(0, 13), blur=22, color=theme.panel_shadow)
        panel = vertical_gradient(rect.size, theme.panel_top, theme.panel_bottom)
        add_noise_overlay(panel, opacity=10, seed=rect.x + rect.y, scale=3)
        paste_rounded(canvas, panel, rect, radius)
        draw_inner_highlight(canvas, rect, radius, theme.panel_highlight)
        draw_bevel_border(
            canvas,
            rect,
            radius,
            highlight=(255, 244, 214, 58),
            shadow=(0, 0, 0, 145),
            outline=theme.panel_outline,
            width=2,
        )

    def _draw_section_title(self, draw: ImageDraw.ImageDraw, rect: Rect, title: str) -> None:
        font = self.fonts.font(32, "display")
        draw_text_shadow(
            draw,
            (rect.x + self.layout.section_pad, rect.y + 26),
            title,
            font=font,
            fill=self.theme.text,
            shadow=(0, 0, 0, 155),
            offset=(0, 2),
        )

    def _draw_avatar(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        theme = self.theme
        slot = self.layout.avatar_medallion
        draw = ImageDraw.Draw(canvas)

        draw_soft_shadow(canvas, slot, slot.w // 2, offset=(0, 18), blur=24, color=(0, 0, 0, 160), spread=3)
        draw.ellipse(slot.box, fill=theme.silver_dark)
        for index, color in enumerate((theme.silver_mid, theme.silver_light, theme.silver_dark)):
            inset = 7 + index * 6
            draw.ellipse(
                (slot.x + inset, slot.y + inset, slot.right - inset, slot.bottom - inset),
                outline=color,
                width=7,
            )
        draw.arc((slot.x + 16, slot.y + 16, slot.right - 16, slot.bottom - 16), 205, 335, fill=(255, 246, 218, 120), width=4)

        avatar_size = 246
        avatar_rect = Rect(slot.x + (slot.w - avatar_size) // 2, slot.y + (slot.h - avatar_size) // 2, avatar_size, avatar_size)
        source = load_rgba_from_bytes(profile.avatar_bytes)
        if source is None:
            avatar = create_avatar_placeholder(
                avatar_size,
                initials=self._initials(profile.display_name or profile.username),
                font=self.fonts.font(82, "display"),
                fill_top=(59, 52, 55, 255),
                fill_bottom=(21, 19, 21, 255),
                accent=(161, 38, 59, 190),
                text_fill=theme.text,
            )
        else:
            avatar = circular_crop(source, avatar_size)

        canvas.paste(avatar, (avatar_rect.x, avatar_rect.y), avatar)
        draw.ellipse(avatar_rect.box, outline=(0, 0, 0, 155), width=5)
        draw.ellipse(
            (avatar_rect.x + 5, avatar_rect.y + 5, avatar_rect.right - 5, avatar_rect.bottom - 5),
            outline=(255, 240, 205, 45),
            width=2,
        )

    def _draw_identity(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.identity_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas)

        name = self._field(profile.display_name, profile.username or "Usuário")
        fields = (
            ("Nome", name),
            ("Pronomes", self._field(profile.pronouns)),
            ("ID de usuário", str(profile.user_id)),
            ("Rank", self._field(profile.rank_text, "Sem rank")),
        )

        label_font = self.fonts.font(26, "display")
        value_start_size = 23
        x = rect.x + 24
        y = rect.y + 24
        value_width = rect.w - 48
        for label, value in fields:
            draw_text_shadow(
                draw,
                (x, y),
                label,
                font=label_font,
                fill=self.theme.text,
                shadow=(0, 0, 0, 150),
                offset=(0, 2),
            )
            value_rect = Rect(x - 4, y + 36, value_width + 8, 48)
            self._draw_recessed_slot(canvas, value_rect)
            value_font = fit_font_to_width(
                draw,
                value,
                value_width - 22,
                font_loader=lambda size: self.fonts.font(size, "regular"),
                start_size=value_start_size,
                min_size=15,
            )
            display_value = truncate_text(draw, value, value_font, value_width - 22)
            draw_text_shadow(
                draw,
                (value_rect.x + 12, value_rect.y + 11),
                display_value,
                font=value_font,
                fill=self.theme.text_soft,
                shadow=(0, 0, 0, 95),
                offset=(0, 1),
            )
            y += 92

    def _draw_ask_me_about(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.ask_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas)
        self._draw_section_title(draw, rect, "Me pergunte sobre")

        topics = self._clean_topics(profile.ask_me_about)
        chips_rect = Rect(rect.x + 28, rect.y + 82, rect.w - 56, rect.h - 112)
        self._render_chips(canvas, chips_rect, topics)

    def _draw_basic_info(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.basic_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas)
        self._draw_section_title(draw, rect, "Informações básicas")

        text = self._field(profile.basic_info, "Não informado.")
        body_rect = Rect(rect.x + 30, rect.y + 82, rect.w - 60, rect.h - 112)
        font = self.fonts.font(26, "regular")
        line_gap = 8
        line_height = text_height(draw, "Ag", font) + line_gap
        max_lines = max(1, body_rect.h // max(1, line_height))
        lines = wrap_text_pixels(draw, text, font, body_rect.w)
        lines = clamp_lines_with_ellipsis(draw, lines, font, body_rect.w, max_lines)

        y = body_rect.y
        for line in lines:
            if y + line_height > body_rect.bottom + 2:
                break
            draw_text_shadow(
                draw,
                (body_rect.x, y),
                line,
                font=font,
                fill=self.theme.text_soft,
                shadow=(0, 0, 0, 110),
                offset=(0, 1),
            )
            y += line_height

    def _draw_badge(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.badge_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas)
        self._draw_section_title(draw, rect, "Insígnia")

        slot = Rect(rect.x + 68, rect.y + 76, 170, 132)
        draw_soft_shadow(canvas, slot, 26, offset=(0, 8), blur=14, color=(0, 0, 0, 110))
        source = load_rgba_from_bytes(profile.badge_image_bytes)
        if source is None:
            badge = create_badge_placeholder(
                (150, 118),
                fill_top=(65, 61, 61, 235),
                fill_bottom=(20, 18, 19, 245),
                accent=self.theme.accent_light,
                line=self.theme.silver_light,
            )
            paste_centered(canvas, badge, slot)
        else:
            paste_centered(canvas, source, slot)

        label = self._field(profile.badge_name, "Sem insígnia")
        label_font = self.fonts.font(24, "regular")
        label = truncate_text(draw, label, label_font, rect.w - 52)
        label_w = text_width(draw, label, label_font)
        draw_text_shadow(
            draw,
            (rect.x + (rect.w - label_w) // 2, rect.y + 226),
            label,
            font=label_font,
            fill=self.theme.text_soft,
            shadow=(0, 0, 0, 120),
            offset=(0, 1),
        )

    def _draw_bonds(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.bonds_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas)
        self._draw_section_title(draw, rect, "Vínculos")

        count = max(0, int(profile.bonds_count))
        count_text = f"{count} vínculo" if count == 1 else f"{count} vínculos"
        count_font = self.fonts.font(28, "regular")
        draw_text_shadow(
            draw,
            (rect.x + 30, rect.y + 98),
            truncate_text(draw, count_text, count_font, 150),
            font=count_font,
            fill=self.theme.text_soft,
            shadow=(0, 0, 0, 110),
            offset=(0, 1),
        )

        mult = self._format_multiplier(profile.bonds_multiplier)
        badge_rect = Rect(rect.right - 120, rect.y + 76, 76, 76)
        draw_soft_shadow(canvas, badge_rect, badge_rect.w // 2, offset=(0, 8), blur=13, color=(0, 0, 0, 125))
        draw.ellipse(badge_rect.box, fill=(113, 106, 98, 255), outline=self.theme.panel_outline, width=2)
        draw.ellipse(
            (badge_rect.x + 6, badge_rect.y + 6, badge_rect.right - 6, badge_rect.bottom - 6),
            outline=(255, 246, 220, 54),
            width=2,
        )
        mult_font = fit_font_to_width(
            draw,
            mult,
            badge_rect.w - 18,
            font_loader=lambda size: self.fonts.font(size, "bold"),
            start_size=24,
            min_size=16,
        )
        mult_w = text_width(draw, mult, mult_font)
        mult_h = text_height(draw, mult, mult_font)
        draw_text_shadow(
            draw,
            (badge_rect.x + (badge_rect.w - mult_w) // 2, badge_rect.y + (badge_rect.h - mult_h) // 2 - 2),
            mult,
            font=mult_font,
            fill=self.theme.text_dark,
            shadow=(255, 255, 255, 40),
            offset=(0, 1),
        )

    def _draw_xp_progress(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.xp_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas)
        self._draw_section_title(draw, rect, "Progresso de XP")

        level = max(0, int(profile.level))
        level_text = f"Nível {level}"
        level_font = self.fonts.font(26, "regular")
        level_text = truncate_text(draw, level_text, level_font, 170)
        level_w = text_width(draw, level_text, level_font)
        draw_text_shadow(
            draw,
            (rect.right - 28 - level_w, rect.y + 34),
            level_text,
            font=level_font,
            fill=self.theme.text_soft,
            shadow=(0, 0, 0, 100),
            offset=(0, 1),
        )

        current = max(0, int(profile.xp_current))
        required = max(0, int(profile.xp_required))
        total = max(0, int(profile.xp_total))
        percent = self._normalize_percent(profile.xp_percent)
        bar_rect = Rect(rect.x + 28, rect.y + 80, rect.w - 56, 50)
        draw_xp_bar(
            canvas,
            bar_rect,
            ratio=percent / 100,
            track_fill=self.theme.xp_track,
            fill_start=self.theme.xp_start,
            fill_end=self.theme.xp_end,
            outline=(167, 151, 119, 160),
            highlight=(255, 238, 190, 56),
        )

        xp_label = f"{current:,} / {required:,} XP".replace(",", ".")
        xp_font = self.fonts.font(25, "bold")
        xp_label = truncate_text(draw, xp_label, xp_font, bar_rect.w - 36)
        xp_w = text_width(draw, xp_label, xp_font)
        xp_h = text_height(draw, xp_label, xp_font)
        draw_text_shadow(
            draw,
            (bar_rect.x + (bar_rect.w - xp_w) // 2, bar_rect.y + (bar_rect.h - xp_h) // 2 - 2),
            xp_label,
            font=xp_font,
            fill=self.theme.text,
            shadow=(0, 0, 0, 210),
            offset=(0, 2),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 160),
        )

        meta_font = self.fonts.font(25, "regular")
        total_text = f"XP Total: {total:,}".replace(",", ".")
        percent_text = f"{self._format_percent(percent)} completo"
        draw_text_shadow(
            draw,
            (rect.x + 34, rect.y + 145),
            truncate_text(draw, total_text, meta_font, 320),
            font=meta_font,
            fill=self.theme.text_soft,
            shadow=(0, 0, 0, 100),
            offset=(0, 1),
        )
        percent_text = truncate_text(draw, percent_text, meta_font, 260)
        percent_w = text_width(draw, percent_text, meta_font)
        draw_text_shadow(
            draw,
            (rect.right - 34 - percent_w, rect.y + 145),
            percent_text,
            font=meta_font,
            fill=self.theme.text_soft,
            shadow=(0, 0, 0, 100),
            offset=(0, 1),
        )

    def _draw_recessed_slot(self, canvas: Image.Image, rect: Rect) -> None:
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(rect.box, radius=rect.h // 2, fill=self.theme.recessed, outline=(0, 0, 0, 120), width=2)
        draw.line((rect.x + 16, rect.y + 4, rect.right - 16, rect.y + 4), fill=(255, 240, 210, 34), width=1)
        draw.line((rect.x + 16, rect.bottom - 3, rect.right - 16, rect.bottom - 3), fill=(0, 0, 0, 95), width=1)

    def _render_chips(self, canvas: Image.Image, rect: Rect, labels: list[str]) -> None:
        draw = ImageDraw.Draw(canvas)
        font = self.fonts.font(22, "bold")
        x = rect.x
        y = rect.y
        gap = 12
        chip_height = 42
        max_y = rect.bottom - chip_height

        index = 0
        while index < len(labels) and y <= max_y:
            raw_label = labels[index]
            max_label_width = min(280, rect.w - 34)
            label = truncate_text(draw, raw_label, font, max_label_width)
            chip_width = max(70, text_width(draw, label, font) + 34)

            if x + chip_width > rect.right:
                if y + chip_height + gap > max_y:
                    self._draw_more_chip(canvas, Rect(x, y, rect.right - x, chip_height), len(labels) - index, font)
                    return
                x = rect.x
                y += chip_height + gap
                continue

            remaining_after = len(labels) - index - 1
            if remaining_after and y + chip_height + gap > max_y:
                more_label = f"+{remaining_after}"
                more_width = max(58, text_width(draw, more_label, font) + 28)
                if x + chip_width + gap + more_width > rect.right:
                    self._draw_more_chip(canvas, Rect(x, y, rect.right - x, chip_height), len(labels) - index, font)
                    return

            draw_chip(
                canvas,
                Rect(x, y, chip_width, chip_height),
                label=label,
                font=font,
                fill=self.theme.chip_fill,
                outline=self.theme.chip_outline,
                highlight=self.theme.chip_highlight,
                text_fill=self.theme.text,
            )
            x += chip_width + gap
            index += 1

        hidden = len(labels) - index
        if hidden > 0:
            self._draw_more_chip(canvas, Rect(x, y, rect.right - x, chip_height), hidden, font)

    def _draw_more_chip(
        self,
        canvas: Image.Image,
        slot: Rect,
        hidden: int,
        font: ImageFont.ImageFont,
    ) -> None:
        if hidden <= 0 or slot.w <= 0:
            return
        draw = ImageDraw.Draw(canvas)
        label = f"+{hidden}"
        chip_width = max(58, text_width(draw, label, font) + 28)
        if chip_width > slot.w:
            return
        draw_chip(
            canvas,
            Rect(slot.x, slot.y, chip_width, slot.h),
            label=label,
            font=font,
            fill=(94, 88, 82, 245),
            outline=self.theme.chip_outline,
            highlight=self.theme.chip_highlight,
            text_fill=self.theme.text,
        )

    def _field(self, value: str | None, fallback: str = "Não informado") -> str:
        if value == REMOVED_CONTENT:
            return value
        if value is None:
            return fallback
        cleaned = str(value).strip()
        return cleaned if cleaned else fallback

    def _clean_topics(self, topics: list[str]) -> list[str]:
        cleaned: list[str] = []
        for topic in topics:
            if topic == REMOVED_CONTENT:
                cleaned.append(topic)
                continue
            normalized = str(topic).strip()
            if normalized:
                cleaned.append(normalized)
        return cleaned or ["Não informado"]

    @staticmethod
    def _initials(name: str) -> str:
        pieces = [piece for piece in name.replace("_", " ").split(" ") if piece]
        if not pieces:
            return "?"
        if len(pieces) == 1:
            return pieces[0][:2]
        return pieces[0][:1] + pieces[1][:1]

    @staticmethod
    def _format_multiplier(value: float) -> str:
        if not math.isfinite(value):
            value = 1.0
        text = f"{max(0.0, value):.1f}".rstrip("0").rstrip(".")
        return f"{text}x"

    @staticmethod
    def _normalize_percent(value: float) -> float:
        if not math.isfinite(value):
            return 0.0
        percent = value * 100 if 0 <= value <= 1 else value
        return clamp(percent, 0.0, 100.0)

    @staticmethod
    def _format_percent(value: float) -> str:
        if abs(value - round(value)) < 0.05:
            return f"{int(round(value))}%"
        return f"{value:.1f}%"
