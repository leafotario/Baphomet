from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from .models import ProfileFieldSourceType
from .services import ProfileValidationError

if TYPE_CHECKING:
    from .cog import ProfileCog
    from .schemas import ProfileSnapshot


LOGGER = logging.getLogger("baphomet.profile.modals")


def _snapshot_default(snapshot: ProfileSnapshot, field_key: str) -> str:
    field = snapshot.fields[field_key]
    if field.status and field.status.value == "removed_by_mod":
        return ""
    value = field.value
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value or "")


class _ProfileFieldsModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        cog: ProfileCog,
        owner_id: int,
        guild_id: int,
        title: str,
        field_keys: tuple[str, ...],
        snapshot: ProfileSnapshot,
    ) -> None:
        super().__init__(title=title, timeout=600)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.field_keys = field_keys
        for field_key in field_keys:
            definition = cog.service.field_registry.get(field_key)
            max_length = definition.max_length if field_key != "interests" else 600
            style = discord.TextStyle.paragraph if definition.field_type.value == "text_long" or field_key == "interests" else discord.TextStyle.short
            self.add_item(
                discord.ui.TextInput(
                    label=definition.label[:45],
                    custom_id=field_key,
                    default=_snapshot_default(snapshot, field_key)[:max_length],
                    max_length=max_length,
                    required=False,
                    style=style,
                    placeholder=self._placeholder_for(field_key),
                )
            )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message("Use isso dentro do servidor certo.", ephemeral=True)
            return
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Esse editor nao e seu.", ephemeral=True)
            return

        values = {
            item.custom_id: str(item.value)
            for item in self.children
            if isinstance(item, discord.ui.TextInput) and str(item.value).strip()
        }
        if not values:
            await interaction.response.send_message("Nada para salvar.", ephemeral=True)
            return

        try:
            for field_key, value in values.items():
                self.cog.service.normalize_field_value(field_key, value)
            for field_key, value in values.items():
                await self.cog.service.set_field(
                    guild_id=self.guild_id,
                    user_id=self.owner_id,
                    field_key=field_key,
                    value=value,
                    updated_by=interaction.user.id,
                    source_type=ProfileFieldSourceType.USER,
                )
        except ProfileValidationError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except Exception:
            LOGGER.exception("profile_modal_submit_failed guild_id=%s user_id=%s", self.guild_id, self.owner_id)
            await interaction.response.send_message("Nao consegui salvar agora.", ephemeral=True)
            return

        await interaction.response.send_message("Salvo.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        LOGGER.error(
            "profile_modal_error guild_id=%s user_id=%s",
            self.guild_id,
            self.owner_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        if interaction.response.is_done():
            await interaction.followup.send("Nao consegui salvar agora.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui salvar agora.", ephemeral=True)

    def _placeholder_for(self, field_key: str) -> str:
        return {
            "pronouns": "ex: ela/dela",
            "headline": "uma frase curta para a ficha",
            "bio": "um pouco sobre voce",
            "basic_info": "idade, cidade, area, o que fizer sentido",
            "ask_me_about": "assuntos que voce curte responder",
            "mood": "como voce esta hoje",
            "interests": "separe interesses por virgula",
        }.get(field_key, "")


class ProfileTextModal(_ProfileFieldsModal):
    def __init__(self, *, cog: ProfileCog, owner_id: int, guild_id: int, snapshot: ProfileSnapshot) -> None:
        super().__init__(
            cog=cog,
            owner_id=owner_id,
            guild_id=guild_id,
            title="Ficha: Texto",
            field_keys=("pronouns", "headline", "bio"),
            snapshot=snapshot,
        )


class ProfileConnectionsModal(_ProfileFieldsModal):
    def __init__(self, *, cog: ProfileCog, owner_id: int, guild_id: int, snapshot: ProfileSnapshot) -> None:
        super().__init__(
            cog=cog,
            owner_id=owner_id,
            guild_id=guild_id,
            title="Ficha: Conexoes",
            field_keys=("basic_info", "ask_me_about", "mood", "interests"),
            snapshot=snapshot,
        )
