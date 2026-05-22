import asyncio
import discord
from discord.ext import commands
from core_db_transaction import BaphometTransactionManager, SacrificeValidationError
from occult_ui_framework import AbyssalRNG

class UltimateSacrificeCog(commands.Cog):
    def __init__(self, bot, tx_manager: BaphometTransactionManager):
        self.bot = bot
        self.tx_manager = tx_manager
        self.rng = AbyssalRNG()
        
        # O limite da submissão exige blocos absurdos
        self.min_bet_required = 50000

    @commands.hybrid_command(name="leviata", description="Rito Extremo Inter-Servidores. Uma chance em dez mil de rasgar a realidade e clamar o jackpot global.")
    async def leviata(self, ctx: commands.Context, aposta: int):
        # Validação estrita do bloqueio absurdo de XP
        if aposta < self.min_bet_required:
            await ctx.send(
                f"Sua insignificância ofende O Leviatã. O acesso exige blocos de sacrifício pré-definidos acima de **{self.min_bet_required} XP**.", 
                ephemeral=True
            )
            return

        try:
            escrow_id = await self.tx_manager.create_escrow(ctx.author.id, ctx.guild.id, aposta)
        except SacrificeValidationError as e:
            await ctx.send(f"Recusa Oculta: {e}", ephemeral=True)
            return

        # Processo de loteria estatística severa: Escala 1 em 10.000
        roll = self.rng.generate_int(1, 10000)
        
        if roll == 6666:
            # Exceção absoluta de sucesso: O Rasgo na Realidade ocorreu.
            
            # 1. Resgate e esvaziamento de todo o jackpot global através do banco
            async with self.tx_manager.connection() as conn:
                async with conn.execute("SELECT SUM(leviathan_jackpot) as total_jackpot FROM guild_economy") as cursor:
                    row = await cursor.fetchone()
                    total_jackpot = row["total_jackpot"] if row and row["total_jackpot"] else 0.0
                    
                # Reseta o sistema multiversal
                await conn.execute("UPDATE guild_economy SET leviathan_jackpot = 0.0")
                await conn.commit()
                
            payout = int(aposta * 10) + int(total_jackpot)
            await self.tx_manager.resolve_escrow(escrow_id, payout)
            
            embed = discord.Embed(
                title="UM RASGO NA REALIDADE GLOBAL",
                description="As fundações do multiverso se estilhaçaram.\n\n**O Leviatã** abriu as garras e coroou seu sacrifício, devendo a você as almas de milhares de mundos mortos.",
                color=0xFFD700 # Dourado de status mitológico final
            )
            embed.add_field(name="O Novo Deus (Mortal Escolhido)", value=f"{ctx.author.name} (Juridição Original: {ctx.guild.name})", inline=False)
            embed.add_field(name="Poder Cósmico Injetado", value=f"{payout} XP", inline=False)
            
            await ctx.send("Seus olhos queimam. Sua alma transborda.", embed=embed)
            
            # Chamadas Sistêmicas Inter-Servidores (Broadcast universal iterando bot.guilds)
            for guild in self.bot.guilds:
                target_channel = None
                # Varredura para encontrar o canal generalista que permita mensagens
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).send_messages:
                        target_channel = channel
                        break
                
                if target_channel:
                    try:
                        # Repasse universal
                        await target_channel.send(
                            "🌩️ **ALERTA SISTÊMICO ABSOLUTO: O VÉU DO LEVIATÃ FOI ROMPIDO** 🌩️\n*Baphomet reverencia o novo ápice...*", 
                            embed=embed
                        )
                    except Exception:
                        pass
        else:
            # Retorno exigido nulo na vasta base temporal das ocorrências repetidas
            await self.tx_manager.resolve_escrow(escrow_id, 0)
            
            # A alimenta parcial pro Leviatã: Aposta base tem 10% enviada ao Jackpot Global
            async with self.tx_manager.connection() as conn:
                jackpot_contribution = aposta * 0.10
                await conn.execute(
                    "INSERT OR IGNORE INTO guild_economy (guild_id, leviathan_jackpot) VALUES (?, 0.0)",
                    (ctx.guild.id,)
                )
                await conn.execute(
                    "UPDATE guild_economy SET leviathan_jackpot = leviathan_jackpot + ? WHERE guild_id = ?",
                    (jackpot_contribution, ctx.guild.id)
                )
                await conn.commit()
                
            await ctx.send(f"O Leviatã sorveu rapidamente seus **{aposta} XP** e cuspiu os restos no poço absoluto.\nA barreira permanece fechada. O abismo ainda tem fome.")

async def setup(bot):
    if hasattr(bot, 'tx_manager'):
        await bot.add_cog(UltimateSacrificeCog(bot, bot.tx_manager))
