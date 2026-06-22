import io
import os
import random
import logging
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

class BoosterGraphicEngine:
    """
    SRE Design: Motor Gráfico In-Memory para renderização Flat Minimalista com Esqueumorfismo.
    Otimizado para operações assíncronas e renderizações múltiplas no TCG.
    """
    def __init__(self, fonts_path: str = "assets/fonts/"):
        self.fonts_path = fonts_path
        
        try:
            self.font_header = ImageFont.truetype(os.path.join(fonts_path, "Montserrat-Black.ttf"), 18)
            self.font_name = ImageFont.truetype(os.path.join(fonts_path, "Montserrat-Black.ttf"), 42)
            self.font_rarity = ImageFont.truetype(os.path.join(fonts_path, "Poppins-Bold.ttf"), 16)
            self.font_label = ImageFont.truetype(os.path.join(fonts_path, "Poppins-Regular.ttf"), 14)
            self.font_stat = ImageFont.truetype(os.path.join(fonts_path, "Montserrat-Black.ttf"), 48)
            self.font_serial = ImageFont.truetype(os.path.join(fonts_path, "Poppins-Regular.ttf"), 10)
        except OSError as e:
            logger.error(f"Erro ao carregar fontes: {e}. Usando fallback.")
            self.font_header = ImageFont.load_default()
            self.font_name = ImageFont.load_default()
            self.font_rarity = ImageFont.load_default()
            self.font_label = ImageFont.load_default()
            self.font_stat = ImageFont.load_default()
            self.font_serial = ImageFont.load_default()

    def _draw_drop_shadow(self, bg_image, shape_mask, offset=(0, 5), blur=10, opacity=120):
        """Cria uma sombra projetada (drop shadow) a partir de uma máscara."""
        shadow = Image.new("RGBA", bg_image.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow.paste((0, 0, 0, opacity), offset, shape_mask)
        shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
        bg_image.alpha_composite(shadow)

    def _create_rounded_rect_mask(self, size, radius):
        """Cria uma máscara em formato de retângulo arredondado."""
        mask = Image.new("L", size, 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
        return mask

    def _draw_inner_shadow(self, image, box, radius, color=(0,0,0,100), blur=5, offset=(5,5)):
        """Simula sombra interna desenhando uma versão maior deslocada com blur e cortando com a máscara original."""
        w, h = image.size
        # Máscara original do elemento
        orig_mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(orig_mask).rounded_rectangle(box, radius=radius, fill=255)
        
        # Sombra deslocada
        shadow = Image.new("RGBA", (w, h), (0,0,0,0))
        shadow_draw = ImageDraw.Draw(shadow)
        # Para inner shadow forte no top-left, desenhamos o inverso ou um stroke grosso
        shadow_draw.rounded_rectangle([box[0]-offset[0], box[1]-offset[1], box[2]-offset[0], box[3]-offset[1]], radius=radius, outline=color, width=blur*2)
        shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
        
        # Aplica shadow apenas DENTRO do box original
        image.paste(shadow, (0,0), orig_mask)

    def _draw_inner_glow(self, image, box, radius, color=(255,255,255,100), blur=5, offset=(0,0)):
        """Simula brilho interno."""
        w, h = image.size
        orig_mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(orig_mask).rounded_rectangle(box, radius=radius, fill=255)
        
        glow = Image.new("RGBA", (w, h), (0,0,0,0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.rounded_rectangle([box[0]+offset[0], box[1]+offset[1], box[2]+offset[0], box[3]+offset[1]], radius=radius, outline=color, width=blur*2)
        glow = glow.filter(ImageFilter.GaussianBlur(blur))
        
        image.paste(glow, (0,0), orig_mask)

    async def render_card(self, user_name: str, pfp_bytes: bytes, atk: int, def_stat: int, spd: int, rarity_label: str) -> io.BytesIO:
        """
        Constrói a carta na nova estética Flat Esqueumórfica.
        Buffer 100% In-Memory.
        """
        width, height = 600, 840
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        
        # --- 1. Base Roxo Profundo com Cantos Arredondados ---
        bg_color = (68, 33, 133, 255) # Roxo vibrante profundo
        card_radius = 40
        
        card_base = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw_base = ImageDraw.Draw(card_base)
        draw_base.rounded_rectangle((0, 0, width, height), radius=card_radius, fill=bg_color)
        
        # Inner Glow (topo) e Inner Shadow (base) para volume do papel
        self._draw_inner_glow(card_base, (0, 0, width, height), card_radius, color=(255,255,255,60), blur=8, offset=(0, -5))
        self._draw_inner_shadow(card_base, (0, 0, width, height), card_radius, color=(0,0,0,120), blur=15, offset=(0, -10))
        
        canvas.alpha_composite(card_base)
        draw = ImageDraw.Draw(canvas)

        # --- 2. Header (Aba Branca Topo) ---
        header_w, header_h = 240, 60
        header_x = (width - header_w) // 2
        header_y = 0
        
        header_mask = Image.new("L", (width, height), 0)
        h_draw = ImageDraw.Draw(header_mask)
        # Aba descendo do topo com cantos inferiores redondos (desenhamos um rect maior pra cima)
        h_draw.rounded_rectangle((header_x, header_y - 20, header_x + header_w, header_y + header_h), radius=20, fill=255)
        
        self._draw_drop_shadow(canvas, header_mask, offset=(0, 6), blur=8, opacity=100)
        
        header_shape = Image.new("RGBA", (width, height), (0,0,0,0))
        ImageDraw.Draw(header_shape).rounded_rectangle((header_x, header_y - 20, header_x + header_w, header_y + header_h), radius=20, fill=(255,255,255,255))
        canvas.alpha_composite(header_shape)
        
        # Texto Header
        head_text = "BAPHOMET\nTCG"
        # BAPHOMET emcima, TCG embaixo, mas a referência mostra tudo em uma linha ou estilizado. Vamos colocar BAPHOMET TCG centralizado
        bbox_h = self.font_header.getbbox("BAPHOMET TCG")
        draw.text((header_x + (header_w - (bbox_h[2]-bbox_h[0]))//2, header_y + 15), "BAPHOMET TCG", font=self.font_header, fill=(0,0,0,255))
        draw.line((header_x + 50, header_y + 40, header_x + header_w - 50, header_y + 40), fill=(0,0,0,255), width=2)

        # --- 3. Avatar (Baixo-Relevo) ---
        pfp_size = 280
        pfp_x = (width - pfp_size) // 2
        pfp_y = 120
        pfp_radius = 30
        
        # Moldura Esqueumórfica (Relevo recesso)
        # Desenhamos o shape da máscara do avatar
        pfp_mask = self._create_rounded_rect_mask((pfp_size, pfp_size), pfp_radius)
        
        try:
            with Image.open(io.BytesIO(pfp_bytes)) as pfp:
                pfp = pfp.convert("RGBA").resize((pfp_size, pfp_size), Image.Resampling.LANCZOS)
                canvas.paste(pfp, (pfp_x, pfp_y), pfp_mask)
        except Exception as e:
            logger.error(f"Erro render avatar: {e}")
            draw.rounded_rectangle((pfp_x, pfp_y, pfp_x+pfp_size, pfp_y+pfp_size), radius=pfp_radius, fill=(30,30,30,255))
            
        # Inner shadow simulando buraco
        self._draw_inner_shadow(canvas, (pfp_x, pfp_y, pfp_x+pfp_size, pfp_y+pfp_size), pfp_radius, color=(0,0,0,180), blur=8, offset=(4,4))
        # Inner glow topo simulando luz pegando na borda interna
        self._draw_inner_glow(canvas, (pfp_x, pfp_y, pfp_x+pfp_size, pfp_y+pfp_size), pfp_radius, color=(255,255,255,80), blur=3, offset=(0,-2))

        # --- 4. Badge de Raridade ---
        rarity_text = rarity_label.upper()
        bbox_r = self.font_rarity.getbbox(rarity_text)
        rw = bbox_r[2] - bbox_r[0] + 40
        rh = 30
        rx = (width - rw) // 2
        ry = pfp_y + pfp_size + 40
        
        badge_mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(badge_mask).rounded_rectangle((rx, ry, rx+rw, ry+rh), radius=rh//2, fill=255)
        self._draw_drop_shadow(canvas, badge_mask, offset=(0, 4), blur=6, opacity=90)
        
        # Pílula base
        draw.rounded_rectangle((rx, ry, rx+rw, ry+rh), radius=rh//2, fill=(194, 34, 34, 255)) # Vermelho vivo
        # Esqueumorfismo na badge
        self._draw_inner_shadow(canvas, (rx, ry, rx+rw, ry+rh), rh//2, color=(0,0,0,80), blur=4, offset=(0,-3))
        self._draw_inner_glow(canvas, (rx, ry, rx+rw, ry+rh), rh//2, color=(255,255,255,100), blur=3, offset=(0,2))
        
        draw.text((rx + (rw - (bbox_r[2]-bbox_r[0]))//2, ry + 3), rarity_text, font=self.font_rarity, fill=(255,255,255,255))

        # --- 5. Nome da Carta (Letterpress Effect) ---
        name_text = user_name.upper()
        if len(name_text) > 13:
            name_text = name_text[:11] + ".."
            
        bbox_n = self.font_name.getbbox(name_text)
        nx = (width - (bbox_n[2]-bbox_n[0])) // 2
        ny = ry + 40
        
        # Drop shadow duro / Relevo
        draw.text((nx + 2, ny + 3), name_text, font=self.font_name, fill=(0,0,0,150)) # Sombra
        draw.text((nx, ny), name_text, font=self.font_name, fill=(255,255,255,255)) # Texto principal

        # --- 6. Painel de Atributos (Rodapé Off-white) ---
        panel_w = width - 40
        panel_h = 160
        panel_x = 20
        panel_y = height - panel_h - 20
        panel_radius = 30
        
        # Painel base
        panel_mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(panel_mask).rounded_rectangle((panel_x, panel_y, panel_x+panel_w, panel_y+panel_h), radius=panel_radius, fill=255)
        self._draw_drop_shadow(canvas, panel_mask, offset=(0, -5), blur=10, opacity=60) # Sombra sutil projetada no fundo roxo
        
        draw.rounded_rectangle((panel_x, panel_y, panel_x+panel_w, panel_y+panel_h), radius=panel_radius, fill=(240, 240, 240, 255))
        # Inner glow no topo do painel
        self._draw_inner_glow(canvas, (panel_x, panel_y, panel_x+panel_w, panel_y+panel_h), panel_radius, color=(255,255,255,255), blur=5, offset=(0, 2))

        # Caixas de Status Embutidas (Recessed)
        box_w = 160
        box_h = 100
        spacing = (panel_w - (3 * box_w)) // 4
        
        stats = [("ATK", str(atk)), ("DEF", str(def_stat)), ("SPD", str(spd))]
        
        for i, (label, val) in enumerate(stats):
            bx = panel_x + spacing + i * (box_w + spacing)
            by = panel_y + (panel_h - box_h) // 2
            b_radius = 20
            
            # Fundo da caixa (cinza base para imitar o fundo da carta ou roxo escuro)
            draw.rounded_rectangle((bx, by, bx+box_w, by+box_h), radius=b_radius, fill=(68, 33, 133, 255)) # Fundo roxo dentro da caixa em relevo
            
            # Sombra interna para afundar (canto superior esquerdo escuro, inferior direito claro)
            self._draw_inner_shadow(canvas, (bx, by, bx+box_w, by+box_h), b_radius, color=(0,0,0,180), blur=8, offset=(5,5))
            self._draw_inner_glow(canvas, (bx, by, bx+box_w, by+box_h), b_radius, color=(255,255,255,100), blur=4, offset=(-2,-2))
            
            # Top header do slot (A área cinza onde fica o label)
            # Desenhando uma abazinha cinza por cima dentro do box afundado
            ab_h = 30
            aba_mask = Image.new("L", (width, height), 0)
            ImageDraw.Draw(aba_mask).rounded_rectangle((bx, by, bx+box_w, by+box_h), radius=b_radius, fill=255)
            # Shape cinza
            aba_shape = Image.new("RGBA", (width, height), (0,0,0,0))
            ImageDraw.Draw(aba_shape).rectangle((bx, by, bx+box_w, by+ab_h), fill=(200,200,200,255))
            canvas.paste(aba_shape, (0,0), aba_mask)
            
            # Label
            bbox_lbl = self.font_label.getbbox(label)
            draw.text((bx + (box_w - (bbox_lbl[2]-bbox_lbl[0]))//2, by + 5), label, font=self.font_label, fill=(80,80,80,255))
            
            # Valor
            bbox_val = self.font_stat.getbbox(val)
            val_x = bx + (box_w - (bbox_val[2]-bbox_val[0]))//2
            val_y = by + ab_h + 5
            # Texto Branco com leve shadow dura
            draw.text((val_x+2, val_y+3), val, font=self.font_stat, fill=(0,0,0,100))
            draw.text((val_x, val_y), val, font=self.font_stat, fill=(255,255,255,255))

        # --- 7. Decorações e Seriais ---
        serial = f"BPH-GTO-{random.randint(1000, 9999)}-01"
        # Canto inferior esquerdo e direito fora das caixas, na borda do painel branco
        draw.text((panel_x + 20, panel_y + panel_h - 20), serial, font=self.font_serial, fill=(120,120,120,255))
        draw.text((panel_x + panel_w - 90, panel_y - 15), serial, font=self.font_serial, fill=(180,180,180,255))

        # Exportação Otimizada
        buffer = io.BytesIO()
        canvas.save(buffer, format='PNG', compress_level=6)
        buffer.seek(0)
        
        canvas.close()
        return buffer
