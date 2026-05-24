import math
import asyncio
import itertools
import time
import uuid
import discord
from discord.ext import commands, tasks
from core_db_transaction import BaphometTransactionManager, SacrificeValidationError
from occult_ui_framework import AbyssalRNG

class DanceJoinModal(discord.ui.Modal, title="Sua Oferta de Sangue"):
    bet_input = discord.ui.TextInput(
        label="Quantia (XP)",
        placeholder="Ex: 500",
        required=True
    )
    
    def __init__(self, view_parent: "DanceJoinView"):
        super().__init__()
        self.view_parent = view_parent

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            aposta = int(self.bet_input.value)
            
            async with self.view_parent.tx_manager.acquire() as conn:
                cursor = await conn.execute("SELECT amount FROM black_flames_participants WHERE session_id = ? AND user_id = ?", (self.view_parent.session_id, interaction.user.id))
                if await cursor.fetchone():
                    await interaction.followup.send("Você já está preso na Roda de Fogo.", ephemeral=True)
                    return
                
                escrow_id = await self.view_parent.tx_manager.create_escrow(interaction.user.id, self.view_parent.guild_id, aposta)
                await conn.execute("INSERT INTO black_flames_participants (session_id, user_id, escrow_id, amount) VALUES (?, ?, ?, ?)", (self.view_parent.session_id, interaction.user.id, escrow_id, aposta))
                await conn.commit()
            
            await interaction.followup.send(
                f"🔥 {interaction.user.mention} pisou nas Chamas Negras ofertando **{aposta} XP**.",
                ephemeral=False
            )
        except ValueError:
            await interaction.followup.send("Submissão corrompida. Use números inteiros.", ephemeral=True)
        except SacrificeValidationError as e:
            await interaction.followup.send(f"Acesso Negado: {e}", ephemeral=True)

class DanceJoinView(discord.ui.View):
    def __init__(self, tx_manager: BaphometTransactionManager, guild_id: int, session_id: str):
        super().__init__(timeout=None)
        self.tx_manager = tx_manager
        self.guild_id = guild_id
        self.session_id = session_id
        
        btn = discord.ui.Button(label="Entrar na Dança", style=discord.ButtonStyle.danger, custom_id=f"dance:join:{self.session_id}")
        btn.callback = self.join_btn
        self.add_item(btn)

    async def join_btn(self, interaction: discord.Interaction):
        await interaction.response.send_modal(DanceJoinModal(self))


