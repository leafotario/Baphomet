import discord
from discord import app_commands
from discord.ext import commands


# ─────────────────────────────────────────────
#  VIEWS DE DENÚNCIA
# ─────────────────────────────────────────────
ROLE_STAFF_ID = 1393546293418397866

class BotaoAbrirDenuncia(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Abrir denúncia", emoji="🚨", custom_id="denuncia:abrir")
    async def abrir(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        membro = interaction.user
        role_staff = guild.get_role(ROLE_STAFF_ID)

        nome_canal = f"denuncia-{membro.name}".lower().replace(" ", "-")[:40]
        canal_existente = discord.utils.get(guild.text_channels, name=nome_canal)
        if canal_existente:
            await interaction.response.send_message(f"❌ Você já tem um ticket aberto em {canal_existente.mention}.", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            membro: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        if role_staff:
            overwrites[role_staff] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)

        canal = await guild.create_text_channel(
            name=nome_canal, overwrites=overwrites, category=interaction.channel.category, reason=f"Ticket de denúncia aberto por {membro}"
        )
        await canal.send(content=f"Denúncia aberta <@&{ROLE_STAFF_ID}>", view=BotaoFecharDenuncia())
        await interaction.response.send_message(f"✅ Seu ticket foi aberto em {canal.mention}.", ephemeral=True)

class BotaoFecharDenuncia(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fechar denúncia", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="denuncia:fechar")
    async def fechar(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_staff = interaction.guild.get_role(ROLE_STAFF_ID)
        eh_staff = role_staff in interaction.user.roles if role_staff else False
        eh_admin = interaction.user.guild_permissions.administrator

        if not (eh_staff or eh_admin):
            await interaction.response.send_message("❌ Apenas a staff pode fechar denúncias.", ephemeral=True)
            return

        await interaction.response.send_message("🔒 Fechando o ticket...", ephemeral=True)
        await interaction.channel.delete(reason=f"Ticket fechado por {interaction.user}")


@app_commands.default_permissions(administrator=True)
class ServerConfig(app_commands.Group):
    """
    Grupo de comandos slash: /server_config
    Centraliza todas as ferramentas de administração obscura do bot.
    """
    def __init__(self):
        super().__init__(
            name="server_config",
            description="Administração obscura: molde as engrenagens do servidor aos seus desígnios."
        )

    @app_commands.command(name="anti_convites", description="Sela o servidor contra convites hereges. Bloqueia links de outros reinos.")
    async def anti_convites(self, interaction: discord.Interaction, estado: bool) -> None:
        cog = interaction.client.get_cog("AntiInviteCog")
        if cog: await cog.anti_invite_toggle(interaction, estado)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="status_anticonvites", description="Revela se o selo contra convites profanos está ativo ou adormecido.")
    async def status_anticonvites(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("AntiInviteCog")
        if cog: await cog.anti_invite_status(interaction)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="anti_spam", description="Invoca o purgatório para mensagens repetitivas. Silencia os tagarelas.")
    async def anti_spam(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("ModerationCog")
        if cog: await cog.antispam_toggle(interaction)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="trancar_canal", description="Tranca os portões deste canal. Apenas os escolhidos poderão ecoar suas vozes.")
    async def trancar_canal(self, interaction: discord.Interaction, canal: discord.TextChannel = None) -> None:
        cog = interaction.client.get_cog("BlacklistCog")
        if cog:
            ctx = await interaction.client.get_context(interaction)
            await cog.bloquear_canal(ctx, canal)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="destrancar_canal", description="Quebra os selos deste canal. Permite que o caos das vozes mortais retorne.")
    async def destrancar_canal(self, interaction: discord.Interaction, canal: discord.TextChannel = None) -> None:
        cog = interaction.client.get_cog("BlacklistCog")
        if cog:
            ctx = await interaction.client.get_context(interaction)
            await cog.desbloquear_canal(ctx, canal)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="canal_bump", description="Elege o altar onde os ritos de ascensão (bump) serão celebrados.")
    async def canal_bump(self, interaction: discord.Interaction, canal: discord.TextChannel) -> None:
        cog = interaction.client.get_cog("BumpReminderCog")
        if cog: await cog.configurar_bump(interaction, canal)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="configurar_limpeza_saida", description="Configura a foice: expurga os rastros deixados por almas que abandonaram o servidor.")
    async def limpeza_saida(self, interaction: discord.Interaction, canal: discord.TextChannel) -> None:
        cog = interaction.client.get_cog("GhostCleanupCog")
        if cog: await cog.configurar_limpeza_saida(interaction, canal)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="canal_logs", description="Define o pergaminho sombrio onde os segredos e ações do servidor serão eternizados.")
    async def canal_logs(self, interaction: discord.Interaction, canal_destino_id: str, servidor_origem_id: str = None) -> None:
        cog = interaction.client.get_cog("DailyLoggerCog")
        if cog: await cog.configurar_logs(interaction, canal_destino_id, servidor_origem_id)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="canal_denuncias", description="Cria um confessionário oculto para que os mortais relatem as heresias ao conselho.")
    async def canal_denuncias(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Denuncia")
        if cog: await cog.denuncia_chat(interaction)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="painel_logs", description="Abre o grimório de registros. Um painel para observar tudo o que rasteja nas sombras.")
    async def painel_logs(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("PunishmentLogs")
        if cog: await cog.setup_logs(interaction)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="canal_convites", description="Demarca o território onde novos adeptos podem ser convocados livremente.")
    async def canal_convites(self, interaction: discord.Interaction, max_age: app_commands.Range[int, 0, 720], max_uses: app_commands.Range[int, 0, 1000]) -> None:
        cog = interaction.client.get_cog("ModerationCog")
        if cog: await cog.set_convite(interaction, max_age, max_uses)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="canal_geral", description="Sagra o salão principal onde a cacofonia das almas recém-chegadas irá ecoar.")
    async def canal_geral(self, interaction: discord.Interaction, canal: discord.TextChannel) -> None:
        cog = interaction.client.get_cog("ModerationCog")
        if cog: await cog.set_geral(interaction, canal)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="canal_permanencia", description="Define o abismo onde a presença contínua dos membros será monitorada e julgada.")
    async def canal_permanencia(self, interaction: discord.Interaction, minutos: app_commands.Range[int, 1, 10080]) -> None:
        cog = interaction.client.get_cog("ModerationCog")
        if cog: await cog.set_permanencia(interaction, minutos)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="status_limpeza_saida", description="Sussurra o estado atual da foice que limpa as cinzas dos que partiram.")
    async def status_limpeza(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("GhostCleanupCog")
        if cog: await cog.status_limpeza_saida(interaction)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="status_logs", description="Expõe se o olho que tudo vê (logs) está observando ou vendado no momento.")
    async def status_logs(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("DailyLoggerCog")
        if cog: await cog.status_logs(interaction)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="status-canais", description="Mapeia as artérias do servidor, revelando a função profana de cada canal consagrado.")
    async def status_canais(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("BlacklistCog")
        if cog:
            ctx = await interaction.client.get_context(interaction)
            await cog.status_canais(ctx)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="boas_vindas", description="Prepara o rito de recepção. Decreta como as almas novas serão saudadas no abismo.")
    async def boas_vindas(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("WelcomeCog")
        if cog: await cog.welcome(interaction)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)


    @app_commands.command(name="denuncia_chat", description="Posta a mensagem de abertura de denúncias no canal.")
    async def denuncia_chat(self, interaction: discord.Interaction) -> None:
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

    @app_commands.command(name="fechar_denuncia", description="Fecha (deleta) o canal de ticket atual.")
    async def fechar_denuncia(self, interaction: discord.Interaction) -> None:
        if not interaction.channel.name.startswith("denuncia-"):
            await interaction.response.send_message("❌ Este comando só pode ser usado dentro de um canal de ticket.", ephemeral=True)
            return
        await interaction.response.send_message("🔒 Fechando o ticket...", ephemeral=True)
        await interaction.channel.delete(reason=f"Ticket fechado por {interaction.user} via comando")

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        msg = "Ocorreu uma falha no sistema interno."
        if isinstance(error, app_commands.MissingPermissions):
            msg = "⛔ Acesso negado: Credenciais insuficientes."
        elif isinstance(error, app_commands.BotMissingPermissions):
            msg = f"🔧 Erro de Configuração: O Bot precisa da permissão {', '.join(error.missing_permissions)} para fazer isso."
        else:
            import logging
            logging.getLogger(__name__).exception("Crash não tratado no grupo de configuração", exc_info=error)

        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(ServerConfig())
    bot.add_view(BotaoAbrirDenuncia())
    bot.add_view(BotaoFecharDenuncia())
