import math
import asyncio
import discord
from discord.ext import commands
from core_db_transaction import BaphometTransactionManager, SacrificeValidationError
from occult_ui_framework import AbyssalRNG

class BloodOracleCog(commands.Cog):
    def __init__(self, bot, tx_manager: BaphometTransactionManager):
        self.bot = bot
        self.tx_manager = tx_manager
        self.rng = AbyssalRNG()
        
        # Vetores visuais restritos (Constelações Visuais)
        self.symbols = ["💀", "🐍", "✡️", "🩸"]
        
        # Chaves dicionárias estritas para Payouts
        self.payout_table = {
            "💀": 2.0,
            "🐍": 4.0,
            "✡️": 8.0
        }

    async def play_oraculo(self, interaction: discord.Interaction, aposta: int):
        await interaction.response.defer()
        try:
            escrow_id = await self.tx_manager.create_escrow(interaction.user.id, interaction.guild_id, aposta)
        except SacrificeValidationError as e:
            await interaction.followup.send(f"Recusa do Pacto: {e}", ephemeral=True)
            return

        embed = discord.Embed(
            title="O Oráculo de Sangue",
            description="Os pilares começam a girar...\n[ 🌀 ] - [ 🌀 ] - [ 🌀 ]",
            color=0x2b2d31
        )
        msg = await interaction.followup.send(embed=embed)
        
        columns = ["🌀", "🌀", "🌀"]
        
        # Motor algorítmico individualizado: atraso proposital para suspensão dramática
        for i in range(3):
            await asyncio.sleep(0.8) # Milissegundos dramáticos
            
            idx = self.rng.generate_int(0, len(self.symbols) - 1)
            columns[i] = self.symbols[idx]
            
            embed.description = f"Os pilares rasgam a escuridão...\n[ {columns[0]} ] - [ {columns[1]} ] - [ {columns[2]} ]"
            await msg.edit(embed=embed)
            
        await asyncio.sleep(0.5)
        
        # Cruzamento Numérico e Avaliação Estrita
        has_blood = "🩸" in columns
        is_triple = columns[0] == columns[1] == columns[2]
        
        payout = 0
        if has_blood:
            # Regra inquebrável: O Sangue anula qualquer trinca subjacente
            embed.description = f"**Seu sangue não foi considerado digno.**\nA corrupção sangrenta tocou os pilares e esmagou a mesa.\n[ {columns[0]} ] - [ {columns[1]} ] - [ {columns[2]} ]"
            embed.color = 0x8B0000
            payout = 0
        elif is_triple:
            symbol = columns[0]
            raw_multiplier = self.payout_table.get(symbol, 0.0)
            adjusted = self.rng.calculate_house_edge(0.33, raw_multiplier)
            payout = int(math.floor((aposta * adjusted) + 1e-9))
            
            embed.description = f"**Bênção Sombria.** A constelação alinhou-se perfeitamente.\n[ {columns[0]} ] - [ {columns[1]} ] - [ {columns[2]} ]"
            embed.color = 0x00FF00
        else:
            embed.description = f"**Vazio Absoluto.** Os astros divergiram. A oferta evaporou.\n[ {columns[0]} ] - [ {columns[1]} ] - [ {columns[2]} ]"
            embed.color = 0xFFFF00
            payout = 0
            
        await self.tx_manager.resolve_escrow(escrow_id, payout)
        
        embed.add_field(name="Tributo Ofertado", value=f"{aposta} XP", inline=True)
        embed.add_field(name="Retorno Autorizado", value=f"{payout} XP", inline=True)
        
        await msg.edit(embed=embed)

async def setup(bot):
    if hasattr(bot, 'tx_manager'):
        await bot.add_cog(BloodOracleCog(bot, bot.tx_manager))
