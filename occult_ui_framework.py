import secrets
import discord
from discord.ui import View

class AbyssalRNG:
    """
    Máquina base do gerador criptográfico. 
    Extrai entropia de hardware evitando geradores preditivos como random.
    """
    def __init__(self):
        # Omitimos import random e usamos exclusivamente secrets
        self._rng = secrets.SystemRandom()

    def generate_float(self) -> float:
        """Gera um valor de ponto flutuante criptograficamente seguro entre 0.0 e 1.0."""
        return self._rng.random()

    def generate_int(self, min_val: int, max_val: int) -> int:
        """Gera um inteiro criptograficamente seguro entre min_val e max_val."""
        return self._rng.randint(min_val, max_val)

    @staticmethod
    def calculate_house_edge(probability_factor: float, raw_multiplier: float, house_edge: float = 0.02) -> float:
        """
        Extração do dízimo matemático: estipula e retira a borda estática da casa.
        
        :param probability_factor: Fator de probabilidade da vitória.
        :param raw_multiplier: Multiplicador base antes da incidência da casa.
        :param house_edge: O 'dízimo' da banca (ex: 0.02 para 2%).
        :return: Multiplicador ajustado (com o edge da casa retido).
        """
        # Reduz do multiplicador base uma porcentagem definida como dízimo estatístico
        adjusted_multiplier = raw_multiplier * (1.0 - house_edge)
        return max(0.0, adjusted_multiplier)


class SacrificialView(View):
    """
    Espinha dorsal visual para interfaces críticas de interação.
    Previne cliques duplos (latência) e mitiga a barreira dos 3 segundos da API Discord.
    """
    def __init__(self, author_id: int, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.message: discord.Message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """
        Bloqueio imperativo contra invasão de contexto. 
        Invalida cliques de almas que não sejam o author_id.
        """
        if interaction.user.id == self.author_id:
            return True
            
        await interaction.response.send_message(
            "Sua alma não foi convidada para este pacto", 
            ephemeral=True
        )
        return False

    async def finalize_view(self, interaction: discord.Interaction) -> None:
        """
        Fechamento absoluto: Itena por todos os componentes visuais, definindo-os
        como inativos (button.disabled = True) e aciona a injeção da mudança na API.
        """
        for child in self.children:
            if hasattr(child, 'disabled'):
                child.disabled = True
                
        try:
            await interaction.edit_original_response(view=self)
        except (discord.errors.NotFound, discord.errors.HTTPException):
            # Fallback para token expirado (Regra dos 15 minutos)
            if self.message:
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass

    async def on_timeout(self) -> None:
        """
        Desativação implacável.
        A view expirou por inatividade e os botões devem morrer.
        """
        for child in self.children:
            if hasattr(child, 'disabled'):
                child.disabled = True
                
        if self.message:
            try:
                embed = self.message.embeds[0]
                embed.set_footer(text="A entidade se entediou. O pacto expirou por inatividade.")
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass
                
        # Subclasses devem dar override neste método para injetar logicamente o aborto do escrow e chamar super().on_timeout()

    # Exemplo base representativo de um callback protegido.
    @discord.ui.button(label="Invocar Pacto", style=discord.ButtonStyle.danger)
    async def invoke_pact_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        Regra Lógica Infalível Aplicada:
        1. Atraso/defer no primeiro instante previne double-click timeout.
        2. Processamento do evento e entrega da recompensa.
        3. Travamento irreversível da View via finalize_view.
        """
        # Instrução Primária Mandatória
        await interaction.response.defer()
        
        try:
            # (Desdobramento da lógica de recompensa ocorre aqui nas subclasses ou extensão desta base)
            pass
        finally:
            # Fechamento Absoluto
            await self.finalize_view(interaction)
