from __future__ import annotations

import asyncio

import discord

from .models import SessionStatus, TemplateSessionSnapshot
from .services import TierListTemplateError, TierListTemplateService


UNALLOCATED_VALUE = "__unallocated__"


class TemplatePublishConfirmView(discord.ui.View):
    def __init__(self, service: TierListTemplateService, *, template_id: int, owner_id: int) -> None:
        super().__init__(timeout=120)
        self.service = service
        self.template_id = template_id
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Esse template não é seu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Publicar nova versão", emoji="📌", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            version = await self.service.publish_template(
                template_id=self.template_id,
                actor_id=interaction.user.id,
            )
        except TierListTemplateError as exc:
            await interaction.response.edit_message(content=f"❌ {exc.user_message}", view=None)
            return

        await interaction.response.edit_message(
            content=f"✅ Template publicado como versão **v{version.version_number}**.",
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Cancelar", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Publicação cancelada.", view=None)
        self.stop()


class TemplateItemSelect(discord.ui.Select):
    def __init__(self, view_instance: "TemplateItemSelectView") -> None:
        self.view_instance = view_instance
        options = []
        for item in view_instance.snapshot.items[:25]:
            current_tier = item.tier_name or "não alocado"
            options.append(
                discord.SelectOption(
                    label=(item.name or "item com imagem")[:100],
                    value=str(item.id),
                    description=f"Atual: {current_tier}"[:100],
                    emoji="🖼️" if item.asset_sha256 else "📝",
                )
            )
        super().__init__(
            placeholder="Escolha o item que deseja mover",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        item_id = int(self.values[0])
        await interaction.response.edit_message(
            content="📌 Agora escolha a tier de destino.",
            view=TemplateTierSelectView(
                self.view_instance.service,
                snapshot=self.view_instance.snapshot,
                item_id=item_id,
                owner_id=self.view_instance.owner_id,
            ),
        )


class TemplateItemSelectView(discord.ui.View):
    def __init__(
        self,
        service: TierListTemplateService,
        *,
        snapshot: TemplateSessionSnapshot,
        owner_id: int,
    ) -> None:
        super().__init__(timeout=120)
        self.service = service
        self.snapshot = snapshot
        self.owner_id = owner_id
        self.add_item(TemplateItemSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Essa sessão não é sua.", ephemeral=True)
            return False
        return True


class TemplateTierSelect(discord.ui.Select):
    def __init__(self, view_instance: "TemplateTierSelectView") -> None:
        self.view_instance = view_instance
        options = [
            discord.SelectOption(
                label=tier.name[:100],
                value=tier.name,
                description=f"Mover para {tier.name}"[:100],
                emoji="📌",
            )
            for tier in view_instance.snapshot.tiers[:24]
        ]
        options.append(
            discord.SelectOption(
                label="Não alocados",
                value=UNALLOCATED_VALUE,
                description="Remover das tiers por enquanto",
                emoji="↩️",
            )
        )
        super().__init__(
            placeholder="Escolha a tier de destino",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        tier_name = None if self.values[0] == UNALLOCATED_VALUE else self.values[0]
        try:
            await self.view_instance.service.move_session_item(
                session_id=self.view_instance.snapshot.session.id,
                item_id=self.view_instance.item_id,
                tier_name=tier_name,
                actor_id=interaction.user.id,
            )
        except TierListTemplateError as exc:
            await interaction.response.edit_message(content=f"❌ {exc.user_message}", view=None)
            return
        except ValueError as exc:
            await interaction.response.edit_message(content=f"❌ {exc}", view=None)
            return

        await refresh_session_panel(
            interaction,
            self.view_instance.service,
            session_id=self.view_instance.snapshot.session.id,
            owner_id=self.view_instance.owner_id,
        )
        target = tier_name or "não alocados"
        await interaction.response.edit_message(content=f"✅ Item movido para **{target}**.", view=None)


class TemplateTierSelectView(discord.ui.View):
    def __init__(
        self,
        service: TierListTemplateService,
        *,
        snapshot: TemplateSessionSnapshot,
        item_id: int,
        owner_id: int,
    ) -> None:
        super().__init__(timeout=120)
        self.service = service
        self.snapshot = snapshot
        self.item_id = item_id
        self.owner_id = owner_id
        self.add_item(TemplateTierSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Essa sessão não é sua.", ephemeral=True)
            return False
        return True


class TemplateSessionControlView(discord.ui.View):
    def __init__(self, service: TierListTemplateService, *, session_id: int, owner_id: int) -> None:
        super().__init__(timeout=30 * 60)
        self.service = service
        self.session_id = session_id
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Essa sessão não é sua.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Mover Item", emoji="📌", style=discord.ButtonStyle.primary)
    async def move_item(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            snapshot = await self.service.get_session_snapshot(
                session_id=self.session_id,
                actor_id=interaction.user.id,
            )
        except TierListTemplateError as exc:
            await interaction.response.send_message(f"❌ {exc.user_message}", ephemeral=True)
            return

        if snapshot.session.status != SessionStatus.ACTIVE:
            await interaction.response.send_message("❌ Essa sessão não está ativa.", ephemeral=True)
            return
        if not snapshot.items:
            await interaction.response.send_message("⚠️ Essa sessão não tem itens.", ephemeral=True)
            return

        await interaction.response.send_message(
            "📌 Escolha o item que deseja mover.",
            view=TemplateItemSelectView(self.service, snapshot=snapshot, owner_id=self.owner_id),
            ephemeral=True,
        )

    @discord.ui.button(label="Prévia", emoji="🖼️", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            snapshot = await self.service.get_session_snapshot(
                session_id=self.session_id,
                actor_id=interaction.user.id,
            )
            guild_icon_bytes = await read_guild_icon_bytes(interaction)
            image_buffer = await asyncio.to_thread(
                self.service.render_session_snapshot,
                snapshot,
                author=interaction.user,
                guild_icon_bytes=guild_icon_bytes,
            )
        except TierListTemplateError as exc:
            await interaction.followup.send(f"❌ {exc.user_message}", ephemeral=True)
            return
        except Exception:
            await interaction.followup.send("❌ Não consegui gerar a prévia agora.", ephemeral=True)
            return

        await interaction.followup.send(
            file=discord.File(image_buffer, filename="tierlist_template_preview.png"),
            ephemeral=True,
        )

    @discord.ui.button(label="Finalizar", emoji="✅", style=discord.ButtonStyle.success)
    async def finalize(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(thinking=True)
        try:
            snapshot = await self.service.get_session_snapshot(
                session_id=self.session_id,
                actor_id=interaction.user.id,
            )
            if not any(item.tier_name for item in snapshot.items):
                await interaction.followup.send(
                    "⚠️ Mova pelo menos um item para uma tier antes de finalizar.",
                    ephemeral=True,
                )
                return
            guild_icon_bytes = await read_guild_icon_bytes(interaction)
            image_buffer = await asyncio.to_thread(
                self.service.render_session_snapshot,
                snapshot,
                author=interaction.user,
                guild_icon_bytes=guild_icon_bytes,
            )
            await self.service.repository.update_session_status(
                session_id=self.session_id,
                status=SessionStatus.FINALIZED,
            )
        except TierListTemplateError as exc:
            await interaction.followup.send(f"❌ {exc.user_message}", ephemeral=True)
            return
        except Exception:
            await interaction.followup.send("❌ Não consegui finalizar essa sessão agora.", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True

        try:
            done_snapshot = await self.service.repository.get_session_snapshot(self.session_id)
            if done_snapshot is not None and interaction.message:
                await interaction.message.edit(embed=self.service.build_session_embed(done_snapshot), view=None)
        except discord.HTTPException:
            pass

        await interaction.followup.send(
            content=f"🖼️ **{discord.utils.escape_markdown(snapshot.session.title)}**",
            file=discord.File(image_buffer, filename="tierlist.png"),
        )
        self.stop()

    @discord.ui.button(label="Abandonar", emoji="❌", style=discord.ButtonStyle.danger)
    async def abandon(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            await self.service.abandon_session(session_id=self.session_id, actor_id=interaction.user.id)
        except TierListTemplateError as exc:
            await interaction.response.send_message(f"❌ {exc.user_message}", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        try:
            snapshot = await self.service.repository.get_session_snapshot(self.session_id)
            embed = self.service.build_session_embed(snapshot) if snapshot else None
            await interaction.response.edit_message(embed=embed, view=None)
        except discord.HTTPException:
            await interaction.response.send_message("✅ Sessão abandonada.", ephemeral=True)
        self.stop()


async def refresh_session_panel(
    interaction: discord.Interaction,
    service: TierListTemplateService,
    *,
    session_id: int,
    owner_id: int,
) -> None:
    snapshot = await service.repository.get_session_snapshot(session_id)
    if snapshot is None or snapshot.session.channel_id is None or snapshot.session.message_id is None:
        return

    channel = interaction.client.get_channel(snapshot.session.channel_id)
    if channel is None:
        try:
            channel = await interaction.client.fetch_channel(snapshot.session.channel_id)
        except (discord.HTTPException, discord.NotFound):
            return
    if not hasattr(channel, "fetch_message"):
        return

    try:
        message = await channel.fetch_message(snapshot.session.message_id)  # type: ignore[attr-defined]
        view = None
        if snapshot.session.status == SessionStatus.ACTIVE:
            view = TemplateSessionControlView(service, session_id=session_id, owner_id=owner_id)
        await message.edit(embed=service.build_session_embed(snapshot), view=view)
    except (discord.HTTPException, discord.NotFound):
        return


async def read_guild_icon_bytes(interaction: discord.Interaction) -> bytes | None:
    if interaction.guild and interaction.guild.icon:
        try:
            return await interaction.guild.icon.replace(format="png", size=128).read()
        except (discord.HTTPException, ValueError, TypeError):
            return None
    return None