class BlackFlamesDanceCog(commands.Cog):
    def __init__(self, bot, tx_manager: BaphometTransactionManager):
        self.bot = bot
        self.tx_manager = tx_manager
        self.rng = AbyssalRNG()
        self.dance_resolver_task.start()

    def cog_unload(self):
        self.dance_resolver_task.cancel()

    async def play_danca_negras(self, interaction: discord.Interaction):
        async with self.tx_manager.acquire() as conn:
            cursor = await conn.execute("SELECT session_id FROM active_games_state WHERE game_type = 'danca' AND channel_id = ?", (interaction.channel_id,))
            if await cursor.fetchone():
                await interaction.response.send_message("As Chamas já ardem neste canal. Aguarde o fim do sacrifício atual.", ephemeral=True)
                return

            session_id = str(uuid.uuid4())
            expires = time.time() + 30.0
            
            await conn.execute(
                "INSERT INTO active_games_state (session_id, game_type, channel_id, guild_id, expires_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, "danca", interaction.channel_id, interaction.guild_id, expires)
            )
            await conn.commit()

        view = DanceJoinView(self.tx_manager, interaction.guild_id, session_id)
        self.bot.add_view(view)
        embed = discord.Embed(
            title="A Dança das Chamas Negras",
            description="Um portal macabro foi aberto. Vocês têm exatos **30 segundos** para submeterem suas almas à roda.\n\nAquele que injetar mais energia (XP), adquire maior peso no sorteio e devora as apostas inimigas.",
            color=0x8B0000
        )
        msg = await interaction.response.send_message(embed=embed, view=view)

    @tasks.loop(seconds=5)
    async def dance_resolver_task(self):
        now = time.time()
        async with self.tx_manager.acquire() as conn:
            cursor = await conn.execute("SELECT session_id, channel_id FROM active_games_state WHERE game_type = 'danca' AND expires_at <= ?", (now,))
            expired_dances = await cursor.fetchall()
            
            if not expired_dances:
                return

            for dance in expired_dances:
                session_id = dance["session_id"]
                channel_id = dance["channel_id"]
                
                cursor = await conn.execute("SELECT user_id, escrow_id, amount FROM black_flames_participants WHERE session_id = ?", (session_id,))
                participants = await cursor.fetchall()
                
                await conn.execute("DELETE FROM active_games_state WHERE session_id = ?", (session_id,))
                await conn.commit()

                channel = self.bot.get_channel(channel_id)
                if not channel:
                    for p in participants:
                        await self.tx_manager.resolve_escrow(p["escrow_id"], p["amount"])
                    continue

                if not participants:
                    await channel.send("As Chamas Negras esfriaram e morreram de fome. Nenhuma alma se apresentou.")
                    continue
                    
                if len(participants) == 1:
                    p = participants[0]
                    await self.tx_manager.resolve_escrow(p["escrow_id"], p["amount"])
                    await channel.send(f"Apenas um tolo avançou ao fogo. O ritual multiplayer exige sangue mútuo. <@{p['user_id']}>, seu XP foi devolvido intacto.")
                    continue

                total_pool = sum(p["amount"] for p in participants)
                weights = [p["amount"] for p in participants]
                cum_weights = list(itertools.accumulate(weights))
                
                roll = self.rng.generate_int(1, total_pool)
                winner_idx = 0
                for i, w in enumerate(cum_weights):
                    if roll <= w:
                        winner_idx = i
                        break
                        
                winner = participants[winner_idx]
                final_pool = int(math.floor((total_pool * self.rng.calculate_house_edge(1.0, 1.0, 0.05)) + 1e-9))
                
                matrix_details = []
                for p in participants:
                    chance_percent = (p["amount"] / total_pool) * 100
                    user = self.bot.get_user(p["user_id"])
                    name = user.name if user else f"User {p['user_id']}"
                    
                    if p["user_id"] == winner["user_id"]:
                        matrix_details.append(f"👑 **{name}** | Oferta: {p['amount']} XP | Peso: {chance_percent:.2f}% | **SOBREVIVEU**")
                        await self.tx_manager.resolve_escrow(p["escrow_id"], final_pool)
                    else:
                        matrix_details.append(f"💀 **{name}** | Oferta: {p['amount']} XP | Peso: {chance_percent:.2f}% | **ESMAGADO**")
                        await self.tx_manager.resolve_escrow(p["escrow_id"], 0)
                        
                res_embed = discord.Embed(
                    title="As Chamas Negras Esmagaram as Vítimas",
                    description="\n".join(matrix_details),
                    color=0x8B0000
                )
                res_embed.add_field(name="Oferta Global Coletada", value=f"{total_pool} XP", inline=True)
                res_embed.add_field(name="Prêmio do Sobrevivente (Pós-Dízimo)", value=f"{final_pool} XP", inline=True)
                
                await channel.send(f"**A DANÇA TERMINOU!** O sangue escorreu, e Baphomet coroa <@{winner['user_id']}>!", embed=res_embed)

    @dance_resolver_task.before_loop
    async def before_dance_resolver(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    if hasattr(bot, 'tx_manager'):
        cog = BlackFlamesDanceCog(bot, bot.tx_manager)
        await bot.add_cog(cog)

        import time
        async with bot.tx_manager.acquire() as conn:
            cursor = await conn.execute("SELECT session_id, guild_id FROM active_games_state WHERE game_type = 'danca' AND expires_at > ?", (time.time(),))
            sessions = await cursor.fetchall()
            for row in sessions:
                view = DanceJoinView(bot.tx_manager, row["guild_id"], row["session_id"])
                bot.add_view(view)
