import io
import os
import random
import logging
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance, ImageFilter

logger = logging.getLogger(__name__)

class BoosterGraphicEngine:
    """
    SRE Design: Motor Gráfico In-Memory para renderização Vibrante, Minimalista e Esqueumórfica.
    Otimizado para operações assíncronas e renderizações múltiplas no TCG.
    """
    def __init__(self, fonts_path: str = "assets/fonts/"):
        self.fonts_path = fonts_path
        
        try:
            self.font_name = ImageFont.truetype(os.path.join(fonts_path, "Montserrat-Black.ttf"), 65)
            self.font_rarity = ImageFont.truetype(os.path.join(fonts_path, "Poppins-Bold.ttf"), 30)
            self.font_label = ImageFont.truetype(os.path.join(fonts_path, "Poppins-Regular.ttf"), 22)
            self.font_stat = ImageFont.truetype(os.path.join(fonts_path, "Montserrat-Black.ttf"), 65)
        except OSError as e:
            logger.error(f"Erro ao carregar fontes: {e}. Usando fallback.")
            self.font_name = ImageFont.load_default()
            self.font_rarity = ImageFont.load_default()
            self.font_label = ImageFont.load_default()
            self.font_stat = ImageFont.load_default()

    def _draw_text_with_shadow(self, canvas: Image.Image, xy: tuple, text: str, font: ImageFont.FreeTypeFont, text_color: str, shadow_color=(0,0,0,120), shadow_offset=(5,5), blur=5):
        """Helper para desenhar textos com soft shadow flutuante usando canais alpha e GaussianBlur."""
        shadow_img = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_img)
        shadow_draw.text((xy[0] + shadow_offset[0], xy[1] + shadow_offset[1]), text, font=font, fill=shadow_color)
        shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(blur))
        canvas.alpha_composite(shadow_img)
        
        text_img = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        text_draw = ImageDraw.Draw(text_img)
        text_draw.text(xy, text, font=font, fill=text_color)
        canvas.alpha_composite(text_img)

    async def render_card(self, user_name: str, pfp_bytes: bytes, atk: int, def_stat: int, spd: int, rarity_label: str) -> io.BytesIO:
        """
        Constrói a carta na nova estética Pop / Skeuomorphic.
        Buffer 100% In-Memory.
        """
        width, height = 800, 1200
        # Canvas principal transparente para os cantos arredondados vazarem pro Discord
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        
        # 1. Base Arredondada e Fundo Vibrante
        colors = {
            "COMUM": "#00d2ff",   # Ciano Neon
            "RARO": "#8a2be2",    # Roxo Elétrico
            "ÉPICO": "#ff007f",   # Magenta Vivo
            "LENDÁRIO": "#ff8c00" # Laranja Solar
        }
        base_color = colors.get(rarity_label.upper(), "#00d2ff")
        
        # Criando o shape do card com radius altíssimo
        card_shape = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw_shape = ImageDraw.Draw(card_shape)
        draw_shape.rounded_rectangle([0, 0, width, height], radius=60, fill=base_color)
        
        # Textura abstrata / Glitch / Grid super suave (baixa opacidade)
        pattern = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        p_draw = ImageDraw.Draw(pattern)
        for i in range(0, height, 40):
            p_draw.line((0, i, width, i), fill=(255, 255, 255, 15), width=2)
        for i in range(0, width, 40):
            p_draw.line((i, 0, i, height), fill=(255, 255, 255, 15), width=2)
            
        # Composição da Base com o Pattern usando a máscara arredondada do card
        canvas = Image.alpha_composite(canvas, card_shape)
        pattern_clipped = Image.composite(pattern, Image.new("RGBA", (width, height), (0,0,0,0)), card_shape.split()[3])
        canvas = Image.alpha_composite(canvas, pattern_clipped)
        
        draw = ImageDraw.Draw(canvas)

        # Esqueumorfismo Frontal: Moldura Acrílica Brilhante
        # Highlight superior/esquerdo (brilho)
        draw.rounded_rectangle([4, 4, width-4, height-4], radius=56, outline=(255, 255, 255, 180), width=6)
        # Drop shadow interno inferior/direito
        draw.rounded_rectangle([10, 10, width-2, height-2], radius=56, outline=(0, 0, 0, 80), width=6)

        # 2. Manipulação do Avatar Pop & Tátil
        with Image.open(io.BytesIO(pfp_bytes)) as pfp:
            pfp = pfp.convert("RGBA")
            pfp = pfp.resize((420, 420), Image.Resampling.LANCZOS)
            
            # Cores saturadas vibrantes em vez de cinza
            pfp_color = ImageEnhance.Color(pfp).enhance(1.6)
            pfp_contrast = ImageEnhance.Contrast(pfp_color).enhance(1.1)

            # Squircle ou Círculo Perfeito
            mask = Image.new("L", (420, 420), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, 420, 420), fill=255)
            
            avatar_x = (width - 420) // 2
            avatar_y = 150
            
            # Anel Cromado / Lente de Vidro ao redor do avatar
            draw.ellipse([avatar_x - 16, avatar_y - 16, avatar_x + 420 + 16, avatar_y + 420 + 16], fill=(40, 40, 40, 255)) # Base shadow
            draw.ellipse([avatar_x - 12, avatar_y - 12, avatar_x + 420 + 12, avatar_y + 420 + 12], fill=(240, 240, 240, 255)) # Chrome highlight
            draw.ellipse([avatar_x - 6, avatar_y - 6, avatar_x + 420 + 6, avatar_y + 420 + 6], fill=(120, 120, 120, 255)) # Inner bevel
            
            # Posição do avatar
            canvas.paste(pfp_contrast, (avatar_x, avatar_y), mask)
            
            # Lente Esqueumórfica frontal (Brilho especular curvado)
            lens_highlight = Image.new("RGBA", (420, 420), (0, 0, 0, 0))
            lens_draw = ImageDraw.Draw(lens_highlight)
            lens_draw.ellipse([30, 20, 390, 160], fill=(255, 255, 255, 60))
            canvas.paste(lens_highlight, (avatar_x, avatar_y), mask)

        # 3. Tipografia Limpa e Flutuante
        rarity_text = rarity_label.upper()
        bbox = self.font_rarity.getbbox(rarity_text)
        rw = bbox[2] - bbox[0]
        rh = bbox[3] - bbox[1]
        
        rx = (width - rw) // 2
        ry = avatar_y + 420 + 40
        
        # Etiqueta Plástica (Pill) para a raridade
        pill_pad_x = 25
        pill_pad_y = 10
        # Sombra da Pill
        draw.rounded_rectangle([rx - pill_pad_x + 5, ry - pill_pad_y + 5, rx + rw + pill_pad_x + 5, ry + rh + pill_pad_y + 5], radius=30, fill=(0, 0, 0, 60))
        # Corpo da Pill Brilhante
        draw.rounded_rectangle([rx - pill_pad_x, ry - pill_pad_y, rx + rw + pill_pad_x, ry + rh + pill_pad_y], radius=30, fill="#ffffff")
        draw.text((rx, ry), rarity_text, font=self.font_rarity, fill=base_color)

        # Nome Flutuante da Carta (Soft Shadow via Helper)
        name_text = user_name.upper()
        if len(name_text) > 13:
            name_text = name_text[:11] + ".."
            
        bbox = self.font_name.getbbox(name_text)
        nw = bbox[2] - bbox[0]
        
        self._draw_text_with_shadow(
            canvas, 
            xy=((width - nw) // 2, ry + 60), 
            text=name_text, 
            font=self.font_name, 
            text_color="#ffffff",
            shadow_color=(0, 0, 0, 160),
            shadow_offset=(4, 6),
            blur=6
        )

        # 4. Bloco de Atributos (Baixo Relevo)
        stats_y = 820
        box_w = 210
        box_h = 160
        spacing = 30
        start_x = (width - (3 * box_w + 2 * spacing)) // 2

        stats = [
            ("ATK", str(atk)),
            ("DEF", str(def_stat)),
            ("SPD", str(spd))
        ]

        for i, (label, val) in enumerate(stats):
            bx = start_x + i * (box_w + spacing)
            
            # Sombra Projetada Inversa (Bevel de Baixo Relevo)
            # Base do slot entalhado
            draw.rounded_rectangle([bx, stats_y, bx + box_w, stats_y + box_h], radius=40, fill=(0, 0, 0, 40))
            # Contorno superior escuro
            draw.rounded_rectangle([bx, stats_y, bx + box_w, stats_y + box_h], radius=40, outline=(0, 0, 0, 90), width=4)
            # Brilho reflexivo inferior
            draw.line([bx + 40, stats_y + box_h + 2, bx + box_w - 40, stats_y + box_h + 2], fill=(255, 255, 255, 120), width=3)
            
            # Label
            bbox_lbl = self.font_label.getbbox(label)
            lbl_x = bx + (box_w - (bbox_lbl[2] - bbox_lbl[0])) // 2
            draw.text((lbl_x, stats_y + 20), label, font=self.font_label, fill=(255, 255, 255, 180))
            
            # Valor Gigante
            bbox_val = self.font_stat.getbbox(val)
            val_x = bx + (box_w - (bbox_val[2] - bbox_val[0])) // 2
            
            # Adiciona soft shadow sutil para o número saltar do baixo-relevo
            self._draw_text_with_shadow(
                canvas,
                xy=(val_x, stats_y + 55),
                text=val,
                font=self.font_stat,
                text_color="#ffffff",
                shadow_color=(0, 0, 0, 100),
                shadow_offset=(3, 3),
                blur=3
            )

        # 5. Exportação Otimizada em PNG Preservando o Alpha (Transparência nos Cantos)
        buffer = io.BytesIO()
        canvas.save(buffer, format='PNG', compress_level=6)
        buffer.seek(0)
        
        canvas.close()
        return buffer
