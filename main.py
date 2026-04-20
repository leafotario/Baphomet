import asyncio
import os
from threading import Thread

import discord
from aiohttp import web
from discord.ext import commands
from dotenv import load_dotenv
from flask import Flask

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

app = Flask("")


@app.route("/")
def home():
    return "Bot está online!"


def run_http() -> None:
    app.run(host="0.0.0.0", port=10000)


async def handle(request: web.Request) -> web.Response:
    return web.Response(text="Baphomet está online!")


async def start_web_server() -> None:
    server = web.Application()
    server.router.add_get("/health", handle)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    print("Servidor web auxiliar iniciado na porta 8080")


class MyBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        asyncio.create_task(start_web_server())
        for filename in sorted(os.listdir("./cogs")):
            if not filename.endswith(".py") or filename.startswith("__"):
                continue
            extension = f"cogs.{filename[:-3]}"
            try:
                await self.load_extension(extension)
                print(f"✅ Cog {extension} carregado.")
            except Exception as exc:
                print(f"❌ Falha ao carregar cog {extension}: {exc}")
        await self.tree.sync()
        print("🚀 Comandos sincronizados globalmente!")

    async def close(self) -> None:
        repository = getattr(self, "xp_repository", None)
        if repository is not None:
            try:
                await repository.close()
            except Exception:
                pass
        await super().close()


bot = MyBot()


@bot.event
async def on_ready() -> None:
    print("---")
    print(f"Bot logado como {bot.user}")
    print(f"ID: {bot.user.id}")
    print("---")


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN não encontrado no .env")
    Thread(target=run_http, daemon=True).start()
    bot.run(TOKEN)