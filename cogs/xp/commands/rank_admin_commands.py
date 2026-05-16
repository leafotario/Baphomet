from __future__ import annotations

import pathlib

import discord
from discord import app_commands
from discord.ext import commands

from ..rank_badges import RankBadgeImageError
from ..xp_runtime import XpRuntime


def _role_manage_error(guild: discord.Guild, role: discord.Role) -> str | None:
    bot_member = guild.me
    if bot_member is None:
        return "Nao consegui identificar meu cargo neste servidor."
    if not bot_member.guild_permissions.manage_roles:
        return "Eu preciso da permissao **Gerenciar Cargos** para sincronizar cargos de nivel."
    if role.is_default():
        return "O cargo @everyone nao pode ser usado como cargo de nivel."
    if role.managed:
        return "Esse cargo e gerenciado por uma integracao e nao pode ser atribuido manualmente."
    if role >= bot_member.top_role:
        return "Nao posso gerenciar esse cargo porque ele esta acima do meu cargo mais alto."
    return None


@app_commands.default_permissions(administrator=True)
class RankAdminCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, runtime: XpRuntime) -> None:
        self.bot = bot
        self.runtime = runtime

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        original = getattr(error, "original", error)
        if isinstance(original, (app_commands.MissingPermissions, app_commands.CheckFailure)):
            await self._send_text(interaction, "Voce precisa ser administrador(a) para usar esse comando.")
            return
        self.runtime.service.logger.error(
            "erro em comando administrativo de rank: %s",
            original,
            exc_info=(type(original), original, original.__traceback__) if isinstance(original, BaseException) else None,
        )
        await self._send_text(interaction, "Nao consegui concluir esse comando de rank agora.")

    async def _send_text(self, interaction: discord.Interaction, content: str, *, ephemeral: bool = True) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)

    async def _send_audit_log(self, guild: discord.Guild, embed: discord.Embed) -> None:
        try:
            config = await self.runtime.service.get_guild_config(guild.id)
            if config.log_channel_id:
                channel = guild.get_channel(config.log_channel_id)
                if isinstance(channel, discord.TextChannel):
                    await channel.send(embed=embed)
        except Exception as exc:
            self.runtime.service.logger.warning("falha ao enviar audit log de rank guild_id=%s error=%s", guild.id, exc)

    @app_commands.command(name="rank_insignia_set", description="Define uma insignia visual de rank para um cargo.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        cargo="Cargo que exibira esta insignia no /rank.",
        imagem="Arquivo de imagem enviado no Discord.",
        url="URL direta de uma imagem.",
        prioridade="Maior prioridade vence quando o membro tem varias insignias.",
        nome="Nome opcional da insignia.",
    )
    async def rank_insignia_set(
        self,
        interaction: discord.Interaction,
        cargo: discord.Role,
        imagem: discord.Attachment | None = None,
        url: str | None = None,
        prioridade: app_commands.Range[int, -100000, 100000] = 0,
        nome: str | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Este comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return
        if (imagem is None and not url) or (imagem is not None and url):
            await interaction.response.send_message("Envie exatamente uma imagem: anexo **ou** URL.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if imagem is not None:
                badge = await self.runtime.badges.set_badge_from_attachment(
                    guild_id=interaction.guild.id,
                    role_id=cargo.id,
                    attachment=imagem,
                    priority=int(prioridade),
                    label=nome,
                )
            else:
                badge = await self.runtime.badges.set_badge_from_url(
                    guild_id=interaction.guild.id,
                    role_id=cargo.id,
                    url=str(url),
                    priority=int(prioridade),
                    label=nome,
                )
        except RankBadgeImageError as exc:
            await interaction.followup.send(f"Nao consegui configurar essa insignia: {exc}", ephemeral=True)
            return

        embed = discord.Embed(title="Insignia de rank configurada", color=discord.Color.dark_purple())
        embed.description = f"Insignia configurada com sucesso para {cargo.mention}."
        embed.add_field(name="Prioridade", value=str(badge.priority), inline=True)
        embed.add_field(name="Nome", value=badge.label or "Sem nome", inline=True)
        embed.add_field(name="Arquivo", value=badge.image_path, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

        self.runtime.service.logger.info(
            "rank_badge_configured guild_id=%s role_id=%s priority=%s path=%s",
            interaction.guild.id,
            cargo.id,
            badge.priority,
            badge.image_path,
        )

    @app_commands.command(name="rank_insignia_remove", description="Remove a insignia visual de rank de um cargo.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def rank_insignia_remove(self, interaction: discord.Interaction, cargo: discord.Role) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Este comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return
        badge = await self.runtime.badges.remove_badge(interaction.guild.id, cargo.id)
        if badge is None:
            await interaction.response.send_message("Esse cargo ainda nao possui insignia configurada.", ephemeral=True)
            return
        await interaction.response.send_message(f"Insignia removida de {cargo.mention}.", ephemeral=True)
        self.runtime.service.logger.info("rank_badge_removed guild_id=%s role_id=%s", interaction.guild.id, cargo.id)

    @app_commands.command(name="rank_insignia_list", description="Lista as insignias de rank configuradas neste servidor.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def rank_insignia_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Este comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return
        badges = await self.runtime.badges.list_badges(interaction.guild.id)
        embed = discord.Embed(title="Insignias de rank", color=discord.Color.dark_purple())
        if not badges:
            embed.description = "Nenhuma insignia configurada neste servidor."
        else:
            lines = []
            for badge in badges:
                role = interaction.guild.get_role(badge.role_id)
                role_text = role.mention if role else f"Cargo deletado `{badge.role_id}`"
                has_image = "sim" if pathlib.Path(badge.image_path).exists() else "arquivo ausente"
                label = badge.label or "sem nome"
                lines.append(f"{role_text} | prioridade **{badge.priority}** | imagem: **{has_image}** | {label}")
            embed.description = "\n".join(lines[:25])
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="rank_insignia_preview", description="Mostra a insignia de rank configurada para um cargo.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def rank_insignia_preview(self, interaction: discord.Interaction, cargo: discord.Role) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Este comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return
        badge = await self.runtime.badges.get_badge(interaction.guild.id, cargo.id)
        if badge is None:
            await interaction.response.send_message("Esse cargo ainda nao possui insignia configurada.", ephemeral=True)
            return
        path = pathlib.Path(badge.image_path)
        if not path.exists():
            await interaction.response.send_message("A insignia esta configurada, mas o arquivo local nao foi encontrado.", ephemeral=True)
            return
        embed = discord.Embed(title="Preview da insignia de rank", color=discord.Color.dark_purple())
        embed.description = f"Cargo: {cargo.mention}\nPrioridade: **{badge.priority}**\nNome: **{badge.label or 'Sem nome'}**"
        file = discord.File(str(path), filename="rank_insignia.png")
        embed.set_image(url="attachment://rank_insignia.png")
        await interaction.response.send_message(embed=embed, file=file, ephemeral=True)

    @app_commands.command(name="rank_levelrole_set", description="Define o cargo automatico de um nivel de rank.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def rank_levelrole_set(
        self,
        interaction: discord.Interaction,
        nivel: app_commands.Range[int, 1, 1000],
        cargo: discord.Role,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Este comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return
        manage_error = _role_manage_error(interaction.guild, cargo)
        if manage_error is not None:
            await interaction.response.send_message(manage_error, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        previous_config = await self.runtime.service.get_guild_config(interaction.guild.id)
        previous_role_id = previous_config.level_roles.get(int(nivel))
        await self.runtime.service.set_level_role(interaction.guild.id, int(nivel), cargo.id)
        cleanup_stats = {"removed": 0}
        if previous_role_id is not None and previous_role_id != cargo.id:
            cleanup_stats = await self.runtime.service.cleanup_removed_level_roles(
                interaction.guild,
                {previous_role_id},
                reason=f"XP: cargo de nivel {int(nivel)} substituido por {interaction.user}",
            )
        stats = await self.runtime.service.sync_guild_level_roles(
            interaction.guild,
            reason=f"XP: cargo de nivel {int(nivel)} configurado por {interaction.user}",
        )
        embed = discord.Embed(title="Cargo de nivel configurado", color=discord.Color.blue())
        embed.description = f"{cargo.mention} agora esta ligado ao **nivel {int(nivel)}**."
        embed.add_field(name="Sync", value=f"{stats['members']} membros | +{stats['added']} / -{stats['removed'] + cleanup_stats['removed']}", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="rank_levelrole_remove", description="Remove um cargo automatico de nivel.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def rank_levelrole_remove(
        self,
        interaction: discord.Interaction,
        nivel: app_commands.Range[int, 1, 1000] | None = None,
        cargo: discord.Role | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Este comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return
        if nivel is None and cargo is None:
            await interaction.response.send_message("Informe um nivel ou um cargo para remover.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        config = await self.runtime.service.get_guild_config(interaction.guild.id)
        levels_to_remove: list[int] = []
        role_ids_to_cleanup: set[int] = set()
        if nivel is not None:
            levels_to_remove.append(int(nivel))
            role_id = config.level_roles.get(int(nivel))
            if role_id is not None:
                role_ids_to_cleanup.add(role_id)
        if cargo is not None:
            for level, role_id in config.level_roles.items():
                if role_id == cargo.id:
                    levels_to_remove.append(level)
                    role_ids_to_cleanup.add(role_id)
        removed_levels: list[int] = []
        for level in sorted(set(levels_to_remove)):
            _config, removed = await self.runtime.service.remove_level_role(interaction.guild.id, level)
            if removed:
                removed_levels.append(level)

        if not removed_levels:
            await interaction.followup.send("Nenhuma configuracao de cargo de nivel foi encontrada para remover.", ephemeral=True)
            return

        cleanup_stats = await self.runtime.service.cleanup_removed_level_roles(
            interaction.guild,
            role_ids_to_cleanup,
            reason=f"XP: cargo de nivel removido por {interaction.user}",
        )
        stats = await self.runtime.service.sync_guild_level_roles(
            interaction.guild,
            reason=f"XP: cargo de nivel removido por {interaction.user}",
        )
        embed = discord.Embed(title="Cargo de nivel removido", color=discord.Color.orange())
        embed.description = "Niveis removidos: " + ", ".join(str(level) for level in removed_levels)
        embed.add_field(name="Sync", value=f"{stats['members']} membros | +{stats['added']} / -{stats['removed'] + cleanup_stats['removed']}", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="rank_levelrole_list", description="Lista os cargos automaticos de nivel deste servidor.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def rank_levelrole_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Este comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return
        config = await self.runtime.service.get_guild_config(interaction.guild.id)
        embed = discord.Embed(title="Cargos de nivel", color=discord.Color.dark_purple())
        if not config.level_roles:
            embed.description = "Nenhum cargo de nivel configurado neste servidor."
        else:
            lines = []
            for level, role_id in sorted(config.level_roles.items()):
                role = interaction.guild.get_role(role_id)
                role_text = role.mention if role else f"Cargo deletado `{role_id}`"
                lines.append(f"Nivel **{level}** -> {role_text}")
            embed.description = "\n".join(lines[:25])
            embed.set_footer(text="Politica atual: cargos acumulativos por nivel atingido.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
