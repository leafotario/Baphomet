import asyncio
import json
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

# =========================================================
# constantes
# =========================================================

SPAM_MESSAGE_LIMIT = 7
SPAM_WINDOW_SECONDS = 3
GHOST_USER_WINDOW_MINUTES = 10
SPAM_TRACKER_CLEANUP_INTERVAL_MINUTES = 5
SETTINGS_FILE = Path("guild_settings.json")

log = logging.getLogger(__name__)


# =========================================================
# persistência leve de configurações
# =========================================================

def _load_settings() -> dict[str, dict[str, int | None]]:
    if SETTINGS_FILE.exists():
        try:
            with SETTINGS_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            log.warning("falha ao carregar %s, usando configuração vazia", SETTINGS_FILE)
    return {}


def _save_settings(data: dict) -> None:
    try:
        with SETTINGS_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        log.exception("falha ao salvar %s", SETTINGS_FILE)


# =========================================================
# cog principal
# =========================================================

class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # anti-spam: chave = (guild_id, channel_id, user_id)
        self.spam_tracker: defaultdict[tuple[int, int, int], deque[datetime]] = defaultdict(deque)

        # horário de entrada registrado via evento (fallback: member.joined_at)
        self.join_times: defaultdict[int, dict[int, datetime]] = defaultdict(dict)

        # configuração persistida: chave no JSON é str(guild_id)
        raw = _load_settings()
        self.guild_settings: defaultdict[int, dict[str, int | None]] = defaultdict(
            lambda: {"geral": None, "entrada": None}
        )
        for guild_id_str, cfg in raw.items():
            self.guild_settings[int(guild_id_str)] = cfg

    # =========================================================
    # task de limpeza periódica do spam_tracker
    # =========================================================

    @tasks.loop(minutes=SPAM_TRACKER_CLEANUP_INTERVAL_MINUTES)
    async def _cleanup_spam_tracker(self) -> None:
        """Remove entradas expiradas do spam_tracker para evitar vazamento de memória."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=SPAM_WINDOW_SECONDS)
        stale_keys = [
            key for key, ts in self.spam_tracker.items()
            if not ts or ts[-1] < cutoff
        ]
        for key in stale_keys:
            self.spam_tracker.pop(key, None)
        if stale_keys:
            log.debug("spam_tracker: %d entradas expiradas removidas", len(stale_keys))

    async def cog_load(self) -> None:
        self._cleanup_spam_tracker.start()

    async def cog_unload(self) -> None:
        self._cleanup_spam_tracker.cancel()

    # =========================================================
    # eventos
    # =========================================================

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        self.join_times[member.guild.id][member.id] = datetime.now(timezone.utc)
        log.info(
            "entrada registrada: user_id=%d guild_id=%d guild=%s",
            member.id, member.guild.id, member.guild.name
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        # preferir o horário registrado pelo evento; fallback para member.joined_at
        joined_at = self.join_times[member.guild.id].pop(member.id, None)

        if joined_at is None:
            if member.joined_at is not None:
                joined_at = member.joined_at
                log.debug(
                    "join_times sem registro para user_id=%d, usando member.joined_at",
                    member.id
                )
            else:
                log.warning(
                    "sem horário de entrada para user_id=%d guild_id=%d, pulando limpeza",
                    member.id, member.guild.id
                )
                return

        left_at = datetime.now(timezone.utc)
        time_in_guild = left_at - joined_at

        if time_in_guild >= timedelta(minutes=GHOST_USER_WINDOW_MINUTES):
            log.info(
                "user_id=%d saiu após %s — não é ghost user",
                member.id, time_in_guild
            )
            return

        log.info(
            "ghost user detectado: user_id=%d ficou %s em guild_id=%d",
            member.id, time_in_guild, member.guild.id
        )

        deleted_member = await self.cleanup_member_messages(
            guild=member.guild,
            member_id=member.id,
            joined_at=joined_at,
            left_at=left_at
        )

        # aguarda bots de boas-vindas/saída postarem suas mensagens
        await asyncio.sleep(2)

        deleted_system = await self.cleanup_system_messages(
            guild=member.guild,
            member=member,
            joined_at=joined_at,
            left_at=datetime.now(timezone.utc)
        )

        log.info(
            "limpeza ghost user finalizada: user_id=%d | msgs membro=%d | msgs sistema=%d",
            member.id, deleted_member, deleted_system
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return

        await self.check_spam_and_kick(message)
        await self.bot.process_commands(message)

    # =========================================================
    # anti-spam
    # =========================================================

    async def check_spam_and_kick(self, message: discord.Message) -> None:
        now = datetime.now(timezone.utc)
        key = (message.guild.id, message.channel.id, message.author.id)
        timestamps = self.spam_tracker[key]

        timestamps.append(now)

        # remove timestamps fora da janela deslizante
        while timestamps and (now - timestamps[0]).total_seconds() > SPAM_WINDOW_SECONDS:
            timestamps.popleft()

        if len(timestamps) <= SPAM_MESSAGE_LIMIT:
            return

        # limpa o tracker antes de qualquer operação I/O para evitar duplo-kick
        self.spam_tracker.pop(key, None)
        author = message.author
        channel = message.channel

        log.warning(
            "spam detectado: user_id=%d channel_id=%d guild_id=%d",
            author.id, channel.id, message.guild.id
        )

        try:
            await author.kick(
                reason=(
                    f"anti-spam: mais de {SPAM_MESSAGE_LIMIT} mensagens "
                    f"em {SPAM_WINDOW_SECONDS}s no canal #{channel}"
                )
            )
        except discord.Forbidden:
            await channel.send(
                "não consegui expulsar o usuário por spam: sem permissão."
            )
            log.error("sem permissão para kickar user_id=%d", author.id)
            return
        except discord.HTTPException:
            await channel.send(
                "tentei expulsar o usuário por spam, mas a API do Discord retornou um erro."
            )
            log.exception("erro HTTP ao kickar user_id=%d", author.id)
            return

        # aviso no canal + limpeza das mensagens de spam
        await channel.send(f"{author.mention} foi expulso(a) automaticamente por spam.")

        try:
            spam_start = now - timedelta(seconds=SPAM_WINDOW_SECONDS + 1)
            await channel.purge(
                limit=SPAM_MESSAGE_LIMIT + 5,
                check=lambda m: m.author.id == author.id,
                after=spam_start,
                bulk=True
            )
        except (discord.Forbidden, discord.HTTPException):
            log.warning("não foi possível limpar mensagens de spam de user_id=%d", author.id)

    # =========================================================
    # limpeza ghost user
    # =========================================================

    def _get_me(self, guild: discord.Guild) -> discord.Member | None:
        """Retorna o membro do bot no servidor de forma segura."""
        if guild.me is not None:
            return guild.me
        if self.bot.user is not None:
            return guild.get_member(self.bot.user.id)
        return None

    async def cleanup_member_messages(
        self,
        guild: discord.Guild,
        member_id: int,
        joined_at: datetime,
        left_at: datetime
    ) -> int:
        me = self._get_me(guild)
        if me is None:
            return 0

        deleted_count = 0
        after = joined_at - timedelta(seconds=1)
        before = left_at + timedelta(seconds=1)
        cutoff_bulk = datetime.now(timezone.utc) - timedelta(days=13, hours=23)

        for channel in guild.text_channels:
            perms = channel.permissions_for(me)
            if not (perms.view_channel and perms.read_message_history and perms.manage_messages):
                continue

            try:
                to_delete_bulk: list[discord.Message] = []
                to_delete_single: list[discord.Message] = []

                async for msg in channel.history(
                    limit=None,
                    after=after,
                    before=before,
                    oldest_first=True
                ):
                    if msg.author.id != member_id:
                        continue
                    if msg.created_at >= cutoff_bulk:
                        to_delete_bulk.append(msg)
                    else:
                        to_delete_single.append(msg)

                # bulk delete (até 100 por chamada)
                for i in range(0, len(to_delete_bulk), 100):
                    chunk = to_delete_bulk[i:i + 100]
                    try:
                        await channel.delete_messages(chunk)
                        deleted_count += len(chunk)
                    except (discord.Forbidden, discord.HTTPException):
                        log.warning(
                            "falha no bulk delete em channel_id=%d", channel.id
                        )

                # delete individual para mensagens antigas
                for msg in to_delete_single:
                    try:
                        await msg.delete()
                        deleted_count += 1
                    except (discord.Forbidden, discord.NotFound):
                        continue
                    except discord.HTTPException:
                        log.exception(
                            "falha ao deletar mensagem msg_id=%d channel_id=%d",
                            msg.id, channel.id
                        )

            except discord.Forbidden:
                continue
            except discord.HTTPException:
                log.exception("falha ao percorrer histórico de channel_id=%d", channel.id)

        return deleted_count

    async def cleanup_system_messages(
        self,
        guild: discord.Guild,
        member: discord.Member,
        joined_at: datetime,
        left_at: datetime
    ) -> int:
        me = self._get_me(guild)
        if me is None:
            return 0

        deleted_count = 0
        settings = self.guild_settings[guild.id]
        target_channel_ids: set[int] = {
            cid for cid in (settings.get("geral"), settings.get("entrada"))
            if cid is not None
        }

        for channel_id in target_channel_ids:
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
                    if not self.is_related_system_message(msg, member):
                        continue
                    try:
                        await msg.delete()
                        deleted_count += 1
                    except (discord.Forbidden, discord.NotFound):
                        continue
                    except discord.HTTPException:
                        log.exception(
                            "falha ao deletar mensagem de sistema msg_id=%d", msg.id
                        )

            except discord.Forbidden:
                continue
            except discord.HTTPException:
                log.exception("falha ao varrer channel_id=%d", channel_id)

        return deleted_count

    def is_related_system_message(
        self,
        message: discord.Message,
        member: discord.Member
    ) -> bool:
        """
        Identifica mensagens de bots/webhooks relacionadas ao membro.
        Prioriza menção direta e ID; usa nome apenas como critério complementar.
        """
        is_system_like = (
            message.author.bot
            or message.webhook_id is not None
            or message.type != discord.MessageType.default
        )
        if not is_system_like:
            return False

        # critério forte: menção direta ou ID na mensagem
        has_mention = any(u.id == member.id for u in message.mentions)
        has_member_id = str(member.id) in message.content

        if has_mention or has_member_id:
            return True

        # critério fraco: busca por nome (mínimo 4 chars para evitar falsos positivos)
        text_parts = [message.content]
        for embed in message.embeds:
            text_parts += [
                embed.title or "",
                embed.description or "",
                getattr(embed.footer, "text", "") or "",
            ]
        combined = " ".join(text_parts).lower()

        possible_names = {
            name.lower()
            for name in (member.name, member.display_name, member.global_name)
            if name and len(name) >= 4
        }

        return any(name in combined for name in possible_names)

    # =========================================================
    # helper do convite
    # =========================================================

    def find_invite_channel(
        self,
        guild: discord.Guild,
        preferred_channel: discord.abc.GuildChannel | None
    ) -> discord.TextChannel | None:
        me = self._get_me(guild)
        if me is None:
            return None

        if preferred_channel is not None:
            if preferred_channel.permissions_for(me).create_instant_invite:
                return preferred_channel  # type: ignore[return-value]

        return next(
            (ch for ch in guild.text_channels
             if ch.permissions_for(me).create_instant_invite),
            None
        )

    # =========================================================
    # slash commands
    # =========================================================

    def _persist_settings(self, guild_id: int) -> None:
        serializable = {
            str(gid): cfg for gid, cfg in self.guild_settings.items()
        }
        _save_settings(serializable)

    @app_commands.command(
        name="set_geral",
        description="Define o canal geral monitorado para limpar mensagens de sistema"
    )
    @app_commands.describe(canal="Canal que será monitorado")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def set_geral(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel
    ) -> None:
        assert interaction.guild is not None
        self.guild_settings[interaction.guild.id]["geral"] = canal.id
        self._persist_settings(interaction.guild.id)
        await interaction.response.send_message(
            f"Canal `geral` configurado: {canal.mention}", ephemeral=True
        )

    @app_commands.command(
        name="set_entrada",
        description="Define o canal de entrada monitorado para limpar mensagens de sistema"
    )
    @app_commands.describe(canal="Canal que será monitorado")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def set_entrada(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel
    ) -> None:
        assert interaction.guild is not None
        self.guild_settings[interaction.guild.id]["entrada"] = canal.id
        self._persist_settings(interaction.guild.id)
        await interaction.response.send_message(
            f"Canal `entrada` configurado: {canal.mention}", ephemeral=True
        )

    @app_commands.command(
        name="convite",
        description="Gera um convite privado do servidor"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def convite(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None

        preferred = (
            interaction.channel
            if isinstance(interaction.channel, discord.abc.GuildChannel)
            else None
        )
        invite_channel = self.find_invite_channel(interaction.guild, preferred)

        if invite_channel is None:
            await interaction.response.send_message(
                "Não encontrei nenhum canal onde eu tenha permissão para criar convite.",
                ephemeral=True
            )
            return

        try:
            invite = await invite_channel.create_invite(
                max_age=0,
                max_uses=0,
                unique=False,
                reason=f"Convite privado solicitado por {interaction.user}"
            )
            await interaction.response.send_message(
                f"Aqui está seu convite privado:\n{invite.url}", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Não consegui criar o convite: sem permissão.", ephemeral=True
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                "Erro ao criar o convite na API do Discord.", ephemeral=True
            )

    # =========================================================
    # tratamento de erros dos slash commands
    # =========================================================

    @set_geral.error
    @set_entrada.error
    @convite.error
    async def admin_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            msg = "Você precisa ser administrador(a) para usar esse comando."
        elif isinstance(error, app_commands.BotMissingPermissions):
            missing = ", ".join(error.missing_permissions)
            msg = f"Estou sem as permissões necessárias: {missing}."
        else:
            msg = "Ocorreu um erro ao executar esse comando."
            log.exception("erro em app command", exc_info=error)

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationCog(bot))