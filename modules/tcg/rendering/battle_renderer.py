import io
from PIL import Image, ImageDraw, ImageFont

class BattleRenderer:
    def __init__(self, assets_path: str = "assets/tcg/"):
        self.assets_path = assets_path

    async def render_battle_state(self, p1_name: str, p1_hp: int, p1_hp_max: int, p1_card_img: io.BytesIO,
                                  p2_name: str, p2_hp: int, p2_hp_max: int, p2_card_img: io.BytesIO) -> io.BytesIO:
        """
        Recebe o estado de HP extraído via Redis e cria um campo de batalha visual e 
        espelhado, gerando retângulos matemáticos e buffer de imagem à cada round.
        """
        canvas_width = 1800
        canvas_height = 900
        
        # Cor de fundo sólida padrão de ringue
        canvas = Image.new("RGBA", (canvas_width, canvas_height), (30, 30, 35, 255))
        draw = ImageDraw.Draw(canvas)
        
        try:
            font = ImageFont.truetype(f"{self.assets_path}fonts/Roboto-Bold.ttf", 45)
            font_hp = ImageFont.truetype(f"{self.assets_path}fonts/Roboto-Regular.ttf", 35)
        except OSError:
            font = ImageFont.load_default()
            font_hp = ImageFont.load_default()

        # Resgata as imagens binárias das cartas em duelo e transforma em Image objects
        p1_card_img.seek(0)
        p2_card_img.seek(0)
        img1 = Image.open(p1_card_img).convert("RGBA")
        img2 = Image.open(p2_card_img).convert("RGBA")
        
        # Reduz as cartas para escala de campo de batalha (Target size)
        target_size = (450, 650)
        img1 = img1.resize(target_size, resample=Image.Resampling.BICUBIC)
        img2 = img2.resize(target_size, resample=Image.Resampling.BICUBIC)
        
        # Sistema de Espelhamento (Margem, Cartas e UI)
        p1_x, p1_y = 150, 100
        p2_x, p2_y = canvas_width - target_size[0] - 150, 100
        
        # Renderiza os guerreiros
        canvas.paste(img1, (p1_x, p1_y), img1)
        canvas.paste(img2, (p2_x, p2_y), img2)
        
        # --- Nomes dos Combatentes ---
        draw.text((p1_x, p1_y - 60), p1_name, font=font, fill=(255, 255, 255, 255))
        
        bbox_p2 = font.getbbox(p2_name)
        p2_name_width = bbox_p2[2] - bbox_p2[0]
        # Alinhamento à direita matemático
        draw.text((p2_x + target_size[0] - p2_name_width, p2_y - 60), p2_name, font=font, fill=(255, 255, 255, 255))

        # --- Matemática dos Retângulos da Barra de HP ---
        bar_width = 450
        bar_height = 35
        y_bar_offset = p1_y + target_size[1] + 30
        
        # === Player 1 ===
        # Proteção matemática para interpolação limpa de ratio
        p1_hp_ratio = max(0.0, min(1.0, p1_hp / p1_hp_max if p1_hp_max > 0 else 0))
        p1_hp_fill_width = int(bar_width * p1_hp_ratio)
        
        # Base/Fundo da Barra (Vermelho/Vazio)
        draw.rectangle([p1_x, y_bar_offset, p1_x + bar_width, y_bar_offset + bar_height], fill=(70, 20, 20, 255), outline=(0, 0, 0, 255), width=2)
        # Preenchimento Vitalidade
        if p1_hp_fill_width > 0:
            # Verde ao Vermelho baseado no ratio pode ser interpolado, aqui setamos verde puro.
            fill_color = (60, 220, 60, 255) if p1_hp_ratio > 0.3 else (220, 160, 0, 255) if p1_hp_ratio > 0.15 else (220, 60, 60, 255)
            draw.rectangle([p1_x, y_bar_offset, p1_x + p1_hp_fill_width, y_bar_offset + bar_height], fill=fill_color)
            
        draw.text((p1_x, y_bar_offset + bar_height + 15), f"{p1_hp} / {p1_hp_max}", font=font_hp, fill=(220, 220, 220, 255))

        # === Player 2 (Espelhado) ===
        p2_hp_ratio = max(0.0, min(1.0, p2_hp / p2_hp_max if p2_hp_max > 0 else 0))
        p2_hp_fill_width = int(bar_width * p2_hp_ratio)
        
        # Base
        draw.rectangle([p2_x, y_bar_offset, p2_x + bar_width, y_bar_offset + bar_height], fill=(70, 20, 20, 255), outline=(0, 0, 0, 255), width=2)
        
        # O preenchimento do Inimigo deve inverter fisicamente (crescer da direita para a esquerda) para simetria autêntica de Fighters
        if p2_hp_fill_width > 0:
            fill_start_x = p2_x + bar_width - p2_hp_fill_width
            fill_color2 = (60, 220, 60, 255) if p2_hp_ratio > 0.3 else (220, 160, 0, 255) if p2_hp_ratio > 0.15 else (220, 60, 60, 255)
            draw.rectangle([fill_start_x, y_bar_offset, p2_x + bar_width, y_bar_offset + bar_height], fill=fill_color2)

        # Label Inimigo alinhado à direita matemática
        hp_str_p2 = f"{p2_hp} / {p2_hp_max}"
        bbox_hp2 = font_hp.getbbox(hp_str_p2)
        hp2_width = bbox_hp2[2] - bbox_hp2[0]
        draw.text((p2_x + bar_width - hp2_width, y_bar_offset + bar_height + 15), hp_str_p2, font=font_hp, fill=(220, 220, 220, 255))

        # Compila a obra e lança pra RAM em PNG binário
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG")
        buffer.seek(0)
        
        return buffer
