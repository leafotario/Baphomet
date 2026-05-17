from __future__ import annotations

import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from .errors import (
    AnimatedImageNotSupportedError,
    ImageTooLargeError,
    InvalidImageError,
    OutputTooLargeError,
    UnsupportedMediaError,
)


SUPPORTED_IMAGE_FORMATS = {"PNG", "JPEG", "JPG", "WEBP", "GIF"}


@dataclass(frozen=True)
class FadeInImageConfig:
    max_input_bytes: int = 15 * 1024 * 1024
    max_output_bytes: int = 8 * 1024 * 1024
    min_width: int = 32
    min_height: int = 32
    max_width: int = 2000
    max_height: int = 2000
    max_pixels: int = 4_000_000
    fps: int = 20
    duration_seconds: float = 1.0
    output_filename: str = "fadein.gif"
    gif_optimize: bool = True


def validate_input_size(data: bytes, config: FadeInImageConfig) -> None:
    """Reject empty inputs or payloads above the configured byte limit."""

    if not data:
        raise InvalidImageError("A imagem enviada esta vazia.", code="image_empty")
    if len(data) > config.max_input_bytes:
        raise ImageTooLargeError(
            "Essa imagem e grande demais para processar com seguranca.",
            code="image_input_too_large",
        )


def open_and_validate_static_image(data: bytes, config: FadeInImageConfig) -> Image.Image:
    """Open, verify, orient, validate, and normalize an image to RGB."""

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image_format = _normalized_format(image)
                if image_format not in SUPPORTED_IMAGE_FORMATS:
                    raise UnsupportedMediaError(
                        "Formato de imagem nao suportado.",
                        code="unsupported_image_format",
                    )
                if _is_animated(image):
                    raise AnimatedImageNotSupportedError(
                        "Imagem animada nao e suportada por este comando.",
                        code="animated_image_not_supported",
                    )
                image.verify()
    except (UnsupportedMediaError, AnimatedImageNotSupportedError):
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageTooLargeError(
            "Essa imagem tem resolucao grande demais para processar com seguranca.",
            code="image_decompression_bomb",
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError(
            "Nao consegui abrir essa imagem. Talvez o arquivo esteja corrompido.",
            code="image_invalid",
        ) from exc

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image_format = _normalized_format(image)
                if image_format not in SUPPORTED_IMAGE_FORMATS:
                    raise UnsupportedMediaError(
                        "Formato de imagem nao suportado.",
                        code="unsupported_image_format",
                    )
                if _is_animated(image):
                    raise AnimatedImageNotSupportedError(
                        "Imagem animada nao e suportada por este comando.",
                        code="animated_image_not_supported",
                    )

                oriented = ImageOps.exif_transpose(image)
                oriented.load()
                validate_dimensions(oriented, config)
                return _flatten_transparency_to_black(oriented)
    except (UnsupportedMediaError, AnimatedImageNotSupportedError, ImageTooLargeError):
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageTooLargeError(
            "Essa imagem tem resolucao grande demais para processar com seguranca.",
            code="image_decompression_bomb",
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError(
            "Nao consegui abrir essa imagem. Talvez o arquivo esteja corrompido.",
            code="image_invalid",
        ) from exc


def validate_dimensions(image: Image.Image, config: FadeInImageConfig) -> None:
    """Validate image dimensions against configurable safety limits."""

    width, height = image.size
    if width < config.min_width:
        raise ImageTooLargeError(
            f"A imagem e estreita demais. Minimo: {config.min_width}px.",
            code="image_width_too_small",
        )
    if height < config.min_height:
        raise ImageTooLargeError(
            f"A imagem e baixa demais. Minimo: {config.min_height}px.",
            code="image_height_too_small",
        )
    if width > config.max_width:
        raise ImageTooLargeError(
            f"A imagem e larga demais. Maximo: {config.max_width}px.",
            code="image_width_too_large",
        )
    if height > config.max_height:
        raise ImageTooLargeError(
            f"A imagem e alta demais. Maximo: {config.max_height}px.",
            code="image_height_too_large",
        )
    if width * height > config.max_pixels:
        raise ImageTooLargeError(
            "Essa imagem tem pixels demais para processar com seguranca.",
            code="image_pixel_limit",
        )


def build_fadein_frames(image: Image.Image, config: FadeInImageConfig) -> list[Image.Image]:
    """Build linear fade-in frames from black to the original RGB image."""

    if image.mode != "RGB":
        image = image.convert("RGB")

    total_frames = _total_frames(config)
    black_frame = Image.new("RGB", image.size, (0, 0, 0))
    frames: list[Image.Image] = []

    for index in range(total_frames):
        if index == 0:
            frame = black_frame.copy()
        elif index == total_frames - 1:
            frame = image.copy()
        else:
            progress = index / (total_frames - 1)
            frame = Image.blend(black_frame, image, progress)
        frames.append(frame)

    return frames


def save_frames_as_gif(frames: list[Image.Image], config: FadeInImageConfig) -> io.BytesIO:
    """Save frames as a looping GIF in memory and validate output size."""

    if not frames:
        raise InvalidImageError("Nenhum frame foi gerado para o GIF.", code="gif_no_frames")

    duration_per_frame_ms = _duration_per_frame_ms(config, len(frames))
    output = io.BytesIO()
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=duration_per_frame_ms,
        loop=0,
        optimize=config.gif_optimize,
        disposal=2,
    )
    output.seek(0)

    if output.getbuffer().nbytes > config.max_output_bytes:
        raise OutputTooLargeError(
            "O GIF final ficou grande demais para enviar.",
            code="output_too_large",
        )
    return output


def process_fadein_image_bytes(
    data: bytes,
    config: FadeInImageConfig | None = None,
) -> tuple[io.BytesIO, str]:
    """Process static image bytes and return a GIF buffer ready for discord.File."""

    processor_config = config or FadeInImageConfig()
    validate_input_size(data, processor_config)
    image = open_and_validate_static_image(data, processor_config)
    frames = build_fadein_frames(image, processor_config)
    output = save_frames_as_gif(frames, processor_config)
    return output, processor_config.output_filename


def _normalized_format(image: Image.Image) -> str:
    image_format = (image.format or "").upper()
    return "JPEG" if image_format == "JPG" else image_format


def _is_animated(image: Image.Image) -> bool:
    return bool(getattr(image, "is_animated", False)) or int(getattr(image, "n_frames", 1)) > 1


def _has_alpha(image: Image.Image) -> bool:
    return "A" in image.getbands() or "transparency" in image.info


def _flatten_transparency_to_black(image: Image.Image) -> Image.Image:
    if _has_alpha(image):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        composed = Image.alpha_composite(background, rgba)
        return composed.convert("RGB")
    return image.convert("RGB")


def _total_frames(config: FadeInImageConfig) -> int:
    if config.fps <= 0:
        raise ValueError("fps must be greater than zero")
    if config.duration_seconds <= 0:
        raise ValueError("duration_seconds must be greater than zero")
    return max(2, int(round(config.fps * config.duration_seconds)))


def _duration_per_frame_ms(config: FadeInImageConfig, frame_count: int) -> int:
    return max(1, int(round((config.duration_seconds * 1000) / frame_count)))
