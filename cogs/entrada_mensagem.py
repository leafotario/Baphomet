import discord
from discord.ext import commands
from discord import app_commands
import json
import os

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

    embed_color = discord.ui.TextInput(
        label='Cor do Embed (Hexadecimal) (Opcional)',
        style=discord.TextStyle.short,
        placeholder='Ex: #FF5733 ou #000000',
        required=False,
        max_length=7
    )

    def __init__(self, cog, channel: discord.TextChannel):
        super().__init__()
        self.cog = cog
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)

        # Trata a cor (adiciona o # se o usuário esquecer)
        color_input = self.embed_color.value.strip()
        if color_input and not color_input.startswith('#'):
            color_input = f"#{color_input}"

        # Salva os dados atrelados ao ID do Servidor (Multi-guild support)
        data = {
            "channel_id": self.channel.id,
            "title": self.embed_title.value,
            "description": self.embed_description.value,
            "color": color_input if color_input else "#2b2d31" # Cor padrão invisível do Discord
        }

        self.cog.config[guild_id] = data
        self.cog.save_config()

        # Mostra uma pré-visualização de como ficou para quem configurou
        preview_embed = self.cog.build_embed(interaction.user, interaction.guild, data)
        await interaction.response.send_message(
            f"✅ **Sistema de Boas-Vindas Configurado Magistralmente!**\nAs mensagens serão enviadas em {self.channel.mention}.\n\n**Pré-visualização:**",
            embed=preview_embed,
            ephemeral=True
        )

# --- COG PRINCIPAL ---
class UltimateWelcomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config_file = "welcome_data.json"
        self.config = self.load_config()

    # Sistema de salvamento seguro e por servidor
    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {} # Retorna vazio se o arquivo corromper
        return {}

    def save_config(self):
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)

    # Construtor do Embed com suporte a variáveis
    def build_embed(self, member: discord.Member, guild: discord.Guild, data: dict):
        # Substituição mágica de variáveis
        desc = data['description'].replace('{mencao}', member.mention)\
                                  .replace('{nome}', member.name)\
                                  .replace('{servidor}', guild.name)\
                                  .replace('{membros}', str(guild.member_count))

        title_raw = data.get('title', '')
        title = title_raw.replace('{nome}', member.name).replace('{servidor}', guild.name)

        # Conversão de cor
        color_str = data.get('color', '#2b2d31')
        try:
            color = discord.Color.from_str(color_str)
        except ValueError:
            color = discord.Color.blurple() # Fallback seguro

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Agora somos {guild.member_count} membros!", icon_url=guild.icon.url if guild.icon else None)
        
        return embed

    # --- COMANDO QUE ABRE O MODAL ---
    @app_commands.command(name="configurar_boasvindas", description="Abre o painel supremo para configurar as boas-vindas do servidor.")
    @app_commands.describe(canal="O canal onde o bot enviará as mensagens.")
    @app_commands.default_permissions(administrator=True)
    async def config_welcome(self, interaction: discord.Interaction, canal: discord.TextChannel):
        # Abre o Modal na tela do usuário
        await interaction.response.send_modal(WelcomeModal(self, canal))

    # --- O EVENTO QUE FAZ TUDO ACONTECER ---
    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild_id = str(member.guild.id)
        
        # Verifica se este servidor configurou o sistema
        if guild_id not in self.config:
            return

        data = self.config[guild_id]
        channel_id = data.get("channel_id")
        
        if not channel_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return # O canal pode ter sido deletado

        # Constrói a mensagem e envia
        try:
            embed = self.build_embed(member, member.guild, data)
            # Adiciona a menção fora do embed para notificar o usuário (ping)
            await channel.send(content=member.mention, embed=embed)
        except discord.Forbidden:
            print(f"Erro: O bot não tem permissão para enviar mensagens no canal {channel.name}.")

async def setup(bot):
    await bot.add_cog(UltimateWelcomeCog(bot))