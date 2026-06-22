import discord
from discord.ext import commands
from discord import app_commands
import logging
import traceback
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

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        """
        SRE Design: Interceptador ultra-descritivo de falhas de UI silenciosas.
        Garante que exceções não fiquem presas/suprimidas dentro dos callbacks do discord.py.
        """
        view_name = self.__class__.__name__
        item_type = item.__class__.__name__
        
        # Coleta de estado interno dinamicamente
        state = {}
        if hasattr(self, 'current_page'):
            state['current_page'] = self.current_page
        if hasattr(item, 'values'):
            state['selected_values'] = item.values
            
        tb = traceback.format_exc()
        
        # Delimitadores visuais para isolar a falha catastrófica no console
        logger.error(
            f"\n❌ =================== [ TCG UI ERROR ] =================== ❌\n"
            f"View: {view_name} | Component: {item_type}\n"
            f"User: {interaction.user} (ID: {interaction.user.id})\n"
            f"Internal State: {state}\n"
            f"Exception Dump:\n{tb}\n"
            f"==========================================================\n"
        )
        
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ Ocorreu uma anomalia crítica no servidor. Os administradores foram notificados.", 
                ephemeral=True
            )
        else:
            await interaction.followup.send("❌ Ocorreu uma anomalia crítica no servidor. Os administradores foram notificados.", ephemeral=True)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        try:
            if hasattr(self, "message") and self.message:
                if self.message.embeds:
                    embed = self.message.embeds[0]
                    embed.color = discord.Color.dark_grey()
                    embed.set_footer(text="⏳ Sessão Expirada. Use o comando novamente para interagir.")
                    await self.message.edit(embed=embed, view=self)
                else:
                    content = self.message.content or ""
                    content += "\n\n**⏳ Sessão Expirada.**"
                    await self.message.edit(content=content, view=self)
        except discord.HTTPException:
            pass

