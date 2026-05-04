import discord
from discord.ext import commands
from discord import app_commands

ROLE_STAFF_ID = 1393546293418397866  # Cargo com acesso aos tickets


# ─────────────────────────────────────────────
#  VIEWS PERSISTENTES
#  (persistent=True para sobreviver a reinicios)
# ─────────────────────────────────────────────

class BotaoAbrirDenuncia(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Abrir denúncia",
        emoji="🚨",
        custom_id="denuncia:abrir",
    )
    async def abrir(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        membro = interaction.user
        role_staff = guild.get_role(ROLE_STAFF_ID)

        # Verifica se o usuário já tem um ticket aberto
        nome_canal = f"denuncia-{membro.name}".lower().replace(" ", "-")[:40]
        canal_existente = discord.utils.get(guild.text_channels, name=nome_canal)
        if canal_existente:
            await interaction.response.send_message(
                f"❌ Você já tem um ticket aberto em {canal_existente.mention}.",
                ephemeral=True,
            )
            return

        # Permissões do canal
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            membro: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
        }
        if role_staff:
            overwrites[role_staff] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
            )

        # Tenta criar o canal na mesma categoria da mensagem, se houver
        categoria = interaction.channel.category

        canal = await guild.create_text_channel(
            name=nome_canal,
            overwrites=overwrites,
            category=categoria,
            reason=f"Ticket de denúncia aberto por {membro}",
        )

        # Mensagem dentro do ticket
        await canal.send(
            content=f"Denúncia aberta <@&{ROLE_STAFF_ID}>",
            view=BotaoFecharDenuncia(),
        )

        await interaction.response.send_message(
            f"✅ Seu ticket foi aberto em {canal.mention}.",
            ephemeral=True,
        )


class BotaoFecharDenuncia(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Fechar denúncia",
        emoji="🔒",
        style=discord.ButtonStyle.secondary,
        custom_id="denuncia:fechar",
    )
    async def fechar(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_staff = interaction.guild.get_role(ROLE_STAFF_ID)
        eh_staff = role_staff in interaction.user.roles if role_staff else False
        eh_admin = interaction.user.guild_permissions.administrator

        if not (eh_staff or eh_admin):
            await interaction.response.send_message(
                "❌ Apenas a staff pode fechar denúncias.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("🔒 Fechando o ticket...", ephemeral=True)
        await interaction.channel.delete(reason=f"Ticket fechado por {interaction.user}")


# ─────────────────────────────────────────────
#  COG
# ─────────────────────────────────────────────

class Denuncia(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Registra as views persistentes para sobreviver a reinicios do bot
        bot.add_view(BotaoAbrirDenuncia())
        bot.add_view(BotaoFecharDenuncia())

    # ── /denuncia_chat ──
    @app_commands.command(
        name="denuncia_chat",
        description="Posta a mensagem de abertura de denúncias no canal (Admin).",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def denuncia_chat(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📋 Sistema de Denúncias",
            description=(
                "Presenciou algo que viola as regras do servidor?\n\n"
                "Clique no botão de abrir denúncia para abrir um ticket de denúncia **privado** "
                "com a staff."
            ),
            color=discord.Color.from_rgb(220, 50, 50),
        )
        await interaction.channel.send(embed=embed, view=BotaoAbrirDenuncia())
        await interaction.response.send_message("✅ Mensagem postada.", ephemeral=True)

    @denuncia_chat.error
    async def denuncia_chat_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ Apenas administradores podem usar esse comando.", ephemeral=True
            )

    # ── /fechar_denuncia ──
    @app_commands.command(
        name="fechar_denuncia",
        description="Fecha (deleta) o canal de ticket atual (Admin).",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def fechar_denuncia(self, interaction: discord.Interaction):
        # Validação: garante que o comando só funciona dentro de um canal de ticket
        if not interaction.channel.name.startswith("denuncia-"):
            await interaction.response.send_message(
                "❌ Este comando só pode ser usado dentro de um canal de ticket.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("🔒 Fechando o ticket...", ephemeral=True)
        await interaction.channel.delete(reason=f"Ticket fechado por {interaction.user} via comando")

    @fechar_denuncia.error
    async def fechar_denuncia_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ Apenas administradores podem usar esse comando.", ephemeral=True
            )


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Denuncia(bot))