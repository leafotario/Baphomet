import io
import aiohttp
import logging
from PIL import Image, ImageEnhance, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

class GracefulDegradationException(Exception):
    """Sinaliza fallback para renderização em texto puro."""
    pass

class CardRenderer:
    def __init__(self, assets_path: str = "assets/tcg/"):
        self.assets_path = assets_path

    async def fetch_avatar(self, avatar_url: str) -> Image.Image:
        """
        Busca o avatar do usuário de forma assíncrona na CDN do Discord.
        O objeto viaja do HTTP diretamente para a memória via io.BytesIO.
        """
        # SRE Design: Timeout explícito para evitar engarrafamento do Event Loop Assíncrono
        timeout = aiohttp.ClientTimeout(total=3.0)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(avatar_url) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        # Uso de context manager libera os fds (File Descriptors) da memória logo após uso
                        with Image.open(io.BytesIO(data)) as img:
                            return img.convert("RGBA")
        except Exception as e:
            logger.error(f"Degradação no fetch do avatar: {e}")
            raise GracefulDegradationException("Falha ao buscar avatar.")
            
        # Fallback de segurança na matriz em RAM
        return Image.new("RGBA", (512, 512), (0, 0, 0, 255))

    def apply_dark_streetwear(self, img: Image.Image) -> Image.Image:
        """
        Filtro Dark Streetwear:
        Aumenta o contraste e reduz o brilho (Luma global).
        """
        enhancer_contrast = ImageEnhance.Contrast(img)
        img = enhancer_contrast.enhance(1.5)
        
        enhancer_brightness = ImageEnhance.Brightness(img)
        # Redução parametrizada entre 0.7 e 0.8
        img = enhancer_brightness.enhance(0.75)
        
        return img

    def apply_glitch_vhs(self, img: Image.Image) -> Image.Image:
        """
        Filtro Glitch/VHS: 
        Desloca canais RGB e adiciona scanlines semi-transparentes em alpha 10-15%.
        """
        if img.mode != "RGBA":
            img = img.convert("RGBA")
            
        # O Split funciona melhor em RGB puro para evitar problemas com canal Alpha
        rgb_img = img.convert("RGB")
        r, g, b = rgb_img.split()
        
        # Desloca Vermelho para a direita (crop/paste nativo sem wrap ao redor)
        r_shifted = Image.new("L", r.size)
        r_shifted.paste(r, (15, 0))
        
        # Desloca Azul verticalmente
        b_shifted = Image.new("L", b.size)
        b_shifted.paste(b, (0, 15))
        
        # Reconstrução via Image.merge()
        glitched = Image.merge("RGB", (r_shifted, g, b_shifted))
        glitched_rgba = glitched.convert("RGBA")
        
        # Restaura o alpha original do avatar
        glitched_rgba.putalpha(img.getchannel("A"))
        
        # Adiciona Scanlines com opacidade controlada (~30 no canal Alpha equivale a ~12%)
        scanlines = Image.new("RGBA", glitched_rgba.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(scanlines)
        for y in range(0, glitched_rgba.height, 4):
            draw.line([(0, y), (glitched_rgba.width, y)], fill=(0, 0, 0, 30), width=2)
            
        final_img = Image.alpha_composite(glitched_rgba, scanlines)

        # Explicit Teardown SRE: Fechar buffers pesados da memória RAM
        rgb_img.close()
        r.close()
        g.close()
        b.close()
        r_shifted.close()
        b_shifted.close()
        glitched.close()
        glitched_rgba.close()
        scanlines.close()

        return final_img

    def _wrap_text(self, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
        """
        Algoritmo rigoroso de quebra de linha usando dimensões reais do font.getbbox().
        Garante que a string não vaze do limite de x_pixels definido.
        """
        words = text.split(" ")
        lines = []
        current_line = ""
        
        for word in words:
            test_line = f"{current_line}{word} " if current_line else f"{word} "
            
            # getbbox retorna (left, top, right, bottom)
            bbox = font.getbbox(test_line)
            # A largura exata ocupada na tela pela string atual com a fonte estipulada
            text_width = bbox[2] - bbox[0]
            
            if text_width <= max_width:
                current_line = test_line
            else:
                # Se excedeu a largura, joga a palavra atual para a próxima linha
                if current_line:
                    lines.append(current_line.strip())
                current_line = f"{word} "
                
        if current_line:
            lines.append(current_line.strip())
            
        return "\n".join(lines)

    async def render_card(self, avatar_url: str, template_name: str, rarity: str, mask_name: str,
                          atk: int, def_stat: int, spd: int, passive: str) -> io.BytesIO:
        """
        Orquestra a montagem da carta em um Buffer na memória de I/O, entregando um 
        arquivo sem gerar contenção de disco no host do Bot.
        """
        # 1. Downloader da Imagem do Discord CDN
        avatar = await self.fetch_avatar(avatar_url)
        
        # 2. Aplicação Condicional de Filtros por Raridade
        rarity_lower = rarity.lower()
        if rarity_lower == "dark_streetwear":
            avatar = self.apply_dark_streetwear(avatar)
        elif rarity_lower == "glitch_vhs":
            avatar = self.apply_glitch_vhs(avatar)
            
        # 3. Carregamento das Texturas
        try:
            with Image.open(f"{self.assets_path}{template_name}.png") as bt:
                base_texture = bt.convert("RGBA")
            with Image.open(f"{self.assets_path}{mask_name}.png") as m:
                mask = m.convert("L")
        except FileNotFoundError:
            base_texture = Image.new("RGBA", (1000, 1400), (20, 20, 20, 255))
            mask = Image.new("L", (1000, 1400), 255)

        avatar_resized = avatar.resize(base_texture.size, resample=Image.Resampling.BICUBIC)
        result_img = Image.composite(avatar_resized, base_texture, mask)
        
        draw = ImageDraw.Draw(result_img)
        try:
            font_title = ImageFont.truetype(f"{self.assets_path}fonts/Impact.ttf", 50)
            font_body = ImageFont.truetype(f"{self.assets_path}fonts/Roboto-Regular.ttf", 36)
        except OSError:
            font_title = ImageFont.load_default()
            font_body = ImageFont.load_default()

        draw.text((100, 1000), f"ATK: {atk}", font=font_title, fill=(255, 60, 60, 255))
        draw.text((450, 1000), f"DEF: {def_stat}", font=font_title, fill=(60, 180, 255, 255))
        draw.text((800, 1000), f"SPD: {spd}", font=font_title, fill=(255, 220, 60, 255))
        
        passive_text = f"Skill Passiva: {passive}"
        wrapped_passive = self._wrap_text(passive_text, font_body, max_width=800)
        draw.multiline_text((100, 1150), wrapped_passive, font=font_body, fill=(200, 200, 200, 255), spacing=15)

        buffer = io.BytesIO()
        result_img.save(buffer, format="PNG")
        buffer.seek(0)
        
        # Explicit OOM Defense: Força coletas manuais de memória
        avatar.close()
        avatar_resized.close()
        base_texture.close()
        mask.close()
        result_img.close()
        
        return buffer