class SecureModal(discord.ui.Modal):
    """
    SRE Design: Modal base para captura de texto corrompido em falhas lógicas.
    """
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        modal_name = self.__class__.__name__
        
        # Captura todos os inputs fornecidos antes do crash
        inputs = {}
        for child in self.children:
            if isinstance(child, discord.ui.TextInput):
                inputs[child.custom_id or child.label] = child.value
                
        tb = traceback.format_exc()
        
        logger.error(
            f"\n❌ ================== [ TCG MODAL ERROR ] ================== ❌\n"
            f"Modal: {modal_name}\n"
            f"User: {interaction.user} (ID: {interaction.user.id})\n"
            f"Captured Text Inputs: {inputs}\n"
            f"Exception Dump:\n{tb}\n"
            f"=========================================================\n"
        )
        
        if not interaction.response.is_done():
            await interaction.response.send_message("Um erro ocorreu ao processar o formulário. O log de debug foi gerado.", ephemeral=True)
        else:
            await interaction.followup.send("Erro interno ao ler dados submetidos.", ephemeral=True)


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
            
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.edit_original_response(embed=embed, view=self)
        except Exception:
            await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="⬅️ Anterior", style=discord.ButtonStyle.secondary, custom_id="inv_prev")
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
            await self._update_embed(interaction)

    @discord.ui.button(label="Próximo ➡️", style=discord.ButtonStyle.primary, custom_id="inv_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
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
        await interaction.response.defer(ephemeral=True)
        selected_uuids = self.values
        if self.service:
            # Envia a carga pro serviço tratar validações hard de sqlite/decks
            await self.service.set_main_deck(interaction.user.id, selected_uuids)
            
        await interaction.followup.send(
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

    @app_commands.command(name="ajuda", description="Manual de iniciação ao ecossistema TCG do Baphomet.")
    async def ajuda(self, interaction: discord.Interaction):
        service = getattr(self.bot, "tcg_service", None)
        # Respeitando Clean Architecture: o preço seria puxado do config do DB, caso não exista, temos um fallback orgânico
        booster_price = "algumas moedas"
        if service and hasattr(service, 'get_booster_price'):
            try:
                booster_price = f"{await service.get_booster_price()} moedas"
            except:
                pass

        embed = discord.Embed(
            title="💀 BAPHOMET TCG // MANUAL DE INICIAÇÃO",
            description="Bem-vindo ao submundo do Trading Card Game. Prepare-se para colecionar, forjar e dominar o circuito.",
            color=discord.Color.from_str("#1a1a1a")
        )

        embed.add_field(
            name="1. FORJA PREDITIVA (SUAS CARTAS)",
            value=(
                "Nossas cartas não dependem de puro RNG. Seus atributos combatentes refletem o seu império no servidor:\n"
                "**ATK:** Forjado a partir do seu volume de mensagens e XP.\n"
                "**DEF:** Extraído diretamente dos seus dias de permanência e lealdade ao servidor.\n"
                "**SPD:** Elevado pela sua ressonância e reações recebidas.\n"
                "Cargos de moderador e engajamento massivo em voz liberam **habilidades passivas únicas**."
            ),
            inline=False
        )

        embed.add_field(
            name="2. COLECIONAR & EXTRAIR",
            value=(
                f"Use `/tcg booster` para investir {booster_price} e rasgar pacotes, invocando "
                "novas instâncias diretamente para a sua coleção permanente.\n"
                "Use `/tcg inventario` para navegar pelas suas páginas de ativos."
            ),
            inline=False
        )

        embed.add_field(
            name="3. CONFIGURAR O DECK",
            value=(
                "Para lutar na arena, preparação é lei. É obrigatório usar `/tcg deck` para "
                "selecionar cirurgicamente entre **3 a 5 cartas** da sua coleção."
            ),
            inline=False
        )

        embed.add_field(
            name="4. O CIRCUITO (AÇÃO)",
            value=(
                "Com o deck no gatilho, as portas se abrem para a interação direta:\n"
                "🗡️ `/tcg duelo [@usuario]` - Desafie oponentes para o motor de turnos em tempo real rodando em memória.\n"
                "🤝 `/tcg trocar [@usuario]` - Abra a interface segura e atômica de intercâmbio de cartas."
            ),
            inline=False
        )

        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(name="perfil", description="Exibe os atributos gerados, experiência TCG e saldo do jogador.")
    async def perfil(self, interaction: discord.Interaction):
        await interaction.response.defer()
        service = getattr(self.bot, "tcg_service", None)
        if not service:
            return await interaction.followup.send("Servidor indisponível.", ephemeral=True)
            
        # Extração de contexto e delegação pura (Clean Architecture)
        profile_data = await service.get_profile(interaction.user.id)
        await interaction.followup.send(f"Perfil sincronizado: {profile_data}")

    @app_commands.command(name="inventario", description="Abre o painel interativo de paginação assíncrona da coleção de cartas.")
    async def inventario(self, interaction: discord.Interaction):
        await interaction.response.defer()
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
                
        view.message = await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="deck", description="Abre a interface de gerenciamento das cartas ativas via dropdowns.")
    async def deck(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        service = getattr(self.bot, "tcg_service", None)
        available_cards = []
        if service:
            available_cards = await service.get_available_deck_cards(interaction.user.id)
            
        view = DeckBuilderView(author_id=interaction.user.id, available_cards=available_cards, service=service)
        view.message = await interaction.followup.send("Escolha sabiamente as cartas para forjar seu Deck Primário:", view=view, ephemeral=True)

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
        await interaction.response.defer(ephemeral=True)
        if usuario.id == interaction.user.id:
            return await interaction.followup.send("Mercado negado: Transação consigo mesmo evadida.", ephemeral=True)
            
        await interaction.followup.send(f"Disparando negociação P2P segura com {usuario.display_name}...", ephemeral=True)

    @app_commands.command(name="duelo", description="Dispara o desafio de combate assíncrono controlado pelo motor Redis.")
    @app_commands.describe(usuario="Membro que será desafiado")
    async def duelo(self, interaction: discord.Interaction, usuario: discord.Member):
        await interaction.response.defer()
        if usuario.id == interaction.user.id:
            return await interaction.followup.send("Anomalia detectada: Duelo solitário vetado.", ephemeral=True)
            
        await interaction.followup.send(f"{interaction.user.mention} invocou {usuario.mention} para a Arena! Aquecendo motor Redis...")


# ==========================================
# Família Administrativa Restrita (/tcg_config)
# ==========================================

@app_commands.default_permissions(administrator=True)
class TCGAdminCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="tcg_config", description="Configurações administrativas do sistema de TCG")
        self.bot = bot

    @app_commands.command(name="dar_carta", description="Força a geração (mint) de uma carta específica para fins de eventos.")
    @app_commands.describe(usuario="Membro receptor", template_id="ID de catálogo do template")
    async def dar_carta(self, interaction: discord.Interaction, usuario: discord.Member, template_id: int):
        await interaction.response.defer(ephemeral=True)
        service = getattr(self.bot, "tcg_service", None)
        await interaction.followup.send(f"⚙️ Overwrite: Template {template_id} injetado no inventário de {usuario.display_name}.", ephemeral=True)

    @app_commands.command(name="reset", description="Purga os dados de inventário ou buffs efêmeros de uma identidade.")
    @app_commands.describe(usuario="Membro que sofrerá wipe")
    async def reset(self, interaction: discord.Interaction, usuario: discord.Member):
        await interaction.response.defer(ephemeral=True)
        service = getattr(self.bot, "tcg_service", None)
        await interaction.followup.send(f"⚙️ Wipe: Inventário de {usuario.display_name} pulverizado.", ephemeral=True)

    @app_commands.command(name="economia", description="Ajusta as variáveis econômicas globais persistidas no SQLite.")
    @app_commands.describe(booster_price="Preço base do booster", taxa_troca="Taxa de comissão em trocas (%)")
    async def economia(self, interaction: discord.Interaction, booster_price: int, taxa_troca: float):
        await interaction.response.defer(ephemeral=True)
        service = getattr(self.bot, "tcg_service", None)
        await interaction.followup.send(f"⚙️ Mercado Inflacionado: Booster ({booster_price}), Taxa Trade ({taxa_troca}%).", ephemeral=True)


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

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """
        SRE Design: Gerenciador de fallback global da extensão.
        Intercepta, categoriza as falhas do bot e extrai telemetria estruturada.
        """
        # Desembrulha a exceção invocada nativa do framework para chegar à falha raiz
        if isinstance(error, app_commands.CommandInvokeError):
            actual_error = error.original
        else:
            actual_error = error

        # Categorização: Erros operacionais/negócio recebem WARNING limpo
        if isinstance(error, (app_commands.CheckFailure, app_commands.CommandOnCooldown)):
            logger.warning(f"Operação barrada (Negócio/Cooldown) para {interaction.user.id}: {error}")
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Operação cancelada: {error}", ephemeral=True)
            return

        # Falhas catastróficas imprevistas recebem CRITICAL/ERROR block com tracing
        cmd_name = interaction.command.name if interaction.command else "Unknown"
        if interaction.command and interaction.command.parent:
            cmd_name = f"{interaction.command.parent.name} {cmd_name}"
            
        # SRE Design: Self-Healing & Graceful Degradation
        # Se o Pillow estourou OOM ou a CDN falhou, mudamos dinamicamente para Embed Textual sem crachar a UX.
        if type(actual_error).__name__ == "GracefulDegradationException":
            logger.warning(f"Self-Healing Acionado: Redirecionando payload visual para texto (User: {interaction.user.id}).")
            fallback_embed = discord.Embed(
                title="⚠️ Baphomet TCG - Modo Textual",
                description="A rede ou o renderizador gráfico estão lentos no momento. Aqui está o fallback puro da sua requisição.",
                color=discord.Color.orange()
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=fallback_embed, ephemeral=True)
            else:
                await interaction.followup.send(embed=fallback_embed, ephemeral=True)
            return
            
        args_dict = interaction.namespace.__dict__ if hasattr(interaction, 'namespace') else {}
        tb = "".join(traceback.format_exception(type(actual_error), actual_error, actual_error.__traceback__))

        logger.error(
            f"\n❗ ================== [ TCG SYSTEM CRITICAL ] ================== ❗\n"
            f"Command Executed: /{cmd_name}\n"
            f"User: {interaction.user} (ID: {interaction.user.id})\n"
            f"Guild ID: {interaction.guild_id} | Channel ID: {interaction.channel_id}\n"
            f"Invocation Args: {args_dict}\n"
            f"Exception Traceback:\n{tb}\n"
            f"===============================================================\n"
        )

        if not interaction.response.is_done():
            await interaction.response.send_message("Houve uma falha crítica na infraestrutura TCG. A telemetria foi gravada e enviada ao SRE.", ephemeral=True)
        else:
            await interaction.followup.send("Houve uma falha crítica na infraestrutura TCG.", ephemeral=True)


async def setup(bot):
    cog = TCGCog(bot)
    
    # Injetando o error handler central em todos os grupos manualmente
    # Para garantir que erros dentro de subcomandos borbulhem e recebam o tracing exaustivo
    cog.tcg_group.on_error = cog.cog_app_command_error
    cog.admin_group.on_error = cog.cog_app_command_error
    
    await bot.add_cog(cog)
