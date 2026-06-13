from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import MyBot

LOGGER = logging.getLogger("baphomet.dungeon_crawler")


class DungeonCrawlerCog(commands.Cog):
    """Cog inaugural responsável pelo framework do aplicativo Dungeon Crawler."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot: MyBot = bot  # type: ignore

    @app_commands.command(name='explorar', description='Inicia uma exploração na Dungeon (cooldown diário).')
    async def explorar(self, interaction: discord.Interaction) -> None:
        """
        Interação base da exploração.
        Inclui middleware defensivo restritivo com alocação em memória (Redis)
        para arbitrar restrições temporais diárias severas e prevenir Thundering Herd.
        """
        redis = self.bot.redis_manager._pool
        
        # Estrutura atômica da chave de interpolação sintática
        redis_key = f'baphomet:cooldown:explore:{interaction.guild_id}:{interaction.user.id}'
        
        # Mecanismo de trava com implantação matemática assíncrona
        lock_acquired = await redis.set(redis_key, '1', ex=86400, nx=True)
        
        # Bifurcação condicional
        if not lock_acquired:
            limit_embed = discord.Embed(
                title="Rate Limit Atingido",
                description="Você já enviou seus batedores para explorar a dungeon hoje. Retorne amanhã.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=limit_embed, ephemeral=True)
            return

        # Evocação de deferência primordial pré-avaliada para evadir limitação de tempo
        await interaction.response.defer(ephemeral=False)

        # Salvaguarda para eventos destrutivos / cataclismos infraestruturais
        try:
            cenarios = ['Vitoria Excepcional', 'Sucesso Padrao', 'Encontro Neutro', 'A Maldicao']
            pesos = [5, 45, 25, 25]
            
            # Matriz analítica estocástica iterada puramente pela função random nativa
            resultado = random.choices(cenarios, weights=pesos, k=1)[0]
            
            base_xp = 100
            xp_service = getattr(self.bot, "xp_service", None)
            
            # Árvore sintática de controle de fluxo
            if resultado == 'Vitoria Excepcional':
                ganho_xp = int(base_xp * 3.5)
                if xp_service and isinstance(interaction.user, discord.Member) and interaction.guild:
                    await xp_service.give_xp(
                        guild=interaction.guild,
                        member=interaction.user,
                        amount=ganho_xp,
                        actor_user_id=self.bot.user.id if self.bot.user else None,
                        reason="Dungeon Crawler: Vitória Excepcional"
                    )
                embed = discord.Embed(
                    title="🌟 Vitória Excepcional",
                    description=f"Um resplendor ofuscante corta a escuridão! Seus batedores desbravaram câmaras ocultas, sobrepujaram horrores indescritíveis com destreza inigualável e retornam triunfantes.\n\nVocê acumulou massivos **{ganho_xp} XP**!",
                    color=discord.Color.gold()
                )
            
            elif resultado == 'Sucesso Padrao':
                ganho_xp = int(base_xp * 1.0)
                if xp_service and isinstance(interaction.user, discord.Member) and interaction.guild:
                    await xp_service.give_xp(
                        guild=interaction.guild,
                        member=interaction.user,
                        amount=ganho_xp,
                        actor_user_id=self.bot.user.id if self.bot.user else None,
                        reason="Dungeon Crawler: Sucesso Padrão"
                    )
                embed = discord.Embed(
                    title="⚔️ Sucesso Padrão",
                    description=f"Os batedores cruzaram a névoa espessa dos corredores primários, executando bestas sombrias comuns e saqueando restos esquecidos.\n\nVocê adquiriu **{ganho_xp} XP**.",
                    color=discord.Color.green()
                )
                
            elif resultado == 'Encontro Neutro':
                # Supressão consciente e silenciosa de repasses
                embed = discord.Embed(
                    title="🌫️ Encontro Neutro",
                    description="O som dos passos ecoou vazio por túneis estéreis. As paredes choravam pó, as salas jaziam desertas. Nenhum monstro foi avistado e nenhum tesouro foi encontrado.\n\nA expedição retorna exatamente como partiu.",
                    color=discord.Color.light_grey()
                )
                
            else:
                # 'A Maldicao' - Isolando o escopo lógico, deixando ganchos preparados
                embed = discord.Embed(
                    title="💀 A Maldição",
                    description="Um calafrio gélido agarra a alma de seus batedores enquanto a escuridão se materializa e sussurra segredos proibidos... Uma energia macabra fixou suas garras em sua essência.",
                    color=discord.Color.dark_purple()
                )
                if xp_service and isinstance(interaction.user, discord.Member) and interaction.guild:
                    await xp_service.repository.connection.execute(
                        "UPDATE xp_profiles SET curse_expires_at = CAST(strftime('%s', 'now') AS INTEGER) + 21600 WHERE user_id = ? AND guild_id = ?",
                        (interaction.user.id, interaction.guild.id)
                    )
                    await xp_service.repository.connection.commit()
            
            await interaction.edit_original_response(embed=embed)
            
        except Exception as e:
            # Tratamento da catástrofe com restituição de estágio cronológico
            LOGGER.error(f"Cataclismo infraestrutural na exploração do usuário {interaction.user.id}: {e}", exc_info=True)
            await redis.delete(redis_key)
            
            error_embed = discord.Embed(
                title="Erro Crítico",
                description="Ocorreu um cataclismo em nossos sistemas durante o processamento analítico estocástico. Seu cooldown foi restituído ativamente. Tente novamente em alguns momentos.",
                color=discord.Color.dark_red()
            )
            await interaction.edit_original_response(embed=error_embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DungeonCrawlerCog(bot))
