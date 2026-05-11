from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw

from .drawing import add_noise_overlay, vertical_gradient
from .fonts import FontManager
from .profile_card_renderer import ProfileCardRenderer
from .profile_card_types import ProfileRenderData


def build_mock_profile() -> ProfileRenderData:
    """Dados fake para inspeção visual local do renderer puro."""

    return ProfileRenderData(
        display_name="Tatu vulgo papiro da madeira quadrada",
        username="tatu.papiro",
        user_id=1494163021960970420,
        avatar_bytes=_mock_avatar_bytes(),
        pronouns="Ele/dele",
        rank_text="#4",
        ask_me_about=[
            "Culinária",
            "Taylor Swift",
            "Minecraft Dungeons",
            "RPG de mesa com nomes grandes",
            "Cinema de terror",
        ],
        basic_info=(
            "oi gente meu nome é Tatu vulgo papiro e voce nao sabe o verdadeiro significado "
            "do rap da madeira quadrada do minecraft em 2026 muito menos do azarado OS remastered."
        ),
        badge_name="LVL 0 - Novato",
        badge_image_bytes=_mock_badge_bytes(),
        bonds_count=3,
        bonds_multiplier=1.3,
        level=2,
        xp_current=104,
        xp_required=400,
        xp_total=484,
        xp_percent=26,
    )


def render_preview(output_path: Path | str = "debug/ficha_profile_card_preview.png") -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    png = ProfileCardRenderer().render(build_mock_profile())
    path.write_bytes(png)
    return path


def _mock_avatar_bytes() -> bytes:
    size = 512
    image = vertical_gradient((size, size), (105, 18, 40, 255), (18, 17, 20, 255))
    add_noise_overlay(image, opacity=18, seed=2026, scale=3)
    draw = ImageDraw.Draw(image)
    draw.ellipse((86, 72, 426, 428), outline=(224, 198, 145, 180), width=14)
    draw.ellipse((128, 116, 384, 372), fill=(24, 21, 22, 155), outline=(170, 42, 67, 220), width=8)
    font = FontManager().font(124, "display")
    text = "TP"
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((size - (bbox[2] - bbox[0])) // 2, (size - (bbox[3] - bbox[1])) // 2 - bbox[1]),
        text,
        font=font,
        fill=(240, 229, 207, 255),
        stroke_width=3,
        stroke_fill=(0, 0, 0, 150),
    )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _mock_badge_bytes() -> bytes:
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((50, 38, 206, 194), fill=(35, 31, 32, 230), outline=(219, 203, 164, 230), width=8)
    draw.polygon(((128, 34), (178, 105), (128, 214), (78, 105)), fill=(104, 22, 43, 245), outline=(238, 211, 155, 245))
    draw.line((128, 52, 128, 202), fill=(238, 211, 155, 255), width=9)
    draw.arc((72, 72, 184, 184), 135, 405, fill=(238, 211, 155, 220), width=9)
    draw.ellipse((116, 204, 140, 228), fill=(238, 211, 155, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


if __name__ == "__main__":
    written = render_preview()
    print(written)
