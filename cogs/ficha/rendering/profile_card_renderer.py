from __future__ import annotations

import io
import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

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
    background: ColorA = (8, 8, 10, 255)

    outer_card: ColorA = (14, 14, 17, 255)
    inner_card: ColorA = (20, 20, 23, 255)

    panel: ColorA = (28, 28, 32, 255)
    panel_alt: ColorA = (24, 24, 28, 255)
    panel_outline: ColorA = (52, 52, 58, 255)

    field: ColorA = (18, 18, 21, 255)
    field_outline: ColorA = (60, 60, 66, 255)

    text: ColorA = (242, 242, 244, 255)
    text_soft: ColorA = (196, 196, 202, 255)
    text_muted: ColorA = (132, 132, 140, 255)

    accent: ColorA = (156, 35, 58, 255)
    accent_soft: ColorA = (92, 28, 42, 255)

    chip_fill: ColorA = (38, 38, 43, 255)
    chip_outline: ColorA = (66, 66, 74, 255)

    xp_track: ColorA = (18, 18, 21, 255)
    xp_fill: ColorA = (156, 35, 58, 255)

    shadow: ColorA = (0, 0, 0, 105)


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
    inner_radius: int = 24
    panel_radius: int = 20
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
        self._apply_finishing(canvas)

        output = io.BytesIO()
        canvas.convert("RGBA").save(output, format="PNG")
        return output.getvalue()

    def _create_background(self) -> Image.Image:
        canvas = Image.new("RGBA", self.layout.canvas, self.theme.background)

        draw = ImageDraw.Draw(canvas, "RGBA")
        width, height = self.layout.canvas

        draw.ellipse(
            (-260, -260, 620, 620),
            fill=(156, 35, 58, 22),
        )
        draw.ellipse(
            (width - 520, height - 420, width + 240, height + 280),
            fill=(156, 35, 58, 14),
        )

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
            offset=(0, 16),
            blur=30,
            color=theme.shadow,
            spread=0,
        )

        outer = Image.new("RGBA", layout.outer_card.size, theme.outer_card)
        paste_rounded(canvas, outer, layout.outer_card, layout.outer_radius)

        inner = Image.new("RGBA", layout.inner_card.size, theme.inner_card)
        paste_rounded(canvas, inner, layout.inner_card, layout.inner_radius)

        draw.rounded_rectangle(
            layout.outer_card.box,
            radius=layout.outer_radius,
            outline=(38, 38, 44, 255),
            width=1,
        )

        draw.rounded_rectangle(
            layout.inner_card.box,
            radius=layout.inner_radius,
            outline=(58, 58, 65, 255),
            width=1,
        )

        self._draw_branding(canvas)

    def _draw_branding(self, canvas: Image.Image) -> None:
        rect = self.layout.inner_card
        draw = ImageDraw.Draw(canvas, "RGBA")

        tiny = self.fonts.font(12, "bold")
        left = "BAPHOMET ID"
        right = "FICHA DE IDENTIFICAÇÃO"

        draw.text(
            (rect.x + 30, rect.y + 22),
            left,
            font=tiny,
            fill=(132, 132, 140, 120),
        )

        right_w = text_width(draw, right, tiny)
        draw.text(
            (rect.right - 30 - right_w, rect.y + 22),
            right,
            font=tiny,
            fill=(132, 132, 140, 120),
        )

        draw.line(
            (rect.x + 30, rect.y + 48, rect.right - 30, rect.y + 48),
            fill=(58, 58, 65, 150),
            width=1,
        )

    def _draw_panel(self, canvas: Image.Image, rect: Rect, *, radius: int | None = None) -> None:
        radius = radius or self.layout.panel_radius

        draw_soft_shadow(
            canvas,
            rect,
            radius,
            offset=(0, 6),
            blur=14,
            color=(0, 0, 0, 80),
        )

        panel = Image.new("RGBA", rect.size, self.theme.panel)
        paste_rounded(canvas, panel, rect, radius)

        draw = ImageDraw.Draw(canvas, "RGBA")
        draw.rounded_rectangle(
            rect.box,
            radius=radius,
            outline=self.theme.panel_outline,
            width=1,
        )

    def _draw_section_title(self, draw: ImageDraw.ImageDraw, rect: Rect, title: str) -> None:
        font = self.fonts.font(26, "display")
        x = rect.x + self.layout.section_pad
        y = rect.y + 22

        draw_text_shadow(
            draw,
            (x, y),
            title,
            font=font,
            fill=self.theme.text,
            shadow=(0, 0, 0, 80),
            offset=(0, 1),
        )

        title_w = text_width(draw, title, font)
        rule_x = x + title_w + 16

        if rule_x < rect.right - 30:
            line_y = y + 17
            draw.line(
                (rule_x, line_y, rect.right - 30, line_y),
                fill=(66, 66, 74, 255),
                width=1,
            )

    def _draw_avatar(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        theme = self.theme
        slot = self.layout.avatar_medallion
        draw = ImageDraw.Draw(canvas, "RGBA")

        avatar_size = 240
        avatar_rect = Rect(
            slot.x + (slot.w - avatar_size) // 2,
            slot.y + (slot.h - avatar_size) // 2,
            avatar_size,
            avatar_size,
        )

        ring_rect = Rect(avatar_rect.x - 18, avatar_rect.y - 18, avatar_rect.w + 36, avatar_rect.h + 36)

        draw_soft_shadow(
            canvas,
            ring_rect,
            ring_rect.w // 2,
            offset=(0, 10),
            blur=24,
            color=(0, 0, 0, 140),
        )

        draw.ellipse(ring_rect.box, fill=theme.panel, outline=theme.panel_outline, width=1)
        draw.ellipse(
            (ring_rect.x + 10, ring_rect.y + 10, ring_rect.right - 10, ring_rect.bottom - 10),
            outline=theme.accent,
            width=4,
        )

        source = load_rgba_from_bytes(profile.avatar_bytes)

        if source is None:
            avatar = create_avatar_placeholder(
                avatar_size,
                initials=self._initials(profile.display_name or profile.username),
                font=self.fonts.font(78, "display"),
                fill_top=(34, 34, 39, 255),
                fill_bottom=(20, 20, 24, 255),
                accent=theme.accent,
                text_fill=theme.text,
            )
        else:
            avatar = circular_crop(source, avatar_size)

        canvas.paste(avatar, (avatar_rect.x, avatar_rect.y), avatar)
        draw.ellipse(avatar_rect.box, outline=(78, 78, 86, 255), width=1)

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

            draw.rounded_rectangle(
                field_rect.box,
                radius=14,
                fill=self.theme.field,
                outline=self.theme.field_outline,
                width=1,
            )

            draw_text_shadow(
                draw,
                (x, value_y),
                display_value,
                font=value_font,
                fill=self.theme.text if label == "Nome" else self.theme.text_soft,
                shadow=(0, 0, 0, 70),
                offset=(0, 1),
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

        self._draw_text_area(draw, body_rect)

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

            draw_text_shadow(
                draw,
                (text_rect.x, y),
                line,
                font=font,
                fill=self.theme.text_soft,
                shadow=(0, 0, 0, 60),
                offset=(0, 1),
            )

            y += line_height

    def _draw_text_area(self, draw: ImageDraw.ImageDraw, rect: Rect) -> None:
        draw.rounded_rectangle(
            rect.box,
            radius=16,
            fill=self.theme.field,
            outline=self.theme.field_outline,
            width=1,
        )

    def _draw_badge(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.badge_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")

        self._draw_section_title(draw, rect, "Insígnia")

        slot = Rect(rect.x + 54, rect.y + 80, rect.w - 108, 122)

        draw.rounded_rectangle(
            slot.box,
            radius=20,
            fill=self.theme.field,
            outline=self.theme.field_outline,
            width=1,
        )

        source = load_rgba_from_bytes(profile.badge_image_bytes)
        image_slot = slot.inset(16, 12)

        if source is None:
            badge = create_badge_placeholder(
                (150, 118),
                fill_top=(34, 34, 39, 255),
                fill_bottom=(20, 20, 24, 255),
                accent=self.theme.accent,
                line=(78, 78, 86, 255),
            )
            paste_centered(canvas, badge, image_slot)
        else:
            paste_centered(canvas, source, image_slot)

        label = self._field(profile.badge_name, "Sem insígnia")
        label_font = self.fonts.font(18, "regular")
        label_rect = Rect(rect.x + 28, rect.y + 224, rect.w - 56, 40)
        label = truncate_text(draw, label, label_font, label_rect.w - 20)

        draw.rounded_rectangle(
            label_rect.box,
            radius=15,
            fill=self.theme.field,
            outline=self.theme.field_outline,
            width=1,
        )

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

        draw_text_shadow(
            draw,
            (rect.x + 30, rect.y + 90),
            count_text,
            font=count_font,
            fill=self.theme.text,
            shadow=(0, 0, 0, 80),
            offset=(0, 1),
        )

        mult = self._format_multiplier(profile.bonds_multiplier)
        badge_rect = Rect(rect.x + 30, rect.y + 158, rect.w - 60, 32)

        draw.rounded_rectangle(
            badge_rect.box,
            radius=16,
            fill=self.theme.field,
            outline=self.theme.field_outline,
            width=1,
        )

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

        draw.rounded_rectangle(
            level_rect.box,
            radius=17,
            fill=self.theme.field,
            outline=self.theme.field_outline,
            width=1,
        )

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

        bar_rect = Rect(rect.x + 30, rect.y + 86, rect.w - 60, 32)
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
            shadow=(0, 0, 0, 110),
        )

        meta_font = self.fonts.font(18, "regular")
        total_text = f"XP Total: {total:,}".replace(",", ".")
        percent_text = f"{self._format_percent(percent)} completo"

        draw_text_shadow(
            draw,
            (rect.x + 34, rect.y + 140),
            truncate_text(draw, total_text, meta_font, 320),
            font=meta_font,
            fill=self.theme.text_muted,
            shadow=(0, 0, 0, 55),
            offset=(0, 1),
        )

        percent_text = truncate_text(draw, percent_text, meta_font, 260)
        percent_w = text_width(draw, percent_text, meta_font)

        draw_text_shadow(
            draw,
            (rect.right - 34 - percent_w, rect.y + 140),
            percent_text,
            font=meta_font,
            fill=self.theme.text_muted,
            shadow=(0, 0, 0, 55),
            offset=(0, 1),
        )

    def _draw_xp_bar(self, canvas: Image.Image, rect: Rect, ratio: float) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        radius = rect.h // 2

        draw.rounded_rectangle(
            rect.box,
            radius=radius,
            fill=self.theme.xp_track,
            outline=self.theme.field_outline,
            width=1,
        )

        ratio = clamp(ratio, 0.0, 1.0)
        fill_width = int(rect.w * ratio)

        if fill_width <= 0:
            return

        fill_rect = Rect(rect.x, rect.y, min(fill_width, rect.w), rect.h)

        mask = Image.new("L", fill_rect.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, fill_rect.w, fill_rect.h),
            radius=radius,
            fill=255,
        )

        fill_layer = Image.new("RGBA", fill_rect.size, self.theme.xp_fill)
        canvas.paste(fill_layer, (fill_rect.x, fill_rect.y), mask)

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

        fill = self.theme.field if muted else self.theme.chip_fill
        outline = self.theme.field_outline if muted else self.theme.chip_outline
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

        draw_text_shadow(
            draw,
            (rect.x + (rect.w - label_width) // 2, rect.y + (rect.h - label_height) // 2 - 2),
            label,
            font=font,
            fill=text_fill,
            shadow=(0, 0, 0, 55),
            offset=(0, 1),
        )

    def _draw_centered_text(
        self,
        draw: ImageDraw.ImageDraw,
        rect: Rect,
        text: str,
        font: ImageFont.ImageFont,
        fill: ColorA,
        *,
        shadow: ColorA = (0, 0, 0, 70),
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