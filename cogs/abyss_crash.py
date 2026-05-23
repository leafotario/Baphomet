import asyncio
import uuid
import time
import discord
from discord.ext import commands, tasks
from core_db_transaction import BaphometTransactionManager, SacrificeValidationError
from occult_ui_framework import AbyssalRNG

class AbyssCrashView(discord.ui.View):
    def __init__(self, tx_manager: BaphometTransactionManager, session_id: str):
        super().__init__(timeout=None)
        self.tx_manager = tx_manager
        self.session_id = session_id
        
        btn = discord.ui.Button(label="Escapar (Cashout)", style=discord.ButtonStyle.success, custom_id=f"crash:esc:{self.session_id}")
        btn.callback = self.escape_btn
        self.add_item(btn)

    async def escape_btn(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        async with self.tx_manager.connection() as conn:
            cursor = await conn.execute(
                "SELECT s.escrow_id, s.user_id, s.current_multiplier, s.is_finalized, e.bet_amount "
                "FROM abyss_crash_state s "
                "JOIN escrows e ON s.escrow_id = e.escrow_id "
                "WHERE s.session_id = ?", (self.session_id,)
            )
            state = await cursor.fetchone()
            
            if not state or state["is_finalized"]:
                await interaction.followup.send("O abismo já devorou tudo ou a fuga já foi selada.", ephemeral=True)
                return
                
            if interaction.user.id != state["user_id"]:
                await interaction.followup.send("Sua alma não foi convidada para este pacto", ephemeral=True)
                return
                
            escrow_id = state["escrow_id"]
            aposta = state["bet_amount"]
            current_multiplier = state["current_multiplier"]
            
            # Finaliza o jogo atômico no SQL
            await conn.execute("UPDATE abyss_crash_state SET is_finalized = 1 WHERE session_id = ?", (self.session_id,))
            await conn.execute("DELETE FROM active_games_state WHERE session_id = ?", (self.session_id,))
            await conn.commit()
            
            final_payout = int(aposta * current_multiplier)
            await self.tx_manager.resolve_escrow(escrow_id, final_payout)
            
            embed = interaction.message.embeds[0]
            embed.description = f"Você saltou no vazio no momento exato: **[{current_multiplier:.2f}x]**!\nUma faísca de sanidade preservou sua vida."
            embed.color = 0x00FF00
            embed.clear_fields()
            embed.add_field(name="Vitalidade Resgatada", value=f"{final_payout} XP", inline=False)
            
            for child in self.children:
                child.disabled = True
                
            await interaction.edit_original_response(embed=embed, view=self)

class AbyssCrashCog(commands.Cog):
    def __init__(self, bot, tx_manager: BaphometTransactionManager):
        self.bot = bot
        self.tx_manager = tx_manager
        self.rng = AbyssalRNG()
        self._message_cache = {}
        self.crash_updater_task.start()

    def cog_unload(self):
        self.crash_updater_task.cancel()

    async def play_crash_abissal(self, interaction: discord.Interaction, aposta: int):
        await interaction.response.defer()
        try:
            escrow_id = await self.tx_manager.create_escrow(interaction.user.id, interaction.guild_id, aposta)
        except SacrificeValidationError as e:
            await interaction.followup.send(f"Recusa do Pacto: {e}", ephemeral=True)
            return

        session_id = str(uuid.uuid4())
        expires = time.time() + 600.0 # 10 mins máximo
        
        u = self.rng.generate_float()
        raw_crash = 1.0 / (1.0 - u)
        crash_point = self.rng.calculate_house_edge(1.0, raw_crash, 0.05)
        if crash_point < 1.0:
            crash_point = 1.0

        embed = discord.Embed(
            title="Colapso Abissal",
            description="O abismo começa a se abrir... **[1.00x]**\nAssista à força do motor puxando você.",
            color=0xFFFF00
        )
        embed.add_field(name="Tributo Ancorado", value=f"{aposta} XP", inline=True)
        
        view = AbyssCrashView(self.tx_manager, session_id)
        self.bot.add_view(view)
        
        msg = await interaction.followup.send(embed=embed, view=view)
        self._message_cache[session_id] = msg
        
        async with self.tx_manager.connection() as conn:
            await conn.execute(
                "INSERT INTO active_games_state (session_id, game_type, channel_id, guild_id, message_id, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, "crash", interaction.channel_id, interaction.guild_id, msg.id, expires)
            )
            await conn.execute(
                "INSERT INTO abyss_crash_state (session_id, escrow_id, user_id, crash_point, current_multiplier, is_finalized) VALUES (?, ?, ?, ?, ?, 0)",
                (session_id, escrow_id, interaction.user.id, crash_point, 1.0)
            )
            await conn.commit()

    @tasks.loop(seconds=1.5)
    async def crash_updater_task(self):
        async with self.tx_manager.connection() as conn:
            cursor = await conn.execute(
                "SELECT s.session_id, s.crash_point, s.current_multiplier, s.is_finalized, "
                "g.channel_id, g.message_id, e.bet_amount "
                "FROM abyss_crash_state s "
                "JOIN active_games_state g ON s.session_id = g.session_id "
                "JOIN escrows e ON s.escrow_id = e.escrow_id "
                "WHERE s.is_finalized = 0"
            )
            active_crashes = await cursor.fetchall()
            
            if not active_crashes:
                return

            for crash in active_crashes:
                session_id = crash["session_id"]
                crash_point = crash["crash_point"]
                multiplier = crash["current_multiplier"]
                channel_id = crash["channel_id"]
                message_id = crash["message_id"]
                
                growth = 0.1 * (multiplier ** 1.1)
                new_multiplier = multiplier + max(0.1, growth)
                
                has_crashed = new_multiplier >= crash_point
                if has_crashed:
                    new_multiplier = crash_point

                if has_crashed:
                    await conn.execute("UPDATE abyss_crash_state SET is_finalized = 1 WHERE session_id = ?", (session_id,))
                    await conn.execute("DELETE FROM active_games_state WHERE session_id = ?", (session_id,))
                else:
                    await conn.execute("UPDATE abyss_crash_state SET current_multiplier = ? WHERE session_id = ?", (new_multiplier, session_id))
                
                await conn.commit()

                # Fetch Message
                msg = self._message_cache.get(session_id)
                if not msg:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        try:
                            msg = await channel.fetch_message(message_id)
                            self._message_cache[session_id] = msg
                        except Exception:
                            pass

                if msg:
                    embed = msg.embeds[0]
                    if has_crashed:
                        try:
                            cursor = await conn.execute("SELECT escrow_id FROM abyss_crash_state WHERE session_id = ?", (session_id,))
                            state_row = await cursor.fetchone()
                            if state_row:
                                await self.tx_manager.resolve_escrow(state_row["escrow_id"], 0)
                        except Exception:
                            pass

                        embed.description = f"COLAPSO ESTRUTURAL! O abismo fechou suas mandíbulas em **[{crash_point:.2f}x]**.\nTodo o sacrifício foi obliterado antes que você pudesse gritar."
                        embed.color = 0x8B0000
                        embed.clear_fields()
                        embed.add_field(name="Retorno", value="0 XP (Devorado)", inline=False)
                        
                        view = discord.ui.View() # view vazia (remove botões)
                        try:
                            await msg.edit(embed=embed, view=view)
                        except Exception:
                            pass
                    else:
                        embed.description = f"O abismo começa a se abrir... **[{new_multiplier:.2f}x]**\nAssista à força do motor puxando você."
                        embed.color = 0xFFFF00
                        try:
                            await msg.edit(embed=embed)
                        except Exception:
                            pass

    @crash_updater_task.before_loop
    async def before_crash_updater(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    if hasattr(bot, 'tx_manager'):
        cog = AbyssCrashCog(bot, bot.tx_manager)
        await bot.add_cog(cog)
        
        import time
        async with bot.tx_manager.connection() as conn:
            cursor = await conn.execute("SELECT session_id FROM active_games_state WHERE game_type = 'crash' AND expires_at > ?", (time.time(),))
            sessions = await cursor.fetchall()
            for row in sessions:
                view = AbyssCrashView(bot.tx_manager, row["session_id"])
                bot.add_view(view)
