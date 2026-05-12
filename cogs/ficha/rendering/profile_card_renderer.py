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
    background_top: ColorA = (5, 3, 5, 255)
    background_bottom: ColorA = (0, 0, 0, 255)

    outer_top: ColorA = (12, 10, 12, 255)
    outer_bottom: ColorA = (2, 1, 2, 255)

    inner_top: ColorA = (19, 16, 17, 255)
    inner_bottom: ColorA = (7, 5, 6, 255)

    panel_top: ColorA = (24, 20, 21, 255)
    panel_bottom: ColorA = (9, 7, 8, 255)

    panel_outline: ColorA = (91, 72, 62, 190)
    panel_highlight: ColorA = (194, 160, 118, 28)
    panel_shadow: ColorA = (0, 0, 0, 160)

    text: ColorA = (239, 231, 214, 255)
    text_soft: ColorA = (198, 184, 164, 255)
    text_muted: ColorA = (132, 111, 99, 255)
    text_dark: ColorA = (7, 5, 6, 255)

    accent_dark: ColorA = (28, 0, 8, 255)
    accent: ColorA = (92, 5, 25, 255)
    accent_light: ColorA = (156, 24, 48, 255)

    bone_dark: ColorA = (68, 55, 47, 255)
    bone_mid: ColorA = (138, 116, 92, 255)
    bone_light: ColorA = (211, 190, 154, 255)

    blood_dark: ColorA = (42, 0, 10, 255)
    blood_mid: ColorA = (96, 4, 24, 255)
    blood_light: ColorA = (155, 23, 45, 255)

    chip_fill: ColorA = (16, 12, 14, 255)
    chip_outline: ColorA = (105, 82, 66, 190)
    chip_highlight: ColorA = (235, 188, 128, 22)

    xp_track: ColorA = (5, 3, 4, 255)
    xp_start: ColorA = (72, 0, 16, 255)
    xp_end: ColorA = (158, 22, 46, 255)

    recessed: ColorA = (6, 4, 5, 255)


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
        draw = ImageDraw.Draw(canvas, "RGBA")
        width, height = layout.canvas

        random.seed(666)

        for _ in range(34):
            x = random.randint(-180, width - 40)
            y = random.randint(-120, height - 40)
            w = random.randint(190, 460)
            h = random.randint(90, 250)
            alpha = random.randint(7, 22)
            color = random.choice(
                [
                    (80, 0, 20, alpha),
                    (55, 40, 30, alpha),
                    (18, 18, 22, alpha),
                ]
            )
            draw.ellipse((x, y, x + w, y + h), fill=color)

        fog = Image.new("RGBA", layout.canvas, (0, 0, 0, 0))
        fog_draw = ImageDraw.Draw(fog, "RGBA")

        for i in range(13):
            x = -250 + i * 145
            y = 120 + int(math.sin(i * 0.85) * 80)
            fog_draw.ellipse(
                (x, y, x + 520, y + 260),
                fill=(105, 86, 72, 8),
            )

        fog = fog.filter(ImageFilter.GaussianBlur(55))
        canvas.alpha_composite(fog)

        self._draw_subtle_cracks(canvas)

        add_noise_overlay(canvas, opacity=7, seed=404, scale=2)

        vignette = Image.new("RGBA", layout.canvas, (0, 0, 0, 0))
        vignette_draw = ImageDraw.Draw(vignette, "RGBA")
        vignette_draw.rectangle((0, 0, width, height), fill=(0, 0, 0, 190))
        vignette_draw.ellipse((-140, -120, width + 140, height + 110), fill=(0, 0, 0, 0))
        vignette = vignette.filter(ImageFilter.GaussianBlur(105))
        canvas.alpha_composite(vignette)

        return canvas

    def _draw_subtle_cracks(self, canvas: Image.Image) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        random.seed(1313)

        for _ in range(28):
            x = random.randint(0, self.layout.canvas[0])
            y = random.randint(0, self.layout.canvas[1])
            steps = random.randint(3, 7)
            points = [(x, y)]

            for _ in range(steps):
                x += random.randint(-24, 28)
                y += random.randint(8, 34)
                points.append((x, y))

            color = random.choice(
                [
                    (0, 0, 0, 55),
                    (90, 70, 58, 24),
                    (120, 12, 32, 18),
                ]
            )

            if len(points) >= 2:
                draw.line(points, fill=color, width=1)

    def _apply_finishing_patina(self, canvas: Image.Image) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        width, height = self.layout.canvas

        random.seed(999)

        for _ in range(520):
            x = random.randint(0, width - 1)
            y = random.randint(0, height - 1)
            alpha = random.randint(8, 26)
            color = random.choice(
                [
                    (255, 235, 190, alpha),
                    (0, 0, 0, alpha + 10),
                    (110, 0, 24, max(4, alpha // 2)),
                ]
            )
            draw.point((x, y), fill=color)

        edge = Image.new("RGBA", self.layout.canvas, (0, 0, 0, 0))
        edge_draw = ImageDraw.Draw(edge, "RGBA")
        edge_draw.rectangle((0, 0, width, height), outline=(0, 0, 0, 190), width=34)
        edge = edge.filter(ImageFilter.GaussianBlur(20))
        canvas.alpha_composite(edge)

        add_noise_overlay(canvas, opacity=4, seed=909, scale=1)

    def _draw_main_frame(self, canvas: Image.Image) -> None:
        layout = self.layout
        theme = self.theme

        draw_soft_shadow(
            canvas,
            layout.outer_card,
            layout.outer_radius,
            offset=(0, 18),
            blur=38,
            color=(0, 0, 0, 230),
            spread=0,
        )

        outer = vertical_gradient(layout.outer_card.size, theme.outer_top, theme.outer_bottom)
        paste_rounded(canvas, outer, layout.outer_card, layout.outer_radius)

        draw = ImageDraw.Draw(canvas, "RGBA")

        draw.rounded_rectangle(
            layout.outer_card.box,
            radius=layout.outer_radius,
            outline=(132, 100, 72, 165),
            width=2,
        )

        draw.rounded_rectangle(
            layout.outer_card.inset(13, 13).box,
            radius=max(1, layout.outer_radius - 12),
            outline=(0, 0, 0, 210),
            width=4,
        )

        inner = vertical_gradient(layout.inner_card.size, theme.inner_top, theme.inner_bottom)
        paste_rounded(canvas, inner, layout.inner_card, layout.inner_radius)

        draw.rounded_rectangle(
            layout.inner_card.box,
            radius=layout.inner_radius,
            outline=(96, 76, 62, 190),
            width=1,
        )

        draw.rounded_rectangle(
            layout.inner_card.inset(8, 8).box,
            radius=max(1, layout.inner_radius - 8),
            outline=(213, 174, 114, 36),
            width=1,
        )

        self._draw_old_paper_edge(draw, layout.inner_card)
        self._draw_gothic_frame(canvas, layout.inner_card)
        self._draw_watermark(canvas)
        self._draw_corner_filigranes(canvas)
        self._draw_document_label(canvas)

    def _draw_old_paper_edge(self, draw: ImageDraw.ImageDraw, rect: Rect) -> None:
        random.seed(4040)

        for _ in range(90):
            side = random.choice(["top", "bottom", "left", "right"])

            if side == "top":
                x = random.randint(rect.x + 20, rect.right - 20)
                y = rect.y + random.randint(1, 10)
            elif side == "bottom":
                x = random.randint(rect.x + 20, rect.right - 20)
                y = rect.bottom - random.randint(1, 10)
            elif side == "left":
                x = rect.x + random.randint(1, 10)
                y = random.randint(rect.y + 20, rect.bottom - 20)
            else:
                x = rect.right - random.randint(1, 10)
                y = random.randint(rect.y + 20, rect.bottom - 20)

            r = random.randint(1, 3)
            draw.ellipse(
                (x - r, y - r, x + r, y + r),
                fill=(0, 0, 0, random.randint(35, 90)),
            )

    def _draw_document_label(self, canvas: Image.Image) -> None:
        rect = self.layout.inner_card
        draw = ImageDraw.Draw(canvas, "RGBA")

        tiny = self.fonts.font(12, "bold")
        left = "REGISTRO DE ALMA · ARQUIVO DO BAPHOMET"
        right = "FICHA DE IDENTIFICAÇÃO"

        draw.text(
            (rect.x + 34, rect.bottom - 30),
            left,
            font=tiny,
            fill=(144, 116, 86, 92),
        )

        right_w = text_width(draw, right, tiny)
        draw.text(
            (rect.right - 34 - right_w, rect.bottom - 30),
            right,
            font=tiny,
            fill=(144, 116, 86, 92),
        )

    def _draw_gothic_frame(self, canvas: Image.Image, rect: Rect) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")

        gold = (154, 119, 78, 78)
        red = (128, 7, 30, 82)
        black = (0, 0, 0, 120)

        inset = 22
        left = rect.x + inset
        right = rect.right - inset
        top = rect.y + inset
        bottom = rect.bottom - inset

        draw.line((left + 55, top, right - 55, top), fill=gold, width=1)
        draw.line((left + 55, bottom, right - 55, bottom), fill=gold, width=1)
        draw.line((left, top + 55, left, bottom - 55), fill=gold, width=1)
        draw.line((right, top + 55, right, bottom - 55), fill=gold, width=1)

        for sx, sy, flip_x, flip_y in (
            (left, top, 1, 1),
            (right, top, -1, 1),
            (left, bottom, 1, -1),
            (right, bottom, -1, -1),
        ):
            self._draw_filigree_corner(draw, sx, sy, flip_x, flip_y, gold, red, black)

    def _draw_filigree_corner(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        flip_x: int,
        flip_y: int,
        gold: ColorA,
        red: ColorA,
        black: ColorA,
    ) -> None:
        p1 = (x, y)
        p2 = (x + flip_x * 44, y)
        p3 = (x, y + flip_y * 44)
        p4 = (x + flip_x * 34, y + flip_y * 34)

        draw.line((p1, p2), fill=gold, width=2)
        draw.line((p1, p3), fill=gold, width=2)
        draw.line((p1, p4), fill=red, width=1)

        box1 = (
            min(x, x + flip_x * 70),
            min(y, y + flip_y * 70),
            max(x, x + flip_x * 70),
            max(y, y + flip_y * 70),
        )

        start = 270 if flip_x == 1 and flip_y == 1 else 180 if flip_x == -1 and flip_y == 1 else 0 if flip_x == 1 else 90
        end = start + 90

        draw.arc(box1, start=start, end=end, fill=gold, width=1)

        for r in (5, 9, 13):
            draw.ellipse(
                (
                    x + flip_x * 34 - r,
                    y + flip_y * 34 - r,
                    x + flip_x * 34 + r,
                    y + flip_y * 34 + r,
                ),
                outline=black if r != 9 else red,
                width=1,
            )

    def _draw_corner_filigranes(self, canvas: Image.Image) -> None:
        rect = self.layout.inner_card
        draw = ImageDraw.Draw(canvas, "RGBA")

        color = (180, 138, 88, 56)
        blood = (130, 7, 32, 70)

        ornaments = [
            (rect.x + 44, rect.y + 44, 1, 1),
            (rect.right - 44, rect.y + 44, -1, 1),
            (rect.x + 44, rect.bottom - 44, 1, -1),
            (rect.right - 44, rect.bottom - 44, -1, -1),
        ]

        for x, y, sx, sy in ornaments:
            draw.line((x, y, x + sx * 26, y + sy * 8), fill=color, width=1)
            draw.line((x, y, x + sx * 8, y + sy * 26), fill=color, width=1)
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=blood)

    def _draw_watermark(self, canvas: Image.Image) -> None:
        rect = self.layout.inner_card
        layer = Image.new("RGBA", self.layout.canvas, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")

        cx, cy = rect.center

        self._draw_horned_sigil(
            draw,
            cx,
            cy - 22,
            scale=2.4,
            fill=(0, 0, 0, 76),
            outline=(122, 10, 34, 32),
        )

        word = "BAPHOMET"
        font = self.fonts.font(136, "display")
        word_w = text_width(draw, word, font)

        draw.text(
            (cx - word_w // 2, cy + 125),
            word,
            font=font,
            fill=(0, 0, 0, 54),
        )

        layer = layer.filter(ImageFilter.GaussianBlur(0.4))
        canvas.alpha_composite(layer)

    def _draw_horned_sigil(
        self,
        draw: ImageDraw.ImageDraw,
        cx: int,
        cy: int,
        *,
        scale: float,
        fill: ColorA,
        outline: ColorA,
    ) -> None:
        r = int(70 * scale)

        points: list[tuple[int, int]] = []

        for i in range(5):
            angle = -math.pi / 2 + i * (2 * math.pi / 5)
            points.append((int(cx + math.cos(angle) * r), int(cy + math.sin(angle) * r)))

        order = [0, 2, 4, 1, 3, 0]

        for a, b in zip(order, order[1:]):
            draw.line(
                (points[a][0], points[a][1], points[b][0], points[b][1]),
                fill=fill,
                width=max(1, int(4 * scale)),
            )

        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=outline, width=max(1, int(2 * scale)))

        horn_w = int(46 * scale)
        horn_h = int(78 * scale)

        draw.arc(
            (cx - r - horn_w, cy - r - horn_h // 2, cx - r + horn_w, cy + r // 2),
            start=210,
            end=340,
            fill=fill,
            width=max(1, int(5 * scale)),
        )
        draw.arc(
            (cx + r - horn_w, cy - r - horn_h // 2, cx + r + horn_w, cy + r // 2),
            start=200,
            end=330,
            fill=fill,
            width=max(1, int(5 * scale)),
        )

        draw.line((cx, cy - r, cx, cy + r), fill=outline, width=max(1, int(1.5 * scale)))

    def _draw_panel(self, canvas: Image.Image, rect: Rect, *, radius: int | None = None) -> None:
        theme = self.theme
        radius = radius or self.layout.panel_radius

        draw_soft_shadow(
            canvas,
            rect,
            radius,
            offset=(0, 9),
            blur=19,
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

        draw.rounded_rectangle(
            rect.inset(6, 6).box,
            radius=max(1, radius - 6),
            outline=(235, 190, 128, 20),
            width=1,
        )

        draw.line(
            (rect.x + 22, rect.y + 1, rect.right - 22, rect.y + 1),
            fill=theme.panel_highlight,
            width=1,
        )

        self._draw_panel_filigree(draw, rect, radius)
        self._draw_panel_stains(draw, rect)

    def _draw_panel_filigree(self, draw: ImageDraw.ImageDraw, rect: Rect, radius: int) -> None:
        color = (158, 120, 78, 58)
        blood = (126, 4, 28, 72)

        anchors = (
            (rect.x + radius, rect.y + 12, 1, 1),
            (rect.right - radius, rect.y + 12, -1, 1),
            (rect.x + radius, rect.bottom - 12, 1, -1),
            (rect.right - radius, rect.bottom - 12, -1, -1),
        )

        for x, y, sx, sy in anchors:
            draw.line((x, y, x + sx * 18, y), fill=color, width=1)
            draw.line((x, y, x, y + sy * 18), fill=color, width=1)
            draw.arc(
                (
                    min(x, x + sx * 28),
                    min(y, y + sy * 28),
                    max(x, x + sx * 28),
                    max(y, y + sy * 28),
                ),
                start=0,
                end=360,
                fill=(0, 0, 0, 44),
                width=1,
            )
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=blood)

    def _draw_panel_stains(self, draw: ImageDraw.ImageDraw, rect: Rect) -> None:
        random.seed(rect.x * 13 + rect.y * 17)

        for _ in range(13):
            x = random.randint(rect.x + 16, rect.right - 16)
            y = random.randint(rect.y + 14, rect.bottom - 14)
            r = random.randint(4, 18)
            fill = random.choice(
                [
                    (0, 0, 0, random.randint(18, 42)),
                    (80, 0, 18, random.randint(10, 26)),
                    (120, 92, 62, random.randint(6, 15)),
                ]
            )
            draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)

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
            shadow=(0, 0, 0, 180),
            offset=(0, 2),
        )

        title_w = text_width(draw, title, font)
        rule_x = x + title_w + 16

        if rule_x < rect.right - 30:
            line_y = y + 17
            draw.line(
                (rule_x, line_y, rect.right - 30, line_y),
                fill=(128, 98, 68, 82),
                width=1,
            )
            draw.ellipse(
                (rect.right - 33, line_y - 3, rect.right - 27, line_y + 3),
                fill=(116, 6, 28, 88),
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

        halo_rect = Rect(avatar_rect.x - 24, avatar_rect.y - 24, avatar_rect.w + 48, avatar_rect.h + 48)

        draw_soft_shadow(
            canvas,
            halo_rect,
            halo_rect.w // 2,
            offset=(0, 13),
            blur=28,
            color=(0, 0, 0, 235),
        )

        draw.ellipse(halo_rect.box, fill=(4, 2, 3, 255), outline=(128, 98, 68, 160), width=3)
        draw.ellipse(
            (halo_rect.x + 12, halo_rect.y + 12, halo_rect.right - 12, halo_rect.bottom - 12),
            outline=(104, 5, 26, 190),
            width=4,
        )
        draw.ellipse(
            (halo_rect.x + 25, halo_rect.y + 25, halo_rect.right - 25, halo_rect.bottom - 25),
            outline=(0, 0, 0, 220),
            width=6,
        )

        self._draw_candle_marks(draw, halo_rect)

        source = load_rgba_from_bytes(profile.avatar_bytes)

        if source is None:
            avatar = create_avatar_placeholder(
                avatar_size,
                initials=self._initials(profile.display_name or profile.username),
                font=self.fonts.font(78, "display"),
                fill_top=(30, 24, 25, 255),
                fill_bottom=(5, 3, 4, 255),
                accent=theme.accent,
                text_fill=theme.text,
            )
        else:
            avatar = circular_crop(source, avatar_size)

        canvas.paste(avatar, (avatar_rect.x, avatar_rect.y), avatar)

        draw.ellipse(avatar_rect.box, outline=(222, 190, 138, 96), width=2)
        draw.ellipse(
            (avatar_rect.x + 7, avatar_rect.y + 7, avatar_rect.right - 7, avatar_rect.bottom - 7),
            outline=(0, 0, 0, 175),
            width=2,
        )

    def _draw_candle_marks(self, draw: ImageDraw.ImageDraw, rect: Rect) -> None:
        cx, cy = rect.center
        radius = rect.w // 2 - 12

        gold = (172, 128, 82, 68)
        red = (126, 4, 28, 70)

        for i in range(12):
            angle = -math.pi / 2 + i * (2 * math.pi / 12)
            x1 = int(cx + math.cos(angle) * (radius - 13))
            y1 = int(cy + math.sin(angle) * (radius - 13))
            x2 = int(cx + math.cos(angle) * radius)
            y2 = int(cy + math.sin(angle) * radius)
            draw.line((x1, y1, x2, y2), fill=gold, width=1)

            if i % 3 == 0:
                dot_x = int(cx + math.cos(angle) * (radius - 27))
                dot_y = int(cy + math.sin(angle) * (radius - 27))
                draw.ellipse((dot_x - 3, dot_y - 3, dot_x + 3, dot_y + 3), fill=red)

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
                shadow=(0, 0, 0, 100),
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
                fill=(6, 4, 5, 210),
                outline=(86, 68, 54, 135),
                width=1,
            )

            draw.line(
                (field_rect.x + 14, field_rect.y + 1, field_rect.right - 14, field_rect.y + 1),
                fill=(230, 185, 120, 18),
                width=1,
            )

            draw_text_shadow(
                draw,
                (x, value_y),
                display_value,
                font=value_font,
                fill=self.theme.text if label == "Nome" else self.theme.text_soft,
                shadow=(0, 0, 0, 150),
                offset=(0, 1),
            )

            divider_y = value_y + text_height(draw, display_value or "Ag", value_font) + 16

            if label != "Rank":
                draw.line(
                    (x, divider_y, x + value_width, divider_y),
                    fill=(118, 88, 62, 58),
                    width=1,
                )

            y += 88

    def _draw_identity_stamp(self, draw: ImageDraw.ImageDraw, rect: Rect) -> None:
        stamp_font = self.fonts.font(18, "bold")
        text = "SELADO"
        text_w = text_width(draw, text, stamp_font)

        stamp = Rect(rect.right - text_w - 62, rect.y + 20, text_w + 36, 32)

        draw.rounded_rectangle(
            stamp.box,
            radius=7,
            outline=(132, 12, 34, 95),
            width=2,
        )

        draw.text(
            (stamp.x + 18, stamp.y + 6),
            text,
            font=stamp_font,
            fill=(150, 20, 42, 105),
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

        self._draw_worn_text_area(draw, body_rect)

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
                shadow=(0, 0, 0, 130),
                offset=(0, 1),
            )

            y += line_height

    def _draw_worn_text_area(self, draw: ImageDraw.ImageDraw, rect: Rect) -> None:
        draw.rounded_rectangle(
            rect.box,
            radius=16,
            fill=(6, 4, 5, 205),
            outline=(86, 68, 54, 125),
            width=1,
        )

        random.seed(rect.x + rect.y + rect.w)

        for _ in range(18):
            x = random.randint(rect.x + 10, rect.right - 10)
            y = random.randint(rect.y + 8, rect.bottom - 8)
            r = random.randint(2, 9)
            draw.ellipse(
                (x - r, y - r, x + r, y + r),
                fill=random.choice(
                    [
                        (0, 0, 0, 28),
                        (96, 4, 24, 14),
                        (136, 104, 68, 11),
                    ]
                ),
            )

        draw.line(
            (rect.x + 16, rect.y + 1, rect.right - 16, rect.y + 1),
            fill=(228, 184, 118, 18),
            width=1,
        )

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
            color=(0, 0, 0, 175),
        )

        draw.rounded_rectangle(
            slot.box,
            radius=20,
            fill=(5, 3, 4, 255),
            outline=(118, 90, 64, 150),
            width=1,
        )

        draw.rounded_rectangle(
            slot.inset(8, 8).box,
            radius=15,
            outline=(122, 6, 30, 105),
            width=1,
        )

        self._draw_blood_drips(draw, slot)

        source = load_rgba_from_bytes(profile.badge_image_bytes)
        image_slot = slot.inset(16, 12)

        if source is None:
            badge = create_badge_placeholder(
                (150, 118),
                fill_top=(26, 20, 21, 255),
                fill_bottom=(5, 3, 4, 255),
                accent=self.theme.accent_light,
                line=(142, 111, 76, 170),
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
            fill=(6, 4, 5, 205),
            outline=(86, 68, 54, 125),
            width=1,
        )

        self._draw_centered_text(draw, label_rect, label, label_font, self.theme.text_soft)

    def _draw_blood_drips(self, draw: ImageDraw.ImageDraw, rect: Rect) -> None:
        random.seed(rect.x * 31 + rect.y)

        for _ in range(6):
            x = random.randint(rect.x + 18, rect.right - 18)
            top = rect.y + random.randint(6, 16)
            length = random.randint(12, 38)
            width = random.randint(2, 4)

            draw.line(
                (x, top, x, top + length),
                fill=(86, 0, 18, 95),
                width=width,
            )
            draw.ellipse(
                (x - width, top + length - 2, x + width, top + length + width * 2),
                fill=(104, 2, 24, 100),
            )

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
            shadow=(0, 0, 0, 155),
            offset=(0, 2),
        )

        draw.line(
            (rect.x + 30, rect.y + 143, rect.right - 30, rect.y + 143),
            fill=(124, 94, 66, 82),
            width=1,
        )

        mult = self._format_multiplier(profile.bonds_multiplier)
        badge_rect = Rect(rect.x + 30, rect.y + 158, rect.w - 60, 32)

        draw.rounded_rectangle(
            badge_rect.box,
            radius=16,
            fill=(6, 4, 5, 225),
            outline=(104, 80, 60, 135),
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
            fill=(6, 4, 5, 225),
            outline=(104, 80, 60, 135),
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
        self._draw_xp_blood_bar(canvas, bar_rect, percent / 100)

        xp_label = f"{current:,} / {required:,} XP".replace(",", ".")
        xp_font = self.fonts.font(20, "bold")
        xp_label = truncate_text(draw, xp_label, xp_font, bar_rect.w - 36)

        self._draw_centered_text(
            draw,
            bar_rect,
            xp_label,
            xp_font,
            self.theme.text,
            shadow=(0, 0, 0, 195),
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
            shadow=(0, 0, 0, 125),
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
            shadow=(0, 0, 0, 125),
            offset=(0, 1),
        )

    def _draw_xp_blood_bar(self, canvas: Image.Image, rect: Rect, ratio: float) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        radius = rect.h // 2

        draw_soft_shadow(
            canvas,
            rect,
            radius,
            offset=(0, 5),
            blur=10,
            color=(0, 0, 0, 155),
        )

        draw.rounded_rectangle(
            rect.box,
            radius=radius,
            fill=self.theme.xp_track,
            outline=(112, 84, 60, 145),
            width=1,
        )

        draw.rounded_rectangle(
            rect.inset(4, 4).box,
            radius=max(1, radius - 4),
            outline=(0, 0, 0, 160),
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

        fill_layer = vertical_gradient(fill_rect.size, self.theme.xp_end, self.theme.xp_start)

        blood = Image.new("RGBA", fill_rect.size, (0, 0, 0, 0))
        blood_draw = ImageDraw.Draw(blood, "RGBA")

        random.seed(fill_rect.w + fill_rect.h)

        for _ in range(18):
            x = random.randint(0, max(1, fill_rect.w - 1))
            y = random.randint(2, fill_rect.h - 2)
            r = random.randint(2, 8)
            blood_draw.ellipse(
                (x - r, y - r, x + r, y + r),
                fill=(255, 88, 88, random.randint(14, 34)),
            )

        blood_draw.rectangle(
            (0, 0, fill_rect.w, max(1, fill_rect.h // 3)),
            fill=(255, 210, 170, 20),
        )

        fill_layer.alpha_composite(blood)

        canvas.paste(fill_layer, (fill_rect.x, fill_rect.y), mask)

        draw.line(
            (fill_rect.right - 2, fill_rect.y + 4, fill_rect.right - 2, fill_rect.bottom - 4),
            fill=(255, 190, 150, 65),
            width=1,
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
            color=(0, 0, 0, 120),
        )

        draw = ImageDraw.Draw(canvas, "RGBA")

        fill = (10, 7, 8, 230) if muted else self.theme.chip_fill
        outline = (82, 64, 52, 135) if muted else self.theme.chip_outline

        draw.rounded_rectangle(
            rect.box,
            radius=rect.h // 2,
            fill=fill,
            outline=outline,
            width=1,
        )

        draw.ellipse(
            (rect.x + 13, rect.y + rect.h // 2 - 3, rect.x + 19, rect.y + rect.h // 2 + 3),
            fill=(132, 8, 32, 110),
        )

        draw.line(
            (rect.x + 25, rect.y + 1, rect.right - 18, rect.y + 1),
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
            shadow=(0, 0, 0, 145),
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
        shadow: ColorA = (0, 0, 0, 145),
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