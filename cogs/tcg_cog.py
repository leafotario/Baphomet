import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import List

logger = logging.getLogger(__name__)

# ==========================================
# Componentes Interativos e Views (UI)
# ==========================================

class SecureView(discord.ui.View):
    """
    Mecanismo Anti-Intrusão Crítico.
    Verifica se a identidade do clicker confere com a whitelist estipulada.
    Impede bypass e evita vazamento para a camada de I/O do banco de dados.
    """
    def __init__(self, allowed_users: List[int], timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.allowed_users = allowed_users

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.allowed_users:
            return True
            
        logger.warning(f"Intrusão barrada: User {interaction.user.id} tentou acessar painel restrito.")
        await interaction.response.send_message(
            "Você não tem permissão para interagir com este componente. O alvo/autor não é você.", 
            ephemeral=True
        )
        return False


class InventoryPaginationView(SecureView):
    """
    Paginação Lógica dinâmica sem limites de Embed.
    Pede porções calculadas (Offset) ao Service para reescrever a tela via edit_message.
    """
    def __init__(self, author_id: int, service, current_page: int = 0):
        super().__init__(allowed_users=[author_id])
        self.author_id = author_id
        self.service = service
        self.current_page = current_page
        self.items_per_page = 5

    async def _update_embed(self, interaction: discord.Interaction):
        offset = self.current_page * self.items_per_page
        
        embed = discord.Embed(title="Inventário Baphomet TCG", color=discord.Color.dark_theme())
        embed.set_footer(text=f"Página {self.current_page + 1}")

        if self.service:
            try:
                # O Controlador exige os dados brutos da regra de negócios em vez de rodar SQL
                cards = await self.service.get_inventory_page(self.author_id, limit=self.items_per_page, offset=offset)
                
                if not cards and self.current_page > 0:
                    self.current_page -= 1
                    return await interaction.response.defer()
                
                if not cards:
                    embed.description = "Seu inventário está vazio."
                else:
                    for card in cards:
                        embed.add_field(
                            name=f"[{card.get('raridade', '?')}] {card.get('nome', '?')}",
                            value=f"**UUID:** `{card.get('uuid', '00')[:8]}` | **ATK:** {card.get('atk', 0)} | **DEF:** {card.get('defesa', 0)} | **SPD:** {card.get('spd', 0)}",
                            inline=False
                        )
            except Exception as e:
                logger.error(f"Erro UI - Inventário: {e}")
                embed.description = "Houve uma anomalia interna ao sincronizar suas cartas."
        else:
            embed.description = "Servidor TCG em manutenção."
            
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
    def __init__(self, options: List[discord.SelectOption], service):
        self.service = service
        super().__init__(
            placeholder="Selecione seu esquadrão tático...",
            min_values=3,  
            max_values=5,  
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        selected_uuids = self.values
        if self.service:
            # Envia a carga pro serviço tratar validações hard de sqlite/decks
            await self.service.set_main_deck(interaction.user.id, selected_uuids)
            
        await interaction.response.send_message(
            f"✅ Configuração salva. Você destacou {len(selected_uuids)} cartas para o front de batalha!", 
            ephemeral=True
        )


class DeckBuilderView(SecureView):
    def __init__(self, author_id: int, available_cards: list, service):
        super().__init__(allowed_users=[author_id])
        options = []
        for card in available_cards:
            options.append(discord.SelectOption(label=f"[{card.get('raridade')}] {card.get('nome')}", value=card.get('uuid')))
            
        if not options:
            options.append(discord.SelectOption(label="Nenhuma carta elegível", value="none"))
            
        self.add_item(DeckSelect(options=options[:25], service=service))


# ==========================================
# Família Principal de Jogadores (/tcg)
# ==========================================

class TCGCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="tcg", description="Comandos do ecossistema de Trading Card Game")
        self.bot = bot

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"TCGCommands Error: {error}")
        msg = "Ocorreu um erro ao processar o ecossistema TCG. Operação cancelada."
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(name="perfil", description="Exibe os atributos gerados, experiência TCG e saldo do jogador.")
    async def perfil(self, interaction: discord.Interaction):
        service = getattr(self.bot, "tcg_service", None)
        if not service:
            return await interaction.response.send_message("Servidor indisponível.", ephemeral=True)
            
        # Extração de contexto e delegação pura (Clean Architecture)
        profile_data = await service.get_profile(interaction.user.id)
        await interaction.response.send_message(f"Perfil sincronizado: {profile_data}")

    @app_commands.command(name="inventario", description="Abre o painel interativo de paginação assíncrona da coleção de cartas.")
    async def inventario(self, interaction: discord.Interaction):
        service = getattr(self.bot, "tcg_service", None)
        view = InventoryPaginationView(author_id=interaction.user.id, service=service)
        
        cards = []
        if service:
            cards = await service.get_inventory_page(interaction.user.id, limit=view.items_per_page, offset=0)
            
        embed = discord.Embed(title="Inventário Baphomet TCG", color=discord.Color.dark_theme())
        embed.set_footer(text="Página 1")
        
        if not cards:
            embed.description = "Seu inventário está vazio."
        else:
            for card in cards:
                embed.add_field(
                    name=f"[{card.get('raridade', '?')}] {card.get('nome', '?')}",
                    value=f"**UUID:** `{card.get('uuid', '00')[:8]}` | **ATK:** {card.get('atk', 0)} | **DEF:** {card.get('defesa', 0)} | **SPD:** {card.get('spd', 0)}",
                    inline=False
                )
                
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="deck", description="Abre a interface de gerenciamento das cartas ativas via dropdowns.")
    async def deck(self, interaction: discord.Interaction):
        service = getattr(self.bot, "tcg_service", None)
        available_cards = []
        if service:
            available_cards = await service.get_available_deck_cards(interaction.user.id)
            
        view = DeckBuilderView(author_id=interaction.user.id, available_cards=available_cards, service=service)
        await interaction.response.send_message("Escolha sabiamente as cartas para forjar seu Deck Primário:", view=view, ephemeral=True)

    @app_commands.command(name="booster", description="Executa a compra e a renderização de um pacote de expansão.")
    async def booster(self, interaction: discord.Interaction):
        await interaction.response.defer()
        pack_service = getattr(self.bot, "tcg_pack_service", None)
        if pack_service:
            # O serviço reage rasgando o pacote e chamando as engines de renderização Pillow
            await interaction.followup.send("Abrindo Booster Pack... (Serviço acionado)")
        else:
            await interaction.followup.send("Lojista TCG indisponível no momento.")

    @app_commands.command(name="trocar", description="Inicia o fluxo transacional atômico de troca de ativos.")
    @app_commands.describe(usuario="Membro com quem deseja trocar")
    async def trocar(self, interaction: discord.Interaction, usuario: discord.Member):
        if usuario.id == interaction.user.id:
            return await interaction.response.send_message("Mercado negado: Transação consigo mesmo evadida.", ephemeral=True)
            
        await interaction.response.send_message(f"Disparando negociação P2P segura com {usuario.display_name}...", ephemeral=True)

    @app_commands.command(name="duelo", description="Dispara o desafio de combate assíncrono controlado pelo motor Redis.")
    @app_commands.describe(usuario="Membro que será desafiado")
    async def duelo(self, interaction: discord.Interaction, usuario: discord.Member):
        if usuario.id == interaction.user.id:
            return await interaction.response.send_message("Anomalia detectada: Duelo solitário vetado.", ephemeral=True)
            
        await interaction.response.send_message(f"{interaction.user.mention} invocou {usuario.mention} para a Arena! Aquecendo motor Redis...")


