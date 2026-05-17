from __future__ import annotations

import asyncio
import logging

import aiohttp
import discord
from discord.ext import commands

from .models import CompassItemPayload, CompassState
from .renderer import CompassRenderItem, render_compass_report_png, snapshot_compass_state


LOGGER = logging.getLogger("baphomet.compass.views")
COMPASS_COMPONENT_IDS = (
    "compass:texto",
    "compass:url",
    "compass:avatar_id",
    "compass:compile",
)


class CompassCooldownError(Exception):
    def __init__(self, retry_after: float, component_id: str) -> None:
        self.retry_after = retry_after
        self.component_id = component_id
        super().__init__(f"cooldown active for {component_id}: {retry_after:.2f}s")


class CompassOwnershipError(Exception):
    pass


def _interaction_user_bucket(interaction: discord.Interaction) -> int:
    return interaction.user.id


def _format_compass_item(index: int, item: CompassItemPayload) -> str:
    abscissa, ordenada = item["coordenadas"]
    content = item["conteudo"]
    if len(content) > 64:
        content = content[:61].rstrip() + "..."
    return f"{index}. [{item['tipo']}] {content} @ ({abscissa:.2f}, {ordenada:.2f})"


def build_compass_embed(state: CompassState) -> discord.Embed:
    embed = discord.Embed(
        title=state.titulo,
        description="Compass transitorio criado para montagem do documento final.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Autor", value=f"<@{state.autor_id}>", inline=True)
    embed.add_field(name="Rotulos", value="\n".join(state.rotulos), inline=True)
    embed.add_field(name="Total", value=str(len(state.lista_itens)), inline=True)
    rendered_items = "\n".join(
        _format_compass_item(index, item)
        for index, item in enumerate(state.lista_itens[-10:], start=max(1, len(state.lista_itens) - 9))
    )
    embed.add_field(
        name="Insercoes recentes",
        value=rendered_items[:1024] if rendered_items else "Nenhum item registrado.",
        inline=False,
    )
    return embed


class CompassView(discord.ui.View):
    def __init__(
        self,
        state: CompassState,
        *,
        bot: commands.Bot,
        http_session: aiohttp.ClientSession,
    ) -> None:
        super().__init__(timeout=900)
        self.state = state
        self.bot = bot
        self.http_session = http_session
        self._cooldowns: dict[str, commands.CooldownMapping[discord.Interaction]] = {
            component_id: commands.CooldownMapping.from_cooldown(1, 2.5, _interaction_user_bucket)
            for component_id in COMPASS_COMPONENT_IDS
        }

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.state.autor_id:
            raise CompassOwnershipError("interaction outside Compass owner scope")

        data = interaction.data if isinstance(interaction.data, dict) else {}
        component_id = str(data.get("custom_id") or "")
        cooldown = self._cooldowns.get(component_id)
        if cooldown is not None:
            retry_after = cooldown.update_rate_limit(interaction)
            if retry_after:
                raise CompassCooldownError(retry_after, component_id)

        return True

    @discord.ui.button(label="Texto", style=discord.ButtonStyle.secondary, custom_id="compass:texto")
    async def texto_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .modals import Modal_Texto

        await interaction.response.send_modal(Modal_Texto(parent_view=self, panel_message=interaction.message))

    @discord.ui.button(label="URL", style=discord.ButtonStyle.secondary, custom_id="compass:url")
    async def url_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .modals import Modal_URL

        await interaction.response.send_modal(Modal_URL(parent_view=self, panel_message=interaction.message))

    @discord.ui.button(label="Avatar ID", style=discord.ButtonStyle.secondary, custom_id="compass:avatar_id")
    async def avatar_id_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .modals import Modal_Avatar_ID

        await interaction.response.send_modal(Modal_Avatar_ID(parent_view=self, panel_message=interaction.message))

    @discord.ui.button(label="Compilar imagem", style=discord.ButtonStyle.success, custom_id="compass:compile")
    async def compile_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True

        await interaction.message.edit(view=self)
        snapshot = snapshot_compass_state(self.state)
        asset_bytes_by_index = await self._resolve_asset_bytes(snapshot.lista_itens)
        loop = asyncio.get_running_loop()
        buffer = await loop.run_in_executor(None, render_compass_report_png, snapshot, asset_bytes_by_index)
        await interaction.followup.send(
            "Relatorio de imagem Compass compilado.",
            file=discord.File(buffer, filename="compass_relatorio.png"),
            ephemeral=True,
        )

    async def _resolve_asset_bytes(self, items: tuple[CompassRenderItem, ...]) -> dict[int, bytes]:
        tasks: list[tuple[int, asyncio.Task[bytes | None]]] = []
        for index, item in enumerate(items, start=1):
            if item.tipo == "url":
                tasks.append((index, asyncio.create_task(self._download_bytes(item.conteudo))))
            elif item.tipo == "avatar_id":
                avatar_url = self._avatar_url_from_cache(item.conteudo)
                if avatar_url is not None:
                    tasks.append((index, asyncio.create_task(self._download_bytes(avatar_url))))

        assets: dict[int, bytes] = {}
        for index, task in tasks:
            try:
                data = await task
            except Exception:
                LOGGER.exception("compass_asset_download_failed index=%s", index)
                continue
            if data:
                assets[index] = data
        return assets

    async def _download_bytes(self, url: str) -> bytes | None:
        if self.http_session.closed:
            raise RuntimeError("Compass HTTP session is closed.")

        async with self.http_session.get(url) as response:
            if response.status >= 400:
                LOGGER.warning("compass_asset_http_error status=%s url=%s", response.status, url)
                return None

            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = 0
                if declared_size > 6 * 1024 * 1024:
                    LOGGER.warning("compass_asset_too_large content_length=%s url=%s", content_length, url)
                    return None

            data = await response.read()
            if len(data) > 6 * 1024 * 1024:
                LOGGER.warning("compass_asset_too_large bytes=%s url=%s", len(data), url)
                return None
            return data

    def _avatar_url_from_cache(self, raw_user_id: str) -> str | None:
        try:
            user_id = int(raw_user_id)
        except ValueError:
            return None

        user = self.bot.get_user(user_id)
        if user is None:
            LOGGER.warning("compass_avatar_user_not_cached user_id=%s", user_id)
            return None

        return str(user.display_avatar.replace(size=128, static_format="png").url)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[discord.ui.View] | None,
    ) -> None:
        if isinstance(error, CompassOwnershipError):
            await self._send_private_error(interaction, "Este painel Compass pertence a outro usuario.")
            return

        if isinstance(error, CompassCooldownError):
            await self._send_private_error(
                interaction,
                f"Aguarde {error.retry_after:.1f}s antes de acionar este componente novamente.",
            )
            return

        LOGGER.error(
            "compass_view_interaction_failed user_id=%s item=%s",
            interaction.user.id,
            getattr(item, "custom_id", None),
            exc_info=(type(error), error, error.__traceback__),
        )
        await self._send_private_error(interaction, "Nao consegui processar essa interacao Compass.")

    async def _send_private_error(self, interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
