from __future__ import annotations

import asyncio
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageSequence, UnidentifiedImageError

LOGGER = logging.getLogger(__name__)

# coloque aqui o arquivo .ttf da "Futura Extra Bold Condensed"
FONT_PATH = Path("assets/fonts/futura_extra_bold_condensed.ttf")

# limite de processamento interno do bot.
# o upload final usa o limite do servidor (guild.filesize_limit) quando disponível.
MAX_INPUT_BYTES = 25 * 1024 * 1024
DEFAULT_UPLOAD_LIMIT = 8 * 1024 * 1024

URL_REGEX = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)

SUPPORTED_STATIC_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp"
}
SUPPORTED_GIF_EXTENSIONS = {".gif"}
SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"
}

# perfis de redução progressiva para vídeo -> gif.
# isso evita que um vídeo razoável vire um gif gigantesco demais para o Discord.
VIDEO_GIF_PROFILES = (
    (15, 720),
    (12, 640),
    (10, 540),
    (8, 480),
    (6, 360),
)


class MediaProcessingError(Exception):
    """Erro base para problemas de download, leitura ou processamento de mídia."""


class UnsupportedMediaError(MediaProcessingError):
    """A mídia existe, mas o formato não é suportado pelo cog."""


class MediaTooLargeError(MediaProcessingError):
    """A mídia de entrada ou saída ultrapassou os limites suportados."""


class NoMediaFoundError(MediaProcessingError):
    """Nenhuma mídia válida foi encontrada na mensagem alvo."""


@dataclass(slots=True)
class MediaPayload:
    """Container simples para transportar a mídia resolvida até o pipeline."""

    filename: str
    data: bytes
    kind: str  # "image" | "gif" | "video"
    source_url: Optional[str] = None
    content_type: Optional[str] = None


def clamp(value: int, minimum: int, maximum: int) -> int:
    """Mantém um valor num intervalo fechado [minimum, maximum]."""
    return max(minimum, min(value, maximum))


def detect_media_kind(filename: str, content_type: Optional[str] = None) -> Optional[str]:
    """
    Detecta o tipo lógico da mídia a partir do content-type e/ou extensão.

    Retorna:
        "image", "gif", "video" ou None.
    """
    ext = Path(filename).suffix.lower()

    if content_type:
        lowered = content_type.lower().split(";")[0].strip()
        if lowered == "image/gif":
            return "gif"
        if lowered.startswith("image/"):
            return "image"
        if lowered.startswith("video/"):
            return "video"

    if ext in SUPPORTED_GIF_EXTENSIONS:
        return "gif"
    if ext in SUPPORTED_STATIC_IMAGE_EXTENSIONS:
        return "image"
    if ext in SUPPORTED_VIDEO_EXTENSIONS:
        return "video"
    return None


def _measure_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    spacing: int = 4,
) -> tuple[int, int, tuple[int, int, int, int]]:
    """
    Mede texto simples ou multilinha.

    Retorna:
        (width, height, bbox)
    """
    if "\n" in text:
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align="center")
    else:
        bbox = draw.textbbox((0, 0), text, font=font)

    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return width, height, bbox


