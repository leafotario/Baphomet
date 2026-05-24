import discord
from discord import app_commands
from discord.ext import commands

class CasinoRouterCog(commands.Cog):
    """
    Roteador Central do Cassino Abissal.
    Gere as permissões, limites e redireciona os comandos em formato Slash para os módulos de jogo.
    """
    cassino = app_commands.Group(name="cassino", description="O Cassino Abissal")
    cassino_config = app_commands.Group(name="cassino_config", description="Administração do Cassino", default_permissions=discord.Permissions(administrator=True))

    def __init__(self, bot):
        self.bot = bot
        if not hasattr(bot, 'tx_manager'):
            raise RuntimeError("Bot não possui tx_manager inicializado.")
        self.tx_manager = bot.tx_manager

    async def validate_bet(self, interaction: discord.Interaction, game_id: str, aposta: int) -> bool:
        """Valida o sacrifício contra as configurações dinâmicas de SQL."""
        config = await self.tx_manager.get_casino_config(game_id)
        
        if not config["is_enabled"]:
            await interaction.response.send_message("Este ritual está selado pelos administradores.", ephemeral=True)
            return False
            
        if aposta < config["min_bet"]:
            await interaction.response.send_message(f"O sacrifício é muito fraco. O abismo exige no mínimo **{config['min_bet']} XP** para este rito.", ephemeral=True)
            return False
            
        if aposta > config["max_bet"]:
            await interaction.response.send_message(f"A entidade não suporta tamanha carga de uma vez. O máximo permitido é **{config['max_bet']} XP**.", ephemeral=True)
            return False
            
        return True

    # =========================================================================
    # CASSINO ADMIN COMMANDS
    # =========================================================================

    @cassino_config.command(name="min_xp", description="Define o valor mínimo de XP para um jogo do cassino.")
    @app_commands.choices(jogo=[
        app_commands.Choice(name="Crash Abissal", value="crash_abissal"),
        app_commands.Choice(name="Labirinto", value="labirinto"),
        app_commands.Choice(name="Dança das Chamas Negras", value="danca_negras"),
        app_commands.Choice(name="Pacto Cego", value="pacto_cego"),
        app_commands.Choice(name="Oráculo de Sangue", value="oraculo"),
        app_commands.Choice(name="Ossos dos Condenados", value="ossos"),
        app_commands.Choice(name="Julgamento (Blackjack)", value="blackjack"),
        app_commands.Choice(name="Leviatã", value="leviata"),
        app_commands.Choice(name="Pesos dos Pecados", value="pesados_pecados"),
        app_commands.Choice(name="Roda Macabra", value="macabra")
    ])
    async def config_min_xp(self, interaction: discord.Interaction, jogo: app_commands.Choice[str], valor: int):
        if valor < 1:
            await interaction.response.send_message("O mínimo não pode ser inferior a 1 XP.", ephemeral=True)
            return
            
        await self.tx_manager.update_casino_min_bet(jogo.value, valor)
        await interaction.response.send_message(f"🔧 O tributo mínimo do ritual **{jogo.name}** foi forjado em **{valor} XP** com sucesso no SQL.")

    # =========================================================================
    # CASSINO GLOBAL COMMANDS
    # =========================================================================

    @cassino.command(name="status", description="O painel analítico do Cassino Abissal. Veja o status e limites dos rituais.")
    async def status(self, interaction: discord.Interaction):
        # Reúne dados reais da Base de Dados
        games = {
            "crash_abissal": "Crash Abissal",
            "labirinto": "Labirinto",
            "danca_negras": "Dança das Chamas Negras",
            "pacto_cego": "Pacto Cego",
            "oraculo": "Oráculo de Sangue",
            "ossos": "Ossos dos Condenados",
            "blackjack": "Julgamento da Alma",
            "leviata": "Sacrifício Supremo",
            "pesados_pecados": "Pesos dos Pecados",
            "macabra": "Roda Macabra"
        }
        
        embed = discord.Embed(title="O Panteão do Cassino Abissal", description="As Leis Herméticas que regem o seu XP.", color=0x8B0000)
        
        for game_id, name in games.items():
            cfg = await self.tx_manager.get_casino_config(game_id)
            status = "🟢 Ativo" if cfg["is_enabled"] else "🔴 Selado"
            embed.add_field(
                name=f"{name}",
                value=f"**Mínimo:** {cfg['min_bet']} XP\n**Máximo:** {cfg['max_bet']} XP\n{status}",
                inline=True
            )
            
        embed.set_footer(text="Use os comandos da família /cassino para invocar os rituais.")
        await interaction.response.send_message(embed=embed)

    @cassino.command(name="ajuda", description="O Grimório Macabro dos rituais. Leia as leis do Abismo antes de sangrar seu XP.")
    async def ajuda(self, interaction: discord.Interaction):
        msg1 = (
            "🕯️ **O GRIMÓRIO DO CASSINO ABISSAL** 🕯️\n\n"
            "O Cassino Abissal não é um local de diversão, mortal. É um altar de sacrifícios onde a moeda corrente é a sua **Vitalidade (XP)**.\n"
            "Ao invocar um ritual, o seu XP é temporariamente **retido pelo Abismo (Escrow)**. Se você sobreviver e vencer, a sua vitalidade é devolvida, purificada e multiplicada, após o dízimo inegociável da casa. Se você falhar, o Abismo consome sua alma e alimenta o Leviatã.\n\n"
            "📜 **OS RITUAIS DE SANGUE** 📜\n\n"
            "📉 **`/cassino crash_abissal` — O Poço Sem Fundo**\n"
            "Encare a colisão de Pareto. Um multiplicador insano cresce a cada segundo que passa, mas o colapso estrutural é iminente. Ajoelhe-se e implore pelo momento certo de usar o *Cashout* e escapar com vida. Se o poço desabar antes de você fugir, o seu XP será obliterado.\n\n"
            "🎡 **`/cassino macabra` — A Roda do Tormento**\n"
            "Um giro sombrio (0 a 5). Oferte sua vitalidade à roda de ossos. O sacrifício pode dobrar a sua essência, pode ser aniquilado sem dor em um empate vazio, ou resultar em perda total e sangrenta.\n\n"
            "🎲 **`/cassino ossos` — Os Ossos dos Condenados**\n"
            "A dança macabra das probabilidades (regras ancestrais de Craps). O primeiro lançamento julga o seu destino:\n"
            "• **Vitória imediata** se os ossos marcarem 7 ou 11.\n"
            "• **Morte fulminante** se marcarem 2, 3 ou 12.\n"
            "Qualquer outro valor torna-se a sua 'Marca'. Você deverá rolar os ossos novamente até atingir a Marca (e vencer) ou rolar um 7 (e encontrar o seu fim).\n\n"
        )
        msg2 = (
            "👁️ **`/cassino pacto_cego` — O Pacto Cego**\n"
            "Uma matriz informacional oculta. Três carrascos aguardam a sua escolha: O Leviatã, O Rastejante ou O Sussurrador. Cada um esconde um multiplicador criptográfico. Escolha o seu carrasco e receba o seu destino... mas saiba que o oblívio absoluto (0x) espreita nas sombras.\n\n"
            "⚖️ **`/cassino pesados_pecados` — A Balança dos Pecados**\n"
            "O rito infinito de adivinhação. Ajoelhe-se perante a balança e tente adivinhar se o próximo pecado será mais *Pesado* ou mais *Leve* que o anterior. Acertar aumenta a sua recompensa, permitindo que você continue num ciclo ganancioso, mas a cada passo, o dízimo da casa (borda) aumenta e esmaga as suas chances.\n\n"
            "🐙 **`/cassino leviata` — O Sacrifício Supremo**\n"
            "O rito global e extremo. A barreira de entrada exige blocos absurdos de XP. A sua chance de vitória? 1 em 10.000. Mas se os deuses antigos sorrirem para você, a própria realidade irá rachar, e você absorverá a totalidade do **Jackpot Multiversal** acumulado por todos os sacrifícios falhos de outros tolos.\n\n"
            "🧩 **`/cassino labirinto`** — Sobreviva à grade minada.\n"
            "🔥 **`/cassino danca_negras`** — O ritual multiplayer de sobrevivência.\n"
            "🔮 **`/cassino oraculo`** — Alinhe os astros no caça-níqueis infernal.\n"
            "🃏 **`/cassino blackjack`** — O julgamento da alma. Vença sem quebrar os selos herméticos (21)."
        )
        await interaction.response.send_message(msg1)
        await interaction.followup.send(msg2)

    @cassino.command(name="crash_abissal", description="Encare a colisão de Pareto. Escape com seus lucros ou morra na queda.")
    async def crash_abissal(self, interaction: discord.Interaction, aposta: int):
        if not await self.validate_bet(interaction, "crash_abissal", aposta): return
        cog = self.bot.get_cog("AbyssCrashCog")
        await cog.play_crash_abissal(interaction, aposta)

    @cassino.command(name="labirinto", description="Navegação em grade. Encontre caminhos seguros ou ative as minas letais.")
    async def labirinto(self, interaction: discord.Interaction, aposta: int):
        if not await self.validate_bet(interaction, "labirinto", aposta): return
        cog = self.bot.get_cog("LabyrinthCog")
        await cog.play_labirinto(interaction, aposta)

    @cassino.command(name="danca_negras", description="A Dança das Chamas Negras (Multiplayer).")
    async def danca_negras(self, interaction: discord.Interaction):
        # Config de bet mínimo gerida pelo Modal internamente, pois não é argumento aqui
        cfg = await self.tx_manager.get_casino_config("danca_negras")
        if not cfg["is_enabled"]:
            await interaction.response.send_message("Ritual selado.", ephemeral=True)
            return
        cog = self.bot.get_cog("BlackFlamesDanceCog")
        await cog.play_danca_negras(interaction)

    @cassino.command(name="pacto_cego", description="Matriz informacional oculta. Escolha seu carrasco e receba seu destino.")
    async def pacto_cego(self, interaction: discord.Interaction, aposta: int):
        if not await self.validate_bet(interaction, "pacto_cego", aposta): return
        cog = self.bot.get_cog("BlindPactCog")
        await cog.play_pacto_cego(interaction, aposta)

    @cassino.command(name="oraculo", description="O Caça-Níqueis Infernal. Alinhe os astros, evite o Sangue Corrompido.")
    async def oraculo(self, interaction: discord.Interaction, aposta: int):
        if not await self.validate_bet(interaction, "oraculo", aposta): return
        cog = self.bot.get_cog("BloodOracleCog")
        await cog.play_oraculo(interaction, aposta)

    @cassino.command(name="ossos", description="Invoque os ossos dos condenados e enfrente as probabilidades do submundo.")
    async def ossos(self, interaction: discord.Interaction, aposta: int, faces: int = 6):
        if not await self.validate_bet(interaction, "ossos", aposta): return
        cog = self.bot.get_cog("BonesCog")
        await cog.play_ossos(interaction, aposta, faces)

    @cassino.command(name="blackjack", description="O Julgamento da Alma. Acumule unidades sem quebrar as leis herméticas.")
    async def blackjack(self, interaction: discord.Interaction, aposta: int):
        if not await self.validate_bet(interaction, "blackjack", aposta): return
        cog = self.bot.get_cog("SoulJudgementCog")
        await cog.play_blackjack(interaction, aposta)

    @cassino.command(name="leviata", description="Rito Extremo. Chance de 1 em 10.000 de rasgar a realidade e clamar o jackpot.")
    async def leviata(self, interaction: discord.Interaction, aposta: int):
        if not await self.validate_bet(interaction, "leviata", aposta): return
        cog = self.bot.get_cog("UltimateSacrificeCog")
        await cog.play_leviata(interaction, aposta)

    @cassino.command(name="pesados_pecados", description="Aposte no peso dos pecados. Push your luck infinito com dízimo escalar crescente.")
    async def pesados_pecados(self, interaction: discord.Interaction, aposta: int):
        if not await self.validate_bet(interaction, "pesados_pecados", aposta): return
        cog = self.bot.get_cog("WeightOfSinsCog")
        await cog.play_pesados_pecados(interaction, aposta)

    @cassino.command(name="macabra", description="Gire a Roda do Tormento. Escolhas secretas e consequências mortais.")
    async def macabra(self, interaction: discord.Interaction, aposta: int):
        if not await self.validate_bet(interaction, "macabra", aposta): return
        cog = self.bot.get_cog("WheelOfTormentCog")
        await cog.play_macabra(interaction, aposta)

async def setup(bot):
    await bot.add_cog(CasinoRouterCog(bot))
