import io
import os
import random
import logging
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

class BoosterGraphicEngine:
    """
    SRE Design: Motor Gráfico In-Memory para renderização Flat Minimalista com Esqueumorfismo Avançado.
    Otimizado com Supersampling (Anti-Aliasing de alta fidelidade) e Sombras Projetadas Reais.
    """
    def __init__(self, fonts_path: str = "assets/fonts/", scale: int = 2):
        self.fonts_path = fonts_path
        self.scale = scale
        
        try:
            self.font_header = ImageFont.truetype(os.path.join(fonts_path, "Montserrat-Black.ttf"), 18 * scale)
            self.font_name = ImageFont.truetype(os.path.join(fonts_path, "Montserrat-Black.ttf"), 42 * scale)
            self.font_rarity = ImageFont.truetype(os.path.join(fonts_path, "Poppins-Bold.ttf"), 16 * scale)
            self.font_label = ImageFont.truetype(os.path.join(fonts_path, "Poppins-Regular.ttf"), 14 * scale)
            self.font_stat = ImageFont.truetype(os.path.join(fonts_path, "Montserrat-Black.ttf"), 48 * scale)
            self.font_serial = ImageFont.truetype(os.path.join(fonts_path, "Poppins-Regular.ttf"), 10 * scale)
        except OSError as e:
            logger.error(f"Erro ao carregar fontes: {e}. Usando fallback.")
            self.font_header = ImageFont.load_default()
            self.font_name = ImageFont.load_default()
            self.font_rarity = ImageFont.load_default()
            self.font_label = ImageFont.load_default()
            self.font_stat = ImageFont.load_default()
            self.font_serial = ImageFont.load_default()

    def _draw_drop_shadow(self, bg_image, box, radius, offset=(0, 10), blur=10, color=(0, 0, 0, 150)):
        """Cria uma sombra projetada (drop shadow) real desenhando a forma e aplicando blur."""
        shadow = Image.new("RGBA", bg_image.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_box = [box[0]+offset[0], box[1]+offset[1], box[2]+offset[0], box[3]+offset[1]]
        shadow_draw.rounded_rectangle(shadow_box, radius=radius, fill=color)
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=blur))
        bg_image.alpha_composite(shadow)

    def _draw_recessed_box(self, bg_image, box, radius, fill_color, dark_color=(0,0,0,150), bright_color=(255,255,255,200), blur=5, offset=5):
        """Simula um buraco (baixo-relevo) usando inner shadows reais de duas direções."""
        w, h = bg_image.size
        draw = ImageDraw.Draw(bg_image)
        
        # Fundo da caixa desenhado diretamente (evita bug do paste de cor)
        if fill_color:
            draw.rounded_rectangle(box, radius=radius, fill=fill_color)
            
        # Máscara exata da caixa
        box_mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(box_mask).rounded_rectangle(box, radius=radius, fill=255)
        
        # Sombra interna (Top-Left)
        shadow_layer = Image.new("RGBA", (w, h), (0,0,0,0))
        ImageDraw.Draw(shadow_layer).rounded_rectangle([box[0]-offset, box[1]-offset, box[2]-offset, box[3]-offset], radius=radius, outline=dark_color, width=blur*2)
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(blur))
        bg_image.paste(shadow_layer, (0,0), box_mask)
        
        # Brilho interno (Bottom-Right)
        glow_layer = Image.new("RGBA", (w, h), (0,0,0,0))
        ImageDraw.Draw(glow_layer).rounded_rectangle([box[0]+offset, box[1]+offset, box[2]+offset, box[3]+offset], radius=radius, outline=bright_color, width=blur*2)
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(blur))
        bg_image.paste(glow_layer, (0,0), box_mask)

    async def render_card(self, user_name: str, pfp_bytes: bytes, atk: int, def_stat: int, spd: int, rarity_label: str) -> io.BytesIO:
        """
        Constrói a carta usando Supersampling, Letterpress, Drop Shadows e Inner Shadows.
        """
        s = self.scale
        
        # Canvas Oversized para Supersampling (ex: 1200x1680)
        width, height = 600 * s, 840 * s
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        
        # --- 1. Base Roxo Profundo com Cantos Arredondados ---
        bg_color = (68, 33, 133, 255)
        card_radius = 40 * s
        
        # Preenche a base desenhando diretamente
        draw.rounded_rectangle((0, 0, width, height), radius=card_radius, fill=bg_color)
        
        # Pseudo Inner Glow na carta toda
        self._draw_recessed_box(canvas, (0, 0, width, height), card_radius, fill_color=None, dark_color=(0,0,0,80), bright_color=(255,255,255,60), blur=15*s, offset=10*s)

        # --- 2. Header (Aba Branca Topo) ---
        header_w, header_h = 240 * s, 60 * s
        header_x = (width - header_w) // 2
        header_y = 0
        header_box = [header_x, header_y - 20*s, header_x + header_w, header_y + header_h]
        
        # Drop shadow da aba
        self._draw_drop_shadow(canvas, header_box, radius=20*s, offset=(0, 8*s), blur=10*s, color=(0, 0, 0, 160))
        
        # Desenha a aba diretamente
        draw.rounded_rectangle(header_box, radius=20*s, fill=(255,255,255,255))
        
        # Texto Header (Centralização Perfeita com anchor="mm")
        head_cx = header_x + header_w // 2
        head_cy = header_y + header_h // 2 + 5*s
        draw.text((head_cx, head_cy), "BAPHOMET TCG", font=self.font_header, fill=(30,30,30,255), anchor="mm")

        # --- 3. Avatar (Relevo / Squircles Máscara Perfeita) ---
        pfp_size = 280 * s
        pfp_x = (width - pfp_size) // 2
        pfp_y = 120 * s
        pfp_radius = 30 * s
        
        # Carregar e redimensionar avatar
        try:
            with Image.open(io.BytesIO(pfp_bytes)) as pfp:
                pfp_img = pfp.convert("RGBA").resize((pfp_size, pfp_size), Image.Resampling.LANCZOS)
                
                # Criar máscara exata do Avatar no tamanho da PFP
                pfp_mask = Image.new("L", (pfp_size, pfp_size), 0)
                ImageDraw.Draw(pfp_mask).rounded_rectangle((0, 0, pfp_size, pfp_size), radius=pfp_radius, fill=255)
                
                # Aplica a máscara no canal alpha
                pfp_img.putalpha(pfp_mask)
                canvas.alpha_composite(pfp_img, (pfp_x, pfp_y))
        except Exception as e:
            logger.error(f"Erro render avatar: {e}")
            draw.rounded_rectangle((pfp_x, pfp_y, pfp_x+pfp_size, pfp_y+pfp_size), radius=pfp_radius, fill=(30,30,30,255))
            
        # Inner shadow do buraco do avatar (simula que a foto ta encaixada)
        avatar_box_mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(avatar_box_mask).rounded_rectangle((pfp_x, pfp_y, pfp_x+pfp_size, pfp_y+pfp_size), radius=pfp_radius, fill=255)
        
        avatar_shadow = Image.new("RGBA", (width, height), (0,0,0,0))
        ImageDraw.Draw(avatar_shadow).rounded_rectangle([pfp_x-6*s, pfp_y-6*s, pfp_x+pfp_size-6*s, pfp_y+pfp_size-6*s], radius=pfp_radius, outline=(0,0,0,180), width=8*s)
        avatar_shadow = avatar_shadow.filter(ImageFilter.GaussianBlur(8*s))
        canvas.paste(avatar_shadow, (0,0), avatar_box_mask)

        # --- 4. Badge de Raridade ---
        rarity_text = rarity_label.upper()
        bbox_r = self.font_rarity.getbbox(rarity_text)
        rw = (bbox_r[2] - bbox_r[0]) + 40 * s
        rh = 30 * s
        rx = (width - rw) // 2
        ry = pfp_y + pfp_size + 40 * s
        badge_box = [rx, ry, rx+rw, ry+rh]
        
        # Real Drop Shadow da Badge
        self._draw_drop_shadow(canvas, badge_box, radius=rh//2, offset=(0, 6*s), blur=8*s, color=(0, 0, 0, 160))
        
        # Pílula Esqueumórfica (Relevo bolha)
        self._draw_recessed_box(canvas, badge_box, rh//2, fill_color=(194, 34, 34, 255), dark_color=(0,0,0,100), bright_color=(255,255,255,140), blur=4*s, offset=3*s)
        
        # Texto Raridade
        draw.text((rx + rw//2, ry + rh//2), rarity_text, font=self.font_rarity, fill=(255,255,255,255), anchor="mm")

        # --- 5. Nome da Carta (Letterpress Effect real) ---
        name_text = user_name.upper()
        if len(name_text) > 13:
            name_text = name_text[:11] + ".."
            
        nx = width // 2
        ny = ry + 50 * s
        
        # Letterpress: Sombra escura levemente deslocada para baixo simulando entalhe
        draw.text((nx, ny + 4*s), name_text, font=self.font_name, fill=(0,0,0,160), anchor="mm") # Shadow/Drop
        draw.text((nx, ny), name_text, font=self.font_name, fill=(255,255,255,255), anchor="mm") # Principal

        # --- 6. Painel de Atributos (Off-white) ---
        panel_w = width - 40 * s
        panel_h = 160 * s
        panel_x = 20 * s
        panel_y = height - panel_h - 20 * s
        panel_radius = 30 * s
        panel_box = [panel_x, panel_y, panel_x+panel_w, panel_y+panel_h]
        
        # Real Drop Shadow do Painel sobre o fundo roxo
        self._draw_drop_shadow(canvas, panel_box, radius=panel_radius, offset=(0, -8*s), blur=12*s, color=(0, 0, 0, 120))
        
        # Painel preenchido diretamente
        draw.rounded_rectangle(panel_box, radius=panel_radius, fill=(240, 240, 240, 255))
        
        # Borda de luz leve no topo
        panel_mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(panel_mask).rounded_rectangle(panel_box, radius=panel_radius, fill=255)
        
        panel_glow = Image.new("RGBA", (width, height), (0,0,0,0))
        ImageDraw.Draw(panel_glow).rounded_rectangle(panel_box, radius=panel_radius, outline=(255,255,255,255), width=2*s)
        canvas.paste(panel_glow, (0,0), panel_mask)

        # --- 7. Caixas de Status Embutidas (Recessed Boxes Realistas) ---
        box_w = 160 * s
        box_h = 100 * s
        spacing = (panel_w - (3 * box_w)) // 4
        
        stats = [("ATK", str(atk)), ("DEF", str(def_stat)), ("SPD", str(spd))]
        
        for i, (label, val) in enumerate(stats):
            bx = panel_x + spacing + i * (box_w + spacing)
            by = panel_y + (panel_h - box_h) // 2
            b_radius = 20 * s
            
            # Caixa afundada (Inner shadow na borda top-left, Inner glow na borda bottom-right)
            self._draw_recessed_box(canvas, (bx, by, bx+box_w, by+box_h), b_radius, fill_color=(50, 24, 100, 255), dark_color=(0,0,0,200), bright_color=(255,255,255,160), blur=6*s, offset=5*s)
            
            # Header cinza da caixa (Aba superior dentro do buraco)
            ab_h = 30 * s
            aba_final_mask = Image.new("L", (width, height), 0)
            ImageDraw.Draw(aba_final_mask).rounded_rectangle((bx, by, bx+box_w, by+box_h), radius=b_radius, fill=255)
            ImageDraw.Draw(aba_final_mask).rectangle((bx, by+ab_h, bx+box_w, by+box_h), fill=0) # Corta parte de baixo
            
            # Preenche a aba superior de cinza diretamente
            aba_layer = Image.new("RGBA", (width, height), (0,0,0,0))
            ImageDraw.Draw(aba_layer).rounded_rectangle((bx, by, bx+box_w, by+box_h), radius=b_radius, fill=(210, 210, 210, 255))
            canvas.paste(aba_layer, (0,0), aba_final_mask)
            
            # Label
            draw.text((bx + box_w//2, by + ab_h//2), label, font=self.font_label, fill=(80,80,80,255), anchor="mm")
            
            # Valor Numérico (Letterpress no buraco roxo)
            val_y = by + ab_h + (box_h - ab_h)//2
            draw.text((bx + box_w//2 + 2*s, val_y + 2*s), val, font=self.font_stat, fill=(0,0,0,150), anchor="mm") # Sombra interna (afunda o número)
            draw.text((bx + box_w//2, val_y), val, font=self.font_stat, fill=(255,255,255,255), anchor="mm")

        # --- 8. Decorações e Seriais ---
        serial = f"BPH-GTO-{random.randint(1000, 9999)}-01"
        draw.text((panel_x + 20*s, panel_y + panel_h - 15*s), serial, font=self.font_serial, fill=(150,150,150,255), anchor="lm")
        draw.text((panel_x + panel_w - 20*s, panel_y - 10*s), serial, font=self.font_serial, fill=(150,150,150,255), anchor="rm")

        # --- 9. Downsampling (Anti-Aliasing final) ---
        final_canvas = canvas.resize((600, 840), Image.Resampling.LANCZOS)
        
        buffer = io.BytesIO()
        final_canvas.save(buffer, format='PNG', compress_level=6)
        buffer.seek(0)
        
        canvas.close()
        final_canvas.close()
        return buffer
