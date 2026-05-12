from __future__ import annotations

import io
import math
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
    background_top: ColorA = (3, 3, 5, 255)
    background_bottom: ColorA = (0, 0, 0, 255)

    smoke: ColorA = (35, 0, 12, 45)

    outer_top: ColorA = (9, 9, 12, 255)
    outer_bottom: ColorA = (2, 2, 4, 255)

    inner_top: ColorA = (16, 16, 19, 255)
    inner_bottom: ColorA = (7, 7, 10, 255)

    panel_top: ColorA = (20, 20, 23, 255)
    panel_bottom: ColorA = (9, 9, 12, 255)

    panel_outline: ColorA = (82, 76, 76, 185)
    panel_highlight: ColorA = (190, 178, 162, 30)
    panel_shadow: ColorA = (0, 0, 0, 120)

    text: ColorA = (238, 234, 225, 255)
    text_soft: ColorA = (190, 184, 174, 255)
    text_muted: ColorA = (126, 118, 112, 255)
    text_dark: ColorA = (8, 8, 10, 255)

    accent_dark: ColorA = (28, 0, 10, 255)
    accent: ColorA = (88, 8, 28, 255)
    accent_light: ColorA = (148, 24, 52, 255)

    silver_dark: ColorA = (48, 46, 48, 255)
    silver_mid: ColorA = (112, 108, 106, 255)
    silver_light: ColorA = (204, 198, 186, 255)

    gold: ColorA = (154, 130, 82, 255)

    chip_fill: ColorA = (16, 16, 19, 255)
    chip_outline: ColorA = (96, 88, 84, 200)
    chip_highlight: ColorA = (210, 200, 178, 26)

    xp_track: ColorA = (6, 6, 8, 255)
    xp_start: ColorA = (82, 6, 24, 255)
    xp_end: ColorA = (166, 32, 60, 255)

    recessed: ColorA = (7, 7, 9, 255)


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
        self._apply_finishing_patina(canvas)

        output = io.BytesIO()
        canvas.convert("RGBA").save(output, format="PNG")
        return output.getvalue()

    def _create_background(self) -> Image.Image:
        layout = self.layout
        theme = self.theme

        canvas = vertical_gradient(layout.canvas, theme.background_top, theme.background_bottom)

        width, height = layout.canvas
        draw = ImageDraw.Draw(canvas, "RGBA")

        for i in range(10):
            alpha = max(8, 34 - i * 3)
            x0 = -220 + i * 72
            y0 = 80 + int(math.sin(i * 1.4) * 55)
            x1 = width + 260 - i * 26
            y1 = height + 180 - i * 34
            draw.ellipse((x0, y0, x1, y1), fill=(50, 0, 18, alpha))

        add_noise_overlay(canvas, opacity=7, seed=404, scale=2)

        smoke = Image.new("RGBA", layout.canvas, (0, 0, 0, 0))
        smoke_draw = ImageDraw.Draw(smoke, "RGBA")
        for i in range(14):
            x = -160 + i * 120
            y = 70 + int(math.sin(i * 0.9) * 70)
            smoke_draw.ellipse(
                (x, y, x + 380, y + 210),
                fill=(58, 58, 64, 9),
            )
        smoke = smoke.filter(ImageFilter.GaussianBlur(42))
        canvas.alpha_composite(smoke)

        vignette = Image.new("RGBA", layout.canvas, (0, 0, 0, 0))
        vignette_draw = ImageDraw.Draw(vignette, "RGBA")
        vignette_draw.rectangle((0, 0, width, height), fill=(0, 0, 0, 165))
        vignette_draw.ellipse((-180, -150, width + 180, height + 120), fill=(0, 0, 0, 0))
        vignette = vignette.filter(ImageFilter.GaussianBlur(105))
        canvas.alpha_composite(vignette)

        return canvas

    def _apply_finishing_patina(self, canvas: Image.Image) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        w, h = self.layout.canvas

        for x in range(0, w, 18):
            alpha = 4 if (x // 18) % 2 == 0 else 2
            draw.line((x, 0, x, h), fill=(255, 255, 255, alpha), width=1)

        for y in range(0, h, 22):
            draw.line((0, y, w, y), fill=(0, 0, 0, 9), width=1)

        add_noise_overlay(canvas, opacity=5, seed=909, scale=1)

    def _draw_main_frame(self, canvas: Image.Image) -> None:
        layout = self.layout
        theme = self.theme

        draw_soft_shadow(
            canvas,
            layout.outer_card,
            layout.outer_radius,
            offset=(0, 18),
            blur=36,
            color=(0, 0, 0, 210),
            spread=0,
        )

        outer = vertical_gradient(layout.outer_card.size, theme.outer_top, theme.outer_bottom)
        paste_rounded(canvas, outer, layout.outer_card, layout.outer_radius)

        inner_shadow = Rect(
            layout.inner_card.x - 10,
            layout.inner_card.y - 10,
            layout.inner_card.w + 20,
            layout.inner_card.h + 20,
        )
        draw_soft_shadow(
            canvas,
            inner_shadow,
            layout.inner_radius + 12,
            offset=(0, 0),
            blur=24,
            color=(0, 0, 0, 150),
            spread=0,
        )

        inner = vertical_gradient(layout.inner_card.size, theme.inner_top, theme.inner_bottom)
        paste_rounded(canvas, inner, layout.inner_card, layout.inner_radius)

        draw = ImageDraw.Draw(canvas, "RGBA")

        draw.rounded_rectangle(
            layout.outer_card.box,
            radius=layout.outer_radius,
            outline=(132, 122, 110, 150),
            width=2,
        )
        draw.rounded_rectangle(
            layout.outer_card.inset(12, 12).box,
            radius=max(1, layout.outer_radius - 10),
            outline=(0, 0, 0, 190),
            width=3,
        )
        draw.rounded_rectangle(
            layout.inner_card.box,
            radius=layout.inner_radius,
            outline=(86, 78, 74, 190),
            width=1,
        )
        draw.rounded_rectangle(
            layout.inner_card.inset(8, 8).box,
            radius=max(1, layout.inner_radius - 8),
            outline=(184, 170, 146, 42),
            width=1,
        )

        self._draw_gothic_frame(canvas, layout.inner_card)
        self._draw_watermark(canvas)
        self._draw_document_marks(canvas)
        self._draw_document_serial(canvas)

    def _draw_document_serial(self, canvas: Image.Image) -> None:
        rect = self.layout.inner_card
        draw = ImageDraw.Draw(canvas, "RGBA")

        tiny = self.fonts.font(12, "bold")
        label = "BAPHOMET CIVIL REGISTRY // SOUL IDENTIFICATION CARD"
        draw.text(
            (rect.x + 32, rect.bottom - 30),
            label,
            font=tiny,
            fill=(140, 130, 118, 80),
        )

        serial = "CPF-DO-ABISMO // 000-666-013"
        serial_w = text_width(draw, serial, tiny)
        draw.text(
            (rect.right - 32 - serial_w, rect.bottom - 30),
            serial,
            font=tiny,
            fill=(140, 130, 118, 80),
        )

    def _draw_gothic_frame(self, canvas: Image.Image, rect: Rect) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        line = (142, 130, 112, 75)
        red = (128, 12, 36, 100)

        inset = 20
        left = rect.x + inset
        right = rect.right - inset
        top = rect.y + inset
        bottom = rect.bottom - inset

        draw.line((left + 44, top, right - 44, top), fill=line, width=1)
        draw.line((left + 44, bottom, right - 44, bottom), fill=line, width=1)
        draw.line((left, top + 44, left, bottom - 44), fill=line, width=1)
        draw.line((right, top + 44, right, bottom - 44), fill=line, width=1)

        for sx in (left, right):
            for sy in (top, bottom):
                sign_x = 1 if sx == left else -1
                sign_y = 1 if sy == top else -1

                draw.arc(
                    (
                        sx - (0 if sign_x == 1 else 56),
                        sy - (0 if sign_y == 1 else 56),
                        sx + (56 if sign_x == 1 else 0),
                        sy + (56 if sign_y == 1 else 0),
                    ),
                    start=0 if sign_x == -1 and sign_y == 1 else 90 if sign_x == -1 else 180 if sign_y == -1 else 270,
                    end=90 if sign_x == -1 and sign_y == 1 else 180 if sign_x == 1 and sign_y == 1 else 270 if sign_x == 1 else 360,
                    fill=line,
                    width=2,
                )

                draw.line((sx, sy, sx + sign_x * 28, sy + sign_y * 28), fill=red, width=1)
                draw.ellipse(
                    (sx + sign_x * 30 - 4, sy + sign_y * 30 - 4, sx + sign_x * 30 + 4, sy + sign_y * 30 + 4),
                    fill=(120, 12, 36, 130),
                )

    def _draw_document_marks(self, canvas: Image.Image) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        rect = self.layout.inner_card
        color = (214, 202, 184, 50)
        red = (120, 10, 34, 75)

        inset = 24
        length = 18

        for x in (rect.x + inset, rect.right - inset):
            for y in (rect.y + inset, rect.bottom - inset):
                draw.line((x - length // 2, y, x + length // 2, y), fill=color, width=1)
                draw.line((x, y - length // 2, x, y + length // 2), fill=color, width=1)
                draw.ellipse((x - 3, y - 3, x + 3, y + 3), outline=red, width=1)

    def _draw_watermark(self, canvas: Image.Image) -> None:
        rect = self.layout.inner_card
        layer = Image.new("RGBA", self.layout.canvas, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")

        cx, cy = rect.center

        self._draw_baphomet_sigil(
            draw,
            cx,
            cy - 4,
            scale=2.35,
            fill=(0, 0, 0, 64),
            outline=(130, 10, 38, 28),
        )

        word = "BAPHOMET"
        font = self.fonts.font(132, "display")
        word_w = text_width(draw, word, font)
        draw.text(
            (cx - word_w // 2, cy + 120),
            word,
            font=font,
            fill=(0, 0, 0, 44),
        )

        layer = layer.filter(ImageFilter.GaussianBlur(0.25))
        canvas.alpha_composite(layer)

    def _draw_baphomet_sigil(
        self,
        draw: ImageDraw.ImageDraw,
        cx: int,
        cy: int,
        *,
        scale: float,
        fill: ColorA,
        outline: ColorA,
    ) -> None:
        r = int(82 * scale)
        points: list[tuple[int, int]] = []

        for i in range(5):
            angle = -math.pi / 2 + i * (2 * math.pi / 5)
            points.append((int(cx + math.cos(angle) * r), int(cy + math.sin(angle) * r)))

        star_order = [0, 2, 4, 1, 3, 0]
        for a, b in zip(star_order, star_order[1:]):
            draw.line((points[a][0], points[a][1], points[b][0], points[b][1]), fill=fill, width=max(1, int(5 * scale)))

        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=outline, width=max(1, int(3 * scale)))
        draw.ellipse((cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2), outline=outline, width=max(1, int(2 * scale)))
        draw.line((cx, cy - r, cx, cy + r), fill=outline, width=max(1, int(2 * scale)))
        draw.line((cx - r, cy, cx + r, cy), fill=outline, width=max(1, int(1 * scale)))

    def _draw_panel(self, canvas: Image.Image, rect: Rect, *, radius: int | None = None) -> None:
        theme = self.theme
        radius = radius or self.layout.panel_radius

        draw_soft_shadow(
            canvas,
            rect,
            radius,
            offset=(0, 8),
            blur=18,
            color=theme.panel_shadow,
        )

        panel = vertical_gradient(rect.size, theme.panel_top, theme.panel_bottom)
        paste_rounded(canvas, panel, rect, radius)

        draw = ImageDraw.Draw(canvas, "RGBA")

        draw.rounded_rectangle(
            rect.box,
            radius=radius,
            outline=theme.panel_outline,
            width=1,
        )

        inner = rect.inset(6, 6)
        draw.rounded_rectangle(
            inner.box,
            radius=max(1, radius - 6),
            outline=(255, 245, 220, 20),
            width=1,
        )

        draw.line(
            (rect.x + 22, rect.y + 1, rect.right - 22, rect.y + 1),
            fill=theme.panel_highlight,
            width=1,
        )

        self._draw_panel_corner_oraments(draw, rect, radius)

    def _draw_panel_corner_oraments(self, draw: ImageDraw.ImageDraw, rect: Rect, radius: int) -> None:
        color = (142, 128, 108, 70)
        red = (128, 10, 36, 75)
        length = 20

        corners = (
            (rect.x + radius, rect.y + 10, 1, 1),
            (rect.right - radius, rect.y + 10, -1, 1),
            (rect.x + radius, rect.bottom - 10, 1, -1),
            (rect.right - radius, rect.bottom - 10, -1, -1),
        )

        for x, y, sx, sy in corners:
            draw.line((x, y, x + sx * length, y), fill=color, width=1)
            draw.line((x, y, x, y + sy * length), fill=color, width=1)
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=red)

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
            shadow=(0, 0, 0, 160),
            offset=(0, 2),
        )

        title_w = text_width(draw, title, font)
        rule_x = x + title_w + 16

        if rule_x < rect.right - 30:
            line_y = y + 16
            draw.line(
                (rule_x, line_y, rect.right - 30, line_y),
                fill=(120, 110, 96, 92),
                width=1,
            )
            draw.line(
                (rule_x, line_y + 3, rect.right - 30, line_y + 3),
                fill=(80, 8, 28, 70),
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

        halo_rect = Rect(avatar_rect.x - 22, avatar_rect.y - 22, avatar_rect.w + 44, avatar_rect.h + 44)
        draw_soft_shadow(
            canvas,
            halo_rect,
            halo_rect.w // 2,
            offset=(0, 12),
            blur=28,
            color=(0, 0, 0, 210),
        )

        draw.ellipse(halo_rect.box, fill=(4, 4, 6, 255), outline=(118, 108, 94, 165), width=3)
        draw.ellipse(
            (halo_rect.x + 12, halo_rect.y + 12, halo_rect.right - 12, halo_rect.bottom - 12),
            outline=(86, 8, 30, 190),
            width=4,
        )
        draw.ellipse(
            (halo_rect.x + 24, halo_rect.y + 24, halo_rect.right - 24, halo_rect.bottom - 24),
            outline=(0, 0, 0, 210),
            width=6,
        )

        self._draw_avatar_runes(draw, halo_rect)

        source = load_rgba_from_bytes(profile.avatar_bytes)
        if source is None:
            avatar = create_avatar_placeholder(
                avatar_size,
                initials=self._initials(profile.display_name or profile.username),
                font=self.fonts.font(78, "display"),
                fill_top=(26, 26, 30, 255),
                fill_bottom=(4, 4, 6, 255),
                accent=theme.accent,
                text_fill=theme.text,
            )
        else:
            avatar = circular_crop(source, avatar_size)

        canvas.paste(avatar, (avatar_rect.x, avatar_rect.y), avatar)

        draw.ellipse(avatar_rect.box, outline=(210, 196, 168, 105), width=2)
        draw.ellipse(
            (avatar_rect.x + 7, avatar_rect.y + 7, avatar_rect.right - 7, avatar_rect.bottom - 7),
            outline=(0, 0, 0, 170),
            width=2,
        )

    def _draw_avatar_runes(self, draw: ImageDraw.ImageDraw, rect: Rect) -> None:
        cx, cy = rect.center
        radius = rect.w // 2 - 12
        color = (172, 150, 120, 70)

        for i in range(16):
            angle = -math.pi / 2 + i * (2 * math.pi / 16)
            x1 = int(cx + math.cos(angle) * (radius - 10))
            y1 = int(cy + math.sin(angle) * (radius - 10))
            x2 = int(cx + math.cos(angle) * radius)
            y2 = int(cy + math.sin(angle) * radius)
            draw.line((x1, y1, x2, y2), fill=color, width=1)

    def _draw_identity(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.identity_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")

        self._draw_identity_stamp(draw, rect)

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

            weight = "display" if label == "Nome" else "regular"
            start_size = 30 if label == "Nome" else 22

            value_font = fit_font_to_width(
                draw,
                value,
                value_width,
                font_loader=lambda size, weight=weight: self.fonts.font(size, weight),
                start_size=start_size,
                min_size=15,
            )

            display_value = truncate_text(draw, value, value_font, value_width)
            value_y = y + 22

            field_rect = Rect(x - 12, value_y - 7, value_width + 24, 40)
            draw.rounded_rectangle(
                field_rect.box,
                radius=15,
                fill=(6, 6, 8, 190),
                outline=(74, 68, 64, 130),
                width=1,
            )

            draw_text_shadow(
                draw,
                (x, value_y),
                display_value,
                font=value_font,
                fill=self.theme.text if label == "Nome" else self.theme.text_soft,
                shadow=(0, 0, 0, 140),
                offset=(0, 1),
            )

            divider_y = value_y + text_height(draw, display_value or "Ag", value_font) + 16

            if label != "Rank":
                draw.line(
                    (x, divider_y, x + value_width, divider_y),
                    fill=(102, 92, 80, 70),
                    width=1,
                )

            y += 88

    def _draw_identity_stamp(self, draw: ImageDraw.ImageDraw, rect: Rect) -> None:
        stamp_font = self.fonts.font(18, "bold")
        text = "VALIDADO"
        text_w = text_width(draw, text, stamp_font)

        stamp = Rect(rect.right - text_w - 62, rect.y + 20, text_w + 36, 32)
        draw.rounded_rectangle(
            stamp.box,
            radius=8,
            outline=(130, 20, 44, 88),
            width=2,
        )
        draw.text(
            (stamp.x + 18, stamp.y + 6),
            text,
            font=stamp_font,
            fill=(130, 20, 44, 92),
        )

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

        self._draw_ruled_area(draw, body_rect)

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
                shadow=(0, 0, 0, 120),
                offset=(0, 1),
            )
            y += line_height

    def _draw_badge(self, canvas: Image.Image, profile: ProfileRenderData) -> None:
        rect = self.layout.badge_panel
        self._draw_panel(canvas, rect)
        draw = ImageDraw.Draw(canvas, "RGBA")
        self._draw_section_title(draw, rect, "Insígnia")

        slot = Rect(rect.x + 54, rect.y + 80, rect.w - 108, 122)

        draw_soft_shadow(
            canvas,
            slot,
            20,
            offset=(0, 8),
            blur=18,
            color=(0, 0, 0, 150),
        )

        draw.rounded_rectangle(
            slot.box,
            radius=20,
            fill=(5, 5, 7, 255),
            outline=(112, 102, 88, 150),
            width=1,
        )
        draw.rounded_rectangle(
            slot.inset(8, 8).box,
            radius=15,
            outline=(120, 10, 34, 110),
            width=1,
        )

        source = load_rgba_from_bytes(profile.badge_image_bytes)
        image_slot = slot.inset(16, 12)

        if source is None:
            badge = create_badge_placeholder(
                (150, 118),
                fill_top=(22, 22, 25, 255),
                fill_bottom=(4, 4, 6, 255),
                accent=self.theme.accent_light,
                line=(118, 108, 94, 180),
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
            fill=(6, 6, 8, 180),
            outline=(74, 68, 64, 115),
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
            shadow=(0, 0, 0, 140),
            offset=(0, 2),
        )

        draw.line(
            (rect.x + 30, rect.y + 143, rect.right - 30, rect.y + 143),
            fill=(118, 108, 94, 80),
            width=1,
        )
        draw.line(
            (rect.x + 30, rect.y + 147, rect.right - 30, rect.y + 147),
            fill=(90, 8, 28, 70),
            width=1,
        )

        mult = self._format_multiplier(profile.bonds_multiplier)
        badge_rect = Rect(rect.x + 30, rect.y + 158, rect.w - 60, 32)

        draw.rounded_rectangle(
            badge_rect.box,
            radius=16,
            fill=(7, 7, 9, 220),
            outline=(112, 102, 88, 125),
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
            fill=(7, 7, 9, 225),
            outline=(112, 102, 88, 135),
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
        self._draw_xp_document_bar(canvas, bar_rect, percent / 100)

        xp_label = f"{current:,} / {required:,} XP".replace(",", ".")
        xp_font = self.fonts.font(20, "bold")
        xp_label = truncate_text(draw, xp_label, xp_font, bar_rect.w - 36)

        self._draw_centered_text(
            draw,
            bar_rect,
            xp_label,
            xp_font,
            self.theme.text,
            shadow=(0, 0, 0, 190),
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
            shadow=(0, 0, 0, 120),
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
            shadow=(0, 0, 0, 120),
            offset=(0, 1),
        )

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
        draw_soft_shadow(
            canvas,
            rect,
            rect.h // 2,
            offset=(0, 3),
            blur=8,
            color=(0, 0, 0, 100),
        )

        draw = ImageDraw.Draw(canvas, "RGBA")

        fill = (10, 10, 12, 230) if muted else self.theme.chip_fill
        outline = (82, 76, 72, 135) if muted else self.theme.chip_outline

        draw.rounded_rectangle(
            rect.box,
            radius=rect.h // 2,
            fill=fill,
            outline=outline,
            width=1,
        )

        draw.line(
            (rect.x + 13, rect.y + rect.h // 2, rect.x + 19, rect.y + rect.h // 2),
            fill=(150, 24, 52, 115),
            width=2,
        )

        draw.line(
            (rect.x + 22, rect.y + 1, rect.right - 18, rect.y + 1),
            fill=self.theme.chip_highlight,
            width=1,
        )

        label_width = text_width(draw, label, font)
        label_height = text_height(draw, label, font)

        draw_text_shadow(
            draw,
            (rect.x + (rect.w - label_width) // 2 + 5, rect.y + (rect.h - label_height) // 2 - 2),
            label,
            font=font,
            fill=self.theme.text_soft if muted else self.theme.text,
            shadow=(0, 0, 0, 140),
            offset=(0, 1),
        )

    def _draw_ruled_area(self, draw: ImageDraw.ImageDraw, rect: Rect) -> None:
        draw.rounded_rectangle(
            rect.box,
            radius=16,
            fill=(6, 6, 8, 190),
            outline=(82, 76, 72, 125),
            width=1,
        )

        line_y = rect.y + 42

        while line_y < rect.bottom - 12:
            draw.line(
                (rect.x + 16, line_y, rect.right - 16, line_y),
                fill=(120, 110, 96, 26),
                width=1,
            )
            line_y += 36

        draw.line(
            (rect.x + 12, rect.y + 1, rect.right - 12, rect.y + 1),
            fill=(255, 244, 220, 18),
            width=1,
        )

    def _draw_xp_document_bar(self, canvas: Image.Image, rect: Rect, ratio: float) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        radius = rect.h // 2

        draw_soft_shadow(
            canvas,
            rect,
            radius,
            offset=(0, 5),
            blur=10,
            color=(0, 0, 0, 135),
        )

        draw.rounded_rectangle(
            rect.box,
            radius=radius,
            fill=self.theme.xp_track,
            outline=(98, 90, 80, 150),
            width=1,
        )

        draw.rounded_rectangle(
            rect.inset(4, 4).box,
            radius=max(1, radius - 4),
            outline=(0, 0, 0, 150),
            width=1,
        )

        ratio = clamp(ratio, 0.0, 1.0)
        fill_width = int(rect.w * ratio)

        if fill_width > 0:
            fill_rect = Rect(rect.x, rect.y, min(fill_width, rect.w), rect.h)

            mask = Image.new("L", fill_rect.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, fill_rect.w, fill_rect.h),
                radius=radius,
                fill=255,
            )

            fill_layer = vertical_gradient(fill_rect.size, self.theme.xp_end, self.theme.xp_start)

            shine = Image.new("RGBA", fill_rect.size, (0, 0, 0, 0))
            shine_draw = ImageDraw.Draw(shine, "RGBA")
            shine_draw.rectangle(
                (0, 0, fill_rect.w, max(1, fill_rect.h // 3)),
                fill=(255, 232, 210, 22),
            )
            fill_layer.alpha_composite(shine)

            canvas.paste(fill_layer, (fill_rect.x, fill_rect.y), mask)

            draw.line(
                (fill_rect.right - 2, fill_rect.y + 4, fill_rect.right - 2, fill_rect.bottom - 4),
                fill=(255, 210, 180, 70),
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
        shadow: ColorA = (0, 0, 0, 140),
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