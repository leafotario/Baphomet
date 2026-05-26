import discord
from discord import app_commands
from discord.ext import commands

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

    @app_commands.command(name="limpeza_saida", description="Configura a foice: expurga os rastros deixados por almas que abandonaram o servidor.")
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

    @app_commands.command(name="status_limpeza", description="Sussurra o estado atual da foice que limpa as cinzas dos que partiram.")
    async def status_limpeza(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("GhostCleanupCog")
        if cog: await cog.status_limpeza_saida(interaction)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="status_logs", description="Expõe se o olho que tudo vê (logs) está observando ou vendado no momento.")
    async def status_logs(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("DailyLoggerCog")
        if cog: await cog.status_logs(interaction)
        else: await interaction.response.send_message("Módulo indisponível.", ephemeral=True)

    @app_commands.command(name="status_canais", description="Mapeia as artérias do servidor, revelando a função profana de cada canal consagrado.")
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


async def setup(bot: commands.Bot) -> None:
    bot.tree.add_command(ServerConfig())
