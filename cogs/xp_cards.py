from __future__ import annotations

import asyncio
import io
import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageOps


# ============================================================
# caminhos / arquivos
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# coloque seus .ttf aqui:
# assets/fonts/Montserrat-Bold.ttf
# assets/fonts/Montserrat-Regular.ttf
BOLD_FONT_PATH = FONTS_DIR / "Montserrat-Bold.ttf"
REGULAR_FONT_PATH = FONTS_DIR / "Montserrat-Regular.ttf"

COLOR_STORE_FILE = DATA_DIR / "rank_colors.json"


# ============================================================
# layout do card
# ============================================================

CARD_WIDTH = 750
CARD_HEIGHT = 250

# fundo externo do mockup (o verde) -> será customizável por servidor
DEFAULT_BACKGROUND_HEX = "#74CC00"

# painel interno roxo escuro
PANEL_LEFT = 17
PANEL_TOP = 12
PANEL_RIGHT = 734
PANEL_BOTTOM = 235
PANEL_RADIUS = 34

# avatar
AVATAR_RING_X = 50
AVATAR_RING_Y = 39
AVATAR_RING_SIZE = 172
AVATAR_PADDING = 14
AVATAR_SIZE = AVATAR_RING_SIZE - (AVATAR_PADDING * 2)

# área de conteúdo à direita
CONTENT_X = 250
USERNAME_Y = 42
LEVEL_Y = 50

# no mockup, a barra fica entre o nome e o texto do xp
BAR_X = 249
BAR_Y = 106
BAR_WIDTH = 403
BAR_HEIGHT = 39
BAR_RADIUS = BAR_HEIGHT // 2

XP_Y = 176

# cores fixas do estilo do card
PANEL_COLOR = (43, 9, 52, 255)
TEXT_PRIMARY = (255, 255, 255, 255)
TEXT_SECONDARY = (235, 235, 235, 255)
AVATAR_RING_COLOR = (226, 226, 226, 255)
BAR_BG_COLOR = (226, 226, 226, 255)
BAR_FILL_COLOR = (203, 108, 230, 255)

PLACEHOLDER_AVATAR_BG = (24, 24, 24, 255)
PLACEHOLDER_AVATAR_FG = (235, 235, 235, 255)


# ============================================================
# choices do /setcolor
# ============================================================

COLOR_CHOICES = [
    app_commands.Choice(name="verde neon", value="#74CC00"),
    app_commands.Choice(name="blurple", value="#5865F2"),
    app_commands.Choice(name="vermelho carmesim", value="#DC143C"),
    app_commands.Choice(name="esmeralda", value="#2ECC71"),
    app_commands.Choice(name="ouro", value="#F1C40F"),
    app_commands.Choice(name="azul meia-noite", value="#191970"),
    app_commands.Choice(name="coral", value="#FF7F50"),
    app_commands.Choice(name="rosa neon", value="#FF4FD8"),
    app_commands.Choice(name="turquesa", value="#1ABC9C"),
    app_commands.Choice(name="laranja queimado", value="#E67E22"),
    app_commands.Choice(name="cinza grafite", value="#2F3136"),
    app_commands.Choice(name="roxo arcano", value="#8E44AD"),
]


# ============================================================
# helpers de fonte / máscara
# ============================================================

@lru_cache(maxsize=64)
def load_ttf_font(font_path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """
    carrega fonte ttf com cache.
    se o arquivo não existir, cai para a fonte padrão do pillow.
    """
    try:
        return ImageFont.truetype(font_path, size=size)
    except OSError:
        return ImageFont.load_default()


@lru_cache(maxsize=8)
def make_circle_mask(size: int) -> Image.Image:
    """
    cria uma máscara circular em tons de cinza (L).
    branco = visível, preto = transparente.
    """
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size - 1, size - 1), fill=255)
    return mask


# ============================================================
# helpers de persistência / cor
# ============================================================

def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """
    converte '#RRGGBB' ou 'RRGGBB' para (R, G, B).
    também aceita formato curto '#RGB'.
    """
    value = hex_color.strip().lstrip("#")

    if len(value) == 3:
        value = "".join(char * 2 for char in value)

    if len(value) != 6:
        raise ValueError(f"hex inválido: {hex_color!r}")

    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError as exc:
        raise ValueError(f"hex inválido: {hex_color!r}") from exc


class GuildColorStore:
    """
    storage simples em json:
    { "guild_id": "#RRGGBB" }
    """

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, str] = load_json(path)
        self._lock = asyncio.Lock()

    def get_hex(self, guild_id: int) -> str:
        return self._data.get(str(guild_id), DEFAULT_BACKGROUND_HEX)

    async def set_hex(self, guild_id: int, hex_color: str) -> None:
        async with self._lock:
            self._data[str(guild_id)] = hex_color.upper()
            await asyncio.to_thread(save_json, self.path, self._data.copy())


# ============================================================
# helpers de imagem
# ============================================================

