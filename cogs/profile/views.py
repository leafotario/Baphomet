from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from .modals import ProfileConnectionsModal, ProfileTextModal
from .models import ProfileFieldSourceType
from .services import ProfileValidationError
from .services.profile_service import VISUAL_FIELD_KEYS

if TYPE_CHECKING:
    from .cog import ProfileCog


LOGGER = logging.getLogger("baphomet.profile.views")

THEME_LABELS = {
    "classic": "Classico",
    "minimal": "Minimal",
    "neon": "Neon",
    "celestial": "Celestial",
    "monochrome": "Monocromatico",
}

CHARM_LABELS = {
    "none": "Nenhum",
    "stars": "Estrelas",
    "vinyl": "Vinil",
    "laurels": "Loureiro",
    "runes": "Runas",
    "glitch": "Glitch",
}

PALETTES = {
    "aurora": ("Aurora", ("#00D1B2", "#7A5CFF", "#FF6B9A")),
    "ember": ("Brasa", ("#FF4D4D", "#FFB000", "#2E2E2E")),
    "garden": ("Jardim", ("#2F9E44", "#A9E34B", "#FFFFFF")),
    "ocean": ("Oceano", ("#0077B6", "#48CAE4", "#F8F9FA")),
    "grape": ("Uva", ("#9D4EDD", "#F15BB5", "#F8F7FF")),
}


