import discord
from discord import app_commands
from discord.ext import commands


class Clear(commands.Cog):
    """Cog de moderação — comando /clear."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /clear ────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="clear",
        description="🧹 Apaga uma quantidade de mensagens do canal.",
    )
    @app_commands.describe(
        quantidade="Número de mensagens a apagar (1 – 100).",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clear(
        self,
        interaction: discord.Interaction,
        quantidade: app_commands.Range[int, 1, 100],
    ) -> None:
        """
        Apaga `quantidade` mensagens do canal atual.

        Parâmetros
        ----------
        quantidade:
            Número de mensagens a deletar (mínimo 1, máximo 100).
        """

        # Adia a resposta de forma efêmera (só o autor vê)
        await interaction.response.defer(ephemeral=True)

        canal: discord.TextChannel = interaction.channel  # type: ignore[assignment]

        try:
            deletadas = await canal.purge(
                limit=quantidade,
                reason=f"[/clear] Solicitado por {interaction.user} (ID: {interaction.user.id})",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Não tenho permissão para apagar mensagens neste canal.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"❌ Ocorreu um erro ao apagar as mensagens: `{exc}`",
                ephemeral=True,
            )
            return

        total = len(deletadas)

        # ── Embed de confirmação (estilo Loritta) ─────────────────────────────
        embed = discord.Embed(
            title="🧹 Mensagens apagadas!",
            description=(
                f"**{total}** mensagen{'s' if total != 1 else ''} "
                f"{'foram' if total != 1 else 'foi'} apagada{'s' if total != 1 else ''} "
                f"com sucesso em {canal.mention}."
            ),
            color=discord.Color.from_str("#7289DA"),
        )
        embed.set_footer(
            text=f"Solicitado por {interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Tratamento de erros do /clear ─────────────────────────────────────────
    @clear.error
    async def clear_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ Você precisa da permissão **Gerenciar Mensagens** para usar este comando.",
                ephemeral=True,
            )
        elif isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"⏳ Aguarde **{error.retry_after:.1f}s** antes de usar o comando novamente.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro inesperado: `{error}`",
                ephemeral=True,
            )


# ── Setup ─────────────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Clear(bot))