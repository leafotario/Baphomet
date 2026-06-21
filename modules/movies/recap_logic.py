import asyncio
import io
import logging
from typing import Any

import aiohttp
import discord
from PIL import Image, ImageDraw

LOGGER = logging.getLogger("baphomet.recap_logic")

POSTER_WIDTH = 300
POSTER_HEIGHT = 450
PADDING = 20
BACKGROUND_COLOR = (25, 25, 25)

NUMBER_EMOJIS = [
    "1️⃣",
    "2️⃣",
    "3️⃣",
    "4️⃣",
    "5️⃣",
    "6️⃣",
    "7️⃣",
    "8️⃣",
    "9️⃣",
    "🔟"
]

async def download_image(session: aiohttp.ClientSession, url: str) -> Image.Image | None:
    if not url or url == "N/A":
        return None
        
    try:
        async with session.get(url, timeout=10.0) as response:
            if response.status == 200:
                data = await response.read()
                image = Image.open(io.BytesIO(data))
                return image.convert("RGBA")
    except Exception as e:
        LOGGER.warning("Falha ao baixar imagem do recap url=%s: %s", url, e)
        return None

async def generate_recap_collage(urls: list[str]) -> io.BytesIO | None:
    if not urls:
        return None

    # Limitar para os primeiros 7 itens conforme a especificação
    urls = urls[:7]
    num_items = len(urls)

    async with aiohttp.ClientSession() as session:
        tasks = [download_image(session, url) for url in urls]
        images = await asyncio.gather(*tasks)

    # Cria imagem base
    total_width = (POSTER_WIDTH * num_items) + (PADDING * (num_items + 1))
    total_height = POSTER_HEIGHT + (PADDING * 2)

    collage = Image.new("RGBA", (total_width, total_height), BACKGROUND_COLOR)

    for idx, img in enumerate(images):
        x_offset = PADDING + (idx * (POSTER_WIDTH + PADDING))
        y_offset = PADDING

        if img:
            # Redimensiona mantendo proporção e cropa para o tamanho ideal
            img_ratio = img.width / img.height
            target_ratio = POSTER_WIDTH / POSTER_HEIGHT

            if img_ratio > target_ratio:
                # Imagem mais larga, ajusta altura e cropa largura
                new_height = POSTER_HEIGHT
                new_width = int(new_height * img_ratio)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                
                left = (new_width - POSTER_WIDTH) // 2
                img = img.crop((left, 0, left + POSTER_WIDTH, POSTER_HEIGHT))
            else:
                # Imagem mais alta, ajusta largura e cropa altura
                new_width = POSTER_WIDTH
                new_height = int(new_width / img_ratio)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                
                top = (new_height - POSTER_HEIGHT) // 2
                img = img.crop((0, top, POSTER_WIDTH, top + POSTER_HEIGHT))

            collage.paste(img, (x_offset, y_offset), img if img.mode == 'RGBA' else None)
        else:
            # Fallback para espaço vazio/placeholder
            placeholder = Image.new("RGBA", (POSTER_WIDTH, POSTER_HEIGHT), (50, 50, 50, 255))
            draw = ImageDraw.Draw(placeholder)
            draw.line((0, 0, POSTER_WIDTH, POSTER_HEIGHT), fill=(100, 100, 100), width=2)
            draw.line((0, POSTER_HEIGHT, POSTER_WIDTH, 0), fill=(100, 100, 100), width=2)
            collage.paste(placeholder, (x_offset, y_offset))

    buffer = io.BytesIO()
    collage.convert("RGB").save(buffer, format="JPEG", quality=90)
    buffer.seek(0)
    return buffer

def build_recap_embed(items: list[dict[str, Any]], system_type: str) -> tuple[discord.Embed, list[str]]:
    title = "🎬 Recap Semanal: Qual foi o seu favorito?" if system_type == "MOTD" else "🎧 Recap Semanal: Qual foi o seu favorito?"
    color = discord.Color.gold() if system_type == "MOTD" else discord.Color.blurple()

    embed = discord.Embed(
        title=title,
        color=color,
        description="Esses foram os selecionados da semana. Vote no seu favorito clicando nas reações abaixo!"
    )

    lines = []
    emojis_to_add = []
    
    for i, item in enumerate(items[:7]):
        emoji = NUMBER_EMOJIS[i]
        emojis_to_add.append(emoji)
        
        name = item.get("title") or item.get("nome") or "Desconhecido"
        lines.append(f"{emoji} **{name}**")
        
    if lines:
        embed.description += "\n\n" + "\n".join(lines)
    else:
        embed.description += "\n\n*(Nenhum item registrado nesta semana)*"

    embed.set_footer(text="Baphomet Weekly Recap")
    return embed, emojis_to_add
