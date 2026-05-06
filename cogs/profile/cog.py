from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from .field_registry import UnknownProfileFieldError
from .models import ProfileFieldStatus
from .services import (
    PresentationChannelService,
    ProfileFieldNotFoundError,
    ProfileRenderService,
    ProfileService,
    ProfileValidationError,
)
from .views import ProfileEditorView


LOGGER = logging.getLogger("baphomet.profile.cog")


async def profile_admin_check(interaction: discord.Interaction) -> bool:
    user = interaction.user
    if isinstance(user, discord.Member):
        permissions = user.guild_permissions
        if permissions.manage_guild or permissions.moderate_members:
            return True
    raise app_commands.MissingPermissions(["moderate_members"])


async def profile_settings_check(interaction: discord.Interaction) -> bool:
    user = interaction.user
    if isinstance(user, discord.Member) and user.guild_permissions.manage_guild:
        return True
    raise app_commands.MissingPermissions(["manage_guild"])


class ProfileCog(commands.GroupCog, group_name="ficha", group_description="Ficha de usuario por servidor."):
    admin = app_commands.Group(name="admin", description="Moderacao de fichas.")

    def __init__(
        self,
        bot: commands.Bot,
        service: ProfileService,
        renderer: ProfileRenderService,
        presentation: PresentationChannelService | None = None,
    ) -> None:
        super().__init__()
        self.bot = bot
        self.service = service
        self.renderer = renderer
        self.presentation = presentation or PresentationChannelService(service)
        self._warned_message_content_guilds: set[int] = set()
        self._context_menu = app_commands.ContextMenu(
            name="Usar como informações básicas",
            callback=self.usar_como_informacoes_basicas,
        )

    async def cog_load(self) -> None:
        try:
            self.bot.tree.add_command(self._context_menu)
        except app_commands.CommandAlreadyRegistered:
            LOGGER.warning("profile_context_menu_already_registered name=%s", self._context_menu.name)

    def cog_unload(self) -> None:
        self.bot.tree.remove_command(self._context_menu.name, type=discord.AppCommandType.message)

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

    @app_commands.command(name="set-apresentacao", description="Define o canal de apresentacao das fichas.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.check(profile_settings_check)
    @app_commands.describe(canal="Canal onde apresentacoes alimentam o campo info basica")
    async def set_apresentacao(self, interaction: discord.Interaction, canal: discord.TextChannel) -> None:
        await self.presentation.set_presentation_channel(interaction.guild_id, canal.id)
        warnings = self._presentation_setup_warnings(interaction.guild, canal)
        suffix = "\n" + "\n".join(warnings) if warnings else ""
        await interaction.response.send_message(
            f"Canal de apresentacao definido: {canal.mention}.{suffix}",
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

    async def usar_como_informacoes_basicas(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Use isso dentro de um servidor.", ephemeral=True)
            return
        if getattr(message.author, "bot", False):
            await interaction.response.send_message("Bots nao possuem ficha.", ephemeral=True)
            return
        if message.author.id != interaction.user.id and not self._can_manage_profiles(interaction):
            await interaction.response.send_message("Essa mensagem nao e sua.", ephemeral=True)
            return
        used = await self.presentation.use_message_as_basic_info(message, actor_id=interaction.user.id)
        if not used:
            await interaction.response.send_message("Nao encontrei texto util nessa mensagem.", ephemeral=True)
            return
        await interaction.response.send_message("Info basica atualizada.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self._warn_if_message_content_unavailable(message)
        await self.presentation.process_message(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        await self.presentation.process_message_edit(before, after)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        await self.presentation.process_message_delete(message)

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

    def _presentation_setup_warnings(self, guild: discord.Guild, channel: discord.TextChannel) -> list[str]:
        warnings: list[str] = []
        if not bool(getattr(getattr(self.bot, "intents", None), "message_content", False)):
            warnings.append("Ative Message Content Intent no portal do Discord e no bot para ler o texto das apresentacoes.")
            LOGGER.warning(
                "profile_presentation_message_content_intent_disabled guild_id=%s channel_id=%s",
                guild.id,
                channel.id,
            )
        me = guild.me or (guild.get_member(self.bot.user.id) if self.bot.user else None)
        if me is not None:
            permissions = channel.permissions_for(me)
            if not permissions.view_channel or not permissions.read_message_history:
                warnings.append("Garanta que eu consigo ver o canal e ler o historico de mensagens.")
        return warnings

    async def _warn_if_message_content_unavailable(self, message: discord.Message) -> None:
        if message.guild is None or message.guild.id in self._warned_message_content_guilds:
            return
        if bool(getattr(getattr(self.bot, "intents", None), "message_content", False)):
            return
        settings = await self.service.repository.get_settings(message.guild.id)
        if settings.presentation_channel_id != getattr(message.channel, "id", None):
            return
        self._warned_message_content_guilds.add(message.guild.id)
        LOGGER.warning(
            "profile_presentation_message_content_unavailable guild_id=%s channel_id=%s",
            message.guild.id,
            settings.presentation_channel_id,
        )

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
