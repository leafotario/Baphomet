import math
import asyncio
import uuid
import time
import json
import logging
import discord
from discord.ext import commands, tasks
from core_db_transaction import BaphometTransactionManager, SacrificeValidationError
from occult_ui_framework import AbyssalRNG
from core_redis_state import AbyssalRedisManager

try:
    from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
except ImportError:
    RedisConnectionError = OSError
    RedisTimeoutError = OSError

logger = logging.getLogger("Baphomet.AbyssCrash")

class AbyssCrashView(discord.ui.View):
    def __init__(self, tx_manager: BaphometTransactionManager, redis_manager: AbyssalRedisManager, redis_key: str):
        super().__init__(timeout=None)
        self.tx_manager = tx_manager
        self.redis_manager = redis_manager
        self.redis_key = redis_key
        
        btn = discord.ui.Button(label="Escapar (Cashout)", style=discord.ButtonStyle.success, custom_id=f"crash:esc:{self.redis_key}")
        btn.callback = self.escape_btn
        self.add_item(btn)

    async def escape_btn(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        if not self.redis_manager._pool:
            await interaction.followup.send("Servidor L1 Inacessível.", ephemeral=True)
            return

        raw_state = await self.redis_manager._pool.get(self.redis_key)
        
        if not raw_state:
            await interaction.followup.send("O abismo já devorou tudo ou a fuga já foi selada.", ephemeral=True)
            return
            
        state = json.loads(raw_state)
            
        if interaction.user.id != state["user_id"]:
            await interaction.followup.send("Sua alma não foi convidada para este pacto", ephemeral=True)
            return
            
        # Condição de Corrida (TOCTOU) Implacável. 
        # A remoção (delete) é atômica no Redis. Se retornar 1, o escape foi validado antes do loop crashar a chave.
        deleted = await self.redis_manager._pool.delete(self.redis_key)
        if not deleted:
            await interaction.followup.send("Tarde demais! A estrutura já entrou em colapso.", ephemeral=True)
            return
            
        # Remoção da listagem paralela
        await self.redis_manager._pool.srem("baphomet:active_crashes", self.redis_key)
            
        escrow_id = state["escrow_id"]
        aposta = state["bet_amount"]
        current_multiplier = state["current_multiplier"]
        
        # Pagamento (Único contato com SQLite)
        final_payout = int(math.floor((aposta * current_multiplier) + 1e-9))
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
    def __init__(self, bot, tx_manager: BaphometTransactionManager, redis_manager: AbyssalRedisManager):
        self.bot = bot
        self.tx_manager = tx_manager
        self.redis_manager = redis_manager
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
        
        # Criação da Chave Multidimensional
        redis_key = self.redis_manager._build_key("crash", interaction.guild_id, interaction.channel_id, interaction.user.id)
        
        view = AbyssCrashView(self.tx_manager, self.redis_manager, redis_key)
        self.bot.add_view(view)
        
        msg = await interaction.followup.send(embed=embed, view=view)
        self._message_cache[redis_key] = msg
        
        state = {
            "escrow_id": escrow_id,
            "user_id": interaction.user.id,
            "crash_point": crash_point,
            "current_multiplier": 1.0,
            "channel_id": interaction.channel_id,
            "message_id": msg.id,
            "bet_amount": aposta
        }
        
        # Upload de Estado em TTL
        await self.redis_manager.set_game_state("crash", interaction.guild_id, interaction.channel_id, interaction.user.id, state, ttl=600)
        await self.redis_manager._pool.sadd("baphomet:active_crashes", redis_key)

    @tasks.loop(seconds=1.5)
    async def crash_updater_task(self):
        if not self.redis_manager._pool:
            return

        # Pilar 2: Circuit Breaker de Tolerância a Falhas.
        # Se o Redis piscar, reiniciar ou a latência de rede estourar,
        # a task NÃO pode morrer. Ela recua e tenta no próximo ciclo de 1.5s.
        try:
            active_keys = await self.redis_manager._pool.smembers("baphomet:active_crashes")
        except (RedisConnectionError, RedisTimeoutError, OSError) as e:
            logger.warning(f"[Crash Loop] Redis inacessível durante SMEMBERS — recuo gracioso: {e}")
            return
        except Exception as e:
            logger.error(f"[Crash Loop] Exceção inesperada durante SMEMBERS: {e}")
            return

        if not active_keys:
            return

        for redis_key in active_keys:
            try:
                raw_state = await self.redis_manager._pool.get(redis_key)
            except (RedisConnectionError, RedisTimeoutError, OSError) as e:
                logger.warning(f"[Crash Loop] Redis inacessível durante GET '{redis_key}' — recuo gracioso: {e}")
                return
            
            if not raw_state:
                # O usuário escapou (cashout atômico apagou a chave) ou o TTL estourou.
                try:
                    await self.redis_manager._pool.srem("baphomet:active_crashes", redis_key)
                except (RedisConnectionError, RedisTimeoutError, OSError):
                    pass
                continue

            state = json.loads(raw_state)
            
            crash_point = state["crash_point"]
            multiplier = state["current_multiplier"]
            channel_id = state["channel_id"]
            message_id = state["message_id"]
            escrow_id = state["escrow_id"]
            
            growth = 0.1 * (multiplier ** 1.1)
            new_multiplier = multiplier + max(0.1, growth)
            
            has_crashed = new_multiplier >= crash_point
            if has_crashed:
                new_multiplier = crash_point

            if has_crashed:
                # Obliteração atômica: loop deleta a chave primeiro para bloquear o view.escape_btn()
                try:
                    deleted = await self.redis_manager._pool.delete(redis_key)
                    await self.redis_manager._pool.srem("baphomet:active_crashes", redis_key)
                except (RedisConnectionError, RedisTimeoutError, OSError) as e:
                    logger.warning(f"[Crash Loop] Redis inacessível durante DELETE/SREM '{redis_key}': {e}")
                    continue
                
                # Se o delete foi feito por nós e não pela fuga
                if deleted:
                    await self.tx_manager.resolve_escrow(escrow_id, 0)
            else:
                try:
                    state["current_multiplier"] = new_multiplier
                    await self.redis_manager._pool.setex(name=redis_key, time=600, value=json.dumps(state))
                except (RedisConnectionError, RedisTimeoutError, OSError) as e:
                    logger.warning(f"[Crash Loop] Redis inacessível durante SETEX '{redis_key}': {e}")
                    continue

            # Fetch Message
            msg = self._message_cache.get(redis_key)
            if not msg:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    try:
                        msg = await channel.fetch_message(message_id)
                        self._message_cache[redis_key] = msg
                    except Exception:
                        pass

            if msg:
                embed = msg.embeds[0]
                if has_crashed:
                    embed.description = f"COLAPSO ESTRUTURAL! O abismo fechou suas mandíbulas em **[{crash_point:.2f}x]**.\nTodo o sacrifício foi obliterado antes que você pudesse gritar."
                    embed.color = 0x8B0000
                    embed.clear_fields()
                    embed.add_field(name="Retorno", value="0 XP (Devorado)", inline=False)
                    
                    view = discord.ui.View() # view vazia (remove botões)
                    
                    # Fallback for RateLimits and Network issues
                    attempts = 0
                    while attempts < 3:
                        try:
                            await msg.edit(embed=embed, view=view)
                            break
                        except discord.HTTPException as e:
                            if e.status == 429: # Rate Limited
                                retry_after = e.retry_after if hasattr(e, 'retry_after') else 2.0
                                await asyncio.sleep(retry_after)
                                attempts += 1
                            else:
                                break
                        except Exception:
                            break
                else:
                    embed.description = f"O abismo começa a se abrir... **[{new_multiplier:.2f}x]**\nAssista à força do motor puxando você."
                    embed.color = 0xFFFF00
                    
                    attempts = 0
                    while attempts < 3:
                        try:
                            await msg.edit(embed=embed)
                            break
                        except discord.HTTPException as e:
                            if e.status == 429:
                                retry_after = e.retry_after if hasattr(e, 'retry_after') else 2.0
                                await asyncio.sleep(retry_after)
                                attempts += 1
                            else:
                                break
                        except Exception:
                            break

    @crash_updater_task.before_loop
    async def before_crash_updater(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    if hasattr(bot, 'tx_manager') and hasattr(bot, 'redis_manager'):
        redis_manager = bot.redis_manager
        redis_alive = redis_manager._is_connected
        
        if not redis_alive:
            logger.error(
                "[Boot] O daemon Redis NÃO respondeu ao Healthcheck de Pré-Ignição. "
                "O módulo AbyssCrash será carregado em MODO DEGRADADO (sem task de atualização). "
                "Verifique a variável de ambiente REDIS_URL e a topologia de rede do contêiner."
            )
        
        cog = AbyssCrashCog(bot, bot.tx_manager, redis_manager)
        
        # Se o Redis não estiver vivo, impedir que o loop rode no vazio
        if not redis_alive:
            cog.crash_updater_task.cancel()
        
        await bot.add_cog(cog)
        
        if redis_alive and redis_manager._pool:
            try:
                active_keys = await redis_manager._pool.smembers("baphomet:active_crashes")
                for redis_key in active_keys:
                    view = AbyssCrashView(bot.tx_manager, redis_manager, redis_key)
                    bot.add_view(view)
            except (RedisConnectionError, RedisTimeoutError, OSError) as e:
                logger.warning(f"[Boot] Falha ao restaurar Views persistentes do Redis: {e}")
            except Exception as e:
                logger.warning(f"[Boot] Exceção inesperada ao restaurar Views: {e}")