async def download_avatar_bytes(session: aiohttp.ClientSession, url: str) -> Optional[bytes]:
    """
    baixa o avatar em memória.
    retorna None se falhar, para o renderer usar placeholder.
    """
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            return await resp.read()
    except aiohttp.ClientError:
        return None
    except asyncio.TimeoutError:
        return None


def truncate_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    """
    corta o texto com reticências se exceder a largura disponível.
    """
    if draw.textlength(text, font=font) <= max_width:
        return text

    ellipsis = "..."
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]

    return (text + ellipsis) if text else ellipsis


def make_placeholder_avatar(username: str) -> Image.Image:
    """
    gera um avatar placeholder circular com a inicial do usuário.
    """
    img = Image.new("RGBA", (AVATAR_SIZE, AVATAR_SIZE), PLACEHOLDER_AVATAR_BG)
    draw = ImageDraw.Draw(img)

    initial = (username.strip()[:1] or "?").upper()
    font = load_ttf_font(str(BOLD_FONT_PATH), 64)

    # centraliza a letra no avatar placeholder
    draw.text(
        (AVATAR_SIZE // 2, AVATAR_SIZE // 2),
        initial,
        font=font,
        fill=PLACEHOLDER_AVATAR_FG,
        anchor="mm",
    )

    mask = make_circle_mask(AVATAR_SIZE).copy()
    img.putalpha(mask)
    return img


def prepare_avatar_image(avatar_bytes: Optional[bytes], username: str) -> Image.Image:
    """
    abre o avatar, redimensiona, recorta em círculo e devolve RGBA pronto.
    """
    if avatar_bytes is None:
        return make_placeholder_avatar(username)

    try:
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
        avatar = ImageOps.fit(
            avatar,
            (AVATAR_SIZE, AVATAR_SIZE),
            method=Image.Resampling.LANCZOS,
        )
    except Exception:
        return make_placeholder_avatar(username)

    mask = make_circle_mask(AVATAR_SIZE).copy()
    avatar.putalpha(mask)
    return avatar


def render_rank_card_sync(
    avatar_bytes: Optional[bytes],
    username: str,
    discriminator: Optional[str],
    current_xp: int,
    max_xp: int,
    level: int,
    background_color: tuple[int, int, int],
) -> bytes:
    """
    renderiza o card de rank de forma síncrona.
    essa função roda dentro de asyncio.to_thread.
    """
    bg_rgba = (*background_color, 255)
    canvas = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), bg_rgba)
    draw = ImageDraw.Draw(canvas)

    # painel roxo interno com cantos arredondados
    draw.rounded_rectangle(
        (PANEL_LEFT, PANEL_TOP, PANEL_RIGHT, PANEL_BOTTOM),
        radius=PANEL_RADIUS,
        fill=PANEL_COLOR,
    )

    # borda circular do avatar
    avatar_ring_box = (
        AVATAR_RING_X,
        AVATAR_RING_Y,
        AVATAR_RING_X + AVATAR_RING_SIZE,
        AVATAR_RING_Y + AVATAR_RING_SIZE,
    )
    draw.ellipse(avatar_ring_box, fill=AVATAR_RING_COLOR)

    # avatar circular por cima da borda
    avatar_img = prepare_avatar_image(avatar_bytes, username)
    canvas.alpha_composite(
        avatar_img,
        (AVATAR_RING_X + AVATAR_PADDING, AVATAR_RING_Y + AVATAR_PADDING),
    )

    # fontes
    username_font = load_ttf_font(str(BOLD_FONT_PATH), 31)
    level_font = load_ttf_font(str(BOLD_FONT_PATH), 22)
    xp_font = load_ttf_font(str(REGULAR_FONT_PATH), 26)

    # texto do level, alinhado à direita
    level_text = f"LEVEL {level}"
    level_bbox = draw.textbbox((0, 0), level_text, font=level_font)
    level_width = level_bbox[2] - level_bbox[0]
    level_x = PANEL_RIGHT - 28 - level_width

    draw.text(
        (level_x, LEVEL_Y),
        level_text,
        font=level_font,
        fill=TEXT_PRIMARY,
    )

    # username + discriminator opcional
    display_name = username.strip() or "usuário"
    if discriminator and discriminator != "0":
        display_name = f"{display_name}#{discriminator}"

    # evita o nome invadir a área do "LEVEL"
    max_name_width = max(80, level_x - CONTENT_X - 18)
    display_name = truncate_text(draw, display_name, username_font, max_name_width)

    draw.text(
        (CONTENT_X, USERNAME_Y),
        display_name,
        font=username_font,
        fill=TEXT_PRIMARY,
    )

    # barra de progresso
    # ratio é limitado entre 0.0 e 1.0 para evitar overflow visual
    safe_current_xp = max(0, current_xp)
    safe_max_xp = max(0, max_xp)
    ratio = 0.0 if safe_max_xp <= 0 else min(max(safe_current_xp / safe_max_xp, 0.0), 1.0)

    # fundo da barra
    draw.rounded_rectangle(
        (BAR_X, BAR_Y, BAR_X + BAR_WIDTH, BAR_Y + BAR_HEIGHT),
        radius=BAR_RADIUS,
        fill=BAR_BG_COLOR,
    )

    # preenchimento da barra
    # para manter o canto esquerdo arredondado de forma bonita, a gente:
    # 1) desenha uma barra cheia arredondada
    # 2) recorta apenas a largura correspondente ao ratio
    fill_width = int(round(BAR_WIDTH * ratio))
    if fill_width > 0:
        full_fill = Image.new("RGBA", (BAR_WIDTH, BAR_HEIGHT), (0, 0, 0, 0))
        fill_draw = ImageDraw.Draw(full_fill)
        fill_draw.rounded_rectangle(
            (0, 0, BAR_WIDTH - 1, BAR_HEIGHT - 1),
            radius=BAR_RADIUS,
            fill=BAR_FILL_COLOR,
        )

        cropped_fill = full_fill.crop((0, 0, fill_width, BAR_HEIGHT))
        canvas.alpha_composite(cropped_fill, (BAR_X, BAR_Y))

    # contador de xp
    # se quiser no formato "120 / 500 XP", é só trocar a string abaixo.
    xp_text = f"{safe_current_xp}/{safe_max_xp}XP"
    draw.text(
        (CONTENT_X, XP_Y),
        xp_text,
        font=xp_font,
        fill=TEXT_PRIMARY,
    )

    # exporta para bytes em memória, sem salvar no disco
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output.getvalue()


