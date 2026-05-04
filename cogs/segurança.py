import asyncio
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

SPAM_MESSAGE_LIMIT = 7
SPAM_WINDOW_SECONDS = 3
GHOST_USER_WINDOW_MINUTES = 10


class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # anti-spam:
        # chave = (guild_id, channel_id, user_id)
        # valor = deque com timestamps das mensagens recentes
        self.spam_tracker: defaultdict[tuple[int, int, int], deque[datetime]] = defaultdict(deque)

        # horário de entrada dos membros
        # join_times[guild_id][member_id] = datetime UTC
        self.join_times: defaultdict[int, dict[int, datetime]] = defaultdict(dict)

        # configuração dos canais monitorados
        # guild_settings[guild_id] = {"geral": canal_id, "entrada": canal_id}
        self.guild_settings: defaultdict[int, dict[str, int | None]] = defaultdict(
            lambda: {"geral": None, "entrada": None}
        )

    # =========================================================
    # eventos
    # =========================================================

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        # registramos o horário de entrada em UTC.
        # usar timezone.utc evita problemas de comparação entre datetimes.
        self.join_times[member.guild.id][member.id] = datetime.now(timezone.utc)
        logging.info("entrada registrada: %s em %s", member, member.guild.name)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        joined_at = self.join_times[member.guild.id].pop(member.id, None)

        # se não temos o horário de entrada, não dá para calcular
        # se ele saiu dentro da janela de 10 minutos.
        if joined_at is None:
            return

        left_at = datetime.now(timezone.utc)
        time_in_guild = left_at - joined_at

        # só dispara a limpeza se o membro saiu em menos de 10 minutos.
        if time_in_guild >= timedelta(minutes=GHOST_USER_WINDOW_MINUTES):
            logging.info(
                "%s saiu após %s, então não entra na regra ghost user",
                member,
                time_in_guild
            )
            return

        logging.info(
            "ghost user detectado: %s ficou apenas %s no servidor",
            member,
            time_in_guild
        )

        # apaga mensagens enviadas pelo membro entre a entrada e a saída
        deleted_member_messages = await self.cleanup_member_messages(
            guild=member.guild,
            member_id=member.id,
            joined_at=joined_at,
            left_at=left_at
        )

        # espera um pouco para dar tempo de bots de boas-vindas/saída
        # postarem as mensagens, caso existam
        await asyncio.sleep(2)

        # limpa mensagens de sistema relacionadas a esse membro
        deleted_system_messages = await self.cleanup_system_messages(
            guild=member.guild,
            member=member,
            joined_at=joined_at,
            left_at=datetime.now(timezone.utc)
        )

        logging.info(
            "limpeza ghost user finalizada para %s | mensagens do membro: %s | mensagens de sistema: %s",
            member,
            deleted_member_messages,
            deleted_system_messages
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return

        if message.author.bot:
            return

        await self.check_spam_and_kick(message)

    # =========================================================
    # anti-spam
    # =========================================================

    async def check_spam_and_kick(self, message: discord.Message) -> None:
        now = datetime.now(timezone.utc)
        key = (message.guild.id, message.channel.id, message.author.id)
        timestamps = self.spam_tracker[key]

        timestamps.append(now)

        # remove timestamps fora da janela de 3 segundos
        while timestamps and (now - timestamps[0]).total_seconds() > SPAM_WINDOW_SECONDS:
            timestamps.popleft()

        # mais de 7 mensagens em 3 segundos => kick
        if len(timestamps) > SPAM_MESSAGE_LIMIT:
            self.spam_tracker.pop(key, None)

            try:
                await message.author.kick(
                    reason=(
                        f"anti-spam: mais de {SPAM_MESSAGE_LIMIT} mensagens "
                        f"em {SPAM_WINDOW_SECONDS}s no canal #{message.channel}"
                    )
                )
                await message.channel.send(
                    f"{message.author.mention} foi expulso(a) automaticamente por spam."
                )
                logging.warning(
                    "usuário expulso por spam: %s em %s/%s",
                    message.author,
                    message.guild.name,
                    message.channel
                )

            except discord.Forbidden:
                await message.channel.send(
                    "não consegui expulsar o usuário por spam porque estou sem permissão."
                )
                logging.exception("sem permissão para kickar %s", message.author)

            except discord.HTTPException:
                await message.channel.send(
                    "tentei expulsar o usuário por spam, mas a api do discord retornou um erro."
                )
                logging.exception("erro de http ao kickar %s", message.author)

    # =========================================================
    # limpeza ghost user
    # =========================================================

    async def cleanup_member_messages(
        self,
        guild: discord.Guild,
        member_id: int,
        joined_at: datetime,
        left_at: datetime
    ) -> int:
        deleted_count = 0
        me = guild.me or guild.get_member(self.bot.user.id)  # type: ignore[arg-type]

        if me is None:
            return 0

        for channel in guild.text_channels:
            perms = channel.permissions_for(me)

            if not (perms.view_channel and perms.read_message_history and perms.manage_messages):
                continue

            try:
                # percorremos apenas mensagens enviadas entre o momento
                # em que o membro entrou e o momento em que saiu.
                # isso limita bastante a busca e deixa a limpeza mais precisa.
                async for msg in channel.history(
                    limit=None,
                    after=joined_at - timedelta(seconds=1),
                    before=left_at + timedelta(seconds=1),
                    oldest_first=True
                ):
                    if msg.author.id == member_id:
                        try:
                            await msg.delete()
                            deleted_count += 1
                        except (discord.Forbidden, discord.NotFound):
                            continue
                        except discord.HTTPException:
                            logging.exception(
                                "falha ao deletar mensagem %s em %s",
                                msg.id,
                                channel
                            )

            except discord.Forbidden:
                continue
            except discord.HTTPException:
                logging.exception("falha ao percorrer histórico do canal %s", channel)

        return deleted_count

    async def cleanup_system_messages(
        self,
        guild: discord.Guild,
        member: discord.Member,
        joined_at: datetime,
        left_at: datetime
    ) -> int:
        deleted_count = 0
        me = guild.me or guild.get_member(self.bot.user.id)  # type: ignore[arg-type]

        if me is None:
            return 0

        settings = self.guild_settings[guild.id]
        target_channel_ids = {settings.get("geral"), settings.get("entrada")}

        for channel_id in target_channel_ids:
            if channel_id is None:
                continue

            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue

            perms = channel.permissions_for(me)
            if not (perms.view_channel and perms.read_message_history and perms.manage_messages):
                continue

            try:
                async for msg in channel.history(
                    limit=200,
                    after=joined_at - timedelta(minutes=1),
                    before=left_at + timedelta(seconds=10),
                    oldest_first=True
                ):
                    if self.is_related_system_message(msg, member):
                        try:
                            await msg.delete()
                            deleted_count += 1
                        except (discord.Forbidden, discord.NotFound):
                            continue
                        except discord.HTTPException:
                            logging.exception(
                                "falha ao deletar mensagem de sistema %s",
                                msg.id
                            )

            except discord.Forbidden:
                continue
            except discord.HTTPException:
                logging.exception("falha ao varrer canal configurado %s", channel)

        return deleted_count

    def is_related_system_message(
        self,
        message: discord.Message,
        member: discord.Member
    ) -> bool:
        """
        tenta identificar mensagens de boas-vindas/saída ligadas ao membro.

        como não existe um padrão universal entre bots de servidor,
        usamos uma heurística:
        - a mensagem veio de bot, webhook ou tipo de sistema
        - e menciona o usuário, nome, display_name ou id dele
        """
        is_system_like = (
            message.author.bot
            or message.webhook_id is not None
            or message.type != discord.MessageType.default
        )

        if not is_system_like:
            return False

        text_parts = [message.content]

        for embed in message.embeds:
            text_parts.append(embed.title or "")
            text_parts.append(embed.description or "")
            text_parts.append(getattr(embed.footer, "text", "") or "")

        combined_text = " ".join(text_parts).lower()

        possible_names = {
            member.name.lower(),
            member.display_name.lower(),
        }

        if member.global_name:
            possible_names.add(member.global_name.lower())

        has_mention = any(user.id == member.id for user in message.mentions)
        has_member_id = str(member.id) in combined_text
        has_name = any(name in combined_text for name in possible_names if name)

        return has_mention or has_member_id or has_name

    # =========================================================
    # helper do convite
    # =========================================================

    def find_invite_channel(
        self,
        guild: discord.Guild,
        preferred_channel: discord.abc.GuildChannel | None
    ):
        me = guild.me or guild.get_member(self.bot.user.id)  # type: ignore[arg-type]
        if me is None:
            return None

        if preferred_channel is not None:
            perms = preferred_channel.permissions_for(me)
            if perms.create_instant_invite:
                return preferred_channel

        for channel in guild.text_channels:
            perms = channel.permissions_for(me)
            if perms.create_instant_invite:
                return channel

        return None

    # =========================================================
    # slash commands
    # =========================================================

    @app_commands.command(
        name="set_geral",
        description="define o canal geral monitorado para limpar mensagens de sistema"
    )
    @app_commands.describe(canal="canal que será monitorado")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def set_geral(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "esse comando só pode ser usado dentro de um servidor.",
                ephemeral=True
            )
            return

        self.guild_settings[interaction.guild.id]["geral"] = canal.id
        await interaction.response.send_message(
            f"canal `geral` configurado com sucesso: {canal.mention}",
            ephemeral=True
        )

    @app_commands.command(
        name="set_entrada",
        description="define o canal de entrada monitorado para limpar mensagens de sistema"
    )
    @app_commands.describe(canal="canal que será monitorado")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def set_entrada(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "esse comando só pode ser usado dentro de um servidor.",
                ephemeral=True
            )
            return

        self.guild_settings[interaction.guild.id]["entrada"] = canal.id
        await interaction.response.send_message(
            f"canal `entrada` configurado com sucesso: {canal.mention}",
            ephemeral=True
        )

    @app_commands.command(
        name="convite",
        description="gera um convite privado do servidor"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def convite(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "esse comando só pode ser usado dentro de um servidor.",
                ephemeral=True
            )
            return

        preferred_channel = (
            interaction.channel
            if isinstance(interaction.channel, discord.abc.GuildChannel)
            else None
        )

        invite_channel = self.find_invite_channel(interaction.guild, preferred_channel)

        if invite_channel is None:
            await interaction.response.send_message(
                "não encontrei nenhum canal onde eu tenha permissão para criar convite.",
                ephemeral=True
            )
            return

        try:
            invite = await invite_channel.create_invite(
                max_age=0,
                max_uses=0,
                unique=False,
                reason=f"convite privado solicitado por {interaction.user}"
            )

            await interaction.response.send_message(
                f"aqui está seu convite privado:\n{invite.url}",
                ephemeral=True
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "não consegui criar o convite porque estou sem permissão.",
                ephemeral=True
            )

        except discord.HTTPException:
            await interaction.response.send_message(
                "deu erro ao criar o convite na api do discord.",
                ephemeral=True
            )

    # =========================================================
    # tratamento de erro dos slash commands da cog
    # =========================================================

    @set_geral.error
    @set_entrada.error
    async def admin_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            msg = "você precisa ser administrador(a) para usar esse comando."
        elif isinstance(error, app_commands.BotMissingPermissions):
            missing = ", ".join(error.missing_permissions)
            msg = f"estou sem as permissões necessárias: {missing}."
        else:
            msg = "aconteceu um erro ao executar esse comando."
            logging.exception("erro em app command da cog", exc_info=error)

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    async def cog_load(self) -> None:
        # registra os slash commands da cog na árvore
        self.bot.tree.add_command(self.set_geral)
        self.bot.tree.add_command(self.set_entrada)
        self.bot.tree.add_command(self.convite)

    async def cog_unload(self) -> None:
        # remove os commands ao descarregar a cog
        self.bot.tree.remove_command(self.set_geral.name, type=self.set_geral.type)
        self.bot.tree.remove_command(self.set_entrada.name, type=self.set_entrada.type)
        self.bot.tree.remove_command(self.convite.name, type=self.convite.type)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationCog(bot))