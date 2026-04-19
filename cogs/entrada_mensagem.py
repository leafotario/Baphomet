import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import aiohttp
from io import BytesIO
from PIL import Image

# --- MODAL DE CONFIGURAÇÃO SUPREMO ---
class WelcomeModal(discord.ui.Modal, title='Configuração de Boas-vindas'):
    
    msg_title = discord.ui.TextInput(
        label='Título do Embed (Opcional)',
        style=discord.TextStyle.short,
        placeholder='Ex: Bem-vindo(a) ao {servidor}!',
        required=False,
        max_length=256
    )

    msg_description = discord.ui.TextInput(
        label='Mensagem Principal',
        style=discord.TextStyle.paragraph,
        placeholder='Use: {mencao}, {nome}, {servidor}, {membros}\nEx: Olá {mencao}! Leia as regras em <#ID>.',
        required=True,
        max_length=2000
    )

    def __init__(self, cog, channel: discord.TextChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)

        # Persistência de Dados: Salva as configs no dicionário do Cog
        self.cog.config[guild_id] = {
            "channel_id": self.channel.id,
            "title": self.msg_title.value,
            "description": self.msg_description.value
        }
        self.cog.save_config() # Escreve no arquivo JSON imediatamente

        # Gera uma prévia em tempo real
        dom_color = await self.cog.get_dominant_color(interaction.user.display_avatar.url)
        preview_embed = self.cog.build_embed(interaction.user, interaction.guild, self.cog.config[guild_id], dom_color)
        
        await interaction.response.send_message(
            f"✅ **Configuração Salva!**\nAs mensagens serão enviadas em: {self.channel.mention}",
            embed=preview_embed,
            ephemeral=True
        )

# --- COG PRINCIPAL (O MOTOR DO SISTEMA) ---
class UltimateWelcomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config_file = "welcome_storage.json"
        self.config = self.load_config()

    # --- SISTEMA DE PERSISTÊNCIA (MEMÓRIA) ---
    def load_config(self):
        """Carrega os dados do JSON. Se não existir, cria um vazio."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def save_config(self):
        """Salva as configurações atuais no arquivo físico."""
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)

    # --- PROCESSAMENTO DE IMAGEM (A COR DA PFP) ---
    async def get_dominant_color(self, avatar_url: str) -> discord.Color:
        """Extrai a cor média da foto de perfil para o embed."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(avatar_url) as response:
                    if response.status != 200:
                        return discord.Color.blurple()
                    data = await response.read()
            
            img = Image.open(BytesIO(data)).convert("RGB")
            img = img.resize((1, 1)) # Achata a imagem para 1 pixel para pegar a média
            color = img.getpixel((0, 0))
            return discord.Color.from_rgb(*color)
        except Exception:
            return discord.Color.blurple() # Cor de fallback caso falhe

    # --- CONSTRUTOR DE EMBED ---
    def build_embed(self, member, guild, data, color):
        title = data.get('title') or f"Bem-vindo(a)!"
        desc = data.get('description', "Seja bem-vindo(a) ao servidor!")

        # Substituição dinâmica de variáveis
        placeholders = {
            "{mencao}": member.mention,
            "{nome}": member.name,
            "{servidor}": guild.name,
            "{membros}": str(guild.member_count)
        }
        
        for key, val in placeholders.items():
            title = title.replace(key, val)
            desc = desc.replace(key, val)

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Membro #{guild.member_count}", icon_url=guild.icon.url if guild.icon else None)
        return embed

    # --- COMANDO DE CONFIGURAÇÃO (ADMIN ONLY) ---
    @app_commands.command(name="configurar_entrada", description="Configura o sistema de boas-vindas do servidor.")
    @app_commands.describe(canal="O canal onde o bot enviará as boas-vindas.")
    @app_commands.default_permissions(administrator=True)
    async def config_welcome(self, interaction: discord.Interaction, canal: discord.TextChannel):
        """Abre o modal para o administrador configurar o sistema."""
        # Verificação extra de segurança
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Apenas administradores podem usar isso.", ephemeral=True)
            
        await interaction.response.send_modal(WelcomeModal(self, canal))

    # --- LISTENER DE ENTRADA (O GATILHO) ---
    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild_id = str(member.guild.id)
        
        # Verifica se o servidor está na "memória" do bot
        if guild_id not in self.config:
            return

        data = self.config[guild_id]
        channel = self.bot.get_channel(data['channel_id'])
        
        if not channel:
            return # Canal deletado ou bot sem acesso

        # Executa a mágica da cor e envia
        dom_color = await self.get_dominant_color(member.display_avatar.url)
        embed = self.build_embed(member, member.guild, data, dom_color)
        
        try:
            await channel.send(content=member.mention, embed=embed)
        except discord.Forbidden:
            print(f"Erro: Sem permissão para postar no canal {channel.id}")

async def setup(bot):
    await bot.add_cog(UltimateWelcomeCog(bot))