import discord
from discord.ext import commands
from core_db_transaction import BaphometTransactionManager, SacrificeValidationError
from occult_ui_framework import AbyssalRNG, SacrificialView

class BonesNextThrowView(SacrificialView):
    def __init__(self, author_id: int, tx_manager: BaphometTransactionManager, escrow_id: int, aposta: int, point: int, rng: AbyssalRNG):
        super().__init__(author_id)
        self.tx_manager = tx_manager
        self.escrow_id = escrow_id
        self.aposta = aposta
        self.point = point
        self.rng = rng
        self.is_finalized = False

    @discord.ui.button(label="Lançar os Ossos Novamente", style=discord.ButtonStyle.danger)
    async def throw_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        Regra Lógica Infalível Aplicada: Diferimento primário, 
        processamento do novo laço algorítmico, e eventual finalização e fechamento.
        """
        await interaction.response.defer()
        
        try:
            d1 = self.rng.generate_int(1, 6)
            d2 = self.rng.generate_int(1, 6)
            total = d1 + d2
            
            embed = interaction.message.embeds[0]
            
            payout = 0
            if total == self.point:
                raw_multiplier = 2.0
                payout = int(self.aposta * self.rng.calculate_house_edge(0.5, raw_multiplier))
                embed.description += f"\n\nNovo lançamento revelou **{total}**! O alvo foi estilhaçado. Sua vitalidade prospera nas trevas."
                embed.add_field(name="Retorno Final", value=f"{payout} XP", inline=False)
                await self.tx_manager.resolve_escrow(self.escrow_id, payout)
                self.is_finalized = True
                
            elif total == 7:
                embed.description += f"\n\nSúbito e mortal: **{total}**. A ceifa é inevitável. Sua alma pagou o preço."
                embed.add_field(name="Retorno Final", value="0 XP", inline=False)
                await self.tx_manager.resolve_escrow(self.escrow_id, 0)
                self.is_finalized = True
                
            else:
                embed.description += f"\nNovo lançamento: **{total}**. O purgatório hesita. Atire novamente buscando {self.point} ou temendo o 7."
                await interaction.edit_original_response(embed=embed, view=self)
                
        finally:
            if self.is_finalized:
                await self.finalize_view(interaction)


class BonesOfTheDamnedCog(commands.Cog):
    def __init__(self, bot, tx_manager: BaphometTransactionManager):
        self.bot = bot
        self.tx_manager = tx_manager
        self.rng = AbyssalRNG()

    @commands.hybrid_command(name="ossos", description="Invoque os ossos dos condenados e enfrente as probabilidades do submundo.")
    async def ossos(self, ctx: commands.Context, aposta: int):
        try:
            escrow_id = await self.tx_manager.create_escrow(ctx.author.id, ctx.guild.id, aposta)
        except SacrificeValidationError as e:
            await ctx.send(f"Recusa do Pacto: {e}", ephemeral=True)
            return

        d1 = self.rng.generate_int(1, 6)
        d2 = self.rng.generate_int(1, 6)
        total = d1 + d2
        
        embed = discord.Embed(title="Os Ossos dos Condenados", color=0x8B0000)
        embed.add_field(name="Tributo Ofertado", value=f"{aposta} XP", inline=False)
        
        payout = 0
        if total in (7, 11):
            raw_multiplier = 2.0
            payout = int(aposta * self.rng.calculate_house_edge(0.5, raw_multiplier))
            embed.description = f"Soma macabra inicial: **{total}**. A sorte dos decaídos sorri para você. Vitória iminente liberada."
            embed.add_field(name="Retorno Final", value=f"{payout} XP", inline=False)
            await self.tx_manager.resolve_escrow(escrow_id, payout)
            await ctx.send(embed=embed)
            
        elif total in (2, 3, 12):
            embed.description = f"Ruína absoluta atingida na primeira invocação: **{total}**. Baphomet recolhe seus ossos. Valores retidos a zero."
            embed.add_field(name="Retorno Final", value=f"{payout} XP", inline=False)
            await self.tx_manager.resolve_escrow(escrow_id, payout)
            await ctx.send(embed=embed)
            
        else:
            embed.description = f"Um alvo de sangue foi selado: **{total}**. O rito exige novos lançamentos incessantes."
            view = BonesNextThrowView(ctx.author.id, self.tx_manager, escrow_id, aposta, total, self.rng)
            await ctx.send(embed=embed, view=view)

async def setup(bot):
    if hasattr(bot, 'tx_manager'):
        await bot.add_cog(BonesOfTheDamnedCog(bot, bot.tx_manager))
