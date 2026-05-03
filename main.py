import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")


class MyBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        for filename in sorted(os.listdir("./cogs")):
            if filename.startswith("__") or filename.startswith("xp_"):
                continue
            if filename.endswith(".py"):
                extension = f"cogs.{filename[:-3]}"
            elif os.path.isdir(f"./cogs/{filename}") and os.path.isfile(f"./cogs/{filename}/__init__.py"):
                extension = f"cogs.{filename}"
            else:
                continue
            try:
                await self.load_extension(extension)
                print(f"✅ Cog {extension} carregado.")
            except Exception as exc:
                print(f"❌ Falha ao carregar cog {extension}: {exc}")
        await self.tree.sync()
        print("🚀 Comandos sincronizados globalmente!")

    async def close(self) -> None:
        runtime = getattr(self, "xp_runtime", None)
        if runtime is not None:
            try:
                await runtime.repository.close()
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
    bot.run(TOKEN)