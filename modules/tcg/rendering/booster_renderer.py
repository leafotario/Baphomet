"""
Motor Gráfico de Renderização de Cartas TCG — Baphomet Bot.

SRE Design: Renderização In-Memory com Supersampling (Anti-Aliasing de alta
fidelidade), Sombras Projetadas Reais, Inner Shadows direcionais e Gradientes
dinâmicos para estética Glassmorphism Avançado.

Toda manipulação gráfica ocorre em RAM via Pillow (PIL). Nenhum I/O de disco é
gerado durante a renderização — o resultado final é entregue como ``io.BytesIO``.
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
    Motor gráfico para cartas TCG do Baphomet.

    Utiliza Supersampling (``scale`` ≥ 2) para garantir anti-aliasing perfeito
    em todas as formas arredondadas. O canvas é construído em resolução
    ``CARD_W * scale × CARD_H * scale`` e downsampled com LANCZOS no final.

    Parameters
    ----------
    fonts_path : str
        Caminho para o diretório contendo as fontes TTF/OTF.
    scale : int
        Fator de supersampling. Valores ≥ 2 garantem bordas suaves.
    """

    def __init__(self, fonts_path: str = "assets/fonts/", scale: int = 3) -> None:
        self.fonts_path = fonts_path
        self.scale = scale
        self._load_fonts()

    # ------------------------------------------------------------------
    # Font Management
    # ------------------------------------------------------------------

    def _load_fonts(self) -> None:
        """Carrega todas as fontes necessárias com escalas massivas."""
        s = self.scale
        pairs = {
            "font_name":         ("Montserrat-Black.ttf",   64 * s), # Massivo
            "font_rarity":       ("Poppins-Bold.ttf",       20 * s),
            "font_label":        ("Poppins-Bold.ttf",       16 * s),
            "font_stat":         ("Montserrat-Black.ttf",   58 * s), # Massivo
        }
        for attr, (filename, size) in pairs.items():
            path = os.path.join(self.fonts_path, filename)
            try:
                setattr(self, attr, ImageFont.truetype(path, size=size))
            except OSError:
                logger.warning("Fonte '%s' não encontrada, usando fallback.", path)
                setattr(self, attr, ImageFont.load_default())

    # ------------------------------------------------------------------
    # Utilitários de Cor e Glassmorphism
    # ------------------------------------------------------------------

    def _get_dominant_color(self, img: Image.Image) -> Tuple[int, int, int]:
        """Extrai a cor média redimensionando a imagem para 1x1."""
        img_1x1 = img.resize((1, 1), resample=Image.Resampling.LANCZOS)
        color = img_1x1.getpixel((0, 0))
        if isinstance(color, tuple) and len(color) >= 3:
            # Amplificar a cor para evitar que fique muito escura/opaca
            return (min(255, int(color[0] * 1.2)), min(255, int(color[1] * 1.2)), min(255, int(color[2] * 1.2)))
        return (120, 60, 240)  # Roxo Baphomet fallback

    def _create_glass_background(self, w: int, h: int, pfp_img: Image.Image, dom_color: Tuple[int, int, int]) -> Image.Image:
        """Gera fundo imersivo usando a PFP distorcida e borrada (Glassmorphism)."""
        bg = ImageOps.fit(pfp_img, (w, h), method=Image.Resampling.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=40 * self.scale))
        
        # Overlay super escuro tingido com a cor dominante para garantir contraste
        overlay_color = (dom_color[0] // 4, dom_color[1] // 4, dom_color[2] // 4, 210)
        overlay = Image.new("RGBA", (w, h), overlay_color)
        
        bg_rgba = Image.alpha_composite(bg, overlay)
        return bg_rgba

    # ------------------------------------------------------------------
    # Primitivas Gráficas Esqueumórficas
    # ------------------------------------------------------------------

    @staticmethod
    def _rounded_rect_mask(size: Tuple[int, int], radius: int) -> Image.Image:
        """Cria uma máscara L com retângulo arredondado perfeito (supersampled 4×)."""
        ss = 4
        big = (size[0] * ss, size[1] * ss)
        mask = Image.new("L", big, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, big[0] - 1, big[1] - 1), radius=radius * ss, fill=255,
        )
        return mask.resize(size, Image.Resampling.LANCZOS)

    def _drop_shadow(
        self,
        canvas: Image.Image,
        box: Tuple[int, int, int, int],
        radius: int,
        *,
        offset: Tuple[int, int] = (0, 10),
        blur: int = 12,
        color: Tuple[int, int, int, int] = (0, 0, 0, 150),
    ) -> None:
        """Projeta uma sombra real abaixo de um retângulo arredondado."""
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(shadow)
        sb = (box[0] + offset[0], box[1] + offset[1],
              box[2] + offset[0], box[3] + offset[1])
        d.rounded_rectangle(sb, radius=radius, fill=color)
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=blur))
        canvas.alpha_composite(shadow)

    def _inner_shadow(
        self,
        canvas: Image.Image,
        box: Tuple[int, int, int, int],
        radius: int,
        *,
        dark: Tuple[int, int, int, int] = (0, 0, 0, 100),
        light: Tuple[int, int, int, int] = (255, 255, 255, 120),
        blur: int = 6,
        offset: int = 5,
    ) -> None:
        """
        Inner shadow bidirecional — sombra escura no top-left e brilho
        no bottom-right — simulando profundidade "afundada".
        """
        w, h = canvas.size

        clip = Image.new("L", (w, h), 0)
        ImageDraw.Draw(clip).rounded_rectangle(box, radius=radius, fill=255)

        shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shifted_box = (box[0] - offset, box[1] - offset,
                       box[2] - offset, box[3] - offset)
        ImageDraw.Draw(shadow_layer).rounded_rectangle(
            shifted_box, radius=radius, outline=dark, width=blur * 2,
        )
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(blur))
        clipped = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        clipped.paste(shadow_layer, (0, 0), clip)
        canvas.alpha_composite(clipped)

        glow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shifted_box2 = (box[0] + offset, box[1] + offset,
                        box[2] + offset, box[3] + offset)
        ImageDraw.Draw(glow_layer).rounded_rectangle(
            shifted_box2, radius=radius, outline=light, width=blur * 2,
        )
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(blur))
        clipped2 = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        clipped2.paste(glow_layer, (0, 0), clip)
        canvas.alpha_composite(clipped2)

    def _vertical_gradient(
        self,
        size: Tuple[int, int],
        top_color: Tuple[int, int, int, int],
        bottom_color: Tuple[int, int, int, int],
    ) -> Image.Image:
        """Gera um gradiente vertical RGBA suave."""
        w, h = size
        gradient = Image.new("RGBA", (1, h), (0, 0, 0, 0))
        for y in range(h):
            t = y / max(h - 1, 1)
            r = int(top_color[0] + (bottom_color[0] - top_color[0]) * t)
            g = int(top_color[1] + (bottom_color[1] - top_color[1]) * t)
            b = int(top_color[2] + (bottom_color[2] - top_color[2]) * t)
            a = int(top_color[3] + (bottom_color[3] - top_color[3]) * t)
            gradient.putpixel((0, y), (r, g, b, a))
        return gradient.resize(size, Image.Resampling.BILINEAR)

    # ------------------------------------------------------------------
    # Componentes Individuais da Carta
    # ------------------------------------------------------------------

    def _draw_avatar(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        pfp_img: Image.Image,
        dom_color: Tuple[int, int, int],
        s: int,
    ) -> int:
        """PFP quadrada massiva com cantos arredondados e borda translúcida colorida."""
        w = canvas.size[0]

        pfp_size = 400 * s  # Tamanho massivo
        pfp_radius = 40 * s
        pfp_x = (w - pfp_size) // 2
        pfp_y = 50 * s

        # --- Moldura externa ---
        frame_pad = 8 * s
        frame_box = (
            pfp_x - frame_pad, pfp_y - frame_pad,
            pfp_x + pfp_size + frame_pad, pfp_y + pfp_size + frame_pad,
        )
        frame_radius = pfp_radius + 6 * s

        self._drop_shadow(
            canvas, frame_box, frame_radius,
            offset=(0, 6 * s), blur=14 * s,
            color=(0, 0, 0, 180),
        )

        # Moldura tingida com a cor predominante
        draw.rounded_rectangle(
            frame_box, radius=frame_radius,
            fill=(dom_color[0], dom_color[1], dom_color[2], 60), 
            outline=(255, 255, 255, 90), width=2 * s,
        )

        # --- Carregar e aplicar PFP ---
        pfp_resized = ImageOps.fit(
            pfp_img, (pfp_size, pfp_size), method=Image.Resampling.LANCZOS,
        )
        pfp_mask = self._rounded_rect_mask((pfp_size, pfp_size), pfp_radius)
        pfp_resized.putalpha(pfp_mask)
        canvas.alpha_composite(pfp_resized, (pfp_x, pfp_y))

        # --- Inner shadow ---
        self._inner_shadow(
            canvas,
            (pfp_x, pfp_y, pfp_x + pfp_size, pfp_y + pfp_size),
            pfp_radius,
            dark=(0, 0, 0, 160),
            light=(255, 255, 255, 80),
            blur=8 * s,
            offset=6 * s,
        )

        return pfp_y + pfp_size

    def _draw_rarity_badge(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        rarity_label: str,
        dom_color: Tuple[int, int, int],
        s: int,
        y_start: int,
    ) -> int:
        """Pílula de raridade baseada na paleta dinâmica extraída."""
        w = canvas.size[0]
        text = rarity_label.upper()

        bbox = self.font_rarity.getbbox(text)
        text_w = bbox[2] - bbox[0]
        pill_w = text_w + 60 * s
        pill_h = 42 * s
        pill_x = (w - pill_w) // 2
        pill_y = y_start + 40 * s
        pill_box = (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h)
        pill_radius = pill_h // 2

        self._drop_shadow(
            canvas, pill_box, pill_radius,
            offset=(0, 6 * s), blur=10 * s,
            color=(0, 0, 0, 160),
        )

        # Gradiente derivado da cor dominante (claro no topo, escuro em baixo)
        top_color = (min(255, int(dom_color[0]*1.3)), min(255, int(dom_color[1]*1.3)), min(255, int(dom_color[2]*1.3)), 255)
        bot_color = (int(dom_color[0]*0.6), int(dom_color[1]*0.6), int(dom_color[2]*0.6), 255)

        gradient = self._vertical_gradient((pill_w, pill_h), top_color, bot_color)
        mask = self._rounded_rect_mask((pill_w, pill_h), pill_radius)
        pill_layer = Image.new("RGBA", (pill_w, pill_h), (0, 0, 0, 0))
        pill_layer.paste(gradient, (0, 0), mask)
        canvas.alpha_composite(pill_layer, (pill_x, pill_y))

        highlight = Image.new("RGBA", (pill_w, pill_h // 2), (255, 255, 255, 50))
        h_mask = self._rounded_rect_mask((pill_w, pill_h // 2), pill_radius)
        highlight.putalpha(h_mask)
        canvas.alpha_composite(highlight, (pill_x, pill_y))

        draw.text(
            (pill_x + pill_w // 2, pill_y + pill_h // 2),
            text, font=self.font_rarity, fill=COLOR_WHITE, anchor="mm",
        )

        return pill_y + pill_h

    def _draw_member_name(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        name: str,
        s: int,
        y_start: int,
    ) -> int:
        """Nome massivo do membro com sombra dura."""
        w = canvas.size[0]
        text = name.upper()

        if len(text) > 13:
            text = text[:12] + "…"

        cx = w // 2
        ny = y_start + 45 * s

        draw.text(
            (cx, ny + 4 * s), text,
            font=self.font_name, fill=(0, 0, 0, 180), anchor="mm",
        )
        draw.text(
            (cx, ny), text,
            font=self.font_name, fill=COLOR_WHITE, anchor="mm",
        )

        bbox = self.font_name.getbbox(text)
        return ny + (bbox[3] - bbox[1]) // 2

    def _draw_attributes_panel(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        atk: int,
        def_stat: int,
        spd: int,
        dom_color: Tuple[int, int, int],
        s: int,
    ) -> None:
        """Painel massivo transparente com efeito de vidro opaco e caixas translúcidas."""
        w, h = canvas.size

        # Dimensões maximizadas
        panel_margin_x = 24 * s
        panel_w = w - (panel_margin_x * 2)
        panel_h = 190 * s
        panel_x = panel_margin_x
        panel_y = h - panel_h - 24 * s
        panel_radius = 32 * s
        panel_box = (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h)

        self._drop_shadow(
            canvas, panel_box, panel_radius,
            offset=(0, 8 * s), blur=16 * s,
            color=(0, 0, 0, 160),
        )

        # Painel base translúcido
        draw.rounded_rectangle(
            panel_box, radius=panel_radius,
            fill=(0, 0, 0, 120), outline=(255, 255, 255, 60), width=2 * s,
        )

        # Luz no topo
        self._inner_shadow(
            canvas, panel_box, panel_radius,
            dark=(0, 0, 0, 0), light=(255, 255, 255, 50),
            blur=6 * s, offset=4 * s,
        )

        # --- Caixas de Status ---
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

            # Fundo translucido escuro das caixas internas
            draw.rounded_rectangle(
                stat_box, radius=b_radius,
                fill=(0, 0, 0, 160),
                outline=(255, 255, 255, 40), width=1 * s,
            )

            # Inner shadow reforçando o buraco negro das caixas
            self._inner_shadow(
                canvas, stat_box, b_radius,
                dark=(0, 0, 0, 200),
                light=(255, 255, 255, 40),
                blur=6 * s, offset=4 * s,
            )

            # Header cinza translúcido
            aba_h = 40 * s
            aba_clip = Image.new("L", (w, h), 0)
            ImageDraw.Draw(aba_clip).rounded_rectangle(stat_box, radius=b_radius, fill=255)
            ImageDraw.Draw(aba_clip).rectangle(
                (bx, by + aba_h, bx + box_w, by + box_h), fill=0,
            )

            aba_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            # Aba levemente colorida pela paleta dominante
            ImageDraw.Draw(aba_layer).rounded_rectangle(
                stat_box, radius=b_radius, fill=(dom_color[0], dom_color[1], dom_color[2], 90),
            )
            clipped_aba = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            clipped_aba.paste(aba_layer, (0, 0), aba_clip)
            canvas.alpha_composite(clipped_aba)

            draw.line(
                [(bx + 4 * s, by + aba_h), (bx + box_w - 4 * s, by + aba_h)],
                fill=(255, 255, 255, 60), width=1 * s,
            )

            draw.text(
                (bx + box_w // 2, by + aba_h // 2 + 1 * s),
                label, font=self.font_label,
                fill=(230, 230, 230, 255), anchor="mm",
            )

            val_area_top = by + aba_h
            val_area_h = box_h - aba_h
            val_cy = val_area_top + val_area_h // 2

            # Valor text
            draw.text(
                (bx + box_w // 2, val_cy + 3 * s), val,
                font=self.font_stat, fill=(0, 0, 0, 200), anchor="mm",
            )
            
            # Cor do texto do número recebe a cor dominante, mas clarificada pra brilhar no escuro
            val_color = (min(255, int(dom_color[0]*1.8)), min(255, int(dom_color[1]*1.8)), min(255, int(dom_color[2]*1.8)), 255)
            
            # Em caso da cor ficar muito escura (quase preta), forçar clareamento mínimo
            if (val_color[0] + val_color[1] + val_color[2]) < 250:
                val_color = (180, 180, 220, 255)

            draw.text(
                (bx + box_w // 2, val_cy), val,
                font=self.font_stat, fill=val_color, anchor="mm",
            )

    # ------------------------------------------------------------------
    # Ponto de Entrada Principal
    # ------------------------------------------------------------------

    async def render_card(
        self,
        user_name: str,
        pfp_bytes: bytes,
        atk: int,
        def_stat: int,
        spd: int,
        rarity_label: str,
        *,
        serial_code: str | None = None,
    ) -> io.BytesIO:
        """Renderiza uma carta TCG completa com escalas massivas e Glassmorphism."""
        s = self.scale
        width, height = CARD_W * s, CARD_H * s

        try:
            pfp_img = Image.open(io.BytesIO(pfp_bytes)).convert("RGBA")
        except Exception as exc:
            logger.error("Erro ao processar imagem base: %s", exc)
            pfp_img = Image.new("RGBA", (500, 500), (88, 42, 160, 255))

        # Extração da cor predominante
        dom_color = self._get_dominant_color(pfp_img)

        # 1. Base da carta (Fundo dinâmico com Glassmorphism)
        bg_glass = self._create_glass_background(width, height, pfp_img, dom_color)
        
        # Borda de luz final da carta inteira
        bg_draw = ImageDraw.Draw(bg_glass)
        bg_draw.rounded_rectangle(
            (0, 0, width - 1, height - 1), radius=CARD_RADIUS * s,
            outline=(255, 255, 255, 70), width=3 * s,
        )

        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.alpha_composite(bg_glass)
        draw = ImageDraw.Draw(canvas)

        # 2. Avatar do membro (Massivo)
        avatar_bottom = self._draw_avatar(canvas, draw, pfp_img, dom_color, s)

        # 3. Badge de raridade (Paleta dinâmica)
        rarity_bottom = self._draw_rarity_badge(canvas, draw, rarity_label, dom_color, s, avatar_bottom)

        # 4. Nome do membro (Fonte gigante)
        self._draw_member_name(canvas, draw, user_name, s, rarity_bottom)

        # 5. Painel de atributos translúcido
        self._draw_attributes_panel(canvas, draw, atk, def_stat, spd, dom_color, s)

        # --- Downsampling final (Anti-Aliasing LANCZOS) ---
        final = canvas.resize((CARD_W, CARD_H), Image.Resampling.LANCZOS)

        final_mask = self._rounded_rect_mask((CARD_W, CARD_H), CARD_RADIUS)
        output = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
        output.paste(final, (0, 0), final_mask)

        buffer = io.BytesIO()
        output.save(buffer, format="PNG", compress_level=6)
        buffer.seek(0)

        # Explicit OOM Defense
        pfp_img.close()
        bg_glass.close()
        canvas.close()
        final.close()
        output.close()

        return buffer
