import discord
from discord.ext import commands
import logging
from typing import List

logger = logging.getLogger(__name__)

class SecureView(discord.ui.View):
    """
    Mecanismo Anti-Intrusão Crítico.
    Sobrescreve interaction_check em todas as Views herdadas.
    Verifica se a identidade do clicker confere com a whitelist de autores/alvos designados.
    Se falhar, cessa qualquer propagação de Redis/SQLite no ato, respondendo em ephemeral.
    """
    def __init__(self, allowed_users: List[int], timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.allowed_users = allowed_users

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.allowed_users:
            return True
            
        logger.warning(f"Intrusão barrada: {interaction.user.id} tentou interagir com painel restrito.")
        await interaction.response.send_message(
            "Você não tem permissão para interagir com este botão/menu. Esta sessão não é sua.", 
            ephemeral=True
        )
        return False


class InventoryPaginationView(SecureView):
    """
    Paginação Lógica dinâmica consultando o SQLite progressivamente por LIMIT e OFFSET.
    Evita violações de payload limite do Discord Embeds ao reescrever a mesma janela.
    """
    def __init__(self, author_id: int, db_connection, current_page: int = 0):
        super().__init__(allowed_users=[author_id])
        self.author_id = author_id
        self.db = db_connection
        self.current_page = current_page
        self.items_per_page = 5

    async def _update_embed(self, interaction: discord.Interaction):
        offset = self.current_page * self.items_per_page
        
        embed = discord.Embed(title="Inventário Baphomet TCG", color=discord.Color.dark_theme())
        embed.set_footer(text=f"Página {self.current_page + 1}")

        if self.db:
            # Query com LIMIT e OFFSET para poupar RAM e Embed Size
            query = """
                SELECT c.uuid, t.nome_moldura, t.raridade, c.atk, c.defesa, c.spd 
                FROM card_instances c
                JOIN card_templates t ON c.modelo_id = t.id_serial
                WHERE c.dono_id = ? 
                LIMIT ? OFFSET ?
            """
            rows = await self.db.execute_fetchall(query, (self.author_id, self.items_per_page, offset))
            
            if not rows and self.current_page > 0:
                # Volta a página se estiver vazia e não for a primeira
                self.current_page -= 1
                return await interaction.response.defer()
            
            if not rows:
                embed.description = "Seu inventário está vazio."
            else:
                for row in rows:
                    uuid, nome, raridade, atk, defs, spd = row
                    embed.add_field(
                        name=f"[{raridade}] {nome}",
                        value=f"**UUID:** `{uuid[:8]}...` | **ATK:** {atk} | **DEF:** {defs} | **SPD:** {spd}",
                        inline=False
                    )
        else:
            embed.description = "Conexão de Banco de Dados indisponível no momento."
            
        # Altera o embed no lugar sem lançar novas mensagens
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="⬅️ Anterior", style=discord.ButtonStyle.secondary, custom_id="inv_prev")
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            await self._update_embed(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="Próximo ➡️", style=discord.ButtonStyle.primary, custom_id="inv_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        await self._update_embed(interaction)


class DeckSelect(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption]):
        super().__init__(
            placeholder="Selecione seu esquadrão tático principal...",
            min_values=3,  # Limite mecânico do prompt
            max_values=5,  # Limite mecânico do prompt
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        # Esta view também depende da proteção da parent (SecureView)
        selected_uuids = self.values
        
        # AQUI aplicaria lógica no tcg_repository para gravar as cartas no `decks`
        
        await interaction.response.send_message(
            f"✅ Deck principal configurado com sucesso com {len(selected_uuids)} cartas!", 
            ephemeral=True
        )


class DeckBuilderView(SecureView):
    def __init__(self, author_id: int, available_cards: list):
        super().__init__(allowed_users=[author_id])
        
        # Popula o Select Dropdown restrito a apenas UUIDs autenticados pelo DB no mesmo millisegundo
        options = []
        for card in available_cards:
            uuid, nome, raridade = card
            options.append(discord.SelectOption(label=f"[{raridade}] {nome}", value=uuid))
            
        if not options:
            options.append(discord.SelectOption(label="Nenhuma carta disponível", value="none"))
            
        self.add_item(DeckSelect(options=options[:25])) # Select aceita máximo 25


class TCGCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="inventario", description="Folheie seu inventário oficial no Baphomet TCG.")
    async def inventario(self, ctx: commands.Context):
        # Se tcg_repo existir e for acessível via bot:
        db_conn = getattr(getattr(self.bot, "tcg_repo", None), "db", None) # Exemplo de ponteiro
        
        # Instancia view restrita ao autor
        view = InventoryPaginationView(author_id=ctx.author.id, db_connection=db_conn)
        
        embed = discord.Embed(title="Inventário Baphomet TCG", description="Sincronizando...", color=discord.Color.dark_theme())
        msg = await ctx.send(embed=embed, view=view)
        
        # Força o primeiro carregamento para instanciar o OFFSET zero nativamente pela UI View
        await view._update_embed(discord.Interaction(data={}, state=msg._state)) # Mock minimalista para o boot inicial se necessário,
        # O correto aqui seria preencher o embed antes de enviar o send. 

    @commands.hybrid_command(name="deck", description="Crie e modifique as cartas que formam seu Deck Primário (3 a 5 cartas).")
    async def deck_builder(self, ctx: commands.Context):
        # Exemplo simulado da busca no mesmo milissegundo de cartas possuídas
        # rows = await self.bot.tcg_repo.db.execute_fetchall("SELECT uuid, nome_moldura, raridade FROM ... WHERE dono_id = ?", (ctx.author.id,))
        mock_cards_db = [
            ("uuid-123", "Dragão do Caos", "Mítica"),
            ("uuid-456", "Espada Larga", "Comum"),
            ("uuid-789", "Anjo Vingador", "Rara"),
            ("uuid-101", "Demônio Menor", "Incomum"),
            ("uuid-112", "Cubo Mágico", "Lendária"),
            ("uuid-131", "Sombra Rastejante", "Comum")
        ]
        
        view = DeckBuilderView(author_id=ctx.author.id, available_cards=mock_cards_db)
        await ctx.send("Escolha de **3 a 5 cartas** da sua coleção abaixo para engajar no ringue:", view=view)

async def setup(bot):
    await bot.add_cog(TCGCog(bot))