def _split_long_word(
    draw: ImageDraw.ImageDraw,
    word: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """
    Quebra uma palavra muito longa em pedaços menores quando ela sozinha
    ultrapassa a largura máxima permitida.
    """
    pieces: list[str] = []
    current = ""

    for char in word:
        candidate = f"{current}{char}"
        candidate_width, _, _ = _measure_text(draw, candidate, font)

        if candidate_width <= max_width or not current:
            current = candidate
        else:
            pieces.append(current)
            current = char

    if current:
        pieces.append(current)

    return pieces


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> str:
    """
    Faz word wrap guloso (greedy wrap).

    lógica:
    1) normaliza espaços.
    2) tenta adicionar a próxima palavra à linha atual.
    3) se a largura extrapolar `max_width`, fecha a linha e começa outra.
    4) se uma única palavra for maior que `max_width`, ela é quebrada em blocos.

    esse algoritmo é estável e previsível para memes porque:
    - respeita a largura real do texto renderizado pela fonte;
    - evita sobras enormes de espaço horizontal;
    - não depende de contagem fixa de caracteres, e sim da medida real em pixels.
    """
    text = " ".join(text.strip().split())
    if not text:
        return ""

    words = text.split(" ")
    lines: list[str] = []
    current_line = ""

    for word in words:
        word_width, _, _ = _measure_text(draw, word, font)
        if word_width > max_width:
            chunks = _split_long_word(draw, word, font, max_width)
        else:
            chunks = [word]

        for chunk in chunks:
            candidate = chunk if not current_line else f"{current_line} {chunk}"
            candidate_width, _, _ = _measure_text(draw, candidate, font)

            if candidate_width <= max_width:
                current_line = candidate
            else:
                if current_line:
                    lines.append(current_line)
                current_line = chunk

    if current_line:
        lines.append(current_line)

    return "\n".join(lines)


def build_caption_layout(
    image_width: int,
    image_height: int,
    caption: str,
    font_path: Path,
) -> tuple[ImageFont.FreeTypeFont, str, int, int]:
    """
    Calcula tipografia e altura da faixa branca.

    matemática do layout:
    - a largura útil do texto é `image_width - 2 * side_padding`
    - o tamanho inicial da fonte cresce proporcionalmente à largura da mídia
    - o texto é quebrado com base na largura real em pixels
    - a altura da faixa branca vira: `altura_do_texto + padding_vertical * 2`

    o loop reduz o font_size até a legenda caber com bom equilíbrio visual.
    isso evita duas coisas:
    - cortar frases longas
    - gerar uma barra branca gigantesca e desproporcional
    """
    if not font_path.exists():
        raise FileNotFoundError(
            f"fonte não encontrada em '{font_path}'. coloque o arquivo .ttf da futura extra bold condensed nesse caminho."
        )

    probe_image = Image.new("RGBA", (max(1, image_width), max(1, image_height)), (255, 255, 255, 0))
    draw = ImageDraw.Draw(probe_image)

    side_padding = max(20, int(image_width * 0.04))
    max_text_width = max(100, image_width - (side_padding * 2))

    start_font_size = clamp(int(image_width * 0.105), 22, 140)
    min_font_size = clamp(int(image_width * 0.04), 14, 32)

    chosen_font: Optional[ImageFont.FreeTypeFont] = None
    chosen_text = caption
    chosen_bar_height = 0
    chosen_spacing = 4

    for font_size in range(start_font_size, min_font_size - 1, -2):
        font = ImageFont.truetype(str(font_path), font_size)
        spacing = max(4, int(font_size * 0.15))

        wrapped = wrap_text(draw, caption, font, max_text_width)
        text_width, text_height, _ = _measure_text(draw, wrapped, font, spacing=spacing)

        vertical_padding = max(14, int(font_size * 0.40))
        bar_height = text_height + (vertical_padding * 2)

        # limite visual: a faixa branca não deve engolir a mídia inteira
        if text_width <= max_text_width and bar_height <= max(int(image_height * 0.50), 90):
            chosen_font = font
            chosen_text = wrapped
            chosen_bar_height = bar_height
            chosen_spacing = spacing
            break

    if chosen_font is None:
        chosen_font = ImageFont.truetype(str(font_path), min_font_size)
        chosen_spacing = max(4, int(min_font_size * 0.15))
        chosen_text = wrap_text(draw, caption, chosen_font, max_text_width)
        _, text_height, _ = _measure_text(draw, chosen_text, chosen_font, spacing=chosen_spacing)
        chosen_bar_height = text_height + (max(14, int(min_font_size * 0.40)) * 2)

    return chosen_font, chosen_text, chosen_bar_height, chosen_spacing


def add_caption_to_frame(frame: Image.Image, caption: str, font_path: Path) -> Image.Image:
    """
    Aplica a faixa branca + legenda em um frame único.

    Essa função serve tanto para:
    - imagens estáticas
    - cada frame individual de um GIF
    """
    source = frame.convert("RGBA")
    width, height = source.size

    font, wrapped_text, bar_height, spacing = build_caption_layout(width, height, caption, font_path)

    canvas = Image.new("RGBA", (width, height + bar_height), (255, 255, 255, 255))
    canvas.alpha_composite(source, dest=(0, bar_height))

    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, width, bar_height), fill=(255, 255, 255, 255))

    text_width, text_height, bbox = _measure_text(draw, wrapped_text, font, spacing=spacing)
    text_x = ((width - text_width) / 2) - bbox[0]
    text_y = ((bar_height - text_height) / 2) - bbox[1]

    draw.multiline_text(
        (text_x, text_y),
        wrapped_text,
        fill=(0, 0, 0, 255),
        font=font,
        align="center",
        spacing=spacing,
    )
    return canvas