async def build_rank_file(
    session: aiohttp.ClientSession,
    avatar_url: str,
    username: str,
    discriminator: Optional[str],
    current_xp: int,
    max_xp: int,
    level: int,
    background_color: tuple[int, int, int],
) -> discord.File:
    """
    função auxiliar assíncrona pedida no enunciado.
    baixa o avatar e despacha o render do pillow para uma thread.
    """
    avatar_bytes = await download_avatar_bytes(session, avatar_url)

    png_bytes = await asyncio.to_thread(
        render_rank_card_sync,
        avatar_bytes,
        username,
        discriminator,
        current_xp,
        max_xp,
        level,
        background_color,
    )

    buffer = io.BytesIO(png_bytes)
    buffer.seek(0)
    return discord.File(buffer, filename="rank_card.png")


# ============================================================
# cog
# ============================================================

class RankCardCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.color_store = GuildColorStore(COLOR_STORE_FILE)
        self.http_session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self.http_session is None or self.http_session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self.http_session = aiohttp.ClientSession(timeout=timeout)
        return self.http_session

    def cog_unload(self) -> None:
        if self.http_session and not self.http_session.closed:
            asyncio.create_task(self.http_session.close())

    @app_commands.command(name="setcolor", description="define a cor de fundo do card de rank deste servidor")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(nova_cor="escolha uma cor pré-definida para o fundo do card")
    @app_commands.choices(nova_cor=COLOR_CHOICES)
    async def setcolor(
        self,
        interaction: discord.Interaction,
        nova_cor: app_commands.Choice[str],
    ) -> None:
        """
        salva a cor escolhida para o guild_id atual.
        """
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "esse comando só pode ser usado dentro de um servidor.",
                ephemeral=True,
            )
            return

        await self.color_store.set_hex(interaction.guild_id, nova_cor.value)

        await interaction.response.send_message(
            f"cor do card atualizada para **{nova_cor.name}** (`{nova_cor.value}`).",
            ephemeral=True,
        )

    @app_commands.command(name="rank", description="gera o card de rank")
    @app_commands.guild_only()
    @app_commands.describe(
        usuario="usuário alvo do card",
        current_xp="xp atual",
        max_xp="xp máximo do nível",
        level="nível atual",
    )
    async def rank(
        self,
        interaction: discord.Interaction,
        current_xp: int,
        max_xp: int,
        level: int,
        usuario: Optional[discord.Member] = None,
    ) -> None:
        """
        comando de exemplo para testar o renderer.
        no seu projeto real, você pode substituir current_xp/max_xp/level
        pelos valores vindos do seu banco/sistema de xp.
        """
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "esse comando só pode ser usado dentro de um servidor.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        target = usuario or interaction.user
        session = await self.get_session()

        background_hex = self.color_store.get_hex(interaction.guild_id)
        background_rgb = hex_to_rgb(background_hex)

        avatar_url = target.display_avatar.replace(static_format="png", size=256).url

        rank_file = await build_rank_file(
            session=session,
            avatar_url=avatar_url,
            username=target.display_name,
            discriminator=getattr(target, "discriminator", None),
            current_xp=current_xp,
            max_xp=max_xp,
            level=level,
            background_color=background_rgb,
        )

        await interaction.followup.send(file=rank_file)

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            if interaction.response.is_done():
                await interaction.followup.send(
                    "você precisa ser administrador pra usar esse comando.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "você precisa ser administrador pra usar esse comando.",
                    ephemeral=True,
                )
            return

        if interaction.response.is_done():
            await interaction.followup.send(
                f"deu uma bugadinha aqui: `{error}`",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"deu uma bugadinha aqui: `{error}`",
                ephemeral=True,
            )


# ============================================================
# setup
# ============================================================

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RankCardCog(bot))