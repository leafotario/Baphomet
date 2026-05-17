from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .models import CompassItemKind, CompassState


@dataclass(frozen=True, slots=True)
class CompassRenderItem:
    tipo: CompassItemKind
    conteudo: str
    coordenadas: tuple[float, float]


@dataclass(frozen=True, slots=True)
class CompassRenderSnapshot:
    titulo: str
    rotulos: tuple[str, str, str, str]
    lista_itens: tuple[CompassRenderItem, ...]


def snapshot_compass_state(state: CompassState) -> CompassRenderSnapshot:
    return CompassRenderSnapshot(
        titulo=state.titulo,
        rotulos=state.rotulos,
        lista_itens=tuple(
            CompassRenderItem(
                tipo=item["tipo"],
                conteudo=item["conteudo"],
                coordenadas=item["coordenadas"],
            )
            for item in state.lista_itens
        ),
    )


def render_compass_report_png(
    snapshot: CompassRenderSnapshot,
    asset_bytes_by_index: Mapping[int, bytes],
) -> io.BytesIO:
    width, height = 1120, 820
    plot_left, plot_top = 128, 156
    plot_right, plot_bottom = 992, 704
    center_x = (plot_left + plot_right) // 2
    center_y = (plot_top + plot_bottom) // 2

    image = Image.new("RGB", (width, height), (18, 20, 28))
    draw = ImageDraw.Draw(image)
    title_font = _load_font(34, bold=True)
    label_font = _load_font(18, bold=True)
    item_font = _load_font(15, bold=False)
    small_font = _load_font(12, bold=False)

    draw.rounded_rectangle((46, 38, width - 46, height - 38), radius=24, fill=(24, 27, 37), outline=(63, 72, 98), width=2)
    _draw_centered_text(draw, (width // 2, 78), snapshot.titulo[:120], title_font, (245, 248, 255))

    draw.rounded_rectangle((plot_left, plot_top, plot_right, plot_bottom), radius=18, fill=(30, 34, 45), outline=(82, 94, 126), width=2)
    draw.line((plot_left, center_y, plot_right, center_y), fill=(116, 132, 170), width=2)
    draw.line((center_x, plot_top, center_x, plot_bottom), fill=(116, 132, 170), width=2)

    for tick in range(-10, 11, 5):
        tick_x, _ = _project_to_pixel(float(tick), 0.0, plot_left, plot_top, plot_right, plot_bottom)
        _, tick_y = _project_to_pixel(0.0, float(tick), plot_left, plot_top, plot_right, plot_bottom)
        draw.line((tick_x, center_y - 6, tick_x, center_y + 6), fill=(92, 105, 136), width=1)
        draw.line((center_x - 6, tick_y, center_x + 6, tick_y), fill=(92, 105, 136), width=1)
        if tick:
            _draw_centered_text(draw, (tick_x, center_y + 22), str(tick), small_font, (171, 183, 205))
            _draw_centered_text(draw, (center_x - 24, tick_y), str(tick), small_font, (171, 183, 205))

    _draw_centered_text(draw, (center_x, plot_top - 36), snapshot.rotulos[0], label_font, (213, 224, 244))
    _draw_centered_text(draw, (center_x, plot_bottom + 34), snapshot.rotulos[1], label_font, (213, 224, 244))
    _draw_centered_text(draw, (plot_left - 58, center_y), snapshot.rotulos[2], label_font, (213, 224, 244))
    _draw_centered_text(draw, (plot_right + 58, center_y), snapshot.rotulos[3], label_font, (213, 224, 244))

    palette = {
        "texto": (120, 197, 255),
        "url": (128, 224, 164),
        "avatar_id": (246, 182, 102),
    }
    for index, item in enumerate(snapshot.lista_itens, start=1):
        point_x, point_y = _project_to_pixel(*item.coordenadas, plot_left, plot_top, plot_right, plot_bottom)
        color = palette.get(item.tipo, (229, 232, 238))
        draw.line((center_x, center_y, point_x, point_y), fill=(58, 66, 88), width=1)

        asset_bytes = asset_bytes_by_index.get(index)
        pasted = _paste_item_asset(image, asset_bytes, item.tipo, point_x, point_y)
        if not pasted:
            draw.ellipse((point_x - 8, point_y - 8, point_x + 8, point_y + 8), fill=color, outline=(255, 255, 255), width=2)

        label = f"{index}. {item.tipo}"
        _draw_label(draw, label, point_x + 10, point_y - 34, item_font, color)

    footer = f"{len(snapshot.lista_itens)} insercoes processadas em memoria"
    _draw_centered_text(draw, (width // 2, height - 66), footer, small_font, (166, 178, 200))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _load_font(size: int, *, bold: bool) -> ImageFont.ImageFont:
    font_name = "Poppins-Bold.ttf" if bold else "Poppins-Regular.ttf"
    font_path = Path("assets/fonts") / font_name
    if font_path.exists():
        return ImageFont.truetype(str(font_path), size)
    return ImageFont.load_default()


def _text_bounds(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    width = right - left
    height = bottom - top
    return left, top, width, height


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    left, top, width, height = _text_bounds(draw, text, font)
    origin_x = int(center[0] - (width / 2) - left)
    origin_y = int(center[1] - (height / 2) - top)
    draw.text((origin_x, origin_y), text, font=font, fill=fill)


def _draw_label(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    font: ImageFont.ImageFont,
    accent: tuple[int, int, int],
) -> None:
    left, top, width, height = _text_bounds(draw, text, font)
    pad_x, pad_y = 8, 5
    box = (
        x - pad_x,
        y - pad_y,
        x + width + pad_x,
        y + height + pad_y,
    )
    draw.rounded_rectangle(box, radius=8, fill=(20, 22, 30), outline=accent, width=1)
    draw.text((x - left, y - top), text, font=font, fill=(242, 245, 250))


def _project_to_pixel(
    abscissa: float,
    ordenada: float,
    plot_left: int,
    plot_top: int,
    plot_right: int,
    plot_bottom: int,
) -> tuple[int, int]:
    pixel_x = plot_left + ((abscissa + 10.0) / 20.0) * (plot_right - plot_left)
    pixel_y = plot_bottom - ((ordenada + 10.0) / 20.0) * (plot_bottom - plot_top)
    return int(round(pixel_x)), int(round(pixel_y))


def _paste_item_asset(
    canvas: Image.Image,
    asset_bytes: bytes | None,
    tipo: CompassItemKind,
    point_x: int,
    point_y: int,
) -> bool:
    if not asset_bytes:
        return False

    try:
        if tipo == "avatar_id":
            asset = _make_elliptic_asset(asset_bytes, 56)
        else:
            asset = _make_square_asset(asset_bytes, 52)
    except OSError:
        return False

    paste_x = point_x - asset.width // 2
    paste_y = point_y - asset.height // 2
    canvas.paste(asset, (paste_x, paste_y), asset)
    return True


def _make_square_asset(asset_bytes: bytes, size: int) -> Image.Image:
    with Image.open(io.BytesIO(asset_bytes)) as source:
        return ImageOps.fit(
            source.convert("RGBA"),
            (size, size),
            method=Image.Resampling.LANCZOS,
        )


def _make_elliptic_asset(asset_bytes: bytes, size: int) -> Image.Image:
    with Image.open(io.BytesIO(asset_bytes)) as source:
        fitted = ImageOps.fit(
            source.convert("RGBA"),
            (size, size),
            method=Image.Resampling.LANCZOS,
        )

    alpha = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(alpha)
    mask_draw.ellipse((0, 0, size - 1, size - 1), fill=255)
    fitted.putalpha(alpha)
    return fitted
