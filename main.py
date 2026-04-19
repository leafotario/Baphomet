import discord
import os
import asyncio
from discord.ext import commands
from dotenv import load_dotenv
from flask import Flask
from threading import Thread
app = Flask('')

@app.route('/')
def home():
    return "Bot está online!"

def run():
    app.run(host='0.0.0.0', port=10000)

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

class MyBot(commands.Bot):
    def __init__(self):
        # Intents necessários
        intents = discord.Intents.default()
        intents.message_content = True 
        intents.members = True # Recomendado para bots que gerenciam membros/configurações
        
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Carrega os Cogs
        # Certifique-se de que a pasta 'cogs' existe e o aotd.py está lá dentro
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    await bot.load_extension("anti_invite")
                    print(f'✅ Cog {filename} carregado.')
                except Exception as e:
                    print(f'❌ Falha ao carregar cog {filename}: {e}')
        
        # Sincronização
        # Se quiser sincronizar instantaneamente em um servidor de teste, use:
        # MY_GUILD = discord.Object(id=123456789) # Coloque o ID do seu servidor
        # self.tree.copy_global_to(guild=MY_GUILD)
        # await self.tree.sync(guild=MY_GUILD)
        
        await self.tree.sync() # Global (pode demorar a propagar)
        print("🚀 Comandos sincronizados globalmente!")

bot = MyBot()

@bot.event
async def on_ready():
    print(f'---')
    print(f'Bot logado como {bot.user}')
    print(f'ID: {bot.user.id}')
    print(f'---')

t = Thread(target=run)
t.start()

bot.run(TOKEN)