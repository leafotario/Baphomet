import math
import discord
from discord.ext import commands
from core_db_transaction import BaphometTransactionManager, SacrificeValidationError
from occult_ui_framework import AbyssalRNG

class WheelOfTormentCog(commands.Cog):
    def __init__(self, bot, tx_manager: BaphometTransactionManager):
        self.bot = bot
        self.tx_manager = tx_manager
        self.rng = AbyssalRNG()

    async def play_macabra(self, interaction: discord.Interaction, aposta: int):
        """Simulação linear de escolhas (0 a 5) com impacto na vitalidade."""
        await interaction.response.defer()
        try:
            escrow_id = await self.tx_manager.create_escrow(interaction.user.id, interaction.guild_id, aposta)
        except SacrificeValidationError as e:
            await interaction.followup.send(f"Recusa do Pacto: {e}", ephemeral=True)
            return

        choice = self.rng.generate_int(0, 5)
        payout = 0

        # Regras condicionais do jogo de simulação linear
        if choice == 0:
            msg = "Cinzas e ácido. Suas veias escurecem enquanto Baphomet sorve a experiência arrancada do seu espírito. O cálice estava envenenado"
        elif choice in (1, 2):
            raw_multiplier = 2.0
            adjusted = self.rng.calculate_house_edge(1.0, raw_multiplier)
            payout = int(math.floor((aposta * adjusted) + 1e-9))
            msg = "A sombra acolhe sua audácia. O pacto floresce e sua vitalidade é dobrada pelas chamas."
        else:
            msg = "A roda gira, mas as brasas estão frias. Seu sacrifício foi devorado pelas trevas, sem agonia adicional, porém sem recompensa."

        # Encerramento garantido através do Escrow
        await self.tx_manager.resolve_escrow(escrow_id, payout)

        embed = discord.Embed(
            title="A Roda do Tormento",
            description=msg,
            color=0x8B0000
        )
        embed.add_field(name="Tributo Ofertado", value=f"{aposta} XP", inline=True)
        embed.add_field(name="Retorno Kármico", value=f"{payout} XP", inline=True)
        embed.set_footer(text=f"A entidade cravou o vetor {choice}")
        
        await interaction.followup.send(embed=embed)

async def setup(bot):
    if hasattr(bot, 'tx_manager'):
        await bot.add_cog(WheelOfTormentCog(bot, bot.tx_manager))
