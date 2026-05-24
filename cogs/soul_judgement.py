import math
import discord
from discord.ext import commands
from core_db_transaction import BaphometTransactionManager, SacrificeValidationError
from occult_ui_framework import AbyssalRNG, SacrificialView

class SoulJudgementView(SacrificialView):
    def __init__(self, author_id: int, tx_manager: BaphometTransactionManager, escrow_id: int, aposta: int, rng: AbyssalRNG):
        super().__init__(author_id)
        self.tx_manager = tx_manager
        self.escrow_id = escrow_id
        self.aposta = aposta
        self.rng = rng
        self.is_finalized = False
        
        # Gerenciamento de 18 unidades probabilísticas absolutas (O Deck Macabro)
        # Valores distribuídos entre 2 e 11 para compor um blackjack determinístico e restrito.
        base_deck = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11, 2, 3, 4, 5, 11]
        self.deck = []
        
        # Embaralhamento criptográfico imune a predição local
        while base_deck:
            idx = self.rng.generate_int(0, len(base_deck) - 1)
            self.deck.append(base_deck.pop(idx))
            
        self.player_hand = [self.draw_card(), self.draw_card()]
        self.dealer_hand = [self.draw_card(), self.draw_card()]

    def draw_card(self) -> int:
        if not self.deck:
            # Resiliência da matriz probabilística caso o ciclo estenda o limite hermético
            base_deck = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11, 2, 3, 4, 5, 11]
            while base_deck:
                idx = self.rng.generate_int(0, len(base_deck) - 1)
                self.deck.append(base_deck.pop(idx))
        return self.deck.pop()

    def get_hand_value(self, hand: list[int]) -> int:
        """Controlador de limite: aplica deduções herméticas se exceder 21 e possuir um 11."""
        total = sum(hand)
        aces = hand.count(11)
        while total > 21 and aces > 0:
            total -= 10
            aces -= 1
        return total

    def build_embed(self, status: str = "Aguardando seu veredito...", color: int = 0x2b2d31, final: bool = False) -> discord.Embed:
        embed = discord.Embed(title="O Julgamento da Alma (Blackjack Macabro)", description=status, color=color)
        
        p_val = self.get_hand_value(self.player_hand)
        embed.add_field(name="A Alma Mortal", value=f"Soma: **{p_val}**\nEntidades Retidas: {self.player_hand}", inline=True)
        
        if final:
            d_val = self.get_hand_value(self.dealer_hand)
            embed.add_field(name="O Carrasco (Bot)", value=f"Soma: **{d_val}**\nEntidades Retidas: {self.dealer_hand}", inline=True)
        else:
            embed.add_field(name="O Carrasco (Bot)", value=f"Soma: **?**\nEntidades: [{self.dealer_hand[0]}, ?]", inline=True)
            
        return embed

    @discord.ui.button(label="Invocar", style=discord.ButtonStyle.danger)
    async def invoke_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            self.player_hand.append(self.draw_card())
            p_val = self.get_hand_value(self.player_hand)
            
            if p_val > 21:
                # Violação das Leis Herméticas da capacidade da alma. Destruição imediata.
                self.is_finalized = True
                await self.tx_manager.resolve_escrow(self.escrow_id, 0)
                
                embed = self.build_embed(
                    status="**VIOLAÇÃO HERMÉTICA.** Sua alma se fragmentou ao tentar conter poder demasiado. Você ultrapassou os limites vitais e foi devorado.",
                    color=0x8B0000,
                    final=True
                )
                await self.finalize_view(interaction)
                await interaction.edit_original_response(embed=embed)
            else:
                # Continuidade
                embed = self.build_embed(status="A entidade foi anexada ao seu espírito. Deseja invocar mais dor ou reter sua carga atual?")
                await interaction.edit_original_response(embed=embed, view=self)
                
        except Exception as e:
            if not self.is_finalized:
                await self.tx_manager.resolve_escrow(self.escrow_id, 0)
            raise e

    @discord.ui.button(label="Reter", style=discord.ButtonStyle.primary)
    async def retain_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            if self.is_finalized: return
            self.is_finalized = True
            
            # Dealer assume o controle independente e robótico até alcançar no mínimo 17
            d_val = self.get_hand_value(self.dealer_hand)
            while d_val < 17:
                self.dealer_hand.append(self.draw_card())
                d_val = self.get_hand_value(self.dealer_hand)
                
            p_val = self.get_hand_value(self.player_hand)
            
            if d_val > 21:
                status = "**TRIUNFO.** O Carrasco sucumbiu à própria corrupção. Sua alma retorna intacta e carregada."
                color = 0x00FF00
                payout = int(math.floor((self.aposta * self.rng.calculate_house_edge(0.48, 2.0)) + 1e-9))
            elif p_val > d_val:
                status = "**TRIUNFO.** Seu julgamento foi preciso. Você subjugou as intenções do Carrasco."
                color = 0x00FF00
                payout = int(math.floor((self.aposta * self.rng.calculate_house_edge(0.48, 2.0)) + 1e-9))
            elif p_val == d_val:
                status = "**EQUILÍBRIO.** Forças iguais colidem. Baphomet apenas suspira e devolve seu sacrifício original."
                color = 0xFFFF00
                payout = self.aposta
            else:
                status = "**JULGAMENTO CONDENATÓRIO.** O Carrasco esmaga sua oferta. Sua alma sangra no cálice profano."
                color = 0x8B0000
                payout = 0
                
            await self.tx_manager.resolve_escrow(self.escrow_id, payout)
            embed = self.build_embed(status=status, color=color, final=True)
            embed.add_field(name="Vitalidade Arrecadada", value=f"{payout} XP", inline=False)
            
            await self.finalize_view(interaction)
            await interaction.edit_original_response(embed=embed)
            
        except Exception as e:
            if not self.is_finalized:
                await self.tx_manager.resolve_escrow(self.escrow_id, 0)
            raise e


class SoulJudgementCog(commands.Cog):
    def __init__(self, bot, tx_manager: BaphometTransactionManager):
        self.bot = bot
        self.tx_manager = tx_manager
        self.rng = AbyssalRNG()

    async def play_blackjack(self, interaction: discord.Interaction, aposta: int):
        await interaction.response.defer()
        try:
            escrow_id = await self.tx_manager.create_escrow(interaction.user.id, interaction.guild_id, aposta)
        except SacrificeValidationError as e:
            await interaction.followup.send(f"Recusa do Pacto: {e}", ephemeral=True)
            return

        view = SoulJudgementView(interaction.user.id, self.tx_manager, escrow_id, aposta, self.rng)
        embed = view.build_embed()
        embed.add_field(name="Tributo Ancorado", value=f"{aposta} XP", inline=False)
        
        await interaction.followup.send(embed=embed, view=view)

async def setup(bot):
    if hasattr(bot, 'tx_manager'):
        await bot.add_cog(SoulJudgementCog(bot, bot.tx_manager))
