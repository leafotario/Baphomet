import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

# Configuração de logger básico para o Cog
logger = logging.getLogger(__name__)

class Moderation(commands.Cog):
    """
    Cog responsável por comandos administrativos e ferramentas de moderação.
    Implementa padrões modernos assíncronos e tratamento de erros do discord.py.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="clean",
        description="Limpa um número específico de mensagens neste canal."
    )
    @app_commands.describe(
        amount="A quantidade de mensagens a serem apagadas (entre 1 e 100)."
    )
    # Garante que apenas moderadores com gerência de mensagens possam usar
    @app_commands.checks.has_permissions(manage_messages=True)
    # Garante que o bot tem as permissões vitais no canal antes de tentar rodar
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def clean(
        self, 
        interaction: discord.Interaction, 
        amount: app_commands.Range[int, 1, 100]
    ):
        """
        Executa a limpeza (purge) de mensagens no canal de forma assíncrona.
        Protegido contra o limite de 14 dias do Discord API e timeout de interação.
        """
        # Defer ephemeral IMEDIATAMENTE para evitar "Interaction Failed" em lotes grandes
        await interaction.response.defer(ephemeral=True)

        try:
            # Prevenção Sênior: O Discord (API error 50034) recusa apagar em massa mensagens mais 
            # velhas que 14 dias. Passamos este timedelta no parâmetro `after` para blindagem automática.
            limit_date = discord.utils.utcnow() - timedelta(days=14)

            # Check opcional embutido: pular mensagens fixadas para não destruir o mural do servidor
            def is_not_pinned(message: discord.Message) -> bool:
                return not message.pinned

            # Ação de expurgo nativa e otimizada do discord.py
            deleted_messages = await interaction.channel.purge(
                limit=amount,
                check=is_not_pinned,
                after=limit_date,
                bulk=True
            )

            deleted_count = len(deleted_messages)

            # TODO: [SISTEMA DE LOG DE AUDITORIA]
            # O Discord não registra quem rodou o bulk delete no audit log nativo.
            # Se possuir um canal de log, injete o envio aqui:
            # audit_channel = interaction.guild.get_channel(ID_DO_CANAL)
            # if audit_channel:
            #     await audit_channel.send(f"⚠️ O moderador {interaction.user.mention} usou `/clean` e apagou {deleted_count} mensagens em {interaction.channel.mention}.")

            # Feedback de UX gracioso, efêmero (não polui o chat limpo)
            await interaction.followup.send(
                f"✅ {deleted_count} mensagens foram apagadas com sucesso.",
                ephemeral=True
            )

        except discord.HTTPException as http_err:
            # Captura cirúrgica de falha da API
            if http_err.code == 50034:
                await interaction.followup.send(
                    "⚠️ As mensagens solicitadas têm mais de 14 dias e não podem ser apagadas em massa devido a restrições do Discord.",
                    ephemeral=True
                )
            else:
                logger.error(f"Erro HTTP {http_err.status} ao purgar mensagens: {http_err.text}", exc_info=True)
                await interaction.followup.send(
                    "❌ Falha de comunicação com a API do Discord ao tentar apagar as mensagens.",
                    ephemeral=True
                )
        except discord.Forbidden:
            # Fallback de segurança caso as permissões do cargo não batam com as permissões do canal
            await interaction.followup.send(
                "❌ Ocorreu um erro: Eu não tenho permissões suficientes neste canal para apagar essas mensagens.",
                ephemeral=True
            )
        except Exception as e:
            # Blindagem final contra erros não catalogados
            logger.error(f"Exceção não tratada no comando /clean: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ Ocorreu um erro interno inesperado durante a limpeza. O log foi registrado.",
                ephemeral=True
            )

    @clean.error
    async def clean_error_handler(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """
        Tratador local de erros do comando /clean interceptando falhas pré-execução (checks).
        """
        # Checa se a interação já sofreu um response.defer() em algum middleware misterioso
        send_method = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message

        if isinstance(error, app_commands.MissingPermissions):
            await send_method(
                "❌ Você não tem permissão para gerenciar mensagens.",
                ephemeral=True
            )
        elif isinstance(error, app_commands.BotMissingPermissions):
            await send_method(
                "❌ Eu não tenho permissão para gerenciar mensagens neste canal.",
                ephemeral=True
            )
        else:
            logger.error(f"Erro no check do comando /clean: {error}", exc_info=True)
            await send_method(
                "❌ Ocorreu um erro ao validar as permissões para este comando.",
                ephemeral=True
            )

async def setup(bot: commands.Bot):
    """Entry point essencial para o carregamento dinâmico da Cog."""
    await bot.add_cog(Moderation(bot))