class ProfileEditorView(discord.ui.View):
    def __init__(self, *, cog: ProfileCog, owner_id: int, guild_id: int) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message("Use isso dentro do servidor certo.", ephemeral=True)
            return False
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Esse editor nao e seu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Texto", style=discord.ButtonStyle.primary, custom_id="profile:editor:text", row=0)
    async def text_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        snapshot = await self.cog.service.get_profile_snapshot(interaction.guild, interaction.user)
        await interaction.response.send_modal(
            ProfileTextModal(
                cog=self.cog,
                owner_id=self.owner_id,
                guild_id=self.guild_id,
                snapshot=snapshot,
            )
        )

    @discord.ui.button(label="Conexoes", style=discord.ButtonStyle.secondary, custom_id="profile:editor:connections", row=0)
    async def connections_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        snapshot = await self.cog.service.get_profile_snapshot(interaction.guild, interaction.user)
        await interaction.response.send_modal(
            ProfileConnectionsModal(
                cog=self.cog,
                owner_id=self.owner_id,
                guild_id=self.guild_id,
                snapshot=snapshot,
            )
        )

    @discord.ui.button(label="Visual", style=discord.ButtonStyle.secondary, custom_id="profile:editor:visual", row=0)
    async def visual_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = ProfileVisualView(cog=self.cog, owner_id=self.owner_id, guild_id=self.guild_id)
        await interaction.response.send_message(embed=await view.build_embed(interaction), view=view, ephemeral=True)

    @discord.ui.button(label="Previa", style=discord.ButtonStyle.secondary, custom_id="profile:editor:preview", row=1)
    async def preview_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.send_profile_preview(interaction, target=interaction.user, ephemeral=True)

    @discord.ui.button(label="Resetar", style=discord.ButtonStyle.danger, custom_id="profile:editor:reset", row=1)
    async def reset_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = ProfileResetView(cog=self.cog, owner_id=self.owner_id, guild_id=self.guild_id)
        embed = discord.Embed(
            title="Resetar ficha",
            description="Escolha um campo ou limpe apenas o visual.",
            color=discord.Color.dark_grey(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await _send_component_error(interaction, error, "profile_editor_view_failed")


class ProfileVisualView(discord.ui.View):
    def __init__(self, *, cog: ProfileCog, owner_id: int, guild_id: int) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.add_item(ProfileThemeSelect(self))
        self.add_item(ProfileCharmSelect(self))
        self.add_item(ProfilePaletteSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message("Use isso dentro do servidor certo.", ephemeral=True)
            return False
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Esse painel nao e seu.", ephemeral=True)
            return False
        return True

    async def build_embed(self, interaction: discord.Interaction) -> discord.Embed:
        snapshot = await self.cog.service.get_profile_snapshot(interaction.guild, interaction.user)
        rendered = snapshot.rendered_fields()
        palette = rendered.get("accent_palette") or []
        embed = discord.Embed(
            title="Visual da ficha",
            description="Ajuste tema, charm e paleta.",
            color=discord.Color.dark_purple(),
        )
        embed.add_field(name="Tema", value=str(rendered.get("theme_preset") or "classic"), inline=True)
        embed.add_field(name="Charm", value=str(rendered.get("charm_preset") or "none"), inline=True)
        embed.add_field(name="Paleta", value=", ".join(palette) if isinstance(palette, list) and palette else "padrao", inline=False)
        return embed

    async def save_visual_field(self, interaction: discord.Interaction, field_key: str, value: object) -> None:
        try:
            await self.cog.service.set_field(
                guild_id=self.guild_id,
                user_id=self.owner_id,
                field_key=field_key,
                value=value,
                updated_by=interaction.user.id,
                source_type=ProfileFieldSourceType.MANUAL,
            )
        except ProfileValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.edit_message(embed=await self.build_embed(interaction), view=self)

    async def reset_palette(self, interaction: discord.Interaction) -> None:
        await self.cog.service.reset_field(
            guild_id=self.guild_id,
            user_id=self.owner_id,
            field_key="accent_palette",
            actor_id=interaction.user.id,
            reason="reset de paleta pelo painel visual",
        )
        await interaction.response.edit_message(embed=await self.build_embed(interaction), view=self)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await _send_component_error(interaction, error, "profile_visual_view_failed")


class ProfileThemeSelect(discord.ui.Select):
    def __init__(self, parent: ProfileVisualView) -> None:
        self.parent_view = parent
        options = [
            discord.SelectOption(label=label, value=value)
            for value, label in THEME_LABELS.items()
        ]
        super().__init__(placeholder="Tema", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.save_visual_field(interaction, "theme_preset", self.values[0])


class ProfileCharmSelect(discord.ui.Select):
    def __init__(self, parent: ProfileVisualView) -> None:
        self.parent_view = parent
        options = [
            discord.SelectOption(label=label, value=value)
            for value, label in CHARM_LABELS.items()
        ]
        super().__init__(placeholder="Charm", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.save_visual_field(interaction, "charm_preset", self.values[0])


class ProfilePaletteSelect(discord.ui.Select):
    def __init__(self, parent: ProfileVisualView) -> None:
        self.parent_view = parent
        options = [
            discord.SelectOption(label="Padrao", value="default", description="Remove a paleta personalizada"),
            *[
                discord.SelectOption(label=label, value=value, description=", ".join(colors))
                for value, (label, colors) in PALETTES.items()
            ],
        ]
        super().__init__(placeholder="Paleta", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        if selected == "default":
            await self.parent_view.reset_palette(interaction)
            return
        _, colors = PALETTES[selected]
        await self.parent_view.save_visual_field(interaction, "accent_palette", list(colors))


class ProfileResetView(discord.ui.View):
    def __init__(self, *, cog: ProfileCog, owner_id: int, guild_id: int) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.add_item(ProfileResetFieldSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message("Use isso dentro do servidor certo.", ephemeral=True)
            return False
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Esse reset nao e seu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Resetar visual", style=discord.ButtonStyle.danger, custom_id="profile:reset:visual", row=1)
    async def visual_reset_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        removed_count = await self.cog.service.reset_visual_fields(
            guild_id=self.guild_id,
            user_id=self.owner_id,
            actor_id=interaction.user.id,
            reason="reset visual pelo painel",
        )
        await interaction.response.send_message(f"Visual resetado. Campos removidos: {removed_count}.", ephemeral=True)

    async def reset_field(self, interaction: discord.Interaction, field_key: str) -> None:
        removed = await self.cog.service.reset_field(
            guild_id=self.guild_id,
            user_id=self.owner_id,
            field_key=field_key,
            actor_id=interaction.user.id,
            reason="reset pelo painel",
        )
        await interaction.response.send_message("Campo resetado." if removed else "Esse campo ja estava vazio.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await _send_component_error(interaction, error, "profile_reset_view_failed")


class ProfileResetFieldSelect(discord.ui.Select):
    def __init__(self, parent: ProfileResetView) -> None:
        self.parent_view = parent
        options = [
            discord.SelectOption(label=definition.label[:100], value=definition.key)
            for definition in parent.cog.service.field_registry.all()
            if definition.user_editable and definition.key not in VISUAL_FIELD_KEYS
        ][:25]
        super().__init__(placeholder="Resetar um campo", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.reset_field(interaction, self.values[0])


async def _send_component_error(interaction: discord.Interaction, error: Exception, marker: str) -> None:
    LOGGER.error(
        "%s user_id=%s guild_id=%s",
        marker,
        interaction.user.id,
        interaction.guild_id,
        exc_info=(type(error), error, error.__traceback__),
    )
    if interaction.response.is_done():
        await interaction.followup.send("Nao consegui fazer isso agora.", ephemeral=True)
    else:
        await interaction.response.send_message("Nao consegui fazer isso agora.", ephemeral=True)
