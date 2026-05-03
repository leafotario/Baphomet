import discord
from discord import app_commands
from discord.ext import commands


class AvatarView(discord.ui.View):
    """
    View interativa para o comando de Avatar.
    Contém um botão de download (Link) e um botão para alternar entre Avatar Global e de Servidor.
    """

    def __init__(self, member: discord.Member, is_global: bool = False):
        super().__init__(timeout=180)
        self.member = member
        self.is_global = is_global

        # Verifica se o membro possui um avatar de servidor diferente do global.
        # member.avatar é o global, member.display_avatar é o do servidor (ou fallback para o global).
        has_custom_server_avatar = member.avatar and member.avatar != member.display_avatar

        # URL baseada no estado atual (Global ou Servidor)
        self.current_url = (member.avatar.with_size(1024).url if member.avatar else member.display_avatar.with_size(1024).url) if is_global else member.display_avatar.with_size(1024).url

        # Botão de Download (Sempre aponta para a URL que está sendo exibida)
        self.add_item(
            discord.ui.Button(
                label="Abrir Imagem Original",
                url=self.current_url,
                style=discord.ButtonStyle.link,
                emoji="🔗"
            )
        )

        # Botão de Copiar URL (Melhoria de UX para Mobile)
        copy_button = discord.ui.Button(
            label="Copiar URL",
            style=discord.ButtonStyle.secondary,
            emoji="🔗",
            custom_id="copy_avatar_url"
        )
        copy_button.callback = self.copy_url_callback
        self.add_item(copy_button)

        # Botão de Alternância (Aparece apenas se houver diferença entre Global e Servidor)
        if has_custom_server_avatar:
            label = "Ver Avatar do Servidor" if is_global else "Ver Avatar Global"
            emoji = "🏢" if is_global else "🌍"
            
            swap_button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                emoji=emoji,
                custom_id="swap_avatar"
            )
            swap_button.callback = self.swap_callback
            self.add_item(swap_button)

    async def copy_url_callback(self, interaction: discord.Interaction):
        """
        Callback que contorna a limitação da API enviando uma mensagem
        efêmera com texto limpo, permitindo que o usuário mobile pressione e copie.
        """
        await interaction.response.send_message(content=self.current_url, ephemeral=True)

    async def swap_callback(self, interaction: discord.Interaction):
        # Garante que só quem apertou o botão possa interagir? Para avatar não faz mal ser público.
        
        # Inverte o estado
        self.is_global = not self.is_global
        
        # Atualiza o Embed
        embed = self._build_embed()
        
        # Recria a View com o novo estado (para atualizar a URL do botão de link)
        new_view = AvatarView(self.member, is_global=self.is_global)
        
        await interaction.response.edit_message(embed=embed, view=new_view)

    def _build_embed(self) -> discord.Embed:
        """Gera o Embed com base no estado atual (Global ou Servidor)."""
        color = self.member.color if self.member.color.value != 0 else discord.Color.blurple()
        
        url = (self.member.avatar.with_size(1024).url if self.member.avatar else self.member.display_avatar.with_size(1024).url) if self.is_global else self.member.display_avatar.with_size(1024).url
        
        tipo = "Global" if self.is_global else ("do Servidor" if self.member.avatar != self.member.display_avatar else "")
        title = f"🖼️ Avatar {tipo} de {self.member.display_name}".strip()

        embed = discord.Embed(title=title, color=color)
        embed.set_image(url=url)
        return embed


class UtilitiesCog(commands.Cog, name="Utilidades"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="avatar", description="Exibe a foto de perfil em alta qualidade de um membro.")
    @app_commands.describe(membro="O membro que você deseja ver o avatar. Deixe em branco para ver o seu próprio.")
    async def avatar(self, interaction: discord.Interaction, membro: discord.Member = None):
        # 1. Se o usuário não preencher, usamos ele mesmo.
        target = membro or interaction.user

        # Apenas para tipagem correta, já que interaction.user pode ser User em DMs
        if not isinstance(target, discord.Member):
            target = await interaction.guild.fetch_member(target.id) if interaction.guild else target

        # Inicialmente sempre exibimos o avatar do servidor (display_avatar)
        view = AvatarView(member=target, is_global=False)
        embed = view._build_embed()

        await interaction.response.send_message(embed=embed, view=view)

async def setup(bot: commands.Bot):
    await bot.add_cog(UtilitiesCog(bot))
