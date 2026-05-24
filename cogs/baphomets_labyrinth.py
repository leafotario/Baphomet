import math
import discord
import json
import uuid
from discord.ext import commands
from core_db_transaction import BaphometTransactionManager, SacrificeValidationError
from occult_ui_framework import AbyssalRNG

class LabyrinthButton(discord.ui.Button):
    def __init__(self, x: int, y: int, is_mine: bool, custom_id: str):
        # Distribui os botões nas linhas 0, 1, 2, e 3
        super().__init__(style=discord.ButtonStyle.secondary, label="👁️", row=x, custom_id=custom_id)
        self.x = x
        self.y = y
        self.is_mine = is_mine

    async def callback(self, interaction: discord.Interaction):
        await self.view.process_step(interaction, self)


class BaphometsLabyrinthView(discord.ui.View):
    def __init__(self, tx_manager: BaphometTransactionManager, session_id: str, rng: AbyssalRNG):
        super().__init__(timeout=None)
        self.tx_manager = tx_manager
        self.session_id = session_id
        self.rng = rng
        self.is_finalized = False
        
    async def initialize_grid(self):
        """Reconstrói a grid a partir da base de dados"""
        async with self.tx_manager.acquire() as conn:
            cursor = await conn.execute(
                "SELECT x_idx, y_idx, is_mine, is_revealed FROM labyrinth_cells WHERE session_id = ? ORDER BY x_idx, y_idx",
                (self.session_id,)
            )
            cells = await cursor.fetchall()
            
            for cell in cells:
                btn = LabyrinthButton(cell["x_idx"], cell["y_idx"], bool(cell["is_mine"]), custom_id=f"lab:btn:{self.session_id}:{cell['x_idx']}:{cell['y_idx']}")
                if cell["is_revealed"]:
                    btn.disabled = True
                    if cell["is_mine"]:
                        btn.style = discord.ButtonStyle.danger
                        btn.label = "👹"
                    else:
                        btn.style = discord.ButtonStyle.success
                        btn.label = "✔️"
                self.add_item(btn)

        self.escape_btn = discord.ui.Button(style=discord.ButtonStyle.primary, label="Sacar & Fugir", row=4, custom_id=f"lab:esc:{self.session_id}")
        self.escape_btn.callback = self.process_escape
        self.add_item(self.escape_btn)

    async def process_step(self, interaction: discord.Interaction, button: LabyrinthButton):
        await interaction.response.defer()
        
        async with self.tx_manager.acquire() as conn:
            cursor = await conn.execute(
                "SELECT escrow_id, amount FROM escrows e JOIN active_games_state g ON e.user_id = (SELECT user_id FROM escrows WHERE escrow_id = e.escrow_id) WHERE g.session_id = ?",
                (self.session_id,)
            )
            state_data = await cursor.fetchone()
            if not state_data:
                await interaction.followup.send("O jogo já expirou ou foi processado.", ephemeral=True)
                return
                
            escrow_id = state_data["escrow_id"]
            aposta = state_data["amount"]

            cursor = await conn.execute("SELECT COUNT(*) as found FROM labyrinth_cells WHERE session_id = ? AND is_revealed = 1 AND is_mine = 0", (self.session_id,))
            calm_routes_found = (await cursor.fetchone())["found"]

            try:
                if button.is_mine:
                    await self.tx_manager.resolve_escrow(escrow_id, 0)
                    await conn.execute("DELETE FROM active_games_state WHERE session_id = ?", (self.session_id,))
                    await conn.commit()
                    
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
                    await conn.execute("UPDATE labyrinth_cells SET is_revealed = 1 WHERE session_id = ? AND x_idx = ? AND y_idx = ?", (self.session_id, button.x, button.y))
                    await conn.commit()
                    
                    button.style = discord.ButtonStyle.success
                    button.label = "✔️"
                    button.disabled = True
                    calm_routes_found += 1
                    
                    current_multiplier = 1.0 + (calm_routes_found * 0.15)
                    adjusted_multiplier = self.rng.calculate_house_edge(0.8, current_multiplier)
                    current_payout = int(math.floor((aposta * adjusted_multiplier) + 1e-9))
                    
                    embed = interaction.message.embeds[0]
                    embed.description = "Passos cuidadosos no labirinto. A escuridão ainda permanece adormecida."
                    embed.clear_fields()
                    embed.add_field(name="Vias Seguras", value=str(calm_routes_found), inline=True)
                    embed.add_field(name="Saque Acumulado", value=f"{current_payout} XP", inline=True)
                    
                    # Se desvendou todo o labirinto exceto as 4 minas
                    if calm_routes_found == 12:
                        await self.tx_manager.resolve_escrow(escrow_id, current_payout)
                        await conn.execute("DELETE FROM active_games_state WHERE session_id = ?", (self.session_id,))
                        await conn.commit()
                        
                        for child in self.children:
                            child.disabled = True
                            
                        embed.description = "**TRIUNFO.** O Labirinto foi mapeado. As feras choram no escuro enquanto você ascende."
                        embed.color = 0x00FF00
                        
                    await interaction.edit_original_response(embed=embed, view=self)
                    
            except Exception as e:
                await self.tx_manager.resolve_escrow(escrow_id, 0)
                await conn.execute("DELETE FROM active_games_state WHERE session_id = ?", (self.session_id,))
                await conn.commit()
                raise e

    async def process_escape(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        async with self.tx_manager.acquire() as conn:
            cursor = await conn.execute(
                "SELECT escrow_id, amount FROM escrows e JOIN active_games_state g ON e.user_id = (SELECT user_id FROM escrows WHERE escrow_id = e.escrow_id) WHERE g.session_id = ?",
                (self.session_id,)
            )
            state_data = await cursor.fetchone()
            if not state_data:
                await interaction.followup.send("O jogo já expirou ou foi processado.", ephemeral=True)
                return
                
            escrow_id = state_data["escrow_id"]
            aposta = state_data["amount"]

            cursor = await conn.execute("SELECT COUNT(*) as found FROM labyrinth_cells WHERE session_id = ? AND is_revealed = 1 AND is_mine = 0", (self.session_id,))
            calm_routes_found = (await cursor.fetchone())["found"]

            try:
                # Se tentou fugir sem pisar em nada
                if calm_routes_found == 0:
                    payout = aposta
                else:
                    current_multiplier = 1.0 + (calm_routes_found * 0.15)
                    adjusted_multiplier = self.rng.calculate_house_edge(0.8, current_multiplier)
                    payout = int(math.floor((aposta * adjusted_multiplier) + 1e-9))
                
                await self.tx_manager.resolve_escrow(escrow_id, payout)
                await conn.execute("DELETE FROM active_games_state WHERE session_id = ?", (self.session_id,))
                await conn.commit()
                
                for child in self.children:
                    child.disabled = True
                    
                embed = interaction.message.embeds[0]
                embed.description = "Sua intuição (ou covardia) gritou mais alto. Você corre de volta à entrada."
                embed.color = 0xFFFF00
                embed.clear_fields()
                embed.add_field(name="Vias Seguras Mapeadas", value=str(calm_routes_found), inline=True)
                embed.add_field(name="Vitalidade Preservada", value=f"{payout} XP", inline=True)
                
                await interaction.edit_original_response(embed=embed, view=self)
            except Exception as e:
                await self.tx_manager.resolve_escrow(escrow_id, 0)
                await conn.execute("DELETE FROM active_games_state WHERE session_id = ?", (self.session_id,))
                await conn.commit()
                raise e


class LabyrinthCog(commands.Cog):
    def __init__(self, bot, tx_manager: BaphometTransactionManager):
        self.bot = bot
        self.tx_manager = tx_manager
        self.rng = AbyssalRNG()

    async def play_labirinto(self, interaction: discord.Interaction, aposta: int):
        await interaction.response.defer()
        try:
            escrow_id = await self.tx_manager.create_escrow(interaction.user.id, interaction.guild_id, aposta)
        except SacrificeValidationError as e:
            await interaction.followup.send(f"Acesso Negado: {e}", ephemeral=True)
            return

        session_id = str(uuid.uuid4())
        
        view = BaphometsLabyrinthView(self.tx_manager, session_id, self.rng)
        await view.initialize_grid()
        
        embed = discord.Embed(
            title="O Labirinto de Baphomet",
            description="Dezesseis selos obscuros à sua frente. Pisos seguros multiplicam sua alma, bestas escondidas a aniquilam de imediato.",
            color=0x2b2d31
        )
        embed.add_field(name="Tributo Ancorado", value=f"{aposta} XP", inline=False)
        
        msg = await interaction.followup.send(embed=embed, view=view)
        
        async with self.tx_manager.acquire() as conn:
            import time
            expires = time.time() + 3600 # 1 Hora para jogar
            await conn.execute(
                "INSERT INTO active_games_state (session_id, game_type, channel_id, guild_id, message_id, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, "labirinto", interaction.channel_id, interaction.guild_id, msg.id, expires)
            )
            total_mines = 4
            vector = [True] * total_mines + [False] * (16 - total_mines)
            shuffled = []
            for _ in range(16):
                idx = self.rng.generate_int(0, len(vector) - 1)
                shuffled.append(vector.pop(idx))
            
            idx = 0
            for row in range(4):
                for col in range(4):
                    await conn.execute("INSERT INTO labyrinth_cells (session_id, x_idx, y_idx, is_mine) VALUES (?, ?, ?, ?)", (session_id, row, col, shuffled[idx]))
                    idx += 1
            await conn.commit()
async def setup(bot):
    if hasattr(bot, 'tx_manager'):
        cog = LabyrinthCog(bot, bot.tx_manager)
        await bot.add_cog(cog)
        
        # Reconecta views após restart
        import time
        async with bot.tx_manager.acquire() as conn:
            cursor = await conn.execute("SELECT session_id FROM active_games_state WHERE game_type = 'labirinto' AND expires_at > ?", (time.time(),))
            sessions = await cursor.fetchall()
            for row in sessions:
                view = BaphometsLabyrinthView(bot.tx_manager, row["session_id"], cog.rng)
                await view.initialize_grid()
                bot.add_view(view)
