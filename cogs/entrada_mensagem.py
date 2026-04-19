import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import aiohttp
from io import BytesIO
from PIL import Image

# --- MODAL DE CONFIGURAÇÃO ---
class WelcomeModal(discord.ui.Modal, title='Personalize as Boas-Vindas'):
    
    embed_title = discord.ui.TextInput(
        label='Título da Mensagem (Opcional)',
        style=discord.TextStyle.short,
        placeholder='Ex: Bem-vindo(a) ao {servidor}!',
        required=False,
        max_length=256
    )

    embed_description = discord.ui.TextInput(
        label='Mensagem de Boas-Vindas (Use variáveis)',
        style=discord.TextStyle.paragraph,
        placeholder='Use {mencao}, {nome}, {servidor}, {membros}\nEx: Olá {mencao}, leia as regras em <#ID>!',
        required=True,
        max_length=2000
    )

    def __init__(self, cog, channel: discord.TextChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)

        # Salva os dados atrelados ao ID do Servidor
        data = {
            "channel_id": self.channel.id,
            "title": self.embed_title.value,
            "description": self.embed_description.value
        }

        self.cog.config[guild_id] = data
        self.cog.save_config()

        # Extrai a cor da foto de quem configurou só para a pré-visualização
        cor_teste = await self.cog.get_dominant_color(interaction.user.display_avatar.url)
        preview_embed = self.cog.build_embed(interaction.user, interaction.guild, data, cor_teste)
        
        await interaction.response.send_message(
            f"✅ **Sistema Configurado!**\nDestino: {self.channel.mention}\n\n**Pré-visualização (Usando sua foto):**",
            embed=preview_embed,
            ephemeral=True
        )

# --- COG PRINCIPAL ---
class UltimateWelcomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config_file = "welcome_data.json"
        self.config = self.load_config()

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

    def save_config(self):
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)

    # --- NOVO: EXTRATOR DE COR DOMINANTE ---
    async def get_dominant_color(self, avatar_url: str) -> discord.Color:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(str(avatar_url)) as response:
                    if response.status != 200:
                        return discord.Color.blurple() # Fallback se falhar
                    image_bytes = await response.read()
            
            # Abre a imagem e reduz para 1x1 pixel. Isso mistura todas as cores e nos dá a cor média!
            img = Image.open(BytesIO(image_bytes)).convert("RGB")
            img = img.resize((1, 1), resample=0)
            dominant_color = img.getpixel((0, 0))
            
            return discord.Color.from_rgb(*dominant_color)
        except Exception as e:
            print(f"Erro ao extrair cor da PFP: {e}")
            return discord.Color.blurple()

    # Construtor do Embed 
    def build_embed(self, member: discord.Member, guild: discord.Guild, data: dict, color: discord.Color):
        desc = data['description'].replace('{mencao}', member.mention)\
                                  .replace('{nome}', member.name)\
                                  .replace('{servidor}', guild.name)\
                                  .replace('{membros}', str(guild.member_count))

        title_raw = data.get('title', '')
        title = title_raw.replace('{nome}', member.name).replace('{servidor}', guild.name)

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Agora somos {guild.member_count} membros!", icon_url=guild.icon.url if guild.icon else None)
        
        return embed

    # --- COMANDO RESTRITO AOS ADMINISTRADORES ---
    @app_commands.command(name="configurar_boasvindas", description="Configura as boas-vindas. Exclusivo para Administradores.")
    @app_commands.describe(canal="O canal onde o bot enviará as mensagens.")
    @app_commands.default_permissions(administrator=True) # Oculta na interface
    @app_commands.checks.has_permissions(administrator=True) # Bloqueia na execução
    async def config_welcome(self, interaction: discord.Interaction, canal: discord.TextChannel):
        await interaction.response.send_modal(WelcomeModal(self, canal))

    # --- MENSAGEM DE ERRO SE ALGUÉM TENTAR BURLAR ---
    @config_welcome.error
    async def config_welcome_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message("❌ **Acesso Negado.** Você precisa ser Administrador para usar este comando.", ephemeral=True)

    # --- EVENTO DE ENTRADA ---
    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild_id = str(member.guild.id)
        
        if guild_id not in self.config: return
        
        data = self.config[guild_id]
        channel_id = data.get("channel_id")
        if not channel_id: return

        channel = self.bot.get_channel(channel_id)
        if not channel: return

        # Extrai a cor principal da PFP do novo membro
        user_color = await self.get_dominant_color(member.display_avatar.url)

        try:
            embed = self.build_embed(member, member.guild, data, user_color)
            await channel.send(embed=embed)
        except discord.Forbidden:
            print(f"Erro: Sem permissão para enviar na sala de boas vindas.")

async def setup(bot):
    await bot.add_cog(UltimateWelcomeCog(bot))