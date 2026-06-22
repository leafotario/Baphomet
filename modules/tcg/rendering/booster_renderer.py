"""
Motor Gráfico de Renderização de Cartas TCG — Baphomet Bot.

SRE Design: Renderização In-Memory com Supersampling (Anti-Aliasing de alta
fidelidade), Box Model rigoroso para balanceamento de layout e Esqueumorfismo
físico (Recessed Boxes e Drop Shadows) com Glassmorphism.
"""

from __future__ import annotations

import io
import os
import random
import logging
from typing import Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de Design Geométrico
# ---------------------------------------------------------------------------
CARD_W, CARD_H = 600, 840
CARD_RADIUS = 40

COLOR_WHITE = (255, 255, 255, 255)

class BoosterGraphicEngine:
    """
    Motor gráfico para cartas TCG do Baphomet com Box Model estrito.
    """

    def __init__(self, fonts_path: str = "assets/fonts/", scale: int = 3) -> None:
        self.fonts_path = fonts_path
        self.scale = scale
        self._load_fonts()

        # Layout constants
        self.PADDING_TOP = 40 * scale
        self.PADDING_BOTTOM = 40 * scale
        self.ELEMENT_SPACING = 15 * scale

    # ------------------------------------------------------------------
    # Font Management
    # ------------------------------------------------------------------

    def _load_fonts(self) -> None:
        s = self.scale
        pairs = {
            "font_name":         ("Montserrat-Black.ttf",   64 * s),
            "font_rarity":       ("Poppins-Bold.ttf",       20 * s),
            "font_label":        ("Poppins-Bold.ttf",       16 * s),
            "font_stat":         ("Montserrat-Black.ttf",   58 * s),
        }
        for attr, (filename, size) in pairs.items():
            path = os.path.join(self.fonts_path, filename)
            try:
                setattr(self, attr, ImageFont.truetype(path, size=size))
            except OSError:
                logger.warning("Fonte '%s' não encontrada, usando fallback.", path)
                setattr(self, attr, ImageFont.load_default())

    # ------------------------------------------------------------------
    # Utilitários Gráficos Avançados
    # ------------------------------------------------------------------

    def _get_dominant_color(self, img: Image.Image) -> Tuple[int, int, int]:
        img_1x1 = img.resize((1, 1), resample=Image.Resampling.LANCZOS)
        color = img_1x1.getpixel((0, 0))
        if isinstance(color, tuple) and len(color) >= 3:
            return (min(255, int(color[0] * 1.2)), min(255, int(color[1] * 1.2)), min(255, int(color[2] * 1.2)))
        return (120, 60, 240)

    def _create_glass_background(self, w: int, h: int, pfp_img: Image.Image, dom_color: Tuple[int, int, int]) -> Image.Image:
        bg = ImageOps.fit(pfp_img, (w, h), method=Image.Resampling.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=40 * self.scale))
        
        overlay_color = (dom_color[0] // 4, dom_color[1] // 4, dom_color[2] // 4, 210)
        overlay = Image.new("RGBA", (w, h), overlay_color)
        bg_rgba = Image.alpha_composite(bg, overlay)
        return bg_rgba

    @staticmethod
    def _rounded_rect_mask(size: Tuple[int, int], radius: int) -> Image.Image:
        ss = 4
        big = (size[0] * ss, size[1] * ss)
        mask = Image.new("L", big, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, big[0] - 1, big[1] - 1), radius=radius * ss, fill=255)
        return mask.resize(size, Image.Resampling.LANCZOS)

    def _drop_shadow(self, canvas: Image.Image, box: Tuple[int, int, int, int], radius: int, offset: Tuple[int, int] = (0, 10), blur: int = 12, color: Tuple[int, int, int, int] = (0, 0, 0, 150)) -> None:
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(shadow)
        sb = (box[0] + offset[0], box[1] + offset[1], box[2] + offset[0], box[3] + offset[1])
        d.rounded_rectangle(sb, radius=radius, fill=color)
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=blur))
        canvas.alpha_composite(shadow)

    def _draw_centered_text_bbox(self, draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], text: str, font: ImageFont.FreeTypeFont, fill: Tuple[int, int, int, int]) -> None:
        """Centralização matemática absoluta utilizando bbox."""
        left, top, right, bottom = font.getbbox(text)
        text_width = right - left
        text_height = bottom - top
        
        box_w = box[2] - box[0]
        box_h = box[3] - box[1]
        
        box_center_x = box[0] + (box_w / 2)
        box_center_y = box[1] + (box_h / 2)
        
        text_x = box_center_x - (text_width / 2)
        text_y = box_center_y - (text_height / 2) - top
        
        draw.text((text_x, text_y), text, font=font, fill=fill)

    def _draw_text_drop_shadow(self, draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], text: str, font: ImageFont.FreeTypeFont, fill: Tuple[int, int, int, int], s: int) -> None:
        """Desenha a sombra projetada rigorosa (+4px, +4px) e depois o texto centralizado."""
        left, top, right, bottom = font.getbbox(text)
        text_width = right - left
        text_height = bottom - top
        
        box_w = box[2] - box[0]
        box_h = box[3] - box[1]
        
        box_center_x = box[0] + (box_w / 2)
        box_center_y = box[1] + (box_h / 2)
        
        text_x = box_center_x - (text_width / 2)
        text_y = box_center_y - (text_height / 2) - top
        
        # Drop Shadow escuro offsetado
        shadow_offset = 4 * s
        draw.text((text_x + shadow_offset, text_y + shadow_offset), text, font=font, fill=(0, 0, 0, 180))
        
        # Texto principal
        draw.text((text_x, text_y), text, font=font, fill=fill)

    def _draw_recessed_box(self, canvas: Image.Image, draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], radius: int, fill: Tuple[int, int, int, int], s: int) -> None:
        """
        Simula um bloco fisicamente entalhado com luz refletida usando máscaras precisas.
        """
        # 1. Base preta translúcida
        draw.rounded_rectangle(box, radius=radius, fill=fill)
        
        w, h = canvas.size
        # Máscara rigorosa do formato para aplicar as luzes
        clip = Image.new("L", (w, h), 0)
        ImageDraw.Draw(clip).rounded_rectangle(box, radius=radius, fill=255)
        
        # 2. Linha interna de sombra (Top + Left) - simula profundidade afundada
        shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shifted_shadow_box = (box[0] - 2*s, box[1] - 2*s, box[2] - 2*s, box[3] - 2*s)
        ImageDraw.Draw(shadow_layer).rounded_rectangle(
            shifted_shadow_box, radius=radius, outline=(0, 0, 0, 200), width=4*s
        )
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(3*s))
        clipped_shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        clipped_shadow.paste(shadow_layer, (0, 0), clip)
        canvas.alpha_composite(clipped_shadow)
        
        # 3. Linha interna de luz (Bottom + Right) - simula brilho na quina
        light_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shifted_light_box = (box[0] + 2*s, box[1] + 2*s, box[2] + 2*s, box[3] + 2*s)
        ImageDraw.Draw(light_layer).rounded_rectangle(
            shifted_light_box, radius=radius, outline=(255, 255, 255, 100), width=4*s
        )
        light_layer = light_layer.filter(ImageFilter.GaussianBlur(3*s))
        clipped_light = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        clipped_light.paste(light_layer, (0, 0), clip)
        canvas.alpha_composite(clipped_light)

    # ------------------------------------------------------------------
    # Componentes Individuais da Carta (Box Model)
    # ------------------------------------------------------------------

    def _draw_avatar(self, canvas: Image.Image, draw: ImageDraw.ImageDraw, pfp_img: Image.Image, dom_color: Tuple[int, int, int], s: int, y_start: int) -> int:
        w = canvas.size[0]
        pfp_size = 400 * s
        pfp_radius = 40 * s
        pfp_x = (w - pfp_size) // 2
        pfp_y = y_start

        # --- Aro Concêntrico (Skeuomorphism Físico) ---
        frame_pad = 10 * s
        frame_box = (
            pfp_x - frame_pad, pfp_y - frame_pad,
            pfp_x + pfp_size + frame_pad, pfp_y + pfp_size + frame_pad,
        )
        frame_radius = pfp_radius + 6 * s

        self._drop_shadow(
            canvas, frame_box, frame_radius,
            offset=(0, 6 * s), blur=14 * s, color=(0, 0, 0, 200),
        )

        # Anel Escuro (Externo)
        draw.rounded_rectangle(frame_box, radius=frame_radius, fill=(0, 0, 0, 100), outline=(0, 0, 0, 220), width=3 * s)
        # Anel Claro (Interno)
        inner_frame_box = (
            pfp_x - 3*s, pfp_y - 3*s,
            pfp_x + pfp_size + 3*s, pfp_y + pfp_size + 3*s
        )
        draw.rounded_rectangle(inner_frame_box, radius=pfp_radius+2*s, outline=(255, 255, 255, 130), width=2 * s)

        pfp_resized = ImageOps.fit(pfp_img, (pfp_size, pfp_size), method=Image.Resampling.LANCZOS)
        pfp_mask = self._rounded_rect_mask((pfp_size, pfp_size), pfp_radius)
        pfp_resized.putalpha(pfp_mask)
        canvas.alpha_composite(pfp_resized, (pfp_x, pfp_y))

        return pfp_y + pfp_size

    def _draw_rarity_badge(self, canvas: Image.Image, draw: ImageDraw.ImageDraw, rarity_label: str, dom_color: Tuple[int, int, int], s: int, y_start: int) -> int:
        w = canvas.size[0]
        text = rarity_label.upper()

        left, top, right, bottom = self.font_rarity.getbbox(text)
        text_w = right - left
        pill_w = text_w + 60 * s
        pill_h = 42 * s
        pill_x = (w - pill_w) // 2
        pill_y = y_start
        pill_box = (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h)
        pill_radius = pill_h // 2

        self._drop_shadow(canvas, pill_box, pill_radius, offset=(0, 6 * s), blur=10 * s, color=(0, 0, 0, 160))

        # Dark Glassmorphism para a Pílula
        pill_bg_color = (dom_color[0] // 3, dom_color[1] // 3, dom_color[2] // 3, 200)
        
        self._draw_recessed_box(canvas, draw, pill_box, pill_radius, fill=pill_bg_color, s=s)
        
        # Borda muito sutil
        draw.rounded_rectangle(pill_box, radius=pill_radius, outline=(255, 255, 255, 30), width=1*s)

        # Centralização matemática na caixa da pílula
        self._draw_centered_text_bbox(draw, pill_box, text, self.font_rarity, COLOR_WHITE)

        return pill_y + pill_h

    def _draw_member_name(self, canvas: Image.Image, draw: ImageDraw.ImageDraw, name: str, s: int, y_start: int) -> int:
        w = canvas.size[0]
        text = name.upper()
        if len(text) > 13:
            text = text[:12] + "…"

        left, top, right, bottom = self.font_name.getbbox(text)
        text_h = bottom - top
        
        box_y = y_start
        box = (0, box_y, w, box_y + text_h)
        
        self._draw_text_drop_shadow(draw, box, text, self.font_name, COLOR_WHITE, s)
        
        return box_y + text_h

    def _draw_attributes_panel(self, canvas: Image.Image, draw: ImageDraw.ImageDraw, atk: int, def_stat: int, spd: int, dom_color: Tuple[int, int, int], s: int, bottom_anchor_y: int) -> None:
        w, h = canvas.size

        panel_margin_x = 24 * s
        panel_w = w - (panel_margin_x * 2)
        panel_h = 190 * s
        panel_x = panel_margin_x
        # O painel toca no BOTTOM PADDING cravado
        panel_y = bottom_anchor_y - panel_h
        panel_radius = 32 * s
        panel_box = (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h)

        self._drop_shadow(canvas, panel_box, panel_radius, offset=(0, 8 * s), blur=16 * s, color=(0, 0, 0, 180))

        # Base do painel grande
        draw.rounded_rectangle(panel_box, radius=panel_radius, fill=(0, 0, 0, 100), outline=(255, 255, 255, 40), width=1 * s)

        # --- Caixas de Status com Recessed Logic ---
        inner_margin = 20 * s
        spacing = 16 * s
        box_w = (panel_w - (inner_margin * 2) - (spacing * 2)) // 3
        box_h = 130 * s
        box_top = panel_y + (panel_h - box_h) // 2

        stats = [("ATK", str(atk)), ("DEF", str(def_stat)), ("SPD", str(spd))]

        for i, (label, val) in enumerate(stats):
            bx = panel_x + inner_margin + i * (box_w + spacing)
            by = box_top
            b_radius = 20 * s
            stat_box = (bx, by, bx + box_w, by + box_h)

            # Efeito físico entalhado
            self._draw_recessed_box(canvas, draw, stat_box, b_radius, fill=(0, 0, 0, 150), s=s)

            aba_h = 40 * s
            aba_box = (bx, by, bx + box_w, by + aba_h)
            
            # Label
            self._draw_centered_text_bbox(draw, aba_box, label, self.font_label, (230, 230, 230, 255))
            
            # Line
            draw.line([(bx + 8 * s, by + aba_h), (bx + box_w - 8 * s, by + aba_h)], fill=(255, 255, 255, 40), width=1 * s)

            val_box = (bx, by + aba_h, bx + box_w, by + box_h)
            val_color = (min(255, int(dom_color[0]*1.8)), min(255, int(dom_color[1]*1.8)), min(255, int(dom_color[2]*1.8)), 255)
            if (val_color[0] + val_color[1] + val_color[2]) < 250:
                val_color = (180, 180, 220, 255)

            # Valor text sombreado
            self._draw_text_drop_shadow(draw, val_box, val, self.font_stat, val_color, s)

    # ------------------------------------------------------------------
    # Ponto de Entrada Principal
    # ------------------------------------------------------------------

    async def render_card(self, user_name: str, pfp_bytes: bytes, atk: int, def_stat: int, spd: int, rarity_label: str, *, serial_code: str | None = None) -> io.BytesIO:
        s = self.scale
        width, height = CARD_W * s, CARD_H * s

        try:
            pfp_img = Image.open(io.BytesIO(pfp_bytes)).convert("RGBA")
        except Exception as exc:
            logger.error("Erro ao processar imagem base: %s", exc)
            pfp_img = Image.new("RGBA", (500, 500), (88, 42, 160, 255))

        dom_color = self._get_dominant_color(pfp_img)
        bg_glass = self._create_glass_background(width, height, pfp_img, dom_color)
        
        bg_draw = ImageDraw.Draw(bg_glass)
        bg_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=CARD_RADIUS * s, outline=(255, 255, 255, 70), width=3 * s)

        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.alpha_composite(bg_glass)
        draw = ImageDraw.Draw(canvas)

        # ----------------------------------------------------
        # BOX MODEL LAYOUT SYSTEM
        # ----------------------------------------------------
        
        current_y = self.PADDING_TOP
        
        # 1. Avatar (Top)
        avatar_bottom = self._draw_avatar(canvas, draw, pfp_img, dom_color, s, current_y)
        
        # O Nome e o Badge podem ser ancorados a partir do Bottom, para respeitar o Box.
        # Ou podem descer fluidamente do avatar. Fluid flow:
        current_y = avatar_bottom + self.ELEMENT_SPACING
        
        # 2. Badge de Raridade
        rarity_bottom = self._draw_rarity_badge(canvas, draw, rarity_label, dom_color, s, current_y)
        
        current_y = rarity_bottom + self.ELEMENT_SPACING
        
        # 3. Nome
        self._draw_member_name(canvas, draw, user_name, s, current_y)

        # 4. Attributes Panel (ancorado estritamente no bottom)
        bottom_anchor = height - self.PADDING_BOTTOM
        self._draw_attributes_panel(canvas, draw, atk, def_stat, spd, dom_color, s, bottom_anchor)

        # --- Final ---
        final = canvas.resize((CARD_W, CARD_H), Image.Resampling.LANCZOS)

        final_mask = self._rounded_rect_mask((CARD_W, CARD_H), CARD_RADIUS)
        output = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
        output.paste(final, (0, 0), final_mask)

        buffer = io.BytesIO()
        output.save(buffer, format="PNG", compress_level=6)
        buffer.seek(0)

        pfp_img.close()
        bg_glass.close()
        canvas.close()
        final.close()
        output.close()

        return buffer
