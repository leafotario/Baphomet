from __future__ import annotations

"""Comandos Administrativos Do Sistema De XP."""

import discord
from discord import app_commands
from discord.ext import commands

from ..xp_constants import DIFFICULTY_CHOICES, GuildChannelParam
from ..xp_runtime import XpRuntime
from ..utils import XpDifficulty


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
            self.runtime.service.logger.warning(f"Falha ao enviar audit log para a guild {guild.id}: {exc}")

    @app_commands.command(name="difficulty", description="Define A Dificuldade Da Ascensão ⚙️")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(difficulty=DIFFICULTY_CHOICES)
    async def difficulty(self, interaction: discord.Interaction, difficulty: app_commands.Choice[str]) -> None:
        config = await self.runtime.service.update_guild_config(interaction.guild.id, difficulty=XpDifficulty(difficulty.value))
        await interaction.response.send_message(f"⚙️ Ritual Atualizado! A Dificuldade Agora Está Em **{config.difficulty.label}**.", ephemeral=True)
        
        embed = discord.Embed(title="⚙️ Dificuldade de XP Alterada", color=discord.Color.blue())
        embed.description = f"O administrador {interaction.user.mention} alterou a dificuldade para **{config.difficulty.label}**."
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="cooldown", description="Define O Cooldown De XP ⏳")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cooldown(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 3600]) -> None:
        config = await self.runtime.service.update_guild_config(interaction.guild.id, cooldown_seconds=seconds)
        await interaction.response.send_message(f"⏳ Ritmo Ajustado! O Cooldown Agora É De **{config.cooldown_seconds}S**.", ephemeral=True)
        
        embed = discord.Embed(title="⏳ Cooldown de XP Alterado", color=discord.Color.blue())
        embed.description = f"O administrador {interaction.user.mention} alterou o cooldown para **{config.cooldown_seconds} segundos**."
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="xp-range", description="Define A Faixa De XP Por Mensagem ✨")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
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
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ignore_channel(self, interaction: discord.Interaction, channel: GuildChannelParam, enabled: bool) -> None:
        await self.runtime.service.set_ignored_channel(interaction.guild.id, channel.id, enabled)
        status = "Ignorado Pelo Ritual" if enabled else "Liberado Para Ganhar XP"
        await interaction.response.send_message(f"📍 Canal **{channel.name}**: **{status}**.", ephemeral=True)

        embed = discord.Embed(title="🚪 Configuração de Canal Alterada", color=discord.Color.blue())
        embed.description = f"O administrador {interaction.user.mention} marcou o canal {channel.mention} como **{'Ignorado' if enabled else 'Liberado'}** no sistema de XP."
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="ignore-category", description="Ignora Ou Libera Uma Categoria 🗂️")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ignore_category(self, interaction: discord.Interaction, category: discord.CategoryChannel, enabled: bool) -> None:
        await self.runtime.service.set_ignored_category(interaction.guild.id, category.id, enabled)
        status = "Ignorada Pelo Ritual" if enabled else "Liberada Para Ganhar XP"
        await interaction.response.send_message(f"🗂️ Categoria **{category.name}**: **{status}**.", ephemeral=True)

        embed = discord.Embed(title="🗂️ Configuração de Categoria Alterada", color=discord.Color.blue())
        embed.description = f"O administrador {interaction.user.mention} marcou a categoria **{category.name}** como **{'Ignorada' if enabled else 'Liberada'}** no sistema de XP."
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="ignore-role", description="Ignora Ou Libera Um Cargo 🎭")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ignore_role(self, interaction: discord.Interaction, role: discord.Role, enabled: bool) -> None:
        await self.runtime.service.set_ignored_role(interaction.guild.id, role.id, enabled)
        status = "Ignorado Pelo Ritual" if enabled else "Liberado Para Ganhar XP"
        await interaction.response.send_message(f"🎭 Cargo {role.mention}: **{status}**.", ephemeral=True)

        embed = discord.Embed(title="🎭 Configuração de Cargo Alterada", color=discord.Color.blue())
        embed.description = f"O administrador {interaction.user.mention} marcou o cargo {role.mention} como **{'Ignorado' if enabled else 'Liberado'}** no sistema de XP."
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="add-xp", description="Adiciona XP A Um Membro ✨")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add_xp(self, interaction: discord.Interaction, member: discord.Member, amount: int, reason: str | None = None) -> None:
        if amount <= 0:
            await interaction.response.send_message("⚠️ O valor deve ser maior que zero.", ephemeral=True)
            return
            
        result = await self.runtime.service.give_xp(interaction.guild, member, amount, interaction.user.id, reason)
        await interaction.response.send_message(
            f"✨ Sucesso! Adicionado **{amount:,} XP** a **{member.display_name}**.".replace(",", "."),
            ephemeral=True
        )

        embed = discord.Embed(title="✨ XP Adicionado", color=discord.Color.green())
        embed.add_field(name="Alvo", value=member.mention, inline=True)
        embed.add_field(name="Administrador", value=interaction.user.mention, inline=True)
        embed.add_field(name="Quantidade", value=f"{amount:,}".replace(",", "."), inline=True)
        if reason:
            embed.add_field(name="Motivo", value=reason, inline=False)
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="remove-xp", description="Remove XP De Um Membro 📉")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove_xp(self, interaction: discord.Interaction, member: discord.Member, amount: int, reason: str | None = None) -> None:
        if amount <= 0:
            await interaction.response.send_message("⚠️ O valor deve ser maior que zero.", ephemeral=True)
            return

        result = await self.runtime.service.remove_xp(interaction.guild, member, amount, interaction.user.id, reason)
        await interaction.response.send_message(
            f"📉 Sucesso! Removido **{amount:,} XP** de **{member.display_name}**.".replace(",", "."),
            ephemeral=True
        )

        embed = discord.Embed(title="📉 XP Removido", color=discord.Color.orange())
        embed.add_field(name="Alvo", value=member.mention, inline=True)
        embed.add_field(name="Administrador", value=interaction.user.mention, inline=True)
        embed.add_field(name="Quantidade", value=f"{amount:,}".replace(",", "."), inline=True)
        if reason:
            embed.add_field(name="Motivo", value=reason, inline=False)
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="reset", description="Zera O XP De Um Membro ☠️")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reset(self, interaction: discord.Interaction, member: discord.Member, reason: str | None = None) -> None:
        result = await self.runtime.service.reset_xp(interaction.guild, member, interaction.user.id, reason)
        await interaction.response.send_message(
            f"☠️ O Ritual Foi Reiniciado! **{member.display_name}** Teve O XP Zerado E Perdeu **{abs(result.delta_xp):,} XP** No Processo.".replace(",", "."),
            ephemeral=True,
        )

        embed = discord.Embed(title="☠️ XP Zerado", color=discord.Color.red())
        embed.add_field(name="Alvo", value=member.mention, inline=True)
        embed.add_field(name="Administrador", value=interaction.user.mention, inline=True)
        embed.add_field(name="Perda Total", value=f"{abs(result.delta_xp):,} XP".replace(",", "."), inline=True)
        if reason:
            embed.add_field(name="Motivo", value=reason, inline=False)
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="config", description="Mostra A Configuração Atual ⚙️")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
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
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
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
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
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
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def level_role_add(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000], role: discord.Role) -> None:
        await self.runtime.service.set_level_role(interaction.guild.id, level, role.id)
        await interaction.response.send_message(f"👑 O Cargo {role.mention} Agora Será Concedido No **Nível {level}**.", ephemeral=True)

        embed = discord.Embed(title="👑 Cargo de Nível Adicionado", color=discord.Color.blue())
        embed.description = f"O administrador {interaction.user.mention} vinculou o cargo {role.mention} ao **Nível {level}**."
        await self._send_audit_log(interaction.guild, embed)

    @app_commands.command(name="level-role-remove", description="Desliga O Cargo Automático De Um Nível 🚫")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def level_role_remove(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000]) -> None:
        _config, removed = await self.runtime.service.remove_level_role(interaction.guild.id, level)
        if not removed:
            await interaction.response.send_message(f"⚠️ Nenhum Cargo Automático Estava Ligado Ao **Nível {level}**.", ephemeral=True)
            return
        await interaction.response.send_message(f"🚫 O Cargo Automático Do **Nível {level}** Foi Desfeito.", ephemeral=True)

        embed = discord.Embed(title="🚫 Cargo de Nível Removido", color=discord.Color.orange())
        embed.description = f"O administrador {interaction.user.mention} removeu o cargo automático do **Nível {level}**."
        await self._send_audit_log(interaction.guild, embed)