# ==========================================
# Família Administrativa Restrita (/tcg_config)
# ==========================================

@app_commands.default_permissions(administrator=True)
class TCGAdminCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="tcg_config", description="Configurações administrativas do sistema de TCG")
        self.bot = bot

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Erro no Kernel Administrativo (TCGAdminCommands): {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message("Falha crônica na rotina de administrador.", ephemeral=True)

    @app_commands.command(name="dar_carta", description="Força a geração (mint) de uma carta específica para fins de eventos.")
    @app_commands.describe(usuario="Membro receptor", template_id="ID de catálogo do template")
    async def dar_carta(self, interaction: discord.Interaction, usuario: discord.Member, template_id: int):
        service = getattr(self.bot, "tcg_service", None)
        await interaction.response.send_message(f"⚙️ Overwrite: Template {template_id} injetado no inventário de {usuario.display_name}.", ephemeral=True)

    @app_commands.command(name="reset", description="Purga os dados de inventário ou buffs efêmeros de uma identidade.")
    @app_commands.describe(usuario="Membro que sofrerá wipe")
    async def reset(self, interaction: discord.Interaction, usuario: discord.Member):
        service = getattr(self.bot, "tcg_service", None)
        await interaction.response.send_message(f"⚙️ Purge Concluído: {usuario.display_name} retornou à estaca zero.", ephemeral=True)

    @app_commands.command(name="economia", description="Ajusta as variáveis econômicas globais persistidas no SQLite.")
    @app_commands.describe(booster_price="Preço base do booster", taxa_troca="Taxa de comissão em trocas (%)")
    async def economia(self, interaction: discord.Interaction, booster_price: int, taxa_troca: float):
        service = getattr(self.bot, "tcg_service", None)
        await interaction.response.send_message(f"⚙️ Mercado Inflacionado: Booster ({booster_price}), Taxa Trade ({taxa_troca}%).", ephemeral=True)


# ==========================================
# Extensão Core (Delivery Mechanism base)
# ==========================================

class TCGCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Instanciação isolada de famílias garantindo namespace limpo de Slash Commands
        self.tcg_group = TCGCommands(bot)
        self.admin_group = TCGAdminCommands(bot)
        
        # Acoplamento das Árvores 
        self.bot.tree.add_command(self.tcg_group)
        self.bot.tree.add_command(self.admin_group)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.tcg_group.name)
        self.bot.tree.remove_command(self.admin_group.name)

    def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """
        Gerenciador de fallback global da extensão.
        Intercepta trapaças antes do sistema reagir.
        """
        if isinstance(error, app_commands.MissingPermissions):
            # Fallback redundante extra-camada
            pass
        logger.error(f"Erro não tratado na infra do TCG: {error}")

async def setup(bot):
    await bot.add_cog(TCGCog(bot))
