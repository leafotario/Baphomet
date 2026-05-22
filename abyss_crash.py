import asyncio
import discord
from discord.ext import commands
from core_db_transaction import BaphometTransactionManager, SacrificeValidationError
from occult_ui_framework import AbyssalRNG, SacrificialView

class AbyssCrashView(SacrificialView):
    def __init__(self, author_id: int, tx_manager: BaphometTransactionManager, escrow_id: int, aposta: int, rng: AbyssalRNG, bot_loop: asyncio.AbstractEventLoop):
        super().__init__(author_id)
        self.tx_manager = tx_manager
        self.escrow_id = escrow_id
        self.aposta = aposta
        self.rng = rng
        self.is_finalized = False
        
        # Fórmulas Matemáticas da Colisão do Poço (Curva de Pareto adaptada)
        u = self.rng.generate_float()
        # Se u for 0, o crash é imediato (1.0). Limitamos a curva p/ evitar crash infinito e lidamos c/ a casa.
        # Pareto: x_m / (1 - u)^(1/alpha). Adaptado: x_m = 1.0, alpha=1.
        raw_crash = 1.0 / (1.0 - u)
        
        # Aplica a borda estática p/ proteger a banca (ex: casa puxa o teto pra baixo)
        self.crash_point = self.rng.calculate_house_edge(1.0, raw_crash, 0.05)
        if self.crash_point < 1.0:
            self.crash_point = 1.0
            
        self.current_multiplier = 1.0
        self.interaction_message: discord.Message | None = None
        
        # Gera o ciclo autônomo. Salva a referência para cancelá-lo em caso de fuga
        self.crash_task = bot_loop.create_task(self._crash_loop())

    async def _crash_loop(self):
        """Ciclo assíncrono iterativo focado em varrer a contagem exponencial."""
        try:
            # Aguarda a mensagem ser injetada primeiro
            await asyncio.sleep(2.0)
            
            while not self.is_finalized:
                # Interrupção temporal estrita de 1.2s para mitigar Rate Limits da API
                await asyncio.sleep(1.2)
                
                # Crescimento exponencial assustador (mas calculável visualmente)
                growth = 0.1 * (self.current_multiplier ** 1.1)
                self.current_multiplier += max(0.1, growth)
                
                # Checa a colisão mecânica (Teto atingido)
                if self.current_multiplier >= self.crash_point:
                    self.current_multiplier = self.crash_point
                    await self._execute_crash()
                    break
                
                # Substitui recursivamente o texto de aviso
                if self.interaction_message and not self.is_finalized:
                    embed = self.interaction_message.embeds[0]
                    embed.description = f"O abismo começa a se abrir... **[{self.current_multiplier:.2f}x]**\nAssista à força do motor puxando você."
                    embed.color = 0xFFFF00 # Yellow warning
                    try:
                        await self.interaction_message.edit(embed=embed, view=self)
                    except discord.errors.NotFound:
                        break # Mensagem deletada, aborta loop

        except asyncio.CancelledError:
            # Task foi interrompida com sucesso pela fuga
            pass
        except Exception:
            pass

    async def _execute_crash(self):
        """Dispara a obliteração quando o limite de Pareto é atingido antes da Fuga."""
        if self.is_finalized:
            return
            
        self.is_finalized = True
        try:
            await self.tx_manager.resolve_escrow(self.escrow_id, 0)
            
            if self.interaction_message:
                embed = self.interaction_message.embeds[0]
                embed.description = f"COLAPSO ESTRUTURAL! O abismo fechou suas mandíbulas em **[{self.crash_point:.2f}x]**.\nTodo o sacrifício foi obliterado antes que você pudesse gritar."
                embed.color = 0x8B0000
                embed.clear_fields()
                embed.add_field(name="Retorno", value="0 XP (Devorado)", inline=False)
                
                # Injeta a instrução final de disable e edit_original
                for child in self.children:
                    if hasattr(child, 'disabled'):
                        child.disabled = True
                await self.interaction_message.edit(embed=embed, view=self)
        except Exception:
            pass

    @discord.ui.button(label="Escapar (Cashout)", style=discord.ButtonStyle.success)
    async def escape_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Gatilho primário que interrompe o fluxo repetitivo da thread (fuga)."""
        await interaction.response.defer()
        
        if self.is_finalized:
            return
            
        self.is_finalized = True
        # Interrompe a Task secundária instantaneamente
        if not self.crash_task.done():
            self.crash_task.cancel()
            
        try:
            # Lucro aferido no momento exato do clique
            final_payout = int(self.aposta * self.current_multiplier)
            await self.tx_manager.resolve_escrow(self.escrow_id, final_payout)
            
            embed = interaction.message.embeds[0]
            embed.description = f"Você saltou no vazio no momento exato: **[{self.current_multiplier:.2f}x]**!\nUma faísca de sanidade preservou sua vida antes do teto de colisão (que ocorreria em {self.crash_point:.2f}x)."
            embed.color = 0x00FF00
            embed.clear_fields()
            embed.add_field(name="Vitalidade Resgatada", value=f"{final_payout} XP", inline=False)
            
            await self.finalize_view(interaction)
        except Exception:
            pass


class AbyssCrashCog(commands.Cog):
    def __init__(self, bot, tx_manager: BaphometTransactionManager):
        self.bot = bot
        self.tx_manager = tx_manager
        self.rng = AbyssalRNG()

    @commands.hybrid_command(name="crash_abissal", description="Encare a colisão de Pareto. Escape com seus lucros ou morra na queda.")
    async def crash_abissal(self, ctx: commands.Context, aposta: int):
        try:
            escrow_id = await self.tx_manager.create_escrow(ctx.author.id, ctx.guild.id, aposta)
        except SacrificeValidationError as e:
            await ctx.send(f"Recusa do Pacto: {e}", ephemeral=True)
            return

        embed = discord.Embed(
            title="Colapso Abissal",
            description="O abismo começa a se abrir... **[1.00x]**\nAssista à força do motor puxando você.",
            color=0xFFFF00
        )
        embed.add_field(name="Tributo Ancorado", value=f"{aposta} XP", inline=True)
        
        view = AbyssCrashView(ctx.author.id, self.tx_manager, escrow_id, aposta, self.rng, self.bot.loop)
        
        # Envia a mensagem original e ancora na view para edições do loop
        message = await ctx.send(embed=embed, view=view)
        view.interaction_message = message

async def setup(bot):
    if hasattr(bot, 'tx_manager'):
        await bot.add_cog(AbyssCrashCog(bot, bot.tx_manager))