def _serialize_static_image(processed: Image.Image, upload_limit: int) -> tuple[bytes, str]:
    """
    Serializa a imagem final.

    estratégia:
    1) tenta PNG primeiro para preservar qualidade;
    2) se passar do limite, tenta JPEG otimizado;
    3) se ainda passar, levanta erro amigável.
    """
    png_buffer = io.BytesIO()
    processed.save(png_buffer, format="PNG")
    png_bytes = png_buffer.getvalue()

    if len(png_bytes) <= upload_limit:
        return png_bytes, "png"

    rgb_image = processed.convert("RGB")
    jpeg_buffer = io.BytesIO()
    rgb_image.save(jpeg_buffer, format="JPEG", quality=90, optimize=True)
    jpeg_bytes = jpeg_buffer.getvalue()

    if len(jpeg_bytes) <= upload_limit:
        return jpeg_bytes, "jpg"

    raise MediaTooLargeError(
        "o resultado final ficou maior que o limite de upload do discord para este servidor."
    )


def process_static_image_bytes(
    image_bytes: bytes,
    caption: str,
    font_path: Path,
    upload_limit: int,
) -> tuple[bytes, str]:
    """Processa png/jpg/webp/bmp estático e devolve (bytes, extensão)."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            processed = add_caption_to_frame(image, caption, font_path)
            return _serialize_static_image(processed, upload_limit)
    except UnidentifiedImageError as exc:
        raise UnsupportedMediaError(
            "não consegui abrir essa imagem. confere se o arquivo não está corrompido."
        ) from exc


def process_gif_bytes(
    gif_bytes: bytes,
    caption: str,
    font_path: Path,
) -> tuple[bytes, str]:
    """
    Processa um gif animado frame a frame, preservando duração e loop.

    observação:
    o pillow expõe `duration` por frame e o metadado `loop`, então ambos
    são reaplicados na remontagem do gif.
    """
    try:
        with Image.open(io.BytesIO(gif_bytes)) as gif:
            frames: list[Image.Image] = []
            durations: list[int] = []
            loop = gif.info.get("loop", 0)
            default_duration = gif.info.get("duration", 80)

            for frame in ImageSequence.Iterator(gif):
                durations.append(frame.info.get("duration", default_duration))
                processed_frame = add_caption_to_frame(frame.copy(), caption, font_path)
                frames.append(processed_frame)

            if not frames:
                raise UnsupportedMediaError("esse gif não possui frames válidos para processar.")

            output = io.BytesIO()
            frames[0].save(
                output,
                format="GIF",
                save_all=True,
                append_images=frames[1:],
                duration=durations,
                loop=loop,
                disposal=2,
                optimize=False,
            )
            return output.getvalue(), "gif"
    except UnidentifiedImageError as exc:
        raise UnsupportedMediaError(
            "não consegui abrir esse gif. confere se o arquivo não está corrompido."
        ) from exc


async def convert_video_to_gif(
    input_path: Path,
    output_path: Path,
    fps_cap: int,
    max_width: int,
) -> None:
    """
    Converte vídeo em gif usando ffmpeg.

    usa palettegen/paletteuse para gerar gifs bem melhores do que uma conversão
    ingênua. o áudio é descartado porque o resultado final será gif.
    """
    filter_complex = (
        f"[0:v]fps={fps_cap},"
        f"scale=w='min({max_width},iw)':h=-1:flags=lanczos,"
        f"split[a][b];"
        f"[a]palettegen=stats_mode=diff[p];"
        f"[b][p]paletteuse"
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-an",
        "-filter_complex",
        filter_complex,
        "-loop",
        "0",
        str(output_path),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise MediaProcessingError(
            "ffmpeg não foi encontrado no sistema. instale o ffmpeg e garanta que ele esteja no PATH."
        ) from exc

    _, stderr = await process.communicate()

    if process.returncode != 0 or not output_path.exists():
        stderr_text = stderr.decode("utf-8", errors="ignore").strip()
        raise MediaProcessingError(
            f"falha ao converter o vídeo para gif com ffmpeg. detalhes: {stderr_text[-600:] or 'sem detalhes'}"
        )


async def process_video_bytes(
    video_bytes: bytes,
    caption: str,
    filename: str,
    font_path: Path,
    upload_limit: int,
) -> tuple[bytes, str]:
    """
    pipeline de vídeo:
    1) salva o vídeo temporariamente;
    2) converte para gif com ffmpeg;
    3) aplica a mesma lógica de gif frame a frame;
    4) se ficar grande demais, tenta perfis menores.
    """
    with TemporaryDirectory(prefix="caption_meme_") as temp_dir:
        temp_dir_path = Path(temp_dir)
        input_path = temp_dir_path / filename
        input_path.write_bytes(video_bytes)

        last_error: Optional[Exception] = None

        for fps_cap, max_width in VIDEO_GIF_PROFILES:
            gif_path = temp_dir_path / f"video_{fps_cap}_{max_width}.gif"

            try:
                await convert_video_to_gif(input_path, gif_path, fps_cap=fps_cap, max_width=max_width)
                gif_bytes = gif_path.read_bytes()

                processed_bytes, extension = await asyncio.to_thread(
                    process_gif_bytes,
                    gif_bytes,
                    caption,
                    font_path,
                )

                if len(processed_bytes) <= upload_limit:
                    return processed_bytes, extension

                last_error = MediaTooLargeError(
                    f"o gif gerado com perfil {fps_cap}fps/{max_width}px ainda passou do limite."
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc

        raise MediaTooLargeError(
            "não consegui gerar um gif pequeno o bastante para o limite de upload do discord. tenta um vídeo mais curto ou menor."
        ) from last_error


class CaptionMemeModal(discord.ui.Modal, title="Criar Caption Meme"):
    """modal disparado pelo menu de contexto para receber a legenda do usuário."""

    caption = discord.ui.TextInput(
        label="Legenda",
        placeholder="digite a legenda do meme...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )

    def __init__(self, cog: "CaptionMemeCog", target_message: discord.Message) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.target_message = target_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """recebe a legenda e dispara o pipeline principal usando a mensagem alvo."""
        await self.cog.process_message_media_request(
            interaction=interaction,
            message=self.target_message,
            caption=str(self.caption),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """falha amigável caso o modal quebre por qualquer motivo."""
        LOGGER.exception("erro no modal de caption meme.", exc_info=error)

        if interaction.response.is_done():
            await interaction.followup.send(
                "deu erro ao enviar a legenda. tenta de novo em alguns segundos.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "deu erro ao enviar a legenda. tenta de novo em alguns segundos.",
                ephemeral=True,
            )


class CaptionMemeCog(commands.Cog):
    """
    cog responsável por:
    - slash commands /caption e /meme
    - menu de contexto 'Criar Caption Meme'
    - resolução/download da mídia
    - despacho do pipeline correto (imagem, gif ou vídeo)
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.http_session: Optional[aiohttp.ClientSession] = None

        # context menu registrado manualmente porque ele não usa o decorator
        # tradicional de app_commands dentro do cog.
        self.message_context_menu = app_commands.ContextMenu(
            name="Criar Caption Meme",
            callback=self.caption_context_menu,
        )

    async def cog_load(self) -> None:
        """inicializa sessão http e registra o context menu no commandtree."""
        timeout = aiohttp.ClientTimeout(total=60)
        self.http_session = aiohttp.ClientSession(timeout=timeout)

        try:
            self.bot.tree.add_command(self.message_context_menu)
        except app_commands.CommandAlreadyRegistered:
            LOGGER.warning("context menu 'Criar Caption Meme' já estava registrado.")

    async def cog_unload(self) -> None:
        """remove o context menu e fecha a sessão http."""
        self.bot.tree.remove_command(
            self.message_context_menu.name,
            type=self.message_context_menu.type,
        )

        if self.http_session and not self.http_session.closed:
            await self.http_session.close()

    @app_commands.command(name="caption", description="Cria um caption meme em uma imagem, gif ou vídeo.")
    @app_commands.describe(
        arquivo="Imagem, gif ou vídeo que será transformado em meme.",
        legenda="Texto que ficará na faixa branca no topo.",
    )
    async def caption_slash(
        self,
        interaction: discord.Interaction,
        arquivo: discord.Attachment,
        legenda: str,
    ) -> None:
        """slash command /caption."""
        await self.process_attachment_request(
            interaction=interaction,
            attachment=arquivo,
            caption=legenda,
        )

    @app_commands.command(name="meme", description="Cria um caption meme em uma imagem, gif ou vídeo.")
    @app_commands.describe(
        arquivo="Imagem, gif ou vídeo que será transformado em meme.",
        legenda="Texto que ficará na faixa branca no topo.",
    )
    async def meme_slash(
        self,
        interaction: discord.Interaction,
        arquivo: discord.Attachment,
        legenda: str,
    ) -> None:
        """slash command /meme."""
        await self.process_attachment_request(
            interaction=interaction,
            attachment=arquivo,
            caption=legenda,
        )

    async def caption_context_menu(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        """
        callback do menu de contexto de mensagem.

        se a mensagem tiver mídia plausível, abre o modal.
        se não tiver, responde com erro efêmero.
        """
        if not self.message_has_media_candidate(message):
            await interaction.response.send_message(
                "essa mensagem não tem nenhuma mídia válida pra eu transformar em caption meme.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(CaptionMemeModal(self, message))

    async def process_attachment_request(
        self,
        interaction: discord.Interaction,
        attachment: discord.Attachment,
        caption: str,
    ) -> None:
        """
        pipeline para slash command com anexo direto.

        o defer vem primeiro porque processar gif/vídeo pode levar alguns segundos.
        """
        await interaction.response.defer(thinking=True)

        try:
            payload = await self.attachment_to_payload(attachment)
            result_bytes, extension = await self.process_payload(payload, caption, interaction)
            result_filename = f"{Path(payload.filename).stem}_caption_meme.{extension}"

            await interaction.edit_original_response(
                content="prontinho ✨",
                attachments=[discord.File(io.BytesIO(result_bytes), filename=result_filename)],
            )
        except MediaProcessingError as exc:
            await interaction.edit_original_response(
                content=f"não rolou processar a mídia: {exc}",
                attachments=[],
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("erro inesperado ao processar attachment de caption meme.", exc_info=exc)
            await interaction.edit_original_response(
                content="deu um erro inesperado enquanto eu montava o meme. dá uma olhada nos logs do bot.",
                attachments=[],
            )

    async def process_message_media_request(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
        caption: str,
    ) -> None:
        """pipeline do modal/context menu, resolvendo a mídia a partir da mensagem alvo."""
        await interaction.response.defer(thinking=True)

        try:
            payload = await self.extract_payload_from_message(message)
            result_bytes, extension = await self.process_payload(payload, caption, interaction)
            result_filename = f"{Path(payload.filename).stem}_caption_meme.{extension}"

            await interaction.edit_original_response(
                content="prontinho ✨",
                attachments=[discord.File(io.BytesIO(result_bytes), filename=result_filename)],
            )
        except MediaProcessingError as exc:
            await interaction.edit_original_response(
                content=f"não rolou processar a mídia: {exc}",
                attachments=[],
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("erro inesperado ao processar mensagem para caption meme.", exc_info=exc)
            await interaction.edit_original_response(
                content="deu um erro inesperado enquanto eu montava o meme. dá uma olhada nos logs do bot.",
                attachments=[],
            )

    async def process_payload(
        self,
        payload: MediaPayload,
        caption: str,
        interaction: discord.Interaction,
    ) -> tuple[bytes, str]:
        """
        despacha a mídia para o processador correto.

        imagens e gifs usam pillow em thread separada;
        vídeos usam ffmpeg + pillow.
        """
        upload_limit = getattr(interaction.guild, "filesize_limit", DEFAULT_UPLOAD_LIMIT)

        if len(payload.data) > MAX_INPUT_BYTES:
            raise MediaTooLargeError(
                f"essa mídia tem {len(payload.data) / (1024 * 1024):.1f}mb e passou do limite de processamento de {MAX_INPUT_BYTES / (1024 * 1024):.0f}mb."
            )

        if payload.kind == "image":
            return await asyncio.to_thread(
                process_static_image_bytes,
                payload.data,
                caption,
                FONT_PATH,
                upload_limit,
            )

        if payload.kind == "gif":
            processed_bytes, extension = await asyncio.to_thread(
                process_gif_bytes,
                payload.data,
                caption,
                FONT_PATH,
            )

            if len(processed_bytes) > upload_limit:
                raise MediaTooLargeError(
                    "o gif final ficou maior que o limite de upload do discord para este servidor."
                )

            return processed_bytes, extension

        if payload.kind == "video":
            return await process_video_bytes(
                payload.data,
                caption,
                payload.filename,
                FONT_PATH,
                upload_limit,
            )

        raise UnsupportedMediaError("esse tipo de arquivo ainda não é suportado.")

    async def attachment_to_payload(self, attachment: discord.Attachment) -> MediaPayload:
        """baixa o attachment do discord e o converte em mediapayload."""
        kind = detect_media_kind(attachment.filename, attachment.content_type)
        if kind is None:
            raise UnsupportedMediaError(
                "envie uma imagem, um gif ou um vídeo suportado (png, jpg, webp, gif, mp4, mov, webm, mkv, avi)."
            )

        if attachment.size > MAX_INPUT_BYTES:
            raise MediaTooLargeError(
                f"o arquivo enviado tem {attachment.size / (1024 * 1024):.1f}mb e passou do limite de processamento."
            )

        try:
            data = await attachment.read()
        except discord.HTTPException as exc:
            raise MediaProcessingError("falhei ao baixar o anexo do discord.") from exc

        return MediaPayload(
            filename=attachment.filename,
            data=data,
            kind=kind,
            source_url=attachment.url,
            content_type=attachment.content_type,
        )

    def message_has_media_candidate(self, message: discord.Message) -> bool:
        """
        faz uma triagem barata antes de abrir o modal.

        aqui não baixamos nada ainda; apenas verificamos se existe um anexo,
        embed ou url que *parece* apontar para mídia suportada.
        """
        for attachment in message.attachments:
            if detect_media_kind(attachment.filename, attachment.content_type):
                return True

        for embed in message.embeds:
            image_url = getattr(embed.image, "url", None)
            video_url = getattr(embed.video, "url", None)
            thumbnail_url = getattr(embed.thumbnail, "url", None)
            direct_url = getattr(embed, "url", None)

            for url in (image_url, video_url, thumbnail_url, direct_url):
                if not url:
                    continue
                filename = Path(urlparse(url).path).name or "media"
                if detect_media_kind(filename):
                    return True

        for url in URL_REGEX.findall(message.content or ""):
            filename = Path(urlparse(url).path).name or "media"
            if detect_media_kind(filename):
                return True

        return False

    async def extract_payload_from_message(self, message: discord.Message) -> MediaPayload:
        """
        resolve a mídia da mensagem na ordem:
        1) attachments
        2) embeds
        3) urls cruas no conteúdo da mensagem
        """
        for attachment in message.attachments:
            kind = detect_media_kind(attachment.filename, attachment.content_type)
            if kind:
                return await self.attachment_to_payload(attachment)

        candidate_urls: list[str] = []

        for embed in message.embeds:
            image_url = getattr(embed.image, "url", None)
            video_url = getattr(embed.video, "url", None)
            thumbnail_url = getattr(embed.thumbnail, "url", None)
            direct_url = getattr(embed, "url", None)

            for url in (image_url, video_url, thumbnail_url, direct_url):
                if url and url not in candidate_urls:
                    candidate_urls.append(url)

        for url in URL_REGEX.findall(message.content or ""):
            if url not in candidate_urls:
                candidate_urls.append(url)

        for url in candidate_urls:
            payload = await self.download_media_from_url(url)
            if payload:
                return payload

        raise NoMediaFoundError(
            "não achei nenhum anexo de imagem/vídeo nem um link direto de mídia nessa mensagem."
        )

    async def download_media_from_url(self, url: str) -> Optional[MediaPayload]:
        """
        baixa mídia direta de url usando aiohttp.

        retorna none quando a url não é mídia suportada ou quando o endpoint
        responde sem sucesso.
        """
        if self.http_session is None:
            raise MediaProcessingError("a sessão http do cog ainda não foi inicializada.")

        async with self.http_session.get(url) as response:
            if response.status != 200:
                return None

            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            filename = Path(urlparse(url).path).name or "media"
            kind = detect_media_kind(filename, content_type)

            if kind is None:
                return None

            content_length_header = response.headers.get("Content-Length")
            if content_length_header:
                content_length = int(content_length_header)
                if content_length > MAX_INPUT_BYTES:
                    raise MediaTooLargeError(
                        f"a mídia do link tem {content_length / (1024 * 1024):.1f}mb e passou do limite de processamento."
                    )

            buffer = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                buffer.extend(chunk)
                if len(buffer) > MAX_INPUT_BYTES:
                    raise MediaTooLargeError(
                        f"a mídia baixada passou do limite de processamento de {MAX_INPUT_BYTES / (1024 * 1024):.0f}mb."
                    )

            if not Path(filename).suffix:
                inferred_suffix = {
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                    "image/webp": ".webp",
                    "image/gif": ".gif",
                    "video/mp4": ".mp4",
                    "video/quicktime": ".mov",
                    "video/webm": ".webm",
                }.get(content_type, "")
                filename = f"media{inferred_suffix}"

            return MediaPayload(
                filename=filename,
                data=bytes(buffer),
                kind=kind,
                source_url=url,
                content_type=content_type,
            )

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """fallback de erro para app commands deste cog."""
        LOGGER.exception("erro em app command do captionmemecog.", exc_info=error)

        friendly_message = "deu ruim ao executar esse comando."

        if isinstance(error, app_commands.BotMissingPermissions):
            friendly_message = "eu preciso de permissão para enviar mensagens e anexar arquivos nesse canal."
        elif isinstance(error, app_commands.MissingPermissions):
            friendly_message = "você não tem as permissões necessárias pra usar esse comando aqui."

        if interaction.response.is_done():
            await interaction.followup.send(friendly_message, ephemeral=True)
        else:
            await interaction.response.send_message(friendly_message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """entry-point padrão de extensões discord.py."""
    await bot.add_cog(CaptionMemeCog(bot))