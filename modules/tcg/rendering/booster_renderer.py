import io
import os
import random
import logging
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance

logger = logging.getLogger(__name__)

class BoosterGraphicEngine:
    """
    SRE Design: Motor Gráfico definitivo In-Memory para renderização Brutalista / Dark Streetwear.
    Totalmente otimizado para não utilizar disco (I/O blocks) e evitar Memory Leaks (Pillow).
    """
    def __init__(self, fonts_path: str = "assets/fonts/"):
        self.fonts_path = fonts_path
        
        # Tratamento SRE de Fallbacks: Garantindo que o motor nunca falhe por falta de recursos de disco
        try:
            self.font_name = ImageFont.truetype(os.path.join(fonts_path, "Montserrat-Black.ttf"), 65)
            self.font_rarity = ImageFont.truetype(os.path.join(fonts_path, "Poppins-Bold.ttf"), 25)
            self.font_label = ImageFont.truetype(os.path.join(fonts_path, "Poppins-Regular.ttf"), 20)
            self.font_stat = ImageFont.truetype(os.path.join(fonts_path, "Montserrat-Black.ttf"), 65)
            self.font_barcode = ImageFont.truetype(os.path.join(fonts_path, "Poppins-Regular.ttf"), 14)
        except OSError as e:
            logger.error(f"Erro ao carregar fontes brutais: {e}. Aplicando Fontes Default do Sistema.")
            self.font_name = ImageFont.load_default()
            self.font_rarity = ImageFont.load_default()
            self.font_label = ImageFont.load_default()
            self.font_stat = ImageFont.load_default()
            self.font_barcode = ImageFont.load_default()

    async def render_card(self, user_name: str, pfp_bytes: bytes, atk: int, def_stat: int, spd: int, rarity_label: str) -> io.BytesIO:
        """
        Constrói a carta em estética Gótico Flat / Brutalista.
        100% In-Memory Buffer com I/O otimizado.
        """
        # 1. Base e Fundo (A Base Gótica)
        width, height = 800, 1200
        canvas = Image.new("RGBA", (width, height), "#0f0f0f")
        draw = ImageDraw.Draw(canvas)

        # Moldura Brutalista
        border_color = "#8b0000" if rarity_label.upper() in ["ÉPICO", "LENDÁRIO"] else "#333333"
        draw.rectangle([20, 20, width - 20, height - 20], outline=border_color, width=6)
        
        # Detalhes Marginais (Crosshairs táticos)
        for x, y in [(40, 40), (width - 40, 40), (40, height - 40), (width - 40, height - 40)]:
            draw.line((x - 15, y, x + 15, y), fill="#ffffff", width=3)
            draw.line((x, y - 15, x, y + 15), fill="#ffffff", width=3)

        # 2. Manipulação do Avatar (Glitch/Dark filter)
        with Image.open(io.BytesIO(pfp_bytes)) as pfp:
            pfp = pfp.convert("RGBA")
            pfp = pfp.resize((400, 400), Image.Resampling.LANCZOS)
            
            # Filtro Obrigatório: Grayscale + Extreme Contrast
            pfp_gray = ImageOps.grayscale(pfp)
            pfp_contrast = ImageEnhance.Contrast(pfp_gray).enhance(1.8)
            
            # Colorize tático dependendo da classe
            black_point = "#050505"
            white_point = "#ff0033" if rarity_label.upper() == "LENDÁRIO" else "#b3b3b3"
            pfp_tinted = ImageOps.colorize(pfp_contrast, black=black_point, white=white_point)
            pfp_tinted = pfp_tinted.convert("RGBA")

            # Clipping / Máscara Quadrada Brutalista (em vez de círculo, o quadrado fica mais flat/duro)
            # Vamos usar um círculo de bordas afiadas como pedido opcionalmente, mas quadrado "perfeito"
            # O usuário aceitou quadrado. Vamos desenhar quadrado com borda forte.
            avatar_x = (width - 400) // 2
            avatar_y = 150
            
            # Desenhar Borda grossa ao redor da foto (8px)
            draw.rectangle(
                [avatar_x - 8, avatar_y - 8, avatar_x + 400 + 8, avatar_y + 400 + 8], 
                fill="#1a1a1a", 
                outline="#ffffff" if rarity_label.upper() != "LENDÁRIO" else "#ff0033", 
                width=8
            )
            
            # Paste the avatar
            canvas.paste(pfp_tinted, (avatar_x, avatar_y))

        # 3. Sistema de Tipografia 
        rarity_text = rarity_label.upper()
        bbox = self.font_rarity.getbbox(rarity_text)
        rw = bbox[2] - bbox[0]
        rh = bbox[3] - bbox[1]
        
        rx = (width - rw) // 2
        ry = avatar_y + 400 + 35
        
        # Fundo da Raridade
        draw.rectangle([rx - 15, ry - 5, rx + rw + 15, ry + rh + 10], fill="#ffffff" if rarity_text != "LENDÁRIO" else "#ff0033")
        draw.text((rx, ry), rarity_text, font=self.font_rarity, fill="#000000")

        # Nome Gigante da Carta
        name_text = user_name.upper()
        # Truncar nomes absurdos
        if len(name_text) > 15:
            name_text = name_text[:12] + "..."
            
        bbox = self.font_name.getbbox(name_text)
        nw = bbox[2] - bbox[0]
        draw.text(((width - nw) // 2, ry + 50), name_text, font=self.font_name, fill="#ffffff")

        # 4. Bloco de Atributos
        stats_y = 780
        box_w = 210
        box_h = 160
        spacing = 30
        start_x = (width - (3 * box_w + 2 * spacing)) // 2

        stats = [
            ("ATK", str(atk)),
            ("DEF", str(def_stat)),
            ("SPD", str(spd))
        ]

        # Numéricos "gritando na tela"
        stat_color = "#e60000" if rarity_label.upper() in ["ÉPICO", "LENDÁRIO"] else "#ffffff"

        for i, (label, val) in enumerate(stats):
            bx = start_x + i * (box_w + spacing)
            # Caixas com contornos sólidos
            draw.rectangle([bx, stats_y, bx + box_w, stats_y + box_h], fill="#1a1a1a", outline="#333333", width=4)
            
            # Label
            bbox_lbl = self.font_label.getbbox(label)
            draw.text((bx + (box_w - (bbox_lbl[2] - bbox_lbl[0])) // 2, stats_y + 15), label, font=self.font_label, fill="#888888")
            
            # Value
            bbox_val = self.font_stat.getbbox(val)
            # Centralização matemática vertical/horizontal
            val_x = bx + (box_w - (bbox_val[2] - bbox_val[0])) // 2
            val_y = stats_y + 60
            draw.text((val_x, val_y), val, font=self.font_stat, fill=stat_color)

        # 5. Detalhes Decorativos (Grafismos Streetwear)
        # Fake Barcode
        bar_x = 100
        bar_y = height - 120
        for _ in range(60):
            bw = random.randint(1, 5)
            draw.rectangle([bar_x, bar_y, bar_x + bw, bar_y + 50], fill="#ffffff")
            bar_x += bw + random.randint(1, 4)
            if bar_x > width - 100:
                break
        
        # Serial Number
        serial = f"BPHMT-{random.randint(1000, 9999)} // SYSTEM_OVERRIDE"
        draw.text((100, bar_y + 60), serial, font=self.font_barcode, fill="#555555")

        # 6. Exportação Otimizada
        buffer = io.BytesIO()
        canvas.save(buffer, format='PNG', compress_level=6)
        buffer.seek(0)
        
        # Memory Cleanup explícito para evitar vazamento em picos de concorrência
        canvas.close()
        
        return buffer
