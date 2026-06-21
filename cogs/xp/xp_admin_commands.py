from __future__ import annotations

"""Comandos Administrativos Do Sistema De XP."""

import discord
from discord import app_commands
from discord.ext import commands

from ..xp_constants import DIFFICULTY_CHOICES, GuildChannelParam
from ..xp_runtime import XpRuntime
from ..utils import XpDifficulty
from core.logger import log_exception



def _role_manage_error(guild: discord.Guild, role: discord.Role) -> str | None:
    bot_member = guild.me
    if bot_member is None:
        return "Não consegui identificar meu cargo neste servidor."
    if not bot_member.guild_permissions.manage_roles:
        return "Eu preciso da permissão **Gerenciar Cargos** para sincronizar cargos de nível."
    if role.is_default():
        return "O cargo @everyone não pode ser usado como cargo de nível."
    if role.managed:
        return "Esse cargo é gerenciado por uma integração e não pode ser atribuído manualmente."
    if role >= bot_member.top_role:
        return "Não posso gerenciar esse cargo porque ele está acima do meu cargo mais alto."
    return None


@app_commands.default_permissions(administrator=True)
class XpAdminCommands(commands.GroupCog, group_name="xp", group_description="Comandos De XP, Glória E Configuração ✨"):
    def __init__(self, bot: commands.Bot, runtime: XpRuntime) -> None:
        super().__init__()
        self.bot = bot
        self.runtime = runtime

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message("🕯️ Este Comando Só Pode Ser Usado Dentro De Um Servidor.", ephemeral=True)
            return False
        return True

    async def _send_audit_log(self, guild: discord.Guild, embed: discord.Embed) -> None:
        """Envia um log administrativo se o canal estiver configurado."""
        try:
            config = await self.runtime.service.get_guild_config(guild.id)
            if config.log_channel_id:
                channel = guild.get_channel(config.log_channel_id)
                if isinstance(channel, discord.TextChannel):
                    await channel.send(embed=embed)
        except Exception as exc:
            log_exception(exc)
            self.runtime.service.logger.warning(f"Falha ao enviar audit log para a guild {guild.id}: {exc}")

    @app_commands.command(name="difficulty", description="Define A Dificuldade Da Ascensão ⚙️")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(difficulty=DIFFICULTY_CHOICES)
    async def difficulty(self, interaction: discord.Interaction, difficulty: app_commands.Choice[str]) -> None:
        config = await self.runtime.service.update_guild_config(interaction.guild.id, difficulty=XpDifficulty(difficulty.value))
        await interaction.response.send_message(f"⚙️ Ritual Atualizado! A Dificuldade Agora Está Em **{config.difficulty.label}**.", ephemeral=True)

        embed = discord.Embed(title="⚙️ Dificuldade de XP Alterada", color=discord.Color.blue())
        embed.description = f"O administrador {interaction.user.mention} alterou a dificuldade para **{config.difficulty.label}**."
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="cooldown", description="Define O Cooldown De XP ⏳")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def cooldown(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 3600]) -> None:
        config = await self.runtime.service.update_guild_config(interaction.guild.id, cooldown_seconds=seconds)
        await interaction.response.send_message(f"⏳ Ritmo Ajustado! O Cooldown Agora É De **{config.cooldown_seconds}S**.", ephemeral=True)

        embed = discord.Embed(title="⏳ Cooldown de XP Alterado", color=discord.Color.blue())
        embed.description = f"O administrador {interaction.user.mention} alterou o cooldown para **{config.cooldown_seconds} segundos**."
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="xp-range", description="Define A Faixa De XP Por Mensagem ✨")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def xp_range(self, interaction: discord.Interaction, min_xp: app_commands.Range[int, 1, 1000], max_xp: app_commands.Range[int, 1, 1000]) -> None:
        if min_xp > max_xp:
            await interaction.response.send_message("⚠️ O Valor Mínimo Não Pode Ser Maior Que O Máximo.", ephemeral=True)
            return
        config = await self.runtime.service.update_guild_config(interaction.guild.id, min_xp_per_message=min_xp, max_xp_per_message=max_xp)
        await interaction.response.send_message(f"✨ Faixa Ritualística Ajustada Para **{config.min_xp_per_message}-{config.max_xp_per_message} XP**.", ephemeral=True)

        embed = discord.Embed(title="✨ Faixa de XP Alterada", color=discord.Color.blue())
        embed.description = f"O administrador {interaction.user.mention} alterou a faixa de XP ganho por mensagem para **{config.min_xp_per_message}-{config.max_xp_per_message} XP**."
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="ignore-channel", description="Ignora Ou Libera Um Canal 🚪")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def ignore_channel(self, interaction: discord.Interaction, channel: GuildChannelParam, enabled: bool) -> None:
        await self.runtime.service.set_ignored_channel(interaction.guild.id, channel.id, enabled)
        status = "Ignorado Pelo Ritual" if enabled else "Liberado Para Ganhar XP"
        await interaction.response.send_message(f"📍 Canal **{channel.name}**: **{status}**.", ephemeral=True)

        embed = discord.Embed(title="🚪 Configuração de Canal Alterada", color=discord.Color.blue())
        embed.description = f"O administrador {interaction.user.mention} marcou o canal {channel.mention} como **{'Ignorado' if enabled else 'Liberado'}** no sistema de XP."
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="ignore-category", description="Ignora Ou Libera Uma Categoria 🗂️")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def ignore_category(self, interaction: discord.Interaction, category: discord.CategoryChannel, enabled: bool) -> None:
        await self.runtime.service.set_ignored_category(interaction.guild.id, category.id, enabled)
        status = "Ignorada Pelo Ritual" if enabled else "Liberada Para Ganhar XP"
        await interaction.response.send_message(f"🗂️ Categoria **{category.name}**: **{status}**.", ephemeral=True)

        embed = discord.Embed(title="🗂️ Configuração de Categoria Alterada", color=discord.Color.blue())
        embed.description = f"O administrador {interaction.user.mention} marcou a categoria **{category.name}** como **{'Ignorada' if enabled else 'Liberada'}** no sistema de XP."
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="ignore-role", description="Ignora Ou Libera Um Cargo 🎭")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def ignore_role(self, interaction: discord.Interaction, role: discord.Role, enabled: bool) -> None:
        await self.runtime.service.set_ignored_role(interaction.guild.id, role.id, enabled)
        status = "Ignorado Pelo Ritual" if enabled else "Liberado Para Ganhar XP"
        await interaction.response.send_message(f"🎭 Cargo {role.mention}: **{status}**.", ephemeral=True)

        embed = discord.Embed(title="🎭 Configuração de Cargo Alterada", color=discord.Color.blue())
        embed.description = f"O administrador {interaction.user.mention} marcou o cargo {role.mention} como **{'Ignorado' if enabled else 'Liberado'}** no sistema de XP."
        await self._send_audit_log(interaction.guild, embed)



    @app_commands.command(name="config", description="Mostra A Configuração Atual ⚙️")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def config(self, interaction: discord.Interaction) -> None:
        config = await self.runtime.service.get_guild_config(interaction.guild.id)
        levelup = f"<#{config.levelup_channel_id}>" if config.levelup_channel_id else "Mesmo Canal Da Mensagem"
        logchannel = f"<#{config.log_channel_id}>" if config.log_channel_id else "Não Configurado"
        level_roles = "\n".join(f"Nível {level} → <@&{role_id}>" for level, role_id in sorted(config.level_roles.items())) or "Nenhum"
        embed = discord.Embed(title="⚙️ Ritual De XP", color=discord.Color.dark_purple())
        embed.description = (
            f"**Dificuldade:** {config.difficulty.label}\n"
            f"**Cooldown:** {config.cooldown_seconds}S\n"
            f"**Faixa De XP:** {config.min_xp_per_message}-{config.max_xp_per_message}\n"
            f"**Mín. De Caracteres:** {config.min_message_length}\n"
            f"**Mín. De Palavras Únicas:** {config.min_unique_words}\n"
            f"**Janela Anti-Repeat:** {config.anti_repeat_window_seconds}S\n"
            f"**Similaridade Anti-Repeat:** {config.anti_repeat_similarity:.2f}\n"
            f"**Canal De Level Up:** {levelup}\n"
            f"**Canal De Logs:** {logchannel}\n"
            f"**Canais Ignorados:** {len(config.ignored_channel_ids)}\n"
            f"**Categorias Ignoradas:** {len(config.ignored_category_ids)}\n"
            f"**Cargos Ignorados:** {len(config.ignored_role_ids)}\n"
            f"**Cargos Por Nível:**\n{level_roles}"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="levelup-channel", description="Define O Canal Dos Avisos De Level Up 📣")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def levelup_channel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        config = await self.runtime.service.update_guild_config(interaction.guild.id, levelup_channel_id=channel.id if channel else None)
        if config.levelup_channel_id:
            await interaction.response.send_message(f"📣 Os Avisos De Ascensão Agora Ecoam Em <#{config.levelup_channel_id}>.", ephemeral=True)
        else:
            await interaction.response.send_message("📣 Os Avisos De Ascensão Voltarão Para O Mesmo Canal Da Mensagem.", ephemeral=True)

        embed = discord.Embed(title="📣 Canal de Level-Up Alterado", color=discord.Color.blue())
        embed.description = f"O administrador {interaction.user.mention} definiu o canal de level-up para " + (f"<#{config.levelup_channel_id}>" if config.levelup_channel_id else "**Mesmo da Mensagem**") + "."
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="log-channel", description="Define O Canal De Logs Administrativos 📋")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        config = await self.runtime.service.update_guild_config(interaction.guild.id, log_channel_id=channel.id if channel else None)
        if config.log_channel_id:
            await interaction.response.send_message(f"📋 O Sistema De XP Agora Registrará Logs Em <#{config.log_channel_id}>.", ephemeral=True)
        else:
            await interaction.response.send_message("📋 O Sistema De Logs De XP Foi Desativado.", ephemeral=True)

        if config.log_channel_id:
            embed = discord.Embed(title="📋 Canal de Logs Alterado", color=discord.Color.blue())
            embed.description = f"O administrador {interaction.user.mention} definiu este canal para receber os logs de auditoria do sistema de XP."
            await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="level-role-add", description="Liga Um Cargo A Um Nível 👑")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def level_role_add(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000], role: discord.Role) -> None:
        manage_error = _role_manage_error(interaction.guild, role)
        if manage_error is not None:
            await interaction.response.send_message(manage_error, ephemeral=True)
            return
        previous_config = await self.runtime.service.get_guild_config(interaction.guild.id)
        previous_role_id = previous_config.level_roles.get(int(level))
        await self.runtime.service.set_level_role(interaction.guild.id, level, role.id)
        if previous_role_id is not None and previous_role_id != role.id:
            await self.runtime.service.cleanup_removed_level_roles(
                interaction.guild,
                {previous_role_id},
                reason=f"XP: cargo de nível {level} substituído por {interaction.user}",
            )
        stats = await self.runtime.service.sync_guild_level_roles(
            interaction.guild,
            reason=f"XP: cargo de nível {level} configurado por {interaction.user}",
        )
        await interaction.response.send_message(
            f"👑 O Cargo {role.mention} Agora Será Concedido No **Nível {level}**.\n"
            f"Sync: **{stats['members']}** membros | +**{stats['added']}** / -**{stats['removed']}**.",
            ephemeral=True,
        )

        embed = discord.Embed(title="👑 Cargo de Nível Adicionado", color=discord.Color.blue())
        embed.description = f"O administrador {interaction.user.mention} vinculou o cargo {role.mention} ao **Nível {level}**."
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="level-role-remove", description="Desliga O Cargo Automático De Um Nível 🚫")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def level_role_remove(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000]) -> None:
        previous_config = await self.runtime.service.get_guild_config(interaction.guild.id)
        previous_role_id = previous_config.level_roles.get(int(level))
        _config, removed = await self.runtime.service.remove_level_role(interaction.guild.id, level)
        if not removed:
            await interaction.response.send_message(f"⚠️ Nenhum Cargo Automático Estava Ligado Ao **Nível {level}**.", ephemeral=True)
            return
        cleanup_stats = await self.runtime.service.cleanup_removed_level_roles(
            interaction.guild,
            {previous_role_id} if previous_role_id is not None else set(),
            reason=f"XP: cargo de nível {level} removido por {interaction.user}",
        )
        stats = await self.runtime.service.sync_guild_level_roles(
            interaction.guild,
            reason=f"XP: cargo de nível {level} removido por {interaction.user}",
        )
        await interaction.response.send_message(
            f"🚫 O Cargo Automático Do **Nível {level}** Foi Desfeito.\n"
            f"Sync: **{stats['members']}** membros | +**{stats['added']}** / -**{stats['removed'] + cleanup_stats['removed']}**.",
            ephemeral=True,
        )

        embed = discord.Embed(title="🚫 Cargo de Nível Removido", color=discord.Color.orange())
        embed.description = f"O administrador {interaction.user.mention} removeu o cargo automático do **Nível {level}**."
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="purificar", description="Arranca a essência de um mortal e a oferece aos Lordes do Abismo 🩸")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def purificar(self, interaction: discord.Interaction, usuario: discord.Member) -> None:
        if usuario.bot:
            await interaction.response.send_message("💀 Máquinas não possuem almas para serem ceifadas.", ephemeral=True)
            return

        try:
            total_xp, slice_xp, blessed_members = await self.runtime.service.purify_member(
                interaction.guild, usuario, interaction.user.id
            )
        except ValueError as e:
            await interaction.response.send_message(f"🌑 **O ritual falhou:** {str(e)}", ephemeral=True)
            return

        embed = discord.Embed(
            title="🩸 RITUAL DE PURIFICAÇÃO CONCLUÍDO 🩸",
            description=f"A essência vital de {usuario.mention} foi brutalmente arrancada de seu receptáculo carnal.\n"
                        f"**{total_xp:,}** almas que antes lhe pertenciam agora vagam pelo abismo, divididas e oferecidas como um banquete macabro aos mais fortes do culto.\n\n"
                        f"O mortal agora rasteja na escuridão, desprovido de seu poder (Nível 0, 0 XP).",
            color=discord.Color.dark_red()
        )

        blessed_text = ""
        for m in blessed_members:
            blessed_text += f"🦇 {m.mention} devorou **{slice_xp:,} XP**\n"

        embed.add_field(name="📜 Os Abençoados pelo Banquete", value=blessed_text, inline=False)
        embed.set_footer(text="O abismo sempre cobra seu preço, e a fome dos lordes é eterna.")

        await interaction.response.send_message(embed=embed)
        await self._send_audit_log(interaction.guild, embed)


