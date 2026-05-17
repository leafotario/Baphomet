from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import aiohttp
import discord
from discord.ext import commands

from .models import COMPASS_ROTULOS, CompassItemKind, CompassState, build_compass_item
from .views import CompassView, build_compass_embed

if TYPE_CHECKING:
    from .views import CompassView as ParentCompassView


LOGGER = logging.getLogger("baphomet.compass.modals")


class CompassCreateModal(discord.ui.Modal, title="Criar Compass"):
    def __init__(
        self,
        *,
        bot: commands.Bot,
        http_session: aiohttp.ClientSession,
        autor_id: int,
        rotulos: tuple[str, str, str, str] = COMPASS_ROTULOS,
    ) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.http_session = http_session
        self.autor_id = autor_id
        self.rotulos = rotulos
        self.titulo_input = discord.ui.TextInput(
            label="Titulo do documento final",
            placeholder="Ex: Mapa de prioridades do projeto",
            min_length=1,
            max_length=100,
            required=True,
        )
        self.add_item(self.titulo_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message("Este modal Compass pertence a outro usuario.", ephemeral=True)
            return

        try:
            state = CompassState(
                autor_id=self.autor_id,
                titulo=str(self.titulo_input.value),
                rotulos=self.rotulos,
            )
        except (TypeError, ValueError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.send_message(
            embed=build_compass_embed(state),
            view=CompassView(state, bot=self.bot, http_session=self.http_session),
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        LOGGER.error(
            "compass_modal_failed user_id=%s",
            interaction.user.id,
            exc_info=(type(error), error, error.__traceback__),
        )
        if interaction.response.is_done():
            await interaction.followup.send("Nao consegui criar o Compass agora.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui criar o Compass agora.", ephemeral=True)


class _CompassItemModal(discord.ui.Modal):
    tipo: CompassItemKind

    def __init__(
        self,
        *,
        parent_view: ParentCompassView,
        panel_message: discord.Message,
        title: str,
        content_label: str,
        content_placeholder: str,
        content_max_length: int,
    ) -> None:
        super().__init__(title=title, timeout=300)
        self.parent_view = parent_view
        self.state = parent_view.state
        self.panel_message = panel_message
        self.conteudo_input = discord.ui.TextInput(
            label=content_label,
            placeholder=content_placeholder,
            min_length=1,
            max_length=content_max_length,
            required=True,
        )
        self.abscissa_input = discord.ui.TextInput(
            label="Abscissa X",
            placeholder="-10 a 10",
            default="0",
            min_length=1,
            max_length=24,
            required=True,
        )
        self.ordenada_input = discord.ui.TextInput(
            label="Ordenada Y",
            placeholder="-10 a 10",
            default="0",
            min_length=1,
            max_length=24,
            required=True,
        )
        self.add_item(self.conteudo_input)
        self.add_item(self.abscissa_input)
        self.add_item(self.ordenada_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.state.autor_id:
            await interaction.response.send_message("Este modal Compass pertence a outro usuario.", ephemeral=True)
            return

        try:
            item = build_compass_item(
                tipo=self.tipo,
                conteudo=self._normalize_conteudo(str(self.conteudo_input.value)),
                abscissa=str(self.abscissa_input.value),
                ordenada=str(self.ordenada_input.value),
            )
            self.state.anexar_item(item)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await self.panel_message.edit(
            embed=build_compass_embed(self.state),
            view=self.parent_view,
        )
        await interaction.response.send_message("Insercao registrada no Compass.", ephemeral=True)

    def _normalize_conteudo(self, value: str) -> str:
        return value

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        LOGGER.error(
            "compass_item_modal_failed user_id=%s tipo=%s",
            interaction.user.id,
            self.tipo,
            exc_info=(type(error), error, error.__traceback__),
        )
        if interaction.response.is_done():
            await interaction.followup.send("Nao consegui registrar essa insercao.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui registrar essa insercao.", ephemeral=True)


class Modal_Texto(_CompassItemModal):
    tipo: CompassItemKind = "texto"

    def __init__(self, *, parent_view: ParentCompassView, panel_message: discord.Message) -> None:
        super().__init__(
            parent_view=parent_view,
            panel_message=panel_message,
            title="Modal_Texto",
            content_label="Texto",
            content_placeholder="Conteudo textual do ponto",
            content_max_length=300,
        )


class Modal_URL(_CompassItemModal):
    tipo: CompassItemKind = "url"

    def __init__(self, *, parent_view: ParentCompassView, panel_message: discord.Message) -> None:
        super().__init__(
            parent_view=parent_view,
            panel_message=panel_message,
            title="Modal_URL",
            content_label="URL",
            content_placeholder="https://exemplo.com/imagem.png",
            content_max_length=500,
        )

    def _normalize_conteudo(self, value: str) -> str:
        normalized = " ".join(value.split())
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL deve usar http ou https e possuir dominio.")
        return normalized


class Modal_Avatar_ID(_CompassItemModal):
    tipo: CompassItemKind = "avatar_id"

    def __init__(self, *, parent_view: ParentCompassView, panel_message: discord.Message) -> None:
        super().__init__(
            parent_view=parent_view,
            panel_message=panel_message,
            title="Modal_Avatar_ID",
            content_label="Avatar ID",
            content_placeholder="123456789012345678",
            content_max_length=24,
        )

    def _normalize_conteudo(self, value: str) -> str:
        normalized = value.strip()
        if not normalized.isdecimal():
            raise ValueError("Avatar ID deve conter apenas numeros.")
        return normalized
