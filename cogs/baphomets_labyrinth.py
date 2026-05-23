import discord
from discord.ext import commands
from core_db_transaction import BaphometTransactionManager, SacrificeValidationError
from occult_ui_framework import AbyssalRNG, SacrificialView

class LabyrinthButton(discord.ui.Button):
    def __init__(self, x: int, y: int, is_mine: bool, view_parent: "BaphometsLabyrinthView"):
        # Distribui os botões nas linhas 0, 1, 2, e 3
        super().__init__(style=discord.ButtonStyle.secondary, label="👁️", row=x)
        self.x = x
        self.y = y
        self.is_mine = is_mine
        self.view_parent = view_parent

    async def callback(self, interaction: discord.Interaction):
        await self.view_parent.process_step(interaction, self)


class BaphometsLabyrinthView(SacrificialView):
    def __init__(self, author_id: int, tx_manager: BaphometTransactionManager, escrow_id: int, aposta: int, rng: AbyssalRNG):
        super().__init__(author_id)
        self.tx_manager = tx_manager
        self.escrow_id = escrow_id
        self.aposta = aposta
        self.rng = rng
        self.is_finalized = False
        
        self.calm_routes_found = 0
        self.total_mines = 4
        
        # Gera o vetor booleano (16 posições, 4 minas letais subjacentes)
        vector = [True] * self.total_mines + [False] * (16 - self.total_mines)
        # Embaralhamento criptográfico
        shuffled = []
        for _ in range(16):
            idx = self.rng.generate_int(0, len(vector) - 1)
            shuffled.append(vector.pop(idx))
            
        # Constrói a grid 4x4
        idx = 0
        for row in range(4):
            for col in range(4):
                is_mine = shuffled[idx]
                btn = LabyrinthButton(row, col, is_mine, self)
                self.add_item(btn)
                idx += 1
                
        # Botão de fuga na linha 4
        self.escape_btn = discord.ui.Button(style=discord.ButtonStyle.primary, label="Sacar & Fugir", row=4)
        self.escape_btn.callback = self.process_escape
        self.add_item(self.escape_btn)

    async def process_step(self, interaction: discord.Interaction, button: LabyrinthButton):
        await interaction.response.defer()
        
        try:
            if button.is_mine:
                self.is_finalized = True
                await self.tx_manager.resolve_escrow(self.escrow_id, 0)
                
                # Transforma forçadamente o layout completo revelando as feras
                for child in self.children:
                    if isinstance(child, LabyrinthButton):
                        child.disabled = True
                        if child.is_mine:
                            child.style = discord.ButtonStyle.danger
                            child.label = "👹"
                        else:
                            child.style = discord.ButtonStyle.secondary
                            child.label = "🌫️"
                            
                self.escape_btn.disabled = True
                
                embed = interaction.message.embeds[0]
                embed.description = "**A Fera Abissal foi despertada!** O labirinto se consome em chamas e sua alma é dilacerada nas sombras."
                embed.color = 0x8B0000
                embed.clear_fields()
                embed.add_field(name="Sacrifício Aniquilado", value="0 XP", inline=False)
                
                await interaction.edit_original_response(embed=embed, view=self)
                
            else:
                # Preenchimento de estilo Verde (Success) para rotas calmas
                button.style = discord.ButtonStyle.success
                button.label = "✔️"
                button.disabled = True
                self.calm_routes_found += 1
                
                current_multiplier = 1.0 + (self.calm_routes_found * 0.15)
                adjusted_multiplier = self.rng.calculate_house_edge(0.8, current_multiplier)
                current_payout = int(self.aposta * adjusted_multiplier)
                
                embed = interaction.message.embeds[0]
                embed.description = "Passos cuidadosos no labirinto. A escuridão ainda permanece adormecida."
                embed.clear_fields()
                embed.add_field(name="Vias Seguras", value=str(self.calm_routes_found), inline=True)
                embed.add_field(name="Saque Acumulado", value=f"{current_payout} XP", inline=True)
                
                # Se desvendou todo o labirinto exceto as 4 minas
                if self.calm_routes_found == 12:
                    self.is_finalized = True
                    await self.tx_manager.resolve_escrow(self.escrow_id, current_payout)
                    
                    for child in self.children:
                        child.disabled = True
                        
                    embed.description = "**TRIUNFO.** O Labirinto foi mapeado. As feras choram no escuro enquanto você ascende."
                    embed.color = 0x00FF00
                    
                await interaction.edit_original_response(embed=embed, view=self)
                
        except Exception as e:
            if not self.is_finalized:
                await self.tx_manager.resolve_escrow(self.escrow_id, 0)
            raise e

    async def process_escape(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            self.is_finalized = True
            
            # Se tentou fugir sem pisar em nada
            if self.calm_routes_found == 0:
                payout = self.aposta
            else:
                current_multiplier = 1.0 + (self.calm_routes_found * 0.15)
                adjusted_multiplier = self.rng.calculate_house_edge(0.8, current_multiplier)
                payout = int(self.aposta * adjusted_multiplier)
            
            await self.tx_manager.resolve_escrow(self.escrow_id, payout)
            
            for child in self.children:
                child.disabled = True
                
            embed = interaction.message.embeds[0]
            embed.description = "Sua intuição (ou covardia) gritou mais alto. Você corre de volta à entrada."
            embed.color = 0xFFFF00
            embed.clear_fields()
            embed.add_field(name="Vias Seguras Mapeadas", value=str(self.calm_routes_found), inline=True)
            embed.add_field(name="Vitalidade Preservada", value=f"{payout} XP", inline=True)
            
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            if not self.is_finalized:
                await self.tx_manager.resolve_escrow(self.escrow_id, 0)
            raise e


class LabyrinthCog(commands.Cog):
    def __init__(self, bot, tx_manager: BaphometTransactionManager):
        self.bot = bot
        self.tx_manager = tx_manager
        self.rng = AbyssalRNG()

    @commands.hybrid_command(name="labirinto", description="Navegação em grade. Encontre caminhos seguros ou ative as minas letais.")
    async def labirinto(self, ctx: commands.Context, aposta: int):
        try:
            escrow_id = await self.tx_manager.create_escrow(ctx.author.id, ctx.guild.id, aposta)
        except SacrificeValidationError as e:
            await ctx.send(f"Recusa do Pacto: {e}", ephemeral=True)
            return

        embed = discord.Embed(
            title="O Labirinto de Baphomet",
            description="Dezesseis selos obscuros à sua frente. Pisos seguros multiplicam sua alma, bestas escondidas a aniquilam de imediato.",
            color=0x2b2d31
        )
        embed.add_field(name="Tributo Ancorado", value=f"{aposta} XP", inline=False)
        
        view = BaphometsLabyrinthView(ctx.author.id, self.tx_manager, escrow_id, aposta, self.rng)
        await ctx.send(embed=embed, view=view)

async def setup(bot):
    if hasattr(bot, 'tx_manager'):
        await bot.add_cog(LabyrinthCog(bot, bot.tx_manager))
