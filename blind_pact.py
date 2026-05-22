import discord
from discord.ext import commands
from core_db_transaction import BaphometTransactionManager, SacrificeValidationError
from occult_ui_framework import AbyssalRNG, SacrificialView

class BlindPactSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="O Leviatã", description="As profundezas abissais o aguardam."),
            discord.SelectOption(label="O Rastejante", description="Aquele que se move pelas sombras do túmulo."),
            discord.SelectOption(label="O Sussurrador", description="As vozes ancestrais pedem um tributo.")
        ]
        super().__init__(placeholder="Escolha seu carrasco...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Transfere a lógica infalível para o componente View pai
        view: "BlindPactView" = self.view
        await view.process_selection(interaction, self.values[0])


class BlindPactView(SacrificialView):
    def __init__(self, author_id: int, tx_manager: BaphometTransactionManager, escrow_id: int, aposta: int, rng: AbyssalRNG):
        super().__init__(author_id)
        self.tx_manager = tx_manager
        self.escrow_id = escrow_id
        self.aposta = aposta
        self.rng = rng
        self.add_item(BlindPactSelect())
        
        # Vetores criptográficos de recompensa pré-gerados e ocultos
        multipliers = [0.0, 1.5, 3.0]
        
        # Embaralhamento criptográfico estrito utilizando o gerador AbyssalRNG (hardware entropy)
        shuffled = []
        indices = [0, 1, 2]
        for _ in range(3):
            idx = self.rng.generate_int(0, len(indices) - 1)
            shuffled.append(multipliers.pop(idx))
            
        self.vectors = {
            "O Leviatã": shuffled[0],
            "O Rastejante": shuffled[1],
            "O Sussurrador": shuffled[2]
        }

    async def process_selection(self, interaction: discord.Interaction, choice: str):
        """
        Submissão da seleção que consulta a matriz de carrascos gerada no construtor.
        Desdobramento da lógica protegida pelo bloqueio imperativo da SacrificialView.
        """
        # Instrução primária mandatória
        await interaction.response.defer()
        
        try:
            raw_multiplier = self.vectors[choice]
            
            if raw_multiplier == 0.0:
                payout = 0
                msg = "A narrativa do desespero cego ecoa. Seu carrasco sorri e esmaga sua alma num abismo vazio. O pacto foi selado em dor e seu sacrifício pulverizado a 0x."
            else:
                payout = int(self.aposta * self.rng.calculate_house_edge(0.33, raw_multiplier))
                msg = f"Sua barganha com {choice} foi aceitável. O véu se levanta revelando a recompensa multiplicada transmutada para o seu sangue."
            
            await self.tx_manager.resolve_escrow(self.escrow_id, payout)
            
            embed = interaction.message.embeds[0]
            embed.description = msg
            embed.add_field(name="Múltiplo Secreto do Algoz", value=f"{raw_multiplier}x", inline=True)
            embed.add_field(name="Retorno Real Pós-Dízimo", value=f"{payout} XP", inline=True)
            
        finally:
            # Fechamento absoluto: Itena componentes em button.disabled = True e edita
            await self.finalize_view(interaction)


class BlindPactCog(commands.Cog):
    def __init__(self, bot, tx_manager: BaphometTransactionManager):
        self.bot = bot
        self.tx_manager = tx_manager
        self.rng = AbyssalRNG()

    @commands.hybrid_command(name="pacto_cego", description="Matriz informacional oculta. Escolha seu carrasco e receba seu destino.")
    async def pacto_cego(self, ctx: commands.Context, aposta: int):
        try:
            escrow_id = await self.tx_manager.create_escrow(ctx.author.id, ctx.guild.id, aposta)
        except SacrificeValidationError as e:
            await ctx.send(f"Recusa do Pacto: {e}", ephemeral=True)
            return

        embed = discord.Embed(
            title="O Pacto Cego", 
            description="Perante os tronos do abismo, selecione a qual carrasco do purgatório você confiará sua vitalidade. Cuidado: um deles guarda o vetor de obliteração absoluta (0x).",
            color=0x000000
        )
        embed.add_field(name="Tributo Aprisionado (Escrow)", value=f"{aposta} XP", inline=False)
        
        view = BlindPactView(ctx.author.id, self.tx_manager, escrow_id, aposta, self.rng)
        await ctx.send(embed=embed, view=view)

async def setup(bot):
    if hasattr(bot, 'tx_manager'):
        await bot.add_cog(BlindPactCog(bot, bot.tx_manager))
