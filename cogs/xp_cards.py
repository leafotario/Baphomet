from __future__ import annotations

"""Cards Visuais Do Sistema De XP Do Baphomet (Monalisa Edition)."""

import asyncio
import io
import random
from typing import Iterable

import discord
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .xp_models import LeaderboardEntry, RankSnapshot

# Caminhos sugeridos para as fontes customizadas. 
# O usuário deve colocar as fontes nesta pasta para o efeito máximo.
FONT_REG = "assets/fonts/Poppins-Regular.ttf"
FONT_BOLD = "assets/fonts/Poppins-Bold.ttf"


class XpCardRenderer:
    def __init__(self, *, font_regular_path: str = FONT_REG, font_bold_path: str = FONT_BOLD) -> None:
        self.font_regular_path = font_regular_path
        self.font_bold_path = font_bold_path

    def _font(self, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        path = self.font_bold_path if bold else self.font_regular_path
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            try:
                # Fallbacks nativos do Linux caso a fonte customizada não exista
                fallback = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
                return ImageFont.truetype(fallback, size=size)
            except OSError:
                return ImageFont.load_default()

    async def _read_asset(self, asset: discord.Asset | None) -> bytes | None:
        """Faz o fetch de assets de forma assíncrona (usa aiohttp do discord.py)"""
        if asset is None:
            return None
        try:
            return await asset.read()
        except Exception:
            return None

    # --- PIL Graphics Functions (CPU Bound - Executadas em Threads) ---

    def _get_dominant_color(self, img: Image.Image) -> tuple[int, int, int]:
        """Redimensiona para 1x1 para obter a cor média."""
        img_1x1 = img.resize((1, 1), resample=Image.Resampling.LANCZOS)
        color = img_1x1.getpixel((0, 0))
        if isinstance(color, tuple) and len(color) >= 3:
            return (color[0], color[1], color[2])
        return (120, 60, 240) # Roxo Baphomet fallback

    def _draw_text_with_shadow(
        self, 
        draw: ImageDraw.ImageDraw, 
        pos: tuple[int, int], 
        text: str, 
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont, 
        fill: tuple, 
        shadow_color: tuple = (0, 0, 0, 200), 
        offset: tuple[int, int] = (3, 3)
    ) -> None:
        """Desenha texto com Drop Shadow."""
        x, y = pos
        sx, sy = offset
        draw.text((x + sx, y + sy), text, font=font, fill=shadow_color)
        draw.text((x, y), text, font=font, fill=fill)

    def _create_circular_avatar(self, avatar_bytes: bytes | None, size: int) -> tuple[Image.Image, tuple[int, int, int]]:
        """Recorta um avatar com Anti-Aliasing perfeito e extrai a cor dominante."""
        if avatar_bytes:
            try:
                avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            except Exception:
                avatar = Image.new("RGBA", (size, size), (86, 64, 134, 255))
        else:
            avatar = Image.new("RGBA", (size, size), (86, 64, 134, 255))

        dominant_color = self._get_dominant_color(avatar)
        
        # Redimensionamento com altíssima qualidade
        avatar = ImageOps.fit(avatar, (size, size), method=Image.Resampling.LANCZOS)

        # Máscara supersampled (3x) para anti-aliasing (bordas perfeitas e suaves)
        mask_size = size * 3
        mask = Image.new("L", (mask_size, mask_size), 0)
        draw_mask = ImageDraw.Draw(mask)
        draw_mask.ellipse((0, 0, mask_size, mask_size), fill=255)
        mask = mask.resize((size, size), Image.Resampling.LANCZOS)

        output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        output.paste(avatar, (0, 0), mask)
        
        return output, dominant_color

    def _draw_progress_bar(
        self, 
        draw: ImageDraw.ImageDraw, 
        box: tuple[int, int, int, int], 
        ratio: float, 
        start_color: tuple = (163, 112, 255, 255), 
        end_color: tuple = (0, 255, 255, 255)
    ) -> Image.Image | None:
        """Barra de XP com bordas arredondadas e Gradiente dinâmico."""
        x0, y0, x1, y1 = box
        width = x1 - x0
        height = y1 - y0
        
        # Fundo translúcido da barra
        draw.rounded_rectangle(box, radius=height//2, fill=(0, 0, 0, 140), outline=(255, 255, 255, 40), width=1)
        
        filled_width = int(width * ratio)
        if filled_width < height: # Impede que a barra deforme se estiver muito vazia
             filled_width = min(height, width) if ratio > 0 else 0

        if filled_width > 0:
            # Layer do gradiente
            bar_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            bar_draw = ImageDraw.Draw(bar_layer)
            
            # Máscara para manter os cantos arrendondados perfeitos
            mask = Image.new("L", (width, height), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle((0, 0, filled_width, height), radius=height//2, fill=255)
            
            # Criação do gradiente de cor (Linear)
            for i in range(filled_width):
                r = int(start_color[0] + (end_color[0] - start_color[0]) * (i / width))
                g = int(start_color[1] + (end_color[1] - start_color[1]) * (i / width))
                b = int(start_color[2] + (end_color[2] - start_color[2]) * (i / width))
                a = int(start_color[3] + (end_color[3] - start_color[3]) * (i / width))
                bar_draw.line([(i, 0), (i, height)], fill=(r, g, b, a))
            
            bar_layer.putalpha(mask)
            return bar_layer
        return None

    def _create_glass_background(self, width: int, height: int, image_bytes: bytes | None, fallback_color: tuple = (20, 16, 30, 255)) -> Image.Image:
        """Gera o efeito Glassmorphism imersivo no fundo."""
        bg = None
        if image_bytes:
            try:
                bg = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
            except Exception:
                pass
        
        if not bg:
            bg = Image.new("RGBA", (width, height), fallback_color)
        else:
            bg = ImageOps.fit(bg, (width, height), method=Image.Resampling.LANCZOS)
            # Blur extremo para efeito "vidro"
            bg = bg.filter(ImageFilter.GaussianBlur(radius=35))
        
        # Overlay escuro para forçar legibilidade de elementos claros
        overlay = Image.new("RGBA", (width, height), (15, 10, 25, 170))
        return Image.alpha_composite(bg, overlay)

    def _truncate(self, value: str, max_chars: int) -> str:
        return value if len(value) <= max_chars else value[: max_chars - 1].rstrip() + "…"

    def _build_rank_card_image(self, guild_name: str, snapshot: RankSnapshot, avatar_bytes: bytes | None, banner_bytes: bytes | None) -> io.BytesIO:
        """Função Síncrona e bloqueante que constrói o card (Deve rodar em thread)"""
        width, height = 1040, 340
        
        # Background Glassmorphism (Usa Banner. Se não houver, usa Avatar)
        bg_source = banner_bytes if banner_bytes else avatar_bytes
        canvas = self._create_glass_background(width, height, bg_source)
        
        # Layer de Alpha Compositing
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        
        # Caixa principal fosca e elegante
        card_box = (24, 24, width - 24, height - 24)
        draw.rounded_rectangle(card_box, radius=32, fill=(0, 0, 0, 100), outline=(255, 255, 255, 45), width=2)
        
        # Avatar
        avatar_size = 190
        avatar, dom_color = self._create_circular_avatar(avatar_bytes, avatar_size)
        
        # Borda do Avatar (Sutil e dinâmica baseada na cor da imagem)
        border_size = avatar_size + 14
        draw.ellipse((60 - 7, 75 - 7, 60 + border_size - 7, 75 + border_size - 7), fill=dom_color + (255,))
        layer.paste(avatar, (60, 75), avatar)
        
        # Elementos de Texto
        guild_text = self._truncate(guild_name, 28).upper()
        user_name = self._truncate(snapshot.display_name, 20)
        rank_text = f"RANK #{snapshot.position}" if snapshot.position is not None else "SEM RANK"
        progress_text = f"{snapshot.xp_into_level:,} / {snapshot.xp_for_next_level:,} XP"
        
        # Textos com Sombra
        self._draw_text_with_shadow(draw, (300, 60), guild_text, font=self._font(26, bold=True), fill=(210, 210, 210, 255))
        self._draw_text_with_shadow(draw, (300, 120), user_name, font=self._font(54, bold=True), fill=(255, 255, 255, 255))
        
        self._draw_text_with_shadow(draw, (300, 200), f"LVL {snapshot.level}", font=self._font(42, bold=True), fill=dom_color + (255,))
        self._draw_text_with_shadow(draw, (470, 215), f"Total: {snapshot.total_xp:,} XP", font=self._font(22), fill=(220, 220, 220, 255))
        
        self._draw_text_with_shadow(draw, (width - 340, 60), rank_text, font=self._font(36, bold=True), fill=(255, 220, 50, 255))
        
        # Barra de Progresso
        bar_box = (300, 260, width - 60, 295)
        # Gradiente da cor primária do avatar para neon cyan
        gradient_bar = self._draw_progress_bar(draw, bar_box, snapshot.progress_ratio, start_color=dom_color+(255,), end_color=(0,255,255,255))
        if gradient_bar:
            layer.paste(gradient_bar, (bar_box[0], bar_box[1]), gradient_bar)
            
        # Texto dentro da barra
        text_bbox = draw.textbbox((0, 0), progress_text, font=self._font(20, bold=True))
        text_w = text_bbox[2] - text_bbox[0]
        text_x = bar_box[0] + (bar_box[2] - bar_box[0]) // 2 - text_w // 2
        self._draw_text_with_shadow(draw, (text_x, bar_box[1] + 5), progress_text, font=self._font(20, bold=True), fill=(255, 255, 255, 255), shadow_color=(0,0,0,255))
        
        # Mesclar as Layers
        final_img = Image.alpha_composite(canvas, layer)
        output = io.BytesIO()
        final_img.save(output, format="PNG")
        output.seek(0)
        return output

    # ══════════════════════════════════════════════════════════════════
    # ░░░░░░░░ EASTER EGG: O CARD DO BAPHOMET ░░░░░░░░░░░░░░░░░░░░░░░
    # ══════════════════════════════════════════════════════════════════
    # Este método é SEPARADO de propósito.
    # Ele NÃO consulta o banco de dados. Ele NÃO usa RankSnapshot.
    # Ele existe UNICAMENTE para a brincadeira de quando alguém
    # tenta ver o /rank do próprio bot.
    # ══════════════════════════════════════════════════════════════════

    def _build_baphomet_card(self, guild_name: str, bot_name: str, avatar_bytes: bytes | None) -> io.BytesIO:
        """Card especial do Baphomet: glitchado, corrompido, transcendental."""
        width, height = 1040, 340

        # ── Fundo: Púrpura abissal em vez de glassmorphism ──
        canvas = Image.new("RGBA", (width, height), (15, 5, 25, 255))

        # Scanlines horizontais para efeito de "tela falhando"
        # Cada linha ímpar recebe uma faixa escura translúcida
        scanline_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        scan_draw = ImageDraw.Draw(scanline_layer)
        for y in range(0, height, 4):
            scan_draw.line([(0, y), (width, y)], fill=(0, 0, 0, 60))
        canvas = Image.alpha_composite(canvas, scanline_layer)

        # ── Layer principal de conteúdo ──
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)

        # Caixa principal com borda vermelha neon (alerta de sistema)
        draw.rounded_rectangle(
            (24, 24, width - 24, height - 24),
            radius=32,
            fill=(0, 0, 0, 120),
            outline=(255, 30, 30, 200),
            width=3,
        )

        # ── Avatar do Bot ──
        avatar_size = 190
        avatar, _ = self._create_circular_avatar(avatar_bytes, avatar_size)
        # Borda vermelha neon no avatar
        border_size = avatar_size + 14
        draw.ellipse(
            (60 - 7, 75 - 7, 60 + border_size - 7, 75 + border_size - 7),
            fill=(255, 0, 0, 255),
        )
        layer.paste(avatar, (60, 75), avatar)

        # ── Textos "Corrompidos" ──
        guild_text = self._truncate(guild_name, 28).upper()
        self._draw_text_with_shadow(
            draw, (300, 60), guild_text,
            font=self._font(26, bold=True),
            fill=(210, 60, 60, 255),
        )
        # Nome do bot
        self._draw_text_with_shadow(
            draw, (300, 120), bot_name,
            font=self._font(54, bold=True),
            fill=(255, 255, 255, 255),
        )

        # Nível: [DADOS CORROMPIDOS]
        self._draw_text_with_shadow(
            draw, (300, 200), "LVL ∞",
            font=self._font(42, bold=True),
            fill=(255, 30, 30, 255),
        )
        # XP absurdo
        self._draw_text_with_shadow(
            draw, (470, 215), "Total: ℵ₀ XP",
            font=self._font(22),
            fill=(255, 100, 100, 255),
        )

        # Rank #0 — acima de todo mundo
        self._draw_text_with_shadow(
            draw, (width - 340, 60), "RANK #0",
            font=self._font(36, bold=True),
            fill=(255, 50, 50, 255),
        )

        # ── BARRA DE XP OVERFLOW (O BUG DE PROPÓSITO) ──
        # A barra ULTRAPASSA o limite direito do card intencionalmente
        bar_y0, bar_y1 = 260, 295
        bar_x0 = 300
        bar_overflow_x1 = width + 200  # 200px ALÉM da borda do card

        # Fundo da barra (cinza normal, dentro dos limites)
        draw.rounded_rectangle(
            (bar_x0, bar_y0, width - 60, bar_y1),
            radius=(bar_y1 - bar_y0) // 2,
            fill=(0, 0, 0, 140),
            outline=(255, 255, 255, 40),
            width=1,
        )

        # Barra preenchida: vermelho neon SANGRANDO para fora do card
        overflow_bar = Image.new("RGBA", (width + 200, height), (0, 0, 0, 0))
        overflow_draw = ImageDraw.Draw(overflow_bar)

        bar_width = bar_overflow_x1 - bar_x0
        for i in range(bar_width):
            # Gradiente: roxo escuro → vermelho neon → magenta
            ratio = i / bar_width
            r = int(120 + 135 * ratio)
            g = int(0 + 30 * (1 - ratio))
            b = int(180 * (1 - ratio) + 60 * ratio)
            overflow_draw.line(
                [(bar_x0 + i, bar_y0 + 2), (bar_x0 + i, bar_y1 - 2)],
                fill=(r, g, b, 230),
            )

        # Cola a barra overflow no layer (será cortada pelo tamanho do canvas)
        layer.paste(overflow_bar.crop((0, 0, width, height)), (0, 0), overflow_bar.crop((0, 0, width, height)))

        # Texto dentro da barra
        progress_text = "999.999.999 / 10 XP"
        text_bbox = draw.textbbox((0, 0), progress_text, font=self._font(20, bold=True))
        text_w = text_bbox[2] - text_bbox[0]
        text_x = bar_x0 + (width - 60 - bar_x0) // 2 - text_w // 2
        self._draw_text_with_shadow(
            draw, (text_x, bar_y0 + 5), progress_text,
            font=self._font(20, bold=True),
            fill=(255, 255, 255, 255),
            shadow_color=(0, 0, 0, 255),
        )

        # ── EFEITO GLITCH: Texto aleatório corrompido ──
        glitch_texts = [
            "ERR_OVERFLOW", "0xDEADBEEF", "STACK SMASH",
            "NaN", "segfault", "kernel panic",
            "sudo rm -rf /xp/*", "BUFFER_OVERFLOW",
            "¿¿¿???", "█▓▒░ CORRUPTED ░▒▓█",
        ]
        glitch_font = self._font(14)
        for _ in range(random.randint(4, 8)):
            gx = random.randint(40, width - 200)
            gy = random.randint(30, height - 40)
            text = random.choice(glitch_texts)
            alpha = random.randint(40, 120)
            draw.text(
                (gx, gy), text,
                font=glitch_font,
                fill=(255, random.randint(0, 80), random.randint(0, 80), alpha),
            )

        # ── Linhas de glitch horizontais aleatórias ──
        for _ in range(random.randint(3, 6)):
            gy = random.randint(0, height)
            gh = random.randint(1, 4)
            draw.rectangle(
                [(0, gy), (width, gy + gh)],
                fill=(255, 0, random.randint(60, 180), random.randint(30, 90)),
            )

        # Mesclar Layers
        final_img = Image.alpha_composite(canvas, layer)
        output = io.BytesIO()
        final_img.save(output, format="PNG")
        output.seek(0)
        return output

    async def render_baphomet_card(self, *, guild: discord.Guild, bot_user: discord.Member | discord.User) -> io.BytesIO:
        """API Assíncrona para o Easter Egg do Baphomet. Sem consulta ao BD."""
        avatar_bytes = await self._read_asset(bot_user.display_avatar)
        return await asyncio.to_thread(
            self._build_baphomet_card,
            guild.name,
            bot_user.display_name,
            avatar_bytes,
        )

    async def render_rank_card(self, *, guild: discord.Guild, member: discord.Member | discord.User, snapshot: RankSnapshot) -> io.BytesIO:
        """API Assíncrona. Fetch concorrente de imagens e execução em ThreadPool."""
        
        # Download paralelo
        tasks = [self._read_asset(member.display_avatar)]
        
        # Tenta buscar o banner, requer um objeto User (Member não possui .banner carregado por padrão)
        if getattr(member, "banner", None) is not None:
            tasks.append(self._read_asset(member.banner))
        else:
            # Caso não tenha o banner em cache, tentamos usar a foto base
            async def null_coro(): return None
            tasks.append(null_coro())
            
        results = await asyncio.gather(*tasks)
        avatar_bytes = results[0]
        banner_bytes = results[1]
        
        # Offload para a Thread
        return await asyncio.to_thread(self._build_rank_card_image, guild.name, snapshot, avatar_bytes, banner_bytes)

    def _build_leaderboard_image(self, guild_name: str, icon_bytes: bytes | None, entries_data: list[tuple[LeaderboardEntry, bytes | None]], page_label: str = "") -> io.BytesIO:
        """Constrói o leaderboard escalando a altura magicamente de acordo com N itens."""
        width = 1180
        row_height = 140
        top_padding = 190
        bottom_padding = 60
        
        # Altura dinâmica à prova de futuro
        height = top_padding + (row_height * max(1, len(entries_data))) + bottom_padding
        
        canvas = self._create_glass_background(width, height, icon_bytes)
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        
        draw.rounded_rectangle((24, 24, width - 24, height - 24), radius=35, fill=(0, 0, 0, 110), outline=(255, 255, 255, 30), width=2)
        
        self._draw_text_with_shadow(draw, (60, 50), "HALL DA GLÓRIA", font=self._font(58, bold=True), fill=(255, 255, 255, 255))
        self._draw_text_with_shadow(draw, (60, 120), self._truncate(guild_name, 45), font=self._font(30), fill=(210, 210, 210, 255))
        
        # Indicador de página no canto superior direito
        if page_label:
            self._draw_text_with_shadow(draw, (width - 280, 65), page_label, font=self._font(26, bold=True), fill=(180, 180, 180, 255))
        
        # Cores Premium para o Pódio (posições globais 1, 2, 3)
        medal_colors = {1: (255, 215, 0, 255), 2: (211, 211, 211, 255), 3: (205, 127, 50, 255)}
        
        if not entries_data:
            self._draw_text_with_shadow(draw, (60, 210), "Nenhuma lenda emergiu neste servidor ainda.", font=self._font(30), fill=(180, 180, 180, 255))
        else:
            for visual_index, (entry, avatar_bytes) in enumerate(entries_data):
                y = top_padding + (visual_index * row_height)
                
                # Usa a posição REAL do ranking (entry.position), não o índice local
                rank = entry.position
                
                # Container da Linha
                bg_alpha = 150 if rank == 1 else 110 if rank == 2 else 90 if rank == 3 else 60
                outline_color = (255, 255, 255, 40) if rank <= 3 else None
                draw.rounded_rectangle((50, y, width - 50, y + 120), radius=28, fill=(0, 0, 0, bg_alpha), outline=outline_color, width=2)
                
                # Badge de Posição
                badge_color = medal_colors.get(rank, (80, 80, 80, 200))
                draw.rounded_rectangle((75, y + 25, 145, y + 95), radius=20, fill=badge_color)
                
                # Texto centralizado no badge
                rank_text = f"#{rank}"
                text_bbox = draw.textbbox((0,0), rank_text, font=self._font(36, bold=True))
                text_w = text_bbox[2] - text_bbox[0]
                self._draw_text_with_shadow(draw, (75 + (70 - text_w)//2, y + 25), rank_text, font=self._font(36, bold=True), fill=(0, 0, 0, 255), shadow_color=(0,0,0,0))
                
                # Avatar
                avatar, dom_color = self._create_circular_avatar(avatar_bytes, 85)
                draw.ellipse((161, y + 15, 249, y + 103), fill=dom_color + (255,))
                layer.paste(avatar, (165, y + 19), avatar)
                
                # Info
                display_name = self._truncate(entry.display_name, 22)
                name_color = badge_color if rank <= 3 else (255, 255, 255, 255)
                self._draw_text_with_shadow(draw, (280, y + 25), display_name, font=self._font(32, bold=True), fill=name_color)
                self._draw_text_with_shadow(draw, (280, y + 70), f"LVL {entry.level}  •  {entry.total_xp:,} XP", font=self._font(22), fill=(200, 200, 200, 255))
                
                # Progress Bar Menor
                bar_box = (720, y + 45, 1080, y + 65)
                grad_start = badge_color if rank <= 3 else dom_color + (255,)
                gradient_bar = self._draw_progress_bar(draw, bar_box, entry.progress_ratio, start_color=grad_start, end_color=(0, 255, 255, 255))
                if gradient_bar:
                    layer.paste(gradient_bar, (bar_box[0], bar_box[1]), gradient_bar)
                    
                # Texto Progress Menor
                self._draw_text_with_shadow(draw, (720, y + 75), f"Faltam {entry.remaining_to_next:,} XP", font=self._font(18), fill=(180, 180, 180, 255))
                
        final_img = Image.alpha_composite(canvas, layer)
        output = io.BytesIO()
        final_img.save(output, format="PNG")
        output.seek(0)
        return output

    async def render_leaderboard_card(
        self,
        *,
        guild: discord.Guild,
        entries: Iterable[tuple[LeaderboardEntry, discord.Member | discord.User | None]],
        page_label: str = "",
    ) -> io.BytesIO:
        """Busca todas as imagens (Icone, Avatares) e delega para thread pool."""
        entries_list = list(entries)
        
        # Download paralelo do logo do server e avatares
        tasks = [self._read_asset(guild.icon)]
        for _, member in entries_list:
            if member:
                tasks.append(self._read_asset(member.display_avatar))
            else:
                async def null_coro(): return None
                tasks.append(null_coro())
                
        results = await asyncio.gather(*tasks)
        icon_bytes = results[0]
        avatars_bytes = results[1:]
        
        # Monta payload para o Worker
        entries_data = [(entry, avatar) for (entry, _), avatar in zip(entries_list, avatars_bytes)]
        
        return await asyncio.to_thread(self._build_leaderboard_image, guild.name, icon_bytes, entries_data, page_label)