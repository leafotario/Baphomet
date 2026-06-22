"""Script de teste local para renderização da carta TCG."""
import asyncio
import io
import urllib.request
from PIL import Image


async def main():
    from modules.tcg.rendering.booster_renderer import BoosterGraphicEngine

    # Baixar um pfp falso para o teste (Baphomet icon)
    url = "https://i.imgur.com/8Q5Z2gA.png"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as response:
        pfp_bytes = response.read()

    engine = BoosterGraphicEngine(fonts_path="assets/fonts/", scale=3)
    buffer = await engine.render_card(
        user_name="GUIPATO",
        pfp_bytes=pfp_bytes,
        atk=7,
        def_stat=73,
        spd=6,
        rarity_label="RARO",
    )

    with open("test_render.png", "wb") as f:
        f.write(buffer.read())

    print("[OK] Carta renderizada com sucesso: test_render.png")


if __name__ == "__main__":
    asyncio.run(main())
