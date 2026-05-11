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
    draw_soft_shadow,
    draw_text_shadow,
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
    background_top: ColorA = (13, 14, 16, 255)
    background_bottom: ColorA = (27, 28, 31, 255)
    smoke: ColorA = (92, 96, 102, 24)
    outer_top: ColorA = (94, 96, 98, 255)
    outer_bottom: ColorA = (54, 56, 60, 255)
    inner_top: ColorA = (110, 113, 115, 255)
    inner_bottom: ColorA = (74, 76, 80, 255)
    panel_top: ColorA = (90, 93, 96, 244)
    panel_bottom: ColorA = (62, 65, 69, 248)
    panel_outline: ColorA = (165, 170, 171, 174)
    panel_highlight: ColorA = (214, 218, 216, 42)
    panel_shadow: ColorA = (0, 0, 0, 118)
    text: ColorA = (232, 234, 229, 255)
    text_soft: ColorA = (198, 202, 199, 255)
    text_muted: ColorA = (135, 140, 142, 255)
    text_dark: ColorA = (19, 20, 22, 255)
    accent_dark: ColorA = (22, 12, 17, 255)
    accent: ColorA = (58, 27, 39, 255)
    accent_light: ColorA = (122, 87, 100, 255)
    silver_dark: ColorA = (78, 81, 84, 255)
    silver_mid: ColorA = (141, 146, 148, 255)
    silver_light: ColorA = (215, 218, 213, 255)
    gold: ColorA = (157, 160, 154, 255)
    chip_fill: ColorA = (48, 51, 54, 246)
    chip_outline: ColorA = (155, 160, 161, 168)
    chip_highlight: ColorA = (222, 225, 220, 58)
    xp_track: ColorA = (13, 14, 16, 235)
    xp_start: ColorA = (118, 123, 124, 255)
    xp_end: ColorA = (218, 221, 216, 255)
    recessed: ColorA = (16, 17, 19, 214)


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
    outer_radius: int = 52
    inner_radius: int = 28
    panel_radius: int = 18
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
        add_noise_overlay(canvas, opacity=16, seed=404, scale=4)

        smoke = Image.new("RGBA", self.layout.canvas, (0, 0, 0, 0))
        smoke_draw = ImageDraw.Draw(smoke)
        rng = random.Random(667)
        for _ in range(12):
            cx = rng.randint(-120, width + 120)
            cy = rng.randint(-80, height + 80)
            rx = rng.randint(130, 360)
            ry = rng.randint(80, 230)
            color = (
                self.theme.smoke[0],
                self.theme.smoke[1],
                self.theme.smoke[2],
                rng.randint(6, self.theme.smoke[3]),
            )
            smoke_draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=color)
        smoke = smoke.filter(ImageFilter.GaussianBlur(46))
        canvas.alpha_composite(smoke)

        draw = ImageDraw.Draw(canvas, "RGBA")
        for x in range(0, width, 120):
            draw.line((x, 0, x, height), fill=(255, 255, 255, 1), width=1)
        for y in range(48, height, 96):
            draw.line((0, y, width, y), fill=(255, 255, 255, 1), width=1)
        return canvas

    def _apply_finishing_patina(self, canvas: Image.Image) -> None:
        add_noise_overlay(canvas, opacity=5, seed=909, scale=2)
        draw = ImageDraw.Draw(canvas, "RGBA")
        frame = self.layout.outer_card
        draw.rounded_rectangle(frame.box, radius=self.layout.outer_radius, outline=(255, 255, 255, 16), width=1)
        draw.rounded_rectangle(frame.inset(12).box, radius=max(1, self.layout.outer_radius - 12), outline=(0, 0, 0, 70), width=1)

    def _draw_main_frame(self, canvas: Image.Image) -> None:
        layout = self.layout
        theme = self.theme

        draw_soft_shadow(
            canvas,
            layout.outer_card,
            layout.outer_radius,
            offset=(0, 20),
            blur=34,
            color=(0, 0, 0, 170),
            spread=3,
        )
        outer = vertical_gradient(layout.outer_card.size, theme.outer_top, theme.outer_bottom)
        add_noise_overlay(outer, opacity=8, seed=8, scale=3)
        outer_draw = ImageDraw.Draw(outer, "RGBA")
        self._draw_layer_grid(outer_draw, Rect(0, 0, *layout.outer_card.size), step=80, alpha=1)
        paste_rounded(canvas, outer, layout.outer_card, layout.outer_radius)

        inner = vertical_gradient(layout.inner_card.size, theme.inner_top, theme.inner_bottom)
        add_noise_overlay(inner, opacity=7, seed=11, scale=3)
        inner_draw = ImageDraw.Draw(inner, "RGBA")
        self._draw_layer_grid(inner_draw, Rect(0, 0, *layout.inner_card.size), step=52, alpha=0)
        paste_rounded(canvas, inner, layout.inner_card, layout.inner_radius)

        draw = ImageDraw.Draw(canvas, "RGBA")
        draw.rounded_rectangle(layout.outer_card.box, radius=layout.outer_radius, outline=(139, 144, 145, 135), width=2)
        draw.rounded_rectangle(
            layout.outer_card.inset(18).box,
            radius=max(1, layout.outer_radius - 18),
            outline=(255, 255, 255, 18),
            width=1,
        )
        draw.rounded_rectangle(layout.inner_card.box, radius=layout.inner_radius, outline=(173, 178, 176, 96), width=1)
        draw.rounded_rectangle(layout.inner_card.inset(12).box, radius=16, outline=(0, 0, 0, 90), width=1)
        self._draw_watermark(canvas)
        self._draw_document_marks(canvas)

    def _draw_layer_grid(self, draw: ImageDraw.ImageDraw, rect: Rect, *, step: int, alpha: int) -> None:
        if alpha <= 0:
            return
        for x in range(rect.x + step, rect.right, step):
            draw.line((x, rect.y, x, rect.bottom), fill=(255, 255, 255, alpha), width=1)
        for y in range(rect.y + step, rect.bottom, step):
            draw.line((rect.x, y, rect.right, y), fill=(0, 0, 0, alpha + 2), width=1)

    def _draw_document_marks(self, canvas: Image.Image) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        rect = self.layout.inner_card
        color = (211, 215, 211, 104)
        length = 54
        gap = 22
        points = (
            (rect.x + gap, rect.y + gap, 1, 1),
            (rect.right - gap, rect.y + gap, -1, 1),
            (rect.x + gap, rect.bottom - gap, 1, -1),
            (rect.right - gap, rect.bottom - gap, -1, -1),
        )
        for x, y, sx, sy in points:
            draw.line((x, y, x + sx * length, y), fill=color, width=2)
            draw.line((x, y, x, y + sy * length), fill=color, width=2)
            draw.line((x + sx * 12, y + sy * 12, x + sx * 34, y + sy * 12), fill=(0, 0, 0, 96), width=1)

        for y in (rect.y + 74, rect.bottom - 74):
            draw.line((rect.x + 96, y, rect.right - 96, y), fill=(255, 255, 255, 13), width=1)

    def _draw_watermark(self, canvas: Image.Image) -> None:
        rect = self.layout.inner_card
        layer = Image.new("RGBA", self.layout.canvas, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")
        cx, cy = rect.center
        for radius, alpha in ((255, 15), (190, 12), (122, 14)):
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=(230, 233, 229, alpha), width=2)
        draw.polygon(
            ((cx, cy - 244), (cx + 244, cy), (cx, cy + 244), (cx - 244, cy)),
            outline=(230, 233, 229, 12),
        )
        draw.line((cx, cy - 270, cx, cy + 270), fill=(230, 233, 229, 12), width=2)
        draw.line((cx - 270, cy, cx + 270, cy), fill=(230, 233, 229, 10), width=2)
        word = "BAPHOMET"
        font = self.fonts.font(96, "display")
        word_w = text_width(draw, word, font)
        draw.text((cx - word_w // 2, cy - 48), word, font=font, fill=(230, 233, 229, 10))
        canvas.alpha_composite(layer)

    def _draw_panel(self, canvas: Image.Image, rect: Rect, *, radius: int | None = None) -> None:
        theme = self.theme
        radius = radius or self.layout.panel_radius
        draw_soft_shadow(canvas, rect, radius, offset=(0, 9), blur=16, color=theme.panel_shadow)
        panel = vertical_gradient(rect.size, theme.panel_top, theme.panel_bottom)
        add_noise_overlay(panel, opacity=5, seed=rect.x + rect.y, scale=3)
        paste_rounded(canvas, panel, rect, radius)
        draw = ImageDraw.Draw(canvas, "RGBA")
        draw.rounded_rectangle(rect.box, radius=radius, outline=theme.panel_outline, width=1)
        draw.rounded_rectangle(rect.inset(7).box, radius=max(2, radius - 7), outline=(255, 255, 255, 17), width=1)
        draw.line((rect.x + 18, rect.y + 2, rect.right - 18, rect.y + 2), fill=theme.panel_highlight, width=1)
        draw.line((rect.x + 18, rect.bottom - 2, rect.right - 18, rect.bottom - 2), fill=(0, 0, 0, 88), width=1)
        self._draw_corner_ticks(draw, rect, radius)

    def _draw_section_title(self, draw: ImageDraw.ImageDraw, rect: Rect, title: str) -> None:
        font = self.fonts.font(29, "display")
        x = rect.x + self.layout.section_pad
        y = rect.y + 24
        draw_text_shadow(
            draw,
            (x, y),
            title,
            font=font,
            fill=self.theme.text,
            shadow=(0, 0, 0, 125),
            offset=(0, 1),
        )
        title_w = text_width(draw, title, font)
        rule_x = x + title_w + 18
        if rule_x < rect.right - 30:
            line_y = y + 19
            draw.line((rule_x, line_y, rect.right - 30, line_y), fill=(208, 212, 209, 76), width=1)
            draw.line((rule_x, line_y + 5, min(rect.right - 30, rule_x + 72), line_y + 5), fill=(208, 212, 209, 35), width=1)

    def _draw_corner_ticks(self, draw: ImageDraw.ImageDraw, rect: Rect, radius: int) -> None:
        color = (214, 218, 216, 82)
        length = 18
        inset = max(10, radius // 2)
        draw.line((rect.x + inset, rect.y + 10, rect.x + inset + length, rect.y + 10), fill=color, width=1)
        draw.line((rect.x + 10, rect.y + inset, rect.x + 10, rect.y + inset + length), fill=color, width=1)
        draw.line((rect.right - inset, rect.y + 10, rect.right - inset - length, rect.y + 10), fill=color, width=1)
        draw.line((rect.right - 10, rect.y + inset, rect.right - 10, rect.y + inset + length), fill=color, width=1)
        draw.line((rect.x + inset, rect.bottom - 10, rect.x + inset + length, rect.bottom - 10), fill=color, width=1)
        draw.line((rect.x + 10, rect.bottom - inset, rect.x + 10, rect.bottom - inset - length), fill=color, width=1)
        draw.line((rect.right - inset, rect.bottom - 10, rect.right - inset - length, rect.bottom - 10), fill=color, width=1)
        draw.line((rect.right - 10, rect.bottom - inset, rect.right - 10, rect.bottom - inset - length), fill=color, width=1)

    def _draw_avatar(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        theme = self.theme
        slot = self.layout.avatar_medallion
        draw = ImageDraw.Draw(canvas, "RGBA")

        photo_frame = Rect(slot.x + 8, slot.y + 8, slot.w - 16, slot.h - 16)
        draw_soft_shadow(canvas, photo_frame, 34, offset=(0, 12), blur=20, color=(0, 0, 0, 142), spread=1)
        draw.rounded_rectangle(photo_frame.box, radius=34, fill=(17, 18, 20, 246), outline=(145, 150, 151, 120), width=1)
        draw.rounded_rectangle(photo_frame.inset(10).box, radius=24, outline=(255, 255, 255, 22), width=1)
        self._draw_corner_ticks(draw, photo_frame, 24)

        plate = Rect(slot.x + 30, slot.y + 30, slot.w - 60, slot.h - 60)
        draw.ellipse(plate.box, fill=(43, 45, 48, 255), outline=(168, 172, 170, 122), width=2)
        draw.ellipse(plate.inset(10).box, outline=(0, 0, 0, 118), width=2)
        draw.arc(plate.inset(18).box, 202, 332, fill=(223, 226, 222, 70), width=3)

        avatar_size = 226
        avatar_rect = Rect(slot.x + (slot.w - avatar_size) // 2, slot.y + (slot.h - avatar_size) // 2, avatar_size, avatar_size)
        source = load_rgba_from_bytes(profile.avatar_bytes)
        if source is None:
            avatar = create_avatar_placeholder(
                avatar_size,
                initials=self._initials(profile.display_name or profile.username),
                font=self.fonts.font(76, "display"),
                fill_top=(54, 57, 60, 255),
                fill_bottom=(18, 19, 21, 255),
                accent=(148, 153, 154, 178),
                text_fill=theme.text,
            )
        else:
            avatar = circular_crop(source, avatar_size)

        canvas.paste(avatar, (avatar_rect.x, avatar_rect.y), avatar)
        draw.ellipse(avatar_rect.box, outline=(0, 0, 0, 175), width=4)
        draw.ellipse(avatar_rect.inset(5).box, outline=(240, 243, 238, 54), width=1)
        for y in range(avatar_rect.y + 16, avatar_rect.bottom - 16, 14):
            draw.line((avatar_rect.x + 34, y, avatar_rect.right - 34, y), fill=(255, 255, 255, 2), width=1)

    def _draw_identity(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.identity_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")

        name = self._field(profile.display_name, profile.username or "Usuário")
        fields = (
            ("Nome", name),
            ("Pronomes", self._field(profile.pronouns)),
            ("ID de usuário", str(profile.user_id)),
            ("Rank", self._field(profile.rank_text, "Sem rank")),
        )

        label_font = self.fonts.font(15, "bold")
        x = rect.x + 24
        y = rect.y + 26
        value_width = rect.w - 48
        for label, value in fields:
            label_text = label.upper()
            draw_text_shadow(
                draw,
                (x, y),
                label_text,
                font=label_font,
                fill=self.theme.text_muted,
                shadow=(0, 0, 0, 90),
                offset=(0, 1),
            )
            value_rect = Rect(x - 2, y + 27, value_width + 4, 42)
            self._draw_recessed_slot(canvas, value_rect)
            weight = "bold" if label == "Nome" else "regular"
            start_size = 23 if label == "Nome" else 21
            value_font = fit_font_to_width(
                draw,
                value,
                value_width - 20,
                font_loader=lambda size, weight=weight: self.fonts.font(size, weight),
                start_size=start_size,
                min_size=15,
            )
            display_value = truncate_text(draw, value, value_font, value_width - 20)
            value_y = value_rect.y + (value_rect.h - text_height(draw, display_value or "Ag", value_font)) // 2 - 2
            draw_text_shadow(
                draw,
                (value_rect.x + 12, value_y),
                display_value,
                font=value_font,
                fill=self.theme.text if label == "Nome" else self.theme.text_soft,
                shadow=(0, 0, 0, 95),
                offset=(0, 1),
            )
            draw.line((x, value_rect.bottom + 10, x + value_width, value_rect.bottom + 10), fill=(255, 255, 255, 12), width=1)
            y += 92

    def _draw_ask_me_about(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.ask_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")
        self._draw_section_title(draw, rect, "Me pergunte sobre")

        topics = self._clean_topics(profile.ask_me_about)
        chips_rect = Rect(rect.x + 28, rect.y + 82, rect.w - 56, rect.h - 108)
        self._render_chips(canvas, chips_rect, topics)

    def _draw_basic_info(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.basic_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")
        self._draw_section_title(draw, rect, "Informações básicas")

        text = self._field(profile.basic_info, "Não informado.")
        body_rect = Rect(rect.x + 30, rect.y + 82, rect.w - 60, rect.h - 104)
        self._draw_ruled_area(draw, body_rect)
        font = self.fonts.font(23, "regular")
        line_gap = 5
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
                shadow=(0, 0, 0, 95),
                offset=(0, 1),
            )
            y += line_height

    def _draw_badge(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.badge_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")
        self._draw_section_title(draw, rect, "Insígnia")

        slot = Rect(rect.x + 50, rect.y + 76, rect.w - 100, 134)
        draw_soft_shadow(canvas, slot, 18, offset=(0, 6), blur=12, color=(0, 0, 0, 96))
        draw.rounded_rectangle(slot.box, radius=18, fill=(30, 32, 34, 238), outline=(137, 142, 143, 118), width=1)
        draw.ellipse(
            (slot.x + 37, slot.y + 12, slot.right - 37, slot.bottom - 12),
            fill=(154, 158, 158, 82),
            outline=(224, 227, 222, 58),
            width=1,
        )
        draw.polygon(
            (
                (slot.x + slot.w // 2, slot.y + 10),
                (slot.right - 24, slot.y + slot.h // 2),
                (slot.x + slot.w // 2, slot.bottom - 10),
                (slot.x + 24, slot.y + slot.h // 2),
            ),
            outline=(225, 228, 224, 38),
        )
        source = load_rgba_from_bytes(profile.badge_image_bytes)
        image_slot = slot.inset(18, 10)
        if source is None:
            badge = create_badge_placeholder(
                (150, 118),
                fill_top=(64, 67, 69, 235),
                fill_bottom=(21, 22, 24, 245),
                accent=self.theme.silver_mid,
                line=self.theme.silver_light,
            )
            paste_centered(canvas, badge, image_slot)
        else:
            paste_centered(canvas, source, image_slot)

        label = self._field(profile.badge_name, "Sem insígnia")
        label_font = self.fonts.font(21, "regular")
        label_rect = Rect(rect.x + 28, rect.y + 226, rect.w - 56, 38)
        self._draw_recessed_slot(canvas, label_rect)
        label = truncate_text(draw, label, label_font, label_rect.w - 24)
        self._draw_centered_text(draw, label_rect, label, label_font, self.theme.text_soft)

    def _draw_bonds(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.bonds_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")
        self._draw_section_title(draw, rect, "Vínculos")

        count = max(0, int(profile.bonds_count))
        count_text = f"{count} vínculo" if count == 1 else f"{count} vínculos"
        count_font = fit_font_to_width(
            draw,
            count_text,
            156,
            font_loader=lambda size: self.fonts.font(size, "bold"),
            start_size=30,
            min_size=20,
        )
        count_text = truncate_text(draw, count_text, count_font, 156)
        draw_text_shadow(
            draw,
            (rect.x + 30, rect.y + 100),
            count_text,
            font=count_font,
            fill=self.theme.text,
            shadow=(0, 0, 0, 110),
            offset=(0, 1),
        )
        draw.line((rect.x + 30, rect.y + 148, rect.right - 30, rect.y + 148), fill=(255, 255, 255, 18), width=1)
        draw.line((rect.x + 194, rect.y + 82, rect.x + 194, rect.y + 138), fill=(255, 255, 255, 24), width=1)

        mult = self._format_multiplier(profile.bonds_multiplier)
        badge_rect = Rect(rect.right - 112, rect.y + 92, 78, 42)
        draw_soft_shadow(canvas, badge_rect, 12, offset=(0, 5), blur=9, color=(0, 0, 0, 98))
        draw.rounded_rectangle(badge_rect.box, radius=12, fill=(158, 163, 163, 235), outline=(236, 239, 233, 70), width=1)
        draw.line((badge_rect.x + 11, badge_rect.y + 5, badge_rect.right - 11, badge_rect.y + 5), fill=(255, 255, 255, 54), width=1)
        mult_font = fit_font_to_width(
            draw,
            mult,
            badge_rect.w - 18,
            font_loader=lambda size: self.fonts.font(size, "bold"),
            start_size=22,
            min_size=16,
        )
        self._draw_centered_text(draw, badge_rect, mult, mult_font, self.theme.text_dark, shadow=(255, 255, 255, 36))

    def _draw_xp_progress(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.xp_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")
        self._draw_section_title(draw, rect, "Progresso de XP")

        level = max(0, int(profile.level))
        level_text = f"Nível {level}"
        level_font = self.fonts.font(22, "bold")
        level_rect = Rect(rect.right - 162, rect.y + 29, 132, 34)
        draw.rounded_rectangle(level_rect.box, radius=8, fill=(31, 33, 35, 236), outline=(126, 131, 132, 120), width=1)
        level_text = truncate_text(draw, level_text, level_font, level_rect.w - 18)
        self._draw_centered_text(draw, level_rect, level_text, level_font, self.theme.text_soft)

        current = max(0, int(profile.xp_current))
        required = max(0, int(profile.xp_required))
        total = max(0, int(profile.xp_total))
        percent = self._normalize_percent(profile.xp_percent)
        bar_rect = Rect(rect.x + 30, rect.y + 84, rect.w - 60, 38)
        self._draw_xp_document_bar(canvas, bar_rect, percent / 100)

        xp_label = f"{current:,} / {required:,} XP".replace(",", ".")
        xp_font = self.fonts.font(23, "bold")
        xp_label = truncate_text(draw, xp_label, xp_font, bar_rect.w - 36)
        self._draw_centered_text(
            draw,
            bar_rect,
            xp_label,
            xp_font,
            self.theme.text,
            shadow=(0, 0, 0, 190),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 155),
        )

        meta_font = self.fonts.font(23, "regular")
        total_text = f"XP Total: {total:,}".replace(",", ".")
        percent_text = f"{self._format_percent(percent)} completo"
        draw_text_shadow(
            draw,
            (rect.x + 34, rect.y + 146),
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
            (rect.right - 34 - percent_w, rect.y + 146),
            percent_text,
            font=meta_font,
            fill=self.theme.text_soft,
            shadow=(0, 0, 0, 100),
            offset=(0, 1),
        )

    def _draw_recessed_slot(self, canvas: Image.Image, rect: Rect) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        draw.rounded_rectangle(rect.box, radius=8, fill=self.theme.recessed, outline=(101, 106, 108, 122), width=1)
        draw.line((rect.x + 12, rect.y + 4, rect.right - 12, rect.y + 4), fill=(255, 255, 255, 18), width=1)
        draw.line((rect.x + 12, rect.bottom - 2, rect.right - 12, rect.bottom - 2), fill=(0, 0, 0, 92), width=1)

    def _render_chips(self, canvas: Image.Image, rect: Rect, labels: list[str]) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        font = self.fonts.font(20, "bold")
        x = rect.x
        y = rect.y
        gap = 10
        chip_height = 38
        max_y = rect.bottom - chip_height

        index = 0
        while index < len(labels) and y <= max_y:
            raw_label = labels[index]
            max_label_width = min(272, rect.w - 32)
            label = truncate_text(draw, raw_label, font, max_label_width)
            chip_width = max(68, text_width(draw, label, font) + 32)

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
                more_width = max(56, text_width(draw, more_label, font) + 26)
                if x + chip_width + gap + more_width > rect.right:
                    self._draw_more_chip(canvas, Rect(x, y, rect.right - x, chip_height), len(labels) - index, font)
                    return

            self._draw_topic_chip(canvas, Rect(x, y, chip_width, chip_height), label, font)
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
        draw = ImageDraw.Draw(canvas, "RGBA")
        label = f"+{hidden}"
        chip_width = max(56, text_width(draw, label, font) + 26)
        if chip_width > slot.w:
            return
        self._draw_topic_chip(canvas, Rect(slot.x, slot.y, chip_width, slot.h), label, font, muted=True)

    def _draw_topic_chip(
        self,
        canvas: Image.Image,
        rect: Rect,
        label: str,
        font: ImageFont.ImageFont,
        *,
        muted: bool = False,
    ) -> None:
        draw_soft_shadow(canvas, rect, 10, offset=(0, 4), blur=8, color=(0, 0, 0, 74))
        draw = ImageDraw.Draw(canvas, "RGBA")
        fill = (48, 50, 53, 244) if muted else self.theme.chip_fill
        draw.rounded_rectangle(rect.box, radius=10, fill=fill, outline=self.theme.chip_outline, width=1)
        draw.line((rect.x + 10, rect.y + 5, rect.x + 10, rect.bottom - 5), fill=self.theme.chip_highlight, width=2)
        draw.line((rect.x + 16, rect.y + 5, rect.right - 12, rect.y + 5), fill=(255, 255, 255, 19), width=1)
        label_width = text_width(draw, label, font)
        label_height = text_height(draw, label, font)
        draw_text_shadow(
            draw,
            (rect.x + (rect.w - label_width) // 2 + 3, rect.y + (rect.h - label_height) // 2 - 2),
            label,
            font=font,
            fill=self.theme.text,
            shadow=(0, 0, 0, 92),
            offset=(0, 1),
        )

    def _draw_ruled_area(self, draw: ImageDraw.ImageDraw, rect: Rect) -> None:
        draw.rounded_rectangle(rect.box, radius=10, fill=(15, 16, 18, 38), outline=(255, 255, 255, 14), width=1)
        for y in range(rect.y + 31, rect.bottom, 31):
            draw.line((rect.x + 14, y, rect.right - 14, y), fill=(255, 255, 255, 8), width=1)
        for x in range(rect.x + 48, rect.right, 48):
            draw.line((x, rect.y + 12, x, rect.bottom - 12), fill=(255, 255, 255, 1), width=1)

    def _draw_xp_document_bar(self, canvas: Image.Image, rect: Rect, ratio: float) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        radius = 9
        draw.rounded_rectangle(rect.box, radius=radius, fill=self.theme.xp_track, outline=(134, 139, 140, 142), width=1)
        for index in range(1, 10):
            x = rect.x + rect.w * index // 10
            draw.line((x, rect.y + 7, x, rect.bottom - 7), fill=(255, 255, 255, 19), width=1)

        ratio = clamp(ratio, 0.0, 1.0)
        fill_width = int(rect.w * ratio)
        if fill_width > 0:
            fill_rect = Rect(rect.x, rect.y, min(fill_width, rect.w), rect.h)
            gradient = self._horizontal_gradient(fill_rect.size, self.theme.xp_start, self.theme.xp_end)
            mask = Image.new("L", fill_rect.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, fill_rect.w, fill_rect.h), radius=radius, fill=255)
            canvas.paste(gradient, (fill_rect.x, fill_rect.y), mask)
            draw = ImageDraw.Draw(canvas, "RGBA")
            draw.line((fill_rect.x + 8, fill_rect.y + 5, fill_rect.right - 8, fill_rect.y + 5), fill=(255, 255, 255, 56), width=1)

        draw.rounded_rectangle(rect.box, radius=radius, outline=(220, 224, 220, 74), width=1)

    def _horizontal_gradient(self, size: tuple[int, int], left: ColorA, right: ColorA) -> Image.Image:
        width, height = size
        gradient = Image.new("RGBA", size, left)
        draw = ImageDraw.Draw(gradient)
        denominator = max(1, width - 1)
        for x in range(width):
            ratio = x / denominator
            color = tuple(int(left[i] + (right[i] - left[i]) * ratio) for i in range(4))
            draw.line((x, 0, x, height), fill=color)
        return gradient

    def _draw_centered_text(
        self,
        draw: ImageDraw.ImageDraw,
        rect: Rect,
        text: str,
        font: ImageFont.ImageFont,
        fill: ColorA,
        *,
        shadow: ColorA = (0, 0, 0, 100),
        stroke_width: int = 0,
        stroke_fill: ColorA = (0, 0, 0, 0),
    ) -> None:
        text_w = text_width(draw, text, font)
        text_h = text_height(draw, text or "Ag", font)
        draw_text_shadow(
            draw,
            (rect.x + (rect.w - text_w) // 2, rect.y + (rect.h - text_h) // 2 - 2),
            text,
            font=font,
            fill=fill,
            shadow=shadow,
            offset=(0, 1),
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
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
