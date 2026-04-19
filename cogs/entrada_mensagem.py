import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import aiohttp
from io import BytesIO
from PIL import Image

# --- MODAL DE CONFIGURAÇÃO (O POP-UP) ---
class WelcomeModal(discord.ui.Modal, title='Configurar Boas-vindas'):
    
    embed_title = discord.ui.TextInput(
        label='Título da Mensagem',
        style=discord.TextStyle.short,
        placeholder='Ex: Bem-vindo(a) ao {servidor}!',
        required=False,
        max_length=256
    )

    embed_description = discord.ui.TextInput(
        label='Mensagem (Use: {mencao}, {nome}, {servidor})',
        style=discord.TextStyle.paragraph,
        placeholder='Ex: Olá {mencao}! Leia as regras em <#ID_DO_CANAL>.',
        required=True,
        max_length=2000
    )

    def __init__(self, cog, channel: discord.TextChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)

        # Salva as configurações por servidor (Multi-guild)
        self.cog.config[guild_id] = {
            "channel_id": self.channel.id,
            "title": self.embed_title.value,
            "description": self.embed_description.value
        }
        self.cog.save_config()

        # Gera uma prévia usando a foto de quem está configurando
        cor_teste = await self.cog.get_dominant_color(interaction.user.display_avatar.url)
        preview_embed = self.cog.build_embed(interaction.user, interaction.guild, self.cog.config[guild_id], cor_teste)
        
        await interaction.response.send_message(
            f"✅ **Sistema configurado com sucesso!**\nCanal: {self.channel.mention}\n\n**Prévia do Embed:**",
            embed=preview_embed,
            ephemeral=True
        )

# --- COG PRINCIPAL ---
class WelcomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config_file = "welcome_data.json"
        self.config = self.load_config()

    def load_config(self):
        if os.path.exists(self.config_file):
            with open(self.config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def save_config(self):
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)

    # Lógica para pegar a cor principal da PFP
    async def get_dominant_color(self, avatar_url: str) -> discord.Color:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(str(avatar_url)) as response:
                    if response.status != 200:
                        return discord.Color.blurple()
                    img_data = await response.read()
            
            img = Image.open(BytesIO(img_data)).convert("RGB")
            img = img.resize((1, 1)) # Redimensiona para 1px para obter a média
            color = img.getpixel((0, 0))
            return discord.Color.from_rgb(*color)
        except:
            return discord.Color.blurple()

    # Construtor do Embed com variáveis dinâmicas
    def build_embed(self, member, guild, data, color):
        title = data.get('title') or "Boas-vindas!"
        desc = data.get('description', 'Seja bem-vindo(a)!')

        # Substituição de variáveis
        replacements = {
            "{mencao}": member.mention,
            "{nome}": member.name,
            "{servidor}": guild.name,
            "{membros}": str(guild.member_count)
        }
        
        for placeholder, value in replacements.items():
            title = title.replace(placeholder, value)
            desc = desc.replace(placeholder, value)

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Membro nº {guild.member_count}", icon_url=guild.icon.url if guild.icon else None)
        return embed

    # --- COMANDO SLASH PARA CONFIGURAR ---
    @app_commands.command(name="configurar_entrada", description="Configura o sistema de boas-vindas.")
    @app_commands.describe(canal="Onde as mensagens serão enviadas")
    @app_commands.default_permissions(administrator=True)
    async def config_welcome(self, interaction: discord.Interaction, canal: discord.TextChannel):
        await interaction.response.send_modal(WelcomeModal(self, canal))

    # --- EVENTO DE ENTRADA ---
    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild_id = str(member.guild.id)
        if guild_id not in self.config:
            return

        data = self.config[guild_id]
        channel = self.bot.get_channel(data['channel_id'])
        
        if channel:
            cor = await self.get_dominant_color(member.display_avatar.url)
            embed = self.build_embed(member, member.guild, data, cor)
            await channel.send(content=member.mention, embed=embed)

async def setup(bot):
    await bot.add_cog(WelcomeCog(bot))