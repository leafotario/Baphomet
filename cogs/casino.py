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

    @cassino.command(name="ajuda", description="O Livro Negro das Regras do Cassino. Veja limites e explicações.")
    async def ajuda(self, interaction: discord.Interaction):
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
