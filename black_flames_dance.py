import asyncio
import itertools
import discord
from discord.ext import commands
from core_db_transaction import BaphometTransactionManager, SacrificeValidationError
from occult_ui_framework import AbyssalRNG

class DanceJoinModal(discord.ui.Modal, title="Sua Oferta de Sangue"):
    bet_input = discord.ui.TextInput(
        label="Quantia (XP)",
        placeholder="Ex: 500",
        required=True
    )
    
    def __init__(self, view_parent: "DanceJoinView"):
        super().__init__()
        self.view_parent = view_parent

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            aposta = int(self.bet_input.value)
            
            # Impedir duplas submissões do mesmo mortal
            if any(p["user"].id == interaction.user.id for p in self.view_parent.participants):
                await interaction.followup.send("Você já está preso na Roda de Fogo.", ephemeral=True)
                return
                
            escrow_id = await self.view_parent.tx_manager.create_escrow(interaction.user.id, self.view_parent.guild_id, aposta)
            
            self.view_parent.participants.append({
                "user": interaction.user,
                "escrow_id": escrow_id,
                "amount": aposta
            })
            
            await interaction.followup.send(
                f"🔥 {interaction.user.mention} pisou nas Chamas Negras ofertando **{aposta} XP**.",
                ephemeral=False
            )
        except ValueError:
            await interaction.followup.send("Submissão corrompida. Use números inteiros.", ephemeral=True)
        except SacrificeValidationError as e:
            await interaction.followup.send(f"Acesso Negado: {e}", ephemeral=True)

class DanceJoinView(discord.ui.View):
    def __init__(self, tx_manager: BaphometTransactionManager, guild_id: int):
        # A vida útil principal não importa muito, pois paramos o loop manualmente via código
        super().__init__(timeout=None)
        self.tx_manager = tx_manager
        self.guild_id = guild_id
        self.participants = []
        
    @discord.ui.button(label="Entrar na Dança", style=discord.ButtonStyle.danger)
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DanceJoinModal(self))


class BlackFlamesDanceCog(commands.Cog):
    def __init__(self, bot, tx_manager: BaphometTransactionManager):
        self.bot = bot
        self.tx_manager = tx_manager
        self.rng = AbyssalRNG()
        self.active_dances = set()

    @commands.hybrid_command(name="danca_negras", description="A Dança das Chamas Negras (Multiplayer). Sorteio ponderado global pelo XP lançado na roda.")
    async def danca_negras(self, ctx: commands.Context):
        if ctx.channel.id in self.active_dances:
            await ctx.send("As Chamas já ardem neste canal. Aguarde o fim do sacrifício atual.", ephemeral=True)
            return
            
        self.active_dances.add(ctx.channel.id)
        
        view = DanceJoinView(self.tx_manager, ctx.guild.id)
        embed = discord.Embed(
            title="A Dança das Chamas Negras",
            description="Um portal macabro foi aberto. Vocês têm exatos **30 segundos** para submeterem suas almas à roda.\n\nAquele que injetar mais energia (XP), adquire maior peso no sorteio e devora as apostas inimigas.",
            color=0x8B0000
        )
        msg = await ctx.send(embed=embed, view=view)
        
        # Compasso de espera com asyncio.Event() para sincronia estrita requisitada
        wait_event = asyncio.Event()
        
        async def timer_task():
            await asyncio.sleep(30.0)
            wait_event.set()
            
        self.bot.loop.create_task(timer_task())
        await wait_event.wait()
        
        # Encerramento visual da View
        view.stop()
        for child in view.children:
            child.disabled = True
        await msg.edit(view=view)
        
        # Avaliação de resultados
        if not view.participants:
            self.active_dances.remove(ctx.channel.id)
            await ctx.send("As Chamas Negras esfriaram e morreram de fome. Nenhuma alma se apresentou.")
            return
            
        if len(view.participants) == 1:
            p = view.participants[0]
            await self.tx_manager.resolve_escrow(p["escrow_id"], p["amount"])
            self.active_dances.remove(ctx.channel.id)
            await ctx.send(f"Apenas um tolo avançou ao fogo. O ritual multiplayer exige sangue mútuo. {p['user'].mention}, seu XP foi devolvido intacto.")
            return

        total_pool = sum(p["amount"] for p in view.participants)
        
        # Alocação determinística usando pesos baseados nas quantias investidas
        weights = [p["amount"] for p in view.participants]
        cum_weights = list(itertools.accumulate(weights))
        
        roll = self.rng.generate_int(1, total_pool)
        winner_idx = 0
        for i, w in enumerate(cum_weights):
            if roll <= w:
                winner_idx = i
                break
                
        winner = view.participants[winner_idx]
        
        # Dízimo da casa sobre a mesa total
        final_pool = int(total_pool * self.rng.calculate_house_edge(1.0, 1.0, 0.05))
        
        matrix_details = []
        for p in view.participants:
            chance_percent = (p["amount"] / total_pool) * 100
            if p == winner:
                matrix_details.append(f"👑 **{p['user'].name}** | Oferta: {p['amount']} XP | Peso: {chance_percent:.2f}% | **SOBREVIVEU**")
                await self.tx_manager.resolve_escrow(p["escrow_id"], final_pool)
            else:
                matrix_details.append(f"💀 **{p['user'].name}** | Oferta: {p['amount']} XP | Peso: {chance_percent:.2f}% | **ESMAGADO**")
                await self.tx_manager.resolve_escrow(p["escrow_id"], 0)
                
        res_embed = discord.Embed(
            title="As Chamas Negras Esmagaram as Vítimas",
            description="\n".join(matrix_details),
            color=0x8B0000
        )
        res_embed.add_field(name="Oferta Global Coletada", value=f"{total_pool} XP", inline=True)
        res_embed.add_field(name="Prêmio do Sobrevivente (Pós-Dízimo)", value=f"{final_pool} XP", inline=True)
        
        await ctx.send(f"**A DANÇA TERMINOU!** O sangue escorreu, e Baphomet coroa {winner['user'].mention}!", embed=res_embed)
        self.active_dances.remove(ctx.channel.id)

async def setup(bot):
    if hasattr(bot, 'tx_manager'):
        await bot.add_cog(BlackFlamesDanceCog(bot, bot.tx_manager))
