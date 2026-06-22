"""
Motor Gráfico de Renderização de Cartas TCG — Baphomet Bot.

SRE Design: Renderização In-Memory com Supersampling (Anti-Aliasing de alta
fidelidade), Sombras Projetadas Reais, Inner Shadows direcionais e Gradientes
dinâmicos para estética Flat + Esqueumorfismo Avançado.

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
# Constantes de Design
# ---------------------------------------------------------------------------
CARD_W, CARD_H = 600, 840
CARD_RADIUS = 40

# Paleta — extraída diretamente do mockup fornecido
COLOR_BG_PURPLE = (88, 42, 160, 255)       # Roxo vibrante de fundo
COLOR_BG_PURPLE_DARK = (58, 22, 120, 255)  # Sombra inferior
COLOR_WHITE = (255, 255, 255, 255)
COLOR_OFF_WHITE = (240, 240, 242, 255)      # Painel de atributos
COLOR_PANEL_BORDER = (220, 220, 225, 255)
COLOR_HEADER_TEXT = (50, 45, 60, 255)
COLOR_HEADER_SUB = (120, 110, 140, 255)
COLOR_RARITY_RED = (194, 50, 50, 255)
COLOR_RARITY_RED_LIGHT = (220, 70, 70, 255)
COLOR_RARITY_RED_DARK = (160, 30, 30, 255)
COLOR_STAT_BOX_BG = (255, 255, 255, 255)
COLOR_STAT_LABEL = (140, 135, 155, 255)
COLOR_STAT_VALUE = (88, 42, 160, 255)
COLOR_SERIAL = (170, 165, 180, 255)


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
        """Carrega todas as fontes necessárias com fallback seguro."""
        s = self.scale
        pairs = {
            "font_header_main":  ("Montserrat-Black.ttf",   22 * s),
            "font_header_sub":   ("Poppins-Bold.ttf",       13 * s),
            "font_name":         ("Montserrat-Black.ttf",   40 * s),
            "font_rarity":       ("Poppins-Bold.ttf",       15 * s),
            "font_label":        ("Poppins-Bold.ttf",       13 * s),
            "font_stat":         ("Montserrat-Black.ttf",   44 * s),
            "font_serial":       ("Poppins-Regular.ttf",    9  * s),
        }
        for attr, (filename, size) in pairs.items():
            path = os.path.join(self.fonts_path, filename)
            try:
                setattr(self, attr, ImageFont.truetype(path, size=size))
            except OSError:
                logger.warning("Fonte '%s' não encontrada, usando fallback.", path)
                setattr(self, attr, ImageFont.load_default())

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
        color: Tuple[int, int, int, int] = (0, 0, 0, 120),
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

        # Máscara de clipping exata da caixa
        clip = Image.new("L", (w, h), 0)
        ImageDraw.Draw(clip).rounded_rectangle(box, radius=radius, fill=255)

        # Sombra interna escura (Top-Left)
        shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shifted_box = (box[0] - offset, box[1] - offset,
                       box[2] - offset, box[3] - offset)
        ImageDraw.Draw(shadow_layer).rounded_rectangle(
            shifted_box, radius=radius, outline=dark, width=blur * 2,
        )
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(blur))
        # Aplicar com clipping
        clipped = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        clipped.paste(shadow_layer, (0, 0), clip)
        canvas.alpha_composite(clipped)

        # Brilho interno (Bottom-Right)
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

    def _draw_card_base(self, canvas: Image.Image, draw: ImageDraw.ImageDraw, s: int) -> None:
        """Fundo roxo com gradiente sutil e inner glow."""
        w, h = canvas.size

        # Gradiente vertical: roxo vibrante → roxo escuro
        gradient = self._vertical_gradient(
            (w, h), COLOR_BG_PURPLE, COLOR_BG_PURPLE_DARK,
        )
        # Aplicar com máscara de cantos arredondados
        mask = self._rounded_rect_mask((w, h), CARD_RADIUS * s)
        bg_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        bg_layer.paste(gradient, (0, 0), mask)
        canvas.alpha_composite(bg_layer)

        # Inner glow superior (luz suave no topo)
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.rounded_rectangle(
            (0, 0, w - 1, h - 1), radius=CARD_RADIUS * s,
            outline=(255, 255, 255, 35), width=3 * s,
        )
        glow = glow.filter(ImageFilter.GaussianBlur(radius=4 * s))
        clipped_glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        clipped_glow.paste(glow, (0, 0), mask)
        canvas.alpha_composite(clipped_glow)

        # Borda sutil
        draw.rounded_rectangle(
            (0, 0, w - 1, h - 1), radius=CARD_RADIUS * s,
            outline=(255, 255, 255, 50), width=2 * s,
        )


    def _draw_avatar(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        pfp_bytes: bytes,
        s: int,
    ) -> None:
        """PFP quadrada com cantos arredondados e moldura com baixo-relevo."""
        w = canvas.size[0]

        pfp_size = 260 * s
        pfp_radius = 24 * s
        pfp_x = (w - pfp_size) // 2
        pfp_y = 85 * s

        # --- Moldura externa (borda clara ao redor da PFP) ---
        frame_pad = 6 * s
        frame_box = (
            pfp_x - frame_pad, pfp_y - frame_pad,
            pfp_x + pfp_size + frame_pad, pfp_y + pfp_size + frame_pad,
        )
        frame_radius = pfp_radius + 4 * s

        # Drop shadow da moldura
        self._drop_shadow(
            canvas, frame_box, frame_radius,
            offset=(0, 4 * s), blur=8 * s,
            color=(0, 0, 0, 100),
        )

        # Moldura branca semi-transparente
        draw.rounded_rectangle(
            frame_box, radius=frame_radius,
            fill=(255, 255, 255, 45), outline=(255, 255, 255, 70), width=2 * s,
        )

        # --- Carregar e aplicar PFP ---
        try:
            pfp_img = Image.open(io.BytesIO(pfp_bytes)).convert("RGBA")
            pfp_img = ImageOps.fit(
                pfp_img, (pfp_size, pfp_size), method=Image.Resampling.LANCZOS,
            )
        except Exception as exc:
            logger.error("Erro ao processar avatar: %s", exc)
            pfp_img = Image.new("RGBA", (pfp_size, pfp_size), (40, 20, 80, 255))

        # Máscara de cantos arredondados para a PFP
        pfp_mask = self._rounded_rect_mask((pfp_size, pfp_size), pfp_radius)
        pfp_img.putalpha(pfp_mask)
        canvas.alpha_composite(pfp_img, (pfp_x, pfp_y))

        # --- Inner shadow sobre a PFP (efeito "encaixada") ---
        self._inner_shadow(
            canvas,
            (pfp_x, pfp_y, pfp_x + pfp_size, pfp_y + pfp_size),
            pfp_radius,
            dark=(0, 0, 0, 130),
            light=(255, 255, 255, 60),
            blur=6 * s,
            offset=5 * s,
        )

        # Brilho na borda superior da moldura (simula luz vinda de cima)
        top_glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(top_glow)
        gd.rounded_rectangle(
            (pfp_x - 1, pfp_y - 1, pfp_x + pfp_size + 1, pfp_y + 4 * s),
            radius=pfp_radius,
            fill=(255, 255, 255, 40),
        )
        top_glow = top_glow.filter(ImageFilter.GaussianBlur(radius=3 * s))
        # Clipping dentro da moldura
        clip = Image.new("L", canvas.size, 0)
        ImageDraw.Draw(clip).rounded_rectangle(frame_box, radius=frame_radius, fill=255)
        clipped = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        clipped.paste(top_glow, (0, 0), clip)
        canvas.alpha_composite(clipped)

    def _draw_rarity_badge(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        rarity_label: str,
        s: int,
        y_start: int,
    ) -> int:
        """Pílula de raridade com gradiente e drop shadow. Retorna o Y final."""
        w = canvas.size[0]
        text = rarity_label.upper()

        # Dimensionar a pílula com base no texto
        bbox = self.font_rarity.getbbox(text)
        text_w = bbox[2] - bbox[0]
        pill_w = text_w + 55 * s
        pill_h = 36 * s
        pill_x = (w - pill_w) // 2
        pill_y = y_start
        pill_box = (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h)
        pill_radius = pill_h // 2

        # Drop shadow da pílula
        self._drop_shadow(
            canvas, pill_box, pill_radius,
            offset=(0, 4 * s), blur=6 * s,
            color=(0, 0, 0, 130),
        )

        # Gradiente vermelho (claro no topo → escuro na base)
        gradient = self._vertical_gradient(
            (pill_w, pill_h), COLOR_RARITY_RED_LIGHT, COLOR_RARITY_RED_DARK,
        )
        mask = self._rounded_rect_mask((pill_w, pill_h), pill_radius)
        pill_layer = Image.new("RGBA", (pill_w, pill_h), (0, 0, 0, 0))
        pill_layer.paste(gradient, (0, 0), mask)
        canvas.alpha_composite(pill_layer, (pill_x, pill_y))

        # Brilho sutil no topo da pílula
        highlight = Image.new("RGBA", (pill_w, pill_h // 2), (255, 255, 255, 30))
        h_mask = self._rounded_rect_mask((pill_w, pill_h // 2), pill_radius)
        highlight.putalpha(h_mask)
        canvas.alpha_composite(highlight, (pill_x, pill_y))

        # Texto
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
        """Nome do membro com letterpress (drop shadow duro). Retorna o Y final."""
        w = canvas.size[0]
        text = name.upper()

        # Truncar se muito longo
        if len(text) > 13:
            text = text[:12] + "…"

        cx = w // 2
        ny = y_start

        # Letterpress: sombra escura abaixo simulando entalhe
        draw.text(
            (cx, ny + 3 * s), text,
            font=self.font_name, fill=(0, 0, 0, 120), anchor="mm",
        )
        # Texto principal branco
        draw.text(
            (cx, ny), text,
            font=self.font_name, fill=COLOR_WHITE, anchor="mm",
        )

        # Calcular a altura real para retornar Y final
        bbox = self.font_name.getbbox(text)
        return ny + (bbox[3] - bbox[1]) // 2


    def _draw_attributes_panel(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        atk: int,
        def_stat: int,
        spd: int,
        serial: str,
        s: int,
    ) -> None:
        """Painel off-white com 3 caixas afundadas para ATK/DEF/SPD."""
        w, h = canvas.size

        # Dimensões do painel
        panel_margin_x = 28 * s
        panel_w = w - (panel_margin_x * 2)
        panel_h = 175 * s
        panel_x = panel_margin_x
        panel_y = h - panel_h - 30 * s
        panel_radius = 24 * s
        panel_box = (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h)

        # Drop shadow do painel
        self._drop_shadow(
            canvas, panel_box, panel_radius,
            offset=(0, 4 * s), blur=10 * s,
            color=(0, 0, 0, 90),
        )

        # Painel off-white
        draw.rounded_rectangle(
            panel_box, radius=panel_radius,
            fill=COLOR_OFF_WHITE, outline=COLOR_PANEL_BORDER, width=1 * s,
        )

        # Borda de luz no topo do painel
        self._inner_shadow(
            canvas, panel_box, panel_radius,
            dark=(0, 0, 0, 0), light=(255, 255, 255, 80),
            blur=4 * s, offset=3 * s,
        )

        # --- Caixas de Status ---
        box_w = 150 * s
        box_h = 110 * s
        inner_margin = 22 * s
        spacing = (panel_w - inner_margin * 2 - 3 * box_w) // 2
        box_top = panel_y + (panel_h - box_h) // 2 - 2 * s

        stats = [("ATK", str(atk)), ("DEF", str(def_stat)), ("SPD", str(spd))]

        for i, (label, val) in enumerate(stats):
            bx = panel_x + inner_margin + i * (box_w + spacing)
            by = box_top
            b_radius = 14 * s
            stat_box = (bx, by, bx + box_w, by + box_h)

            # Fundo branco da caixa
            draw.rounded_rectangle(
                stat_box, radius=b_radius,
                fill=COLOR_STAT_BOX_BG,
                outline=(210, 210, 215, 255), width=1 * s,
            )

            # Inner shadow (efeito afundado)
            self._inner_shadow(
                canvas, stat_box, b_radius,
                dark=(0, 0, 0, 80),
                light=(255, 255, 255, 130),
                blur=4 * s, offset=3 * s,
            )

            # Header cinza da caixa (aba superior)
            aba_h = 30 * s
            # Criar máscara que é só a parte superior da caixa arredondada
            aba_clip = Image.new("L", (w, h), 0)
            ImageDraw.Draw(aba_clip).rounded_rectangle(stat_box, radius=b_radius, fill=255)
            # Cortar tudo abaixo da aba
            ImageDraw.Draw(aba_clip).rectangle(
                (bx, by + aba_h, bx + box_w, by + box_h), fill=0,
            )

            aba_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(aba_layer).rounded_rectangle(
                stat_box, radius=b_radius, fill=(225, 225, 230, 255),
            )
            clipped_aba = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            clipped_aba.paste(aba_layer, (0, 0), aba_clip)
            canvas.alpha_composite(clipped_aba)

            # Linha separadora sutil entre aba e valor
            draw.line(
                [(bx + 4 * s, by + aba_h), (bx + box_w - 4 * s, by + aba_h)],
                fill=(200, 200, 205, 255), width=1 * s,
            )

            # Label (ATK / DEF / SPD)
            draw.text(
                (bx + box_w // 2, by + aba_h // 2 + 1 * s),
                label, font=self.font_label,
                fill=COLOR_STAT_LABEL, anchor="mm",
            )

            # Valor numérico centralizado na área roxa
            val_area_top = by + aba_h
            val_area_h = box_h - aba_h
            val_cy = val_area_top + val_area_h // 2

            # Letterpress sutil no valor
            draw.text(
                (bx + box_w // 2, val_cy + 2 * s), val,
                font=self.font_stat, fill=(0, 0, 0, 40), anchor="mm",
            )
            draw.text(
                (bx + box_w // 2, val_cy), val,
                font=self.font_stat, fill=COLOR_STAT_VALUE, anchor="mm",
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
        """
        Renderiza uma carta TCG completa.

        Parameters
        ----------
        user_name : str
            Nome de exibição do membro (será uppercased automaticamente).
        pfp_bytes : bytes
            Bytes da imagem de perfil do Discord.
        atk, def_stat, spd : int
            Atributos numéricos da carta.
        rarity_label : str
            Label de raridade (ex: "RARO", "LENDÁRIO").
        serial_code : str | None
            Número de série customizado. Se ``None``, um é gerado aleatoriamente.

        Returns
        -------
        io.BytesIO
            Buffer PNG da carta renderizada (600×840 final).
        """
        s = self.scale
        width, height = CARD_W * s, CARD_H * s

        # Canvas transparente para supersampling
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)

        serial = serial_code or f"BPH-GTO-{random.randint(1000, 9999):04d}-{random.randint(1, 99):02d}"

        # 1. Base da carta (fundo roxo com gradiente e inner glow)
        self._draw_card_base(canvas, draw, s)


        # 3. Avatar do membro
        self._draw_avatar(canvas, draw, pfp_bytes, s)

        # 4. Badge de raridade
        rarity_y = 375 * s
        rarity_bottom = self._draw_rarity_badge(canvas, draw, rarity_label, s, rarity_y)

        # 5. Nome do membro
        name_y = rarity_bottom + 38 * s
        name_bottom = self._draw_member_name(canvas, draw, user_name, s, name_y)


        # 7. Painel de atributos (ATK/DEF/SPD)
        self._draw_attributes_panel(canvas, draw, atk, def_stat, spd, serial, s)

        # --- Downsampling final (Anti-Aliasing LANCZOS) ---
        final = canvas.resize((CARD_W, CARD_H), Image.Resampling.LANCZOS)

        # Aplicar máscara de cantos arredondados no tamanho final
        final_mask = self._rounded_rect_mask((CARD_W, CARD_H), CARD_RADIUS)
        output = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
        output.paste(final, (0, 0), final_mask)

        buffer = io.BytesIO()
        output.save(buffer, format="PNG", compress_level=6)
        buffer.seek(0)

        # Explicit OOM Defense
        canvas.close()
        final.close()
        output.close()

        return buffer
