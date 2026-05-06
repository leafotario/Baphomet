from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from .field_registry import UnknownProfileFieldError
from .models import ProfileFieldStatus
from .services import ProfileFieldNotFoundError, ProfileRenderService, ProfileService, ProfileValidationError
from .views import ProfileEditorView


LOGGER = logging.getLogger("baphomet.profile.cog")


async def profile_admin_check(interaction: discord.Interaction) -> bool:
    user = interaction.user
    if isinstance(user, discord.Member):
        permissions = user.guild_permissions
        if permissions.manage_guild or permissions.moderate_members:
            return True
    raise app_commands.MissingPermissions(["moderate_members"])


class ProfileCog(commands.GroupCog, group_name="ficha", group_description="Ficha de usuario por servidor."):
    admin = app_commands.Group(name="admin", description="Moderacao de fichas.")

    def __init__(self, bot: commands.Bot, service: ProfileService, renderer: ProfileRenderService) -> None:
        super().__init__()
        self.bot = bot
        self.service = service
        self.renderer = renderer

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Este comando so pode ser usado dentro de um servidor.",
                ephemeral=True,
            )
            return False
        return True

    @app_commands.command(name="criar", description="Cria ou prepara a sua ficha neste servidor.")
    @app_commands.guild_only()
    async def criar(self, interaction: discord.Interaction) -> None:
        await self.service.mark_onboarding_completed(interaction.guild_id, interaction.user.id, completed=True)
        await interaction.response.send_message(
            "Ficha preparada para este servidor. O nome, avatar, XP, level e cargo-insignia serao lidos ao vivo.",
            ephemeral=True,
        )

    @app_commands.command(name="ver", description="Mostra a ficha de um membro.")
    @app_commands.guild_only()
    @app_commands.describe(member="Membro cuja ficha voce quer ver")
    async def ver(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = await self._resolve_target_member(interaction, member)
        if target is None:
            await interaction.response.send_message("Nao consegui resolver esse membro no servidor.", ephemeral=True)
            return
        if target.bot:
            await interaction.response.send_message("Bots nao possuem ficha de usuario.", ephemeral=True)
            return

        await self.send_profile_preview(interaction, target=target, ephemeral=False)

    @app_commands.command(name="editar", description="Abre o editor da sua ficha.")
    @app_commands.guild_only()
    async def editar(self, interaction: discord.Interaction) -> None:
        await self.service.ensure_profile(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            embed=self.build_editor_embed(),
            view=ProfileEditorView(cog=self, owner_id=interaction.user.id, guild_id=interaction.guild_id),
            ephemeral=True,
        )

    @admin.command(name="remover-campo", description="Remove um campo da ficha por moderacao.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.check(profile_admin_check)
    @app_commands.describe(usuario="Usuario alvo", campo="Campo a remover", motivo="Motivo da remocao")
    async def admin_remover_campo(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        campo: str,
        motivo: str,
    ) -> None:
        if not await self._ensure_can_moderate_profiles(interaction):
            return
        await self.service.moderate_field(
            guild_id=interaction.guild_id,
            user_id=usuario.id,
            field_key=campo,
            status=ProfileFieldStatus.REMOVED_BY_MOD,
            actor_id=interaction.user.id,
            reason=motivo,
        )
        await interaction.response.send_message("Campo removido.", ephemeral=True)

    @admin.command(name="restaurar-campo", description="Restaura um campo removido por moderacao.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.check(profile_admin_check)
    @app_commands.describe(usuario="Usuario alvo", campo="Campo a restaurar")
    async def admin_restaurar_campo(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        campo: str,
    ) -> None:
        if not await self._ensure_can_moderate_profiles(interaction):
            return
        await self.service.restore_field(
            guild_id=interaction.guild_id,
            user_id=usuario.id,
            field_key=campo,
            actor_id=interaction.user.id,
            reason="restaurado por moderacao",
        )
        await interaction.response.send_message("Campo restaurado.", ephemeral=True)

    @admin.command(name="editar-campo", description="Edita um campo da ficha por moderacao.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.check(profile_admin_check)
    @app_commands.describe(usuario="Usuario alvo", campo="Campo a editar", valor="Novo valor")
    async def admin_editar_campo(
        self,
        interaction: discord.Interaction,
        usuario: discord.Member,
        campo: str,
        valor: str,
    ) -> None:
        if not await self._ensure_can_moderate_profiles(interaction):
            return
        await self.service.admin_set_field(
            guild_id=interaction.guild_id,
            user_id=usuario.id,
            field_key=campo,
            value=valor,
            actor_id=interaction.user.id,
            reason="editado por moderacao",
        )
        await interaction.response.send_message("Campo editado.", ephemeral=True)

    @admin_remover_campo.autocomplete("campo")
    @admin_restaurar_campo.autocomplete("campo")
    @admin_editar_campo.autocomplete("campo")
    async def admin_campo_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        choices = self.service.field_registry.choices_for_autocomplete(current or "")
        return [app_commands.Choice(name=label[:100], value=value) for label, value in choices]

    @app_commands.command(name="resetar", description="Reseta um campo ou toda a ficha.")
    @app_commands.guild_only()
    @app_commands.describe(
        campo="Campo especifico para resetar; deixe vazio para resetar tudo",
        member="Membro alvo; moderadores podem resetar a ficha de outras pessoas",
    )
    async def resetar(
        self,
        interaction: discord.Interaction,
        campo: str | None = None,
        member: discord.Member | None = None,
    ) -> None:
        target = await self._resolve_target_member(interaction, member)
        if target is None:
            await interaction.response.send_message("Nao consegui resolver esse membro no servidor.", ephemeral=True)
            return
        if target.id != interaction.user.id and not self._can_manage_profiles(interaction):
            await interaction.response.send_message(
                "Voce precisa de permissao de gerenciar servidor para resetar fichas de outras pessoas.",
                ephemeral=True,
            )
            return

        try:
            if campo:
                removed = await self.service.reset_field(
                    guild_id=interaction.guild_id,
                    user_id=target.id,
                    field_key=campo,
                    actor_id=interaction.user.id,
                    reason="reset via /ficha resetar",
                )
                message = "Campo resetado." if removed else "Esse campo ja estava vazio."
            else:
                removed_count = await self.service.reset_profile(
                    guild_id=interaction.guild_id,
                    user_id=target.id,
                    actor_id=interaction.user.id,
                    reason="reset total via /ficha resetar",
                )
                message = f"Ficha resetada. Campos removidos: {removed_count}."
        except UnknownProfileFieldError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.send_message(message, ephemeral=True)

    @resetar.autocomplete("campo")
    async def resetar_campo_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        choices = self.service.field_registry.choices_for_autocomplete(current or "")
        return [app_commands.Choice(name=label[:100], value=value) for label, value in choices]

    async def send_profile_preview(
        self,
        interaction: discord.Interaction,
        *,
        target: discord.Member | discord.User,
        ephemeral: bool,
    ) -> None:
        member = target if isinstance(target, discord.Member) else await self._resolve_target_member(interaction, None)
        if member is None or interaction.guild is None:
            if interaction.response.is_done():
                await interaction.followup.send("Nao consegui resolver esse membro.", ephemeral=True)
            else:
                await interaction.response.send_message("Nao consegui resolver esse membro.", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True, ephemeral=ephemeral)

        snapshot = await self.service.get_profile_snapshot(interaction.guild, member)
        result = await self.renderer.render_profile(snapshot)
        embed = self.renderer.build_preview_embed(snapshot)

        if result.image is not None:
            result.image.seek(0)
            await interaction.followup.send(
                file=discord.File(result.image, filename=result.filename),
                embed=embed,
                ephemeral=ephemeral,
            )
            return

        await interaction.followup.send(content=result.reason, embed=embed, ephemeral=ephemeral)

    def build_editor_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Editar ficha",
            description="Escolha uma parte para ajustar.",
            color=discord.Color.dark_purple(),
        )
        embed.add_field(name="Texto", value="Pronomes, headline e bio.", inline=False)
        embed.add_field(name="Conexoes", value="Info basica, assuntos, mood e interesses.", inline=False)
        embed.add_field(name="Visual", value="Tema, charm e paleta.", inline=False)
        return embed

    async def _resolve_target_member(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None,
    ) -> discord.Member | None:
        if member is not None:
            return member
        if isinstance(interaction.user, discord.Member):
            return interaction.user
        if interaction.guild is None:
            return None
        cached = interaction.guild.get_member(interaction.user.id)
        if cached is not None:
            return cached
        try:
            return await interaction.guild.fetch_member(interaction.user.id)
        except discord.HTTPException:
            return None

    def _can_manage_profiles(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        return isinstance(user, discord.Member) and (
            user.guild_permissions.manage_guild or user.guild_permissions.moderate_members
        )

    async def _ensure_can_moderate_profiles(self, interaction: discord.Interaction) -> bool:
        if self._can_manage_profiles(interaction):
            return True
        await interaction.response.send_message("Voce nao tem permissao para moderar fichas.", ephemeral=True)
        return False

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        original = getattr(error, "original", error)
        if isinstance(error, app_commands.MissingPermissions):
            message = "Voce nao tem permissao para isso."
        elif isinstance(original, UnknownProfileFieldError):
            message = str(original)
        elif isinstance(original, ProfileFieldNotFoundError):
            message = "Esse campo ainda nao tem valor salvo."
        elif isinstance(original, ProfileValidationError):
            message = str(original)
        else:
            LOGGER.error(
                "profile_command_error command=%s user_id=%s guild_id=%s",
                getattr(interaction.command, "qualified_name", None),
                interaction.user.id,
                interaction.guild_id,
                exc_info=(type(error), error, error.__traceback__),
            )
            message = "Nao consegui fazer isso agora."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
