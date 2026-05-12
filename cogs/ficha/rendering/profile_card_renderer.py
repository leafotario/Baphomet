from __future__ import annotations

import io
import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

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
    wrap_text_pixels,
)
from .fonts import FontManager
from .profile_card_types import ProfileRenderData


REMOVED_CONTENT = "[Conteúdo removido]"


@dataclass(frozen=True, slots=True)
class ProfileCardTheme:
    background_fallback: ColorA = (42, 39, 48, 255)

    glass_overlay: ColorA = (18, 15, 24, 180)
    outer_card_fill: ColorA = (0, 0, 0, 105)
    inner_card_fill: ColorA = (0, 0, 0, 62)

    panel_fill: ColorA = (0, 0, 0, 82)
    panel_outline: ColorA = (255, 255, 255, 42)

    field_fill: ColorA = (0, 0, 0, 92)
    field_outline: ColorA = (255, 255, 255, 36)

    text: ColorA = (255, 255, 255, 255)
    text_soft: ColorA = (225, 225, 230, 255)
    text_muted: ColorA = (190, 190, 198, 255)

    rank_gold: ColorA = (255, 220, 50, 255)
    cyan: ColorA = (0, 255, 255, 255)

    bar_track: ColorA = (0, 0, 0, 135)
    shadow: ColorA = (0, 0, 0, 170)


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

    outer_radius: int = 40
    inner_radius: int = 26
    panel_radius: int = 22
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
        self._accent: tuple[int, int, int] = (120, 220, 220)

    def render(self, profile: ProfileRenderData) -> bytes:
        """Renderiza a ficha completa e retorna bytes PNG prontos para Discord."""

        self._accent = self._extract_accent(profile)

        canvas = self._create_glass_background(profile)
        self._draw_main_frame(canvas)
        self._draw_avatar(canvas, profile)
        self._draw_identity(canvas, profile)
        self._draw_ask_me_about(canvas, profile)
        self._draw_basic_info(canvas, profile)
        self._draw_badge(canvas, profile)
        self._draw_bonds(canvas, profile)
        self._draw_xp_progress(canvas, profile)
        self._apply_finishing(canvas)

        output = io.BytesIO()
        canvas.convert("RGBA").save(output, format="PNG")
        return output.getvalue()

    def _extract_accent(self, profile: ProfileRenderData) -> tuple[int, int, int]:
        source = load_rgba_from_bytes(profile.avatar_bytes)

        if source is None:
            return (120, 220, 220)

        return self._get_dominant_color(source)

    def _get_dominant_color(self, img: Image.Image) -> tuple[int, int, int]:
        img = img.convert("RGBA")
        img = img.resize((1, 1), resample=Image.Resampling.LANCZOS)
        pixel = img.getpixel((0, 0))

        if isinstance(pixel, tuple) and len(pixel) >= 3:
            r, g, b = pixel[:3]

            if r + g + b < 90:
                return (120, 220, 220)

            return (int(r), int(g), int(b))

        return (120, 220, 220)

    def _create_glass_background(self, profile: ProfileRenderData) -> Image.Image:
        width, height = self.layout.canvas

        source = load_rgba_from_bytes(profile.avatar_bytes)

        if source is None:
            canvas = Image.new("RGBA", self.layout.canvas, self.theme.background_fallback)
        else:
            canvas = ImageOps.fit(source, self.layout.canvas, method=Image.Resampling.LANCZOS)
            canvas = canvas.filter(ImageFilter.GaussianBlur(radius=45))

        overlay = Image.new("RGBA", self.layout.canvas, self.theme.glass_overlay)
        canvas = Image.alpha_composite(canvas, overlay)

        glow = Image.new("RGBA", self.layout.canvas, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow, "RGBA")

        accent = self._accent

        glow_draw.ellipse(
            (-220, -260, 650, 590),
            fill=(*accent, 34),
        )
        glow_draw.ellipse(
            (width - 620, height - 520, width + 260, height + 250),
            fill=(0, 255, 255, 20),
        )
        glow_draw.ellipse(
            (width // 2 - 420, height // 2 - 260, width // 2 + 420, height // 2 + 260),
            fill=(*accent, 12),
        )

        glow = glow.filter(ImageFilter.GaussianBlur(radius=65))
        canvas.alpha_composite(glow)

        vignette = Image.new("RGBA", self.layout.canvas, (0, 0, 0, 0))
        vignette_draw = ImageDraw.Draw(vignette, "RGBA")
        vignette_draw.rectangle((0, 0, width, height), fill=(0, 0, 0, 118))
        vignette_draw.ellipse((-120, -100, width + 120, height + 100), fill=(0, 0, 0, 0))
        vignette = vignette.filter(ImageFilter.GaussianBlur(radius=90))
        canvas.alpha_composite(vignette)

        return canvas

    def _apply_finishing(self, canvas: Image.Image) -> None:
        add_noise_overlay(canvas, opacity=1, seed=909, scale=2)

    def _draw_main_frame(self, canvas: Image.Image) -> None:
        layout = self.layout
        theme = self.theme
        draw = ImageDraw.Draw(canvas, "RGBA")

        draw_soft_shadow(
            canvas,
            layout.outer_card,
            layout.outer_radius,
            offset=(0, 18),
            blur=38,
            color=(0, 0, 0, 175),
            spread=0,
        )

        outer = Image.new("RGBA", layout.outer_card.size, theme.outer_card_fill)
        paste_rounded(canvas, outer, layout.outer_card, layout.outer_radius)

        inner = Image.new("RGBA", layout.inner_card.size, theme.inner_card_fill)
        paste_rounded(canvas, inner, layout.inner_card, layout.inner_radius)

        draw.rounded_rectangle(
            layout.outer_card.box,
            radius=layout.outer_radius,
            outline=(255, 255, 255, 52),
            width=2,
        )

        draw.rounded_rectangle(
            layout.inner_card.box,
            radius=layout.inner_radius,
            outline=(255, 255, 255, 26),
            width=1,
        )

        self._draw_document_header(canvas)

    def _draw_document_header(self, canvas: Image.Image) -> None:
        rect = self.layout.inner_card
        draw = ImageDraw.Draw(canvas, "RGBA")

        font = self.fonts.font(13, "bold")
        left = "BAPHOMET ID"
        right = "FICHA DE IDENTIFICAÇÃO"

        self._draw_text(
            draw,
            (rect.x + 30, rect.y + 22),
            left,
            font,
            fill=(220, 220, 225, 135),
            shadow=(0, 0, 0, 95),
            offset=(1, 1),
        )

        right_w = text_width(draw, right, font)

        self._draw_text(
            draw,
            (rect.right - 30 - right_w, rect.y + 22),
            right,
            font,
            fill=(220, 220, 225, 135),
            shadow=(0, 0, 0, 95),
            offset=(1, 1),
        )

        draw.line(
            (rect.x + 30, rect.y + 49, rect.right - 30, rect.y + 49),
            fill=(255, 255, 255, 34),
            width=1,
        )

    def _draw_panel(self, canvas: Image.Image, rect: Rect, *, radius: int | None = None) -> None:
        radius = radius or self.layout.panel_radius

        draw_soft_shadow(
            canvas,
            rect,
            radius,
            offset=(0, 8),
            blur=18,
            color=(0, 0, 0, 105),
        )

        panel = Image.new("RGBA", rect.size, self.theme.panel_fill)
        paste_rounded(canvas, panel, rect, radius)

        draw = ImageDraw.Draw(canvas, "RGBA")

        draw.rounded_rectangle(
            rect.box,
            radius=radius,
            outline=self.theme.panel_outline,
            width=1,
        )

        draw.line(
            (rect.x + 24, rect.y + 1, rect.right - 24, rect.y + 1),
            fill=(255, 255, 255, 24),
            width=1,
        )

    def _draw_section_title(self, draw: ImageDraw.ImageDraw, rect: Rect, title: str) -> None:
        font = self.fonts.font(26, "display")
        x = rect.x + self.layout.section_pad
        y = rect.y + 22

        self._draw_text(
            draw,
            (x, y),
            title,
            font,
            fill=self.theme.text,
            shadow=(0, 0, 0, 180),
            offset=(2, 2),
        )

        title_w = text_width(draw, title, font)
        rule_x = x + title_w + 16

        if rule_x < rect.right - 30:
            line_y = y + 17
            draw.line(
                (rule_x, line_y, rect.right - 30, line_y),
                fill=(255, 255, 255, 36),
                width=1,
            )

    def _draw_avatar(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        slot = self.layout.avatar_medallion
        draw = ImageDraw.Draw(canvas, "RGBA")

        avatar_size = 240
        avatar_rect = Rect(
            slot.x + (slot.w - avatar_size) // 2,
            slot.y + (slot.h - avatar_size) // 2,
            avatar_size,
            avatar_size,
        )

        border_size = avatar_size + 18
        border_rect = Rect(
            avatar_rect.x - 9,
            avatar_rect.y - 9,
            border_size,
            border_size,
        )

        draw_soft_shadow(
            canvas,
            border_rect,
            border_rect.w // 2,
            offset=(0, 12),
            blur=28,
            color=(0, 0, 0, 170),
        )

        draw.ellipse(
            border_rect.box,
            fill=(*self._accent, 255),
        )

        source = load_rgba_from_bytes(profile.avatar_bytes)

        if source is None:
            avatar = create_avatar_placeholder(
                avatar_size,
                initials=self._initials(profile.display_name or profile.username),
                font=self.fonts.font(78, "display"),
                fill_top=(68, 64, 82, 255),
                fill_bottom=(28, 25, 36, 255),
                accent=self._accent + (255,),
                text_fill=self.theme.text,
            )
        else:
            avatar = circular_crop(source, avatar_size)

        canvas.paste(avatar, (avatar_rect.x, avatar_rect.y), avatar)

        draw.ellipse(
            avatar_rect.box,
            outline=(255, 255, 255, 65),
            width=2,
        )

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

        label_font = self.fonts.font(13, "bold")

        x = rect.x + 28
        y = rect.y + 32
        value_width = rect.w - 56

        for label, value in fields:
            draw.text(
                (x, y),
                label.upper(),
                font=label_font,
                fill=self.theme.text_muted,
            )

            weight = "display" if label == "Nome" else "regular"
            start_size = 30 if label == "Nome" else 22

            value_font = fit_font_to_width(
                draw,
                value,
                value_width - 24,
                font_loader=lambda size, weight=weight: self.fonts.font(size, weight),
                start_size=start_size,
                min_size=15,
            )

            display_value = truncate_text(draw, value, value_font, value_width - 24)
            value_y = y + 22

            field_rect = Rect(x - 12, value_y - 7, value_width + 24, 40)

            self._draw_field(draw, field_rect)

            self._draw_text(
                draw,
                (x, value_y),
                display_value,
                value_font,
                fill=self.theme.text if label == "Nome" else self.theme.text_soft,
                shadow=(0, 0, 0, 150),
                offset=(2, 2),
            )

            y += 88

    def _draw_ask_me_about(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.ask_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")

        self._draw_section_title(draw, rect, "Me pergunte sobre")

        topics = self._clean_topics(profile.ask_me_about)
        chips_rect = Rect(rect.x + 28, rect.y + 76, rect.w - 56, rect.h - 100)

        self._render_chips(canvas, chips_rect, topics)

    def _draw_basic_info(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.basic_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")

        self._draw_section_title(draw, rect, "Informações básicas")

        text = self._field(profile.basic_info, "Não informado.")
        body_rect = Rect(rect.x + 28, rect.y + 76, rect.w - 56, rect.h - 98)

        self._draw_field(draw, body_rect, radius=16)

        font = self.fonts.font(20, "regular")
        line_gap = 8
        line_height = text_height(draw, "Ag", font) + line_gap
        max_lines = max(1, body_rect.h // max(1, line_height))

        text_rect = body_rect.inset(16, 16)
        lines = wrap_text_pixels(draw, text, font, text_rect.w)
        lines = clamp_lines_with_ellipsis(draw, lines, font, text_rect.w, max_lines)

        y = text_rect.y

        for line in lines:
            if y + line_height > text_rect.bottom + 2:
                break

            self._draw_text(
                draw,
                (text_rect.x, y),
                line,
                font,
                fill=self.theme.text_soft,
                shadow=(0, 0, 0, 115),
                offset=(2, 2),
            )

            y += line_height

    def _draw_badge(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.badge_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")

        self._draw_section_title(draw, rect, "Insígnia")

        slot = Rect(rect.x + 54, rect.y + 80, rect.w - 108, 122)
        self._draw_field(draw, slot, radius=20)

        source = load_rgba_from_bytes(profile.badge_image_bytes)
        image_slot = slot.inset(16, 12)

        if source is None:
            badge = create_badge_placeholder(
                (150, 118),
                fill_top=(58, 54, 68, 255),
                fill_bottom=(30, 27, 38, 255),
                accent=self._accent + (255,),
                line=(255, 255, 255, 65),
            )
            paste_centered(canvas, badge, image_slot)
        else:
            paste_centered(canvas, source, image_slot)

        label = self._field(profile.badge_name, "Sem insígnia")
        label_font = self.fonts.font(18, "regular")
        label_rect = Rect(rect.x + 28, rect.y + 224, rect.w - 56, 40)
        label = truncate_text(draw, label, label_font, label_rect.w - 20)

        self._draw_field(draw, label_rect, radius=15)
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
            font_loader=lambda size: self.fonts.font(size, "display"),
            start_size=36,
            min_size=20,
        )

        count_text = truncate_text(draw, count_text, count_font, rect.w - 62)

        self._draw_text(
            draw,
            (rect.x + 30, rect.y + 90),
            count_text,
            count_font,
            fill=self.theme.text,
            shadow=(0, 0, 0, 170),
            offset=(2, 2),
        )

        mult = self._format_multiplier(profile.bonds_multiplier)
        badge_rect = Rect(rect.x + 30, rect.y + 158, rect.w - 60, 32)

        self._draw_field(draw, badge_rect, radius=16)

        mult_font = fit_font_to_width(
            draw,
            mult,
            badge_rect.w - 18,
            font_loader=lambda size: self.fonts.font(size, "regular"),
            start_size=18,
            min_size=14,
        )

        self._draw_centered_text(draw, badge_rect, mult, mult_font, self.theme.text_soft)

    def _draw_xp_progress(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.xp_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")

        self._draw_section_title(draw, rect, "Progresso de XP")

        level = max(0, int(profile.level))
        level_text = f"Nível {level}"
        level_font = self.fonts.font(20, "display")
        level_width = min(150, max(92, text_width(draw, level_text, level_font) + 30))
        level_rect = Rect(rect.right - 30 - level_width, rect.y + 24, level_width, 34)

        self._draw_field(draw, level_rect, radius=17)

        level_text = truncate_text(draw, level_text, level_font, level_rect.w - 18)

        self._draw_centered_text(
            draw,
            level_rect,
            level_text,
            level_font,
            self.theme.text_soft,
        )

        current = max(0, int(profile.xp_current))
        required = max(0, int(profile.xp_required))
        total = max(0, int(profile.xp_total))
        percent = self._normalize_percent(profile.xp_percent)

        bar_rect = Rect(rect.x + 30, rect.y + 86, rect.w - 60, 35)
        self._draw_xp_bar(canvas, bar_rect, percent / 100)

        xp_label = f"{current:,} / {required:,} XP".replace(",", ".")
        xp_font = self.fonts.font(20, "bold")
        xp_label = truncate_text(draw, xp_label, xp_font, bar_rect.w - 36)

        self._draw_centered_text(
            draw,
            bar_rect,
            xp_label,
            xp_font,
            self.theme.text,
            shadow=(0, 0, 0, 255),
        )

        meta_font = self.fonts.font(18, "regular")
        total_text = f"XP Total: {total:,}".replace(",", ".")
        percent_text = f"{self._format_percent(percent)} completo"

        self._draw_text(
            draw,
            (rect.x + 34, rect.y + 140),
            truncate_text(draw, total_text, meta_font, 320),
            meta_font,
            fill=self.theme.text_muted,
            shadow=(0, 0, 0, 95),
            offset=(1, 1),
        )

        percent_text = truncate_text(draw, percent_text, meta_font, 260)
        percent_w = text_width(draw, percent_text, meta_font)

        self._draw_text(
            draw,
            (rect.right - 34 - percent_w, rect.y + 140),
            percent_text,
            meta_font,
            fill=self.theme.text_muted,
            shadow=(0, 0, 0, 95),
            offset=(1, 1),
        )

    def _draw_xp_bar(self, canvas: Image.Image, rect: Rect, ratio: float) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        radius = rect.h // 2

        draw.rounded_rectangle(
            rect.box,
            radius=radius,
            fill=self.theme.bar_track,
            outline=(255, 255, 255, 42),
            width=1,
        )

        ratio = clamp(ratio, 0.0, 1.0)
        filled_width = int(rect.w * ratio)

        if filled_width < rect.h and ratio > 0:
            filled_width = min(rect.h, rect.w)

        if filled_width <= 0:
            return

        fill_layer = Image.new("RGBA", (rect.w, rect.h), (0, 0, 0, 0))
        fill_draw = ImageDraw.Draw(fill_layer)

        start_color = self._accent + (255,)
        end_color = self.theme.cyan

        for i in range(filled_width):
            t = i / max(1, rect.w)
            r = int(start_color[0] + (end_color[0] - start_color[0]) * t)
            g = int(start_color[1] + (end_color[1] - start_color[1]) * t)
            b = int(start_color[2] + (end_color[2] - start_color[2]) * t)
            a = int(start_color[3] + (end_color[3] - start_color[3]) * t)

            fill_draw.line(
                [(i, 0), (i, rect.h)],
                fill=(r, g, b, a),
            )

        mask = Image.new("L", (rect.w, rect.h), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle(
            (0, 0, filled_width, rect.h),
            radius=radius,
            fill=255,
        )

        fill_layer.putalpha(mask)
        canvas.paste(fill_layer, (rect.x, rect.y), fill_layer)

    def _render_chips(self, canvas: Image.Image, rect: Rect, labels: list[str]) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        font = self.fonts.font(18, "bold")

        x = rect.x
        y = rect.y
        gap = 10
        chip_height = 36
        max_y = rect.bottom - chip_height
        index = 0

        while index < len(labels) and y <= max_y:
            raw_label = labels[index]
            max_label_width = min(272, rect.w - 32)
            label = truncate_text(draw, raw_label, font, max_label_width)
            chip_width = max(68, text_width(draw, label, font) + 32)

            if x + chip_width > rect.right:
                if y + chip_height + gap > max_y:
                    self._draw_more_chip(
                        canvas,
                        Rect(x, y, rect.right - x, chip_height),
                        len(labels) - index,
                        font,
                    )
                    return

                x = rect.x
                y += chip_height + gap
                continue

            remaining_after = len(labels) - index - 1

            if remaining_after and y + chip_height + gap > max_y:
                more_label = f"+{remaining_after}"
                more_width = max(56, text_width(draw, more_label, font) + 26)

                if x + chip_width + gap + more_width > rect.right:
                    self._draw_more_chip(
                        canvas,
                        Rect(x, y, rect.right - x, chip_height),
                        len(labels) - index,
                        font,
                    )
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
        draw = ImageDraw.Draw(canvas, "RGBA")

        fill = (0, 0, 0, 86) if muted else (0, 0, 0, 112)
        outline = (255, 255, 255, 30) if muted else (255, 255, 255, 42)
        text_fill = self.theme.text_muted if muted else self.theme.text_soft

        draw.rounded_rectangle(
            rect.box,
            radius=rect.h // 2,
            fill=fill,
            outline=outline,
            width=1,
        )

        label_width = text_width(draw, label, font)
        label_height = text_height(draw, label, font)

        self._draw_text(
            draw,
            (rect.x + (rect.w - label_width) // 2, rect.y + (rect.h - label_height) // 2 - 2),
            label,
            font,
            fill=text_fill,
            shadow=(0, 0, 0, 135),
            offset=(2, 2),
        )

    def _draw_field(
        self,
        draw: ImageDraw.ImageDraw,
        rect: Rect,
        *,
        radius: int = 14,
    ) -> None:
        draw.rounded_rectangle(
            rect.box,
            radius=radius,
            fill=self.theme.field_fill,
            outline=self.theme.field_outline,
            width=1,
        )

    def _draw_centered_text(
        self,
        draw: ImageDraw.ImageDraw,
        rect: Rect,
        text: str,
        font: ImageFont.ImageFont,
        fill: ColorA,
        *,
        shadow: ColorA = (0, 0, 0, 145),
        stroke_width: int = 0,
        stroke_fill: ColorA = (0, 0, 0, 0),
    ) -> None:
        text_w = text_width(draw, text, font)
        text_h = text_height(draw, text or "Ag", font)

        self._draw_text(
            draw,
            (rect.x + (rect.w - text_w) // 2, rect.y + (rect.h - text_h) // 2 - 2),
            text,
            font,
            fill=fill,
            shadow=shadow,
            offset=(2, 2),
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )

    def _draw_text(
        self,
        draw: ImageDraw.ImageDraw,
        pos: tuple[int, int],
        text: str,
        font: ImageFont.ImageFont,
        *,
        fill: ColorA,
        shadow: ColorA = (0, 0, 0, 160),
        offset: tuple[int, int] = (2, 2),
        stroke_width: int = 0,
        stroke_fill: ColorA = (0, 0, 0, 0),
    ) -> None:
        draw_text_shadow(
            draw,
            pos,
            text,
            font=font,
            fill=fill,
            shadow=shadow,
            offset=offset,
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