import math
import discord
from discord.ext import commands
from core_db_transaction import BaphometTransactionManager, SacrificeValidationError
from occult_ui_framework import AbyssalRNG, SacrificialView

class WeightOfSinsView(SacrificialView):
    def __init__(self, author_id: int, tx_manager: BaphometTransactionManager, escrow_id: int, aposta: int, rng: AbyssalRNG):
        super().__init__(author_id)
        self.tx_manager = tx_manager
        self.escrow_id = escrow_id
        self.aposta = aposta
        self.rng = rng
        self.consecutive_wins = 0
        self.current_raw_multiplier = 1.0
        self.is_finalized = False

    async def _process_guess(self, interaction: discord.Interaction, guess_is_heavy: bool):
        await interaction.response.defer()
        try:
            # Sorteio linear: 0 a 99. Leve (0-49), Pesado (50-99).
            roll = self.rng.generate_int(0, 99)
            is_heavy = roll >= 50
            
            embed = interaction.message.embeds[0]
            
            if guess_is_heavy == is_heavy:
                # Vitória: incrementa multiplicador base e o contador de precisão
                self.consecutive_wins += 1
                self.current_raw_multiplier += 0.5
                
                # A cada avanço, aumenta o limite escalar da casa em +0.2
                current_house_edge = min(0.99, 0.02 + (self.consecutive_wins * 0.2))
                adjusted_multiplier = self.rng.calculate_house_edge(0.5, self.current_raw_multiplier, current_house_edge)
                
                potential_payout = int(math.floor((self.aposta * adjusted_multiplier) + 1e-9))
                
                # Atualização sistemática da UI
                embed.description = f"Seu palpite foi correto! O peso da balança recaiu sobre: **{'Pesado' if is_heavy else 'Leve'}**.\nVocê sobreviveu mais um degrau, mas a ganância aumenta a borda da casa."
                
                # Reseta/Atualiza campos
                embed.clear_fields()
                embed.add_field(name="Vitórias Consecutivas", value=str(self.consecutive_wins), inline=True)
                embed.add_field(name="Múltiplo Atual (Pós-Dízimo)", value=f"{adjusted_multiplier:.2f}x", inline=True)
                embed.add_field(name="Vitalidade Acumulada", value=f"{potential_payout} XP", inline=True)
                embed.set_footer(text="Fuja enquanto o abismo não o devora, ou tente pesar os pecados novamente.")
                
                try:
                    await interaction.edit_original_response(embed=embed, view=self)
                except (discord.errors.NotFound, discord.errors.HTTPException):
                    if self.message:
                        try:
                            await self.message.edit(embed=embed, view=self)
                        except Exception:
                            pass
            else:
                # Falha: O abismo engole a aposta.
                self.is_finalized = True
                await self.tx_manager.resolve_escrow(self.escrow_id, 0)
                
                embed.description = f"O trajeto probabilístico falhou. A balança pendeu para **{'Pesado' if is_heavy else 'Leve'}** e esmagou seus ossos.\nSeu sacrifício foi obliterado."
                embed.clear_fields()
                embed.add_field(name="Vitalidade Restante", value="0 XP", inline=False)
                embed.color = 0x000000
                
                # Limpa totalmente a view original apagando os controles
                try:
                    await interaction.edit_original_response(embed=embed, view=None)
                except (discord.errors.NotFound, discord.errors.HTTPException):
                    if self.message:
                        try:
                            await self.message.edit(embed=embed, view=None)
                        except Exception:
                            pass
                
        except Exception as e:
            if not self.is_finalized:
                self.is_finalized = True
                await self.tx_manager.resolve_escrow(self.escrow_id, 0)
            raise e

    async def on_timeout(self) -> None:
        if not self.is_finalized:
            self.is_finalized = True
            try:
                await self.tx_manager.resolve_escrow(self.escrow_id, 0)
            except Exception:
                pass
        await super().on_timeout()

    @discord.ui.button(label="Pesado", style=discord.ButtonStyle.danger)
    async def guess_heavy(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._process_guess(interaction, True)

    @discord.ui.button(label="Leve", style=discord.ButtonStyle.primary)
    async def guess_light(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._process_guess(interaction, False)

    @discord.ui.button(label="Fugir", style=discord.ButtonStyle.success)
    async def flee(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            self.is_finalized = True
            current_house_edge = min(0.99, 0.02 + (self.consecutive_wins * 0.2))
            adjusted_multiplier = self.rng.calculate_house_edge(0.5, self.current_raw_multiplier, current_house_edge)
            payout = int(math.floor((self.aposta * adjusted_multiplier) + 1e-9))
            
            await self.tx_manager.resolve_escrow(self.escrow_id, payout)
            
            embed = interaction.message.embeds[0]
            embed.description = "A covardia garantiu sua sobrevivência. O pacto foi quebrado voluntariamente e o lucro colhido."
            embed.clear_fields()
            embed.add_field(name="Vitórias Consecutivas", value=str(self.consecutive_wins), inline=True)
            embed.add_field(name="Retorno Resgatado", value=f"{payout} XP", inline=True)
            embed.color = 0x00FF00
            
            await self.finalize_view(interaction)
        except Exception as e:
            if not self.is_finalized:
                await self.tx_manager.resolve_escrow(self.escrow_id, 0)
            raise e


class WeightOfSinsCog(commands.Cog):
    def __init__(self, bot, tx_manager: BaphometTransactionManager):
        self.bot = bot
        self.tx_manager = tx_manager
        self.rng = AbyssalRNG()

    async def play_pesados_pecados(self, interaction: discord.Interaction, aposta: int):
        await interaction.response.defer()
        try:
            escrow_id = await self.tx_manager.create_escrow(interaction.user.id, interaction.guild_id, aposta)
        except SacrificeValidationError as e:
            await interaction.followup.send(f"Recusa do Pacto: {e}", ephemeral=True)
            return

        embed = discord.Embed(
            title="A Balança dos Pecados",
            description="Um prato afunda, outro se ergue. Adivinhe o peso da próxima alma para transcender, mas lembre-se: cada passo aumenta o dízimo de Baphomet.",
            color=0x8B0000
        )
        embed.add_field(name="Tributo Aprisionado", value=f"{aposta} XP", inline=True)
        embed.add_field(name="Múltiplo Inicial", value="1.00x", inline=True)
        
        view = WeightOfSinsView(interaction.user.id, self.tx_manager, escrow_id, aposta, self.rng)
        msg = await interaction.followup.send(embed=embed, view=view)
        view.message = msg

async def setup(bot):
    if hasattr(bot, 'tx_manager'):
        await bot.add_cog(WeightOfSinsCog(bot, bot.tx_manager))
