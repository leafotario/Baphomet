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
    background_top: ColorA = (27, 28, 30, 255)
    background_bottom: ColorA = (44, 44, 47, 255)
    smoke: ColorA = (118, 108, 118, 20)
    outer_top: ColorA = (92, 92, 94, 250)
    outer_bottom: ColorA = (64, 65, 68, 252)
    inner_top: ColorA = (84, 85, 88, 245)
    inner_bottom: ColorA = (61, 62, 65, 247)
    panel_top: ColorA = (92, 93, 96, 238)
    panel_bottom: ColorA = (68, 69, 72, 242)
    panel_outline: ColorA = (154, 151, 144, 78)
    panel_highlight: ColorA = (238, 235, 226, 20)
    panel_shadow: ColorA = (0, 0, 0, 112)
    text: ColorA = (237, 234, 225, 255)
    text_soft: ColorA = (204, 201, 193, 255)
    text_muted: ColorA = (142, 141, 137, 255)
    text_dark: ColorA = (18, 18, 20, 255)
    accent_dark: ColorA = (24, 12, 17, 255)
    accent: ColorA = (58, 24, 35, 255)
    accent_light: ColorA = (119, 77, 90, 255)
    silver_dark: ColorA = (66, 67, 70, 255)
    silver_mid: ColorA = (137, 137, 134, 255)
    silver_light: ColorA = (218, 216, 208, 255)
    gold: ColorA = (172, 164, 142, 255)
    chip_fill: ColorA = (34, 35, 38, 238)
    chip_outline: ColorA = (164, 160, 151, 108)
    chip_highlight: ColorA = (236, 231, 220, 32)
    xp_track: ColorA = (13, 14, 16, 236)
    xp_start: ColorA = (78, 35, 47, 255)
    xp_end: ColorA = (194, 191, 181, 255)
    recessed: ColorA = (16, 17, 19, 174)


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
    inner_radius: int = 34
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
        add_noise_overlay(canvas, opacity=9, seed=404, scale=4)

        atmosphere = Image.new("RGBA", self.layout.canvas, (0, 0, 0, 0))
        atmosphere_draw = ImageDraw.Draw(atmosphere)
        rng = random.Random(667)
        for _ in range(8):
            cx = rng.randint(-160, width + 160)
            cy = rng.randint(-120, height + 120)
            rx = rng.randint(180, 420)
            ry = rng.randint(120, 280)
            color = (
                self.theme.smoke[0],
                self.theme.smoke[1],
                self.theme.smoke[2],
                rng.randint(6, self.theme.smoke[3]),
            )
            atmosphere_draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=color)
        atmosphere = atmosphere.filter(ImageFilter.GaussianBlur(58))
        canvas.alpha_composite(atmosphere)

        vignette = Image.new("RGBA", self.layout.canvas, (0, 0, 0, 0))
        vignette_draw = ImageDraw.Draw(vignette, "RGBA")
        vignette_draw.rectangle((0, 0, width, height), fill=(0, 0, 0, 54))
        vignette_draw.ellipse((-140, -120, width + 140, height + 160), fill=(0, 0, 0, 0))
        vignette = vignette.filter(ImageFilter.GaussianBlur(70))
        canvas.alpha_composite(vignette)
        return canvas

    def _apply_finishing_patina(self, canvas: Image.Image) -> None:
        add_noise_overlay(canvas, opacity=3, seed=909, scale=2)
        draw = ImageDraw.Draw(canvas, "RGBA")
        frame = self.layout.outer_card
        draw.rounded_rectangle(frame.box, radius=self.layout.outer_radius, outline=(255, 255, 255, 10), width=1)
        draw.rounded_rectangle(frame.inset(16).box, radius=max(1, self.layout.outer_radius - 16), outline=(0, 0, 0, 54), width=1)

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
        add_noise_overlay(outer, opacity=4, seed=8, scale=3)
        paste_rounded(canvas, outer, layout.outer_card, layout.outer_radius)

        inner = vertical_gradient(layout.inner_card.size, theme.inner_top, theme.inner_bottom)
        add_noise_overlay(inner, opacity=4, seed=11, scale=3)
        paste_rounded(canvas, inner, layout.inner_card, layout.inner_radius)

        draw = ImageDraw.Draw(canvas, "RGBA")
        draw.rounded_rectangle(layout.outer_card.box, radius=layout.outer_radius, outline=(136, 134, 128, 62), width=1)
        draw.rounded_rectangle(
            layout.outer_card.inset(18).box,
            radius=max(1, layout.outer_radius - 18),
            outline=(246, 242, 231, 12),
            width=1,
        )
        draw.rounded_rectangle(layout.inner_card.box, radius=layout.inner_radius, outline=(179, 177, 168, 48), width=1)
        draw.rounded_rectangle(layout.inner_card.inset(14).box, radius=22, outline=(0, 0, 0, 58), width=1)
        draw.line((layout.inner_card.x + 96, layout.inner_card.y + 1, layout.inner_card.right - 96, layout.inner_card.y + 1), fill=(255, 252, 241, 14), width=1)
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
        color = (229, 225, 214, 16)
        inset = 34
        length = 64
        for x0, x1 in ((rect.x + inset, rect.x + inset + length), (rect.right - inset, rect.right - inset - length)):
            draw.line((x0, rect.y + inset, x1, rect.y + inset), fill=color, width=1)
            draw.line((x0, rect.bottom - inset, x1, rect.bottom - inset), fill=color, width=1)
        draw.line((rect.x + 150, rect.bottom - 58, rect.right - 150, rect.bottom - 58), fill=(255, 255, 255, 5), width=1)

    def _draw_watermark(self, canvas: Image.Image) -> None:
        rect = self.layout.inner_card
        layer = Image.new("RGBA", self.layout.canvas, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")
        cx, cy = rect.center
        draw.ellipse((cx - 246, cy - 246, cx + 246, cy + 246), outline=(230, 226, 216, 4), width=2)
        draw.ellipse((cx - 178, cy - 178, cx + 178, cy + 178), outline=(230, 226, 216, 4), width=1)
        word = "BAPHOMET"
        font = self.fonts.font(88, "display")
        word_w = text_width(draw, word, font)
        draw.text((cx - word_w // 2, cy - 44), word, font=font, fill=(230, 226, 216, 4))
        canvas.alpha_composite(layer)

    def _draw_panel(self, canvas: Image.Image, rect: Rect, *, radius: int | None = None) -> None:
        theme = self.theme
        radius = radius or self.layout.panel_radius
        draw_soft_shadow(canvas, rect, radius, offset=(0, 12), blur=20, color=theme.panel_shadow)
        panel = vertical_gradient(rect.size, theme.panel_top, theme.panel_bottom)
        add_noise_overlay(panel, opacity=3, seed=rect.x + rect.y, scale=3)
        paste_rounded(canvas, panel, rect, radius)
        draw = ImageDraw.Draw(canvas, "RGBA")
        draw.rounded_rectangle(rect.box, radius=radius, outline=theme.panel_outline, width=1)
        draw.rounded_rectangle(rect.inset(8).box, radius=max(2, radius - 8), outline=(255, 255, 255, 7), width=1)
        draw.line((rect.x + 30, rect.y + 3, rect.right - 30, rect.y + 3), fill=theme.panel_highlight, width=1)
        draw.line((rect.x + 30, rect.bottom - 2, rect.right - 30, rect.bottom - 2), fill=(0, 0, 0, 42), width=1)

    def _draw_section_title(self, draw: ImageDraw.ImageDraw, rect: Rect, title: str) -> None:
        font = self.fonts.font(28, "bold")
        x = rect.x + self.layout.section_pad
        y = rect.y + 23
        draw_text_shadow(
            draw,
            (x, y),
            title,
            font=font,
            fill=self.theme.text,
            shadow=(0, 0, 0, 105),
            offset=(0, 1),
        )
        title_w = text_width(draw, title, font)
        rule_x = x + title_w + 18
        if rule_x < rect.right - 30:
            line_y = y + 18
            draw.line((rule_x, line_y, rect.right - 30, line_y), fill=(208, 204, 194, 34), width=1)
            draw.line((rule_x, line_y + 5, min(rect.right - 30, rule_x + 64), line_y + 5), fill=(88, 48, 60, 76), width=1)

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

        plate = Rect(slot.x + 17, slot.y + 17, slot.w - 34, slot.h - 34)
        draw_soft_shadow(canvas, plate, plate.w // 2, offset=(0, 16), blur=24, color=(0, 0, 0, 152), spread=1)
        halo = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        halo_draw = ImageDraw.Draw(halo, "RGBA")
        halo_draw.ellipse(plate.inset(-10).box, fill=(88, 72, 80, 22))
        halo = halo.filter(ImageFilter.GaussianBlur(16))
        canvas.alpha_composite(halo)
        draw = ImageDraw.Draw(canvas, "RGBA")
        draw.ellipse(plate.box, fill=(25, 26, 29, 248), outline=(176, 172, 162, 120), width=2)
        draw.ellipse(plate.inset(10).box, outline=(255, 251, 240, 26), width=1)
        draw.ellipse(plate.inset(19).box, outline=(0, 0, 0, 132), width=2)

        avatar_size = 236
        avatar_rect = Rect(slot.x + (slot.w - avatar_size) // 2, slot.y + (slot.h - avatar_size) // 2, avatar_size, avatar_size)
        source = load_rgba_from_bytes(profile.avatar_bytes)
        if source is None:
            avatar = create_avatar_placeholder(
                avatar_size,
                initials=self._initials(profile.display_name or profile.username),
                font=self.fonts.font(78, "bold"),
                fill_top=(50, 47, 50, 255),
                fill_bottom=(18, 18, 21, 255),
                accent=(126, 85, 95, 178),
                text_fill=theme.text,
            )
        else:
            avatar = circular_crop(source, avatar_size)

        canvas.paste(avatar, (avatar_rect.x, avatar_rect.y), avatar)
        draw.ellipse(avatar_rect.box, outline=(0, 0, 0, 170), width=4)
        draw.ellipse(avatar_rect.inset(5).box, outline=(241, 237, 226, 52), width=1)
        draw.arc(avatar_rect.inset(11).box, 202, 332, fill=(236, 231, 220, 42), width=2)

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

        label_font = self.fonts.font(14, "bold")
        x = rect.x + 28
        y = rect.y + 30
        value_width = rect.w - 56
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
            weight = "bold" if label == "Nome" else "regular"
            start_size = 25 if label == "Nome" else 22
            value_font = fit_font_to_width(
                draw,
                value,
                value_width,
                font_loader=lambda size, weight=weight: self.fonts.font(size, weight),
                start_size=start_size,
                min_size=15,
            )
            display_value = truncate_text(draw, value, value_font, value_width)
            value_y = y + 26
            draw_text_shadow(
                draw,
                (x, value_y),
                display_value,
                font=value_font,
                fill=self.theme.text if label == "Nome" else self.theme.text_soft,
                shadow=(0, 0, 0, 90),
                offset=(0, 1),
            )
            divider_y = value_y + text_height(draw, display_value or "Ag", value_font) + 18
            if label != "Rank":
                draw.line((x, divider_y, x + value_width, divider_y), fill=(255, 255, 255, 13), width=1)
            y += 88

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
        font = self.fonts.font(22, "regular")
        line_gap = 7
        line_height = text_height(draw, "Ag", font) + line_gap
        max_lines = max(1, body_rect.h // max(1, line_height))
        text_rect = body_rect.inset(18, 16)
        lines = wrap_text_pixels(draw, text, font, text_rect.w)
        lines = clamp_lines_with_ellipsis(draw, lines, font, text_rect.w, max_lines)

        y = text_rect.y
        for line in lines:
            if y + line_height > text_rect.bottom + 2:
                break
            draw_text_shadow(
                draw,
                (text_rect.x, y),
                line,
                font=font,
                fill=self.theme.text_soft,
                shadow=(0, 0, 0, 82),
                offset=(0, 1),
            )
            y += line_height

    def _draw_badge(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.badge_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")
        self._draw_section_title(draw, rect, "Insígnia")

        slot = Rect(rect.x + 54, rect.y + 82, rect.w - 108, 122)
        draw_soft_shadow(canvas, slot, 32, offset=(0, 8), blur=16, color=(0, 0, 0, 112))
        draw.rounded_rectangle(slot.box, radius=32, fill=(20, 21, 24, 220), outline=(159, 155, 146, 82), width=1)
        draw.ellipse(
            (slot.x + 42, slot.y + 17, slot.right - 42, slot.bottom - 17),
            fill=(118, 106, 112, 24),
            outline=(229, 224, 213, 30),
            width=1,
        )
        source = load_rgba_from_bytes(profile.badge_image_bytes)
        image_slot = slot.inset(22, 12)
        if source is None:
            badge = create_badge_placeholder(
                (150, 118),
                fill_top=(48, 46, 49, 235),
                fill_bottom=(18, 18, 21, 245),
                accent=self.theme.accent_light,
                line=self.theme.silver_light,
            )
            paste_centered(canvas, badge, image_slot)
        else:
            paste_centered(canvas, source, image_slot)

        label = self._field(profile.badge_name, "Sem insígnia")
        label_font = self.fonts.font(21, "bold")
        label_rect = Rect(rect.x + 28, rect.y + 224, rect.w - 56, 40)
        label = truncate_text(draw, label, label_font, label_rect.w - 20)
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
            rect.w - 62,
            font_loader=lambda size: self.fonts.font(size, "bold"),
            start_size=30,
            min_size=20,
        )
        count_text = truncate_text(draw, count_text, count_font, rect.w - 62)
        draw_text_shadow(
            draw,
            (rect.x + 30, rect.y + 94),
            count_text,
            font=count_font,
            fill=self.theme.text,
            shadow=(0, 0, 0, 110),
            offset=(0, 1),
        )
        draw.line((rect.x + 30, rect.y + 143, rect.right - 30, rect.y + 143), fill=(255, 255, 255, 16), width=1)

        mult = self._format_multiplier(profile.bonds_multiplier)
        badge_rect = Rect(rect.x + 30, rect.y + 156, rect.w - 60, 36)
        draw_soft_shadow(canvas, badge_rect, 18, offset=(0, 5), blur=10, color=(0, 0, 0, 72))
        draw.rounded_rectangle(badge_rect.box, radius=18, fill=(45, 27, 34, 226), outline=(184, 176, 160, 78), width=1)
        draw.line((badge_rect.x + 18, badge_rect.y + 5, badge_rect.right - 18, badge_rect.y + 5), fill=(255, 255, 255, 28), width=1)
        mult_font = fit_font_to_width(
            draw,
            mult,
            badge_rect.w - 18,
            font_loader=lambda size: self.fonts.font(size, "bold"),
            start_size=21,
            min_size=16,
        )
        self._draw_centered_text(draw, badge_rect, mult, mult_font, self.theme.text, shadow=(0, 0, 0, 84))

    def _draw_xp_progress(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.xp_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")
        self._draw_section_title(draw, rect, "Progresso de XP")

        level = max(0, int(profile.level))
        level_text = f"Nível {level}"
        level_font = self.fonts.font(22, "bold")
        level_width = min(150, max(92, text_width(draw, level_text, level_font) + 30))
        level_rect = Rect(rect.right - 30 - level_width, rect.y + 28, level_width, 34)
        draw.rounded_rectangle(level_rect.box, radius=17, fill=(22, 23, 26, 166), outline=(153, 149, 140, 72), width=1)
        level_text = truncate_text(draw, level_text, level_font, level_rect.w - 18)
        self._draw_centered_text(draw, level_rect, level_text, level_font, self.theme.text_soft)

        current = max(0, int(profile.xp_current))
        required = max(0, int(profile.xp_required))
        total = max(0, int(profile.xp_total))
        percent = self._normalize_percent(profile.xp_percent)
        bar_rect = Rect(rect.x + 30, rect.y + 86, rect.w - 60, 36)
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

        meta_font = self.fonts.font(22, "regular")
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
        draw.rounded_rectangle(rect.box, radius=rect.h // 2, fill=self.theme.recessed, outline=(126, 124, 118, 70), width=1)
        draw.line((rect.x + 14, rect.y + 4, rect.right - 14, rect.y + 4), fill=(255, 255, 255, 14), width=1)

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
        draw_soft_shadow(canvas, rect, rect.h // 2, offset=(0, 4), blur=8, color=(0, 0, 0, 68))
        draw = ImageDraw.Draw(canvas, "RGBA")
        fill = (45, 45, 48, 226) if muted else self.theme.chip_fill
        draw.rounded_rectangle(rect.box, radius=rect.h // 2, fill=fill, outline=self.theme.chip_outline, width=1)
        dot = Rect(rect.x + 12, rect.y + rect.h // 2 - 3, 6, 6)
        draw.ellipse(dot.box, fill=(112, 67, 80, 190))
        draw.line((rect.x + 24, rect.y + 5, rect.right - 14, rect.y + 5), fill=self.theme.chip_highlight, width=1)
        label_width = text_width(draw, label, font)
        label_height = text_height(draw, label, font)
        draw_text_shadow(
            draw,
            (rect.x + (rect.w - label_width) // 2 + 5, rect.y + (rect.h - label_height) // 2 - 2),
            label,
            font=font,
            fill=self.theme.text,
            shadow=(0, 0, 0, 92),
            offset=(0, 1),
        )

    def _draw_ruled_area(self, draw: ImageDraw.ImageDraw, rect: Rect) -> None:
        draw.rounded_rectangle(rect.box, radius=24, fill=(13, 14, 16, 58), outline=(255, 255, 255, 13), width=1)
        draw.line((rect.x + 22, rect.y + 5, rect.right - 22, rect.y + 5), fill=(255, 255, 255, 11), width=1)

    def _draw_xp_document_bar(self, canvas: Image.Image, rect: Rect, ratio: float) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        radius = rect.h // 2
        draw_soft_shadow(canvas, rect, radius, offset=(0, 5), blur=10, color=(0, 0, 0, 78))
        draw = ImageDraw.Draw(canvas, "RGBA")
        draw.rounded_rectangle(rect.box, radius=radius, fill=self.theme.xp_track, outline=(134, 130, 122, 112), width=1)

        ratio = clamp(ratio, 0.0, 1.0)
        fill_width = int(rect.w * ratio)
        if fill_width > 0:
            fill_rect = Rect(rect.x, rect.y, min(fill_width, rect.w), rect.h)
            gradient = self._horizontal_gradient(fill_rect.size, self.theme.xp_start, self.theme.xp_end)
            mask = Image.new("L", fill_rect.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, fill_rect.w, fill_rect.h), radius=radius, fill=255)
            canvas.paste(gradient, (fill_rect.x, fill_rect.y), mask)
            draw = ImageDraw.Draw(canvas, "RGBA")
            if fill_rect.w > 18:
                draw.line((fill_rect.x + 12, fill_rect.y + 7, fill_rect.right - 12, fill_rect.y + 7), fill=(255, 255, 255, 44), width=1)

        draw.rounded_rectangle(rect.box, radius=radius, outline=(220, 216, 206, 66), width=1)

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
