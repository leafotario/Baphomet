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
SPAM_TRACKER_CLEANUP_INTERVAL_MINUTES = 5
SETTINGS_FILE = Path("guild_settings.json")

DEFAULT_GUILD_SETTINGS: dict = {
    "geral": None,
    "antispam_enabled": True,
    "permanencia_minutos": 10,
    "invite_max_age": 0,    # 0 = nunca expira
    "invite_max_uses": 0,   # 0 = usos ilimitados
}

log = logging.getLogger(__name__)


# =========================================================
# persistência
# =========================================================

def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with SETTINGS_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            log.warning("falha ao carregar %s — usando configuração vazia", SETTINGS_FILE)
    return {}


def _save_settings(data: dict) -> None:
    try:
        with SETTINGS_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        log.exception("falha ao salvar %s", SETTINGS_FILE)


# =========================================================
# cog
# =========================================================

class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # anti-spam: chave = (guild_id, channel_id, user_id)
        self.spam_tracker: defaultdict[tuple[int, int, int], deque[datetime]] = defaultdict(deque)

        # rastreia horário de entrada para checar permanência mínima
        self.join_times: defaultdict[int, dict[int, datetime]] = defaultdict(dict)

        # carrega configurações persistidas
        raw = _load_settings()
        self.guild_settings: defaultdict[int, dict] = defaultdict(
            lambda: dict(DEFAULT_GUILD_SETTINGS)
        )
        for guild_id_str, cfg in raw.items():
            merged = dict(DEFAULT_GUILD_SETTINGS)
            merged.update(cfg)
            self.guild_settings[int(guild_id_str)] = merged

    # =========================================================
    # helpers internos
    # =========================================================

    def _get_me(self, guild: discord.Guild) -> discord.Member | None:
        if guild.me is not None:
            return guild.me
        if self.bot.user is not None:
            return guild.get_member(self.bot.user.id)
        return None

    def _persist(self) -> None:
        _save_settings({
            str(gid): cfg for gid, cfg in self.guild_settings.items()
        })

    def _settings(self, guild_id: int) -> dict:
        return self.guild_settings[guild_id]

    async def _send_to_geral(
        self,
        guild: discord.Guild,
        content: str
    ) -> None:
        channel_id = self._settings(guild.id).get("geral")
        if channel_id is None:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        me = self._get_me(guild)
        if me and not channel.permissions_for(me).send_messages:
            return
        try:
            await channel.send(content)
        except discord.HTTPException:
            log.exception("falha ao enviar mensagem no canal geral guild_id=%d", guild.id)

    # =========================================================
    # task de limpeza do spam_tracker
    # =========================================================

    @tasks.loop(minutes=SPAM_TRACKER_CLEANUP_INTERVAL_MINUTES)
    async def _cleanup_spam_tracker(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=SPAM_WINDOW_SECONDS)
        stale = [k for k, ts in self.spam_tracker.items() if not ts or ts[-1] < cutoff]
        for k in stale:
            self.spam_tracker.pop(k, None)
        if stale:
            log.debug("spam_tracker: %d entradas expiradas removidas", len(stale))

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

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        joined_at = self.join_times[member.guild.id].pop(member.id, None)

        if joined_at is None:
            joined_at = member.joined_at

        if joined_at is None:
            return

        left_at = datetime.now(timezone.utc)
        time_in_guild = left_at - joined_at
        minutos = self._settings(member.guild.id)["permanencia_minutos"]

        if time_in_guild < timedelta(minutes=minutos):
            minutos_reais = round(time_in_guild.total_seconds() / 60, 1)
            log.info(
                "saída antecipada: user_id=%d ficou %.1f min (mínimo=%d) em guild_id=%d",
                member.id, minutos_reais, minutos, member.guild.id
            )
            await self._send_to_geral(
                member.guild,
                f"⚠️ **{discord.utils.escape_markdown(str(member))}** "
                f"entrou e saiu em **{minutos_reais} min** "
                f"(mínimo configurado: {minutos} min)."
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return

        if self._settings(message.guild.id)["antispam_enabled"]:
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

        while timestamps and (now - timestamps[0]).total_seconds() > SPAM_WINDOW_SECONDS:
            timestamps.popleft()

        if len(timestamps) <= SPAM_MESSAGE_LIMIT:
            return

        self.spam_tracker.pop(key, None)
        author = message.author
        channel = message.channel

        log.warning(
            "spam: user_id=%d channel_id=%d guild_id=%d",
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
            await channel.send("Não consegui expulsar o usuário por spam: sem permissão.")
            log.error("sem permissão para kickar user_id=%d", author.id)
            return
        except discord.HTTPException:
            await channel.send("Erro ao expulsar o usuário por spam.")
            log.exception("erro HTTP ao kickar user_id=%d", author.id)
            return

        await channel.send(f"{author.mention} foi expulso(a) automaticamente por spam.")

        try:
            spam_start = now - timedelta(seconds=SPAM_WINDOW_SECONDS + 1)
            await channel.purge(
                limit=SPAM_MESSAGE_LIMIT + 5,
                check=lambda m: m.author.id == author.id,
                after=spam_start,
                bulk=True,
            )
        except (discord.Forbidden, discord.HTTPException):
            log.warning("não foi possível limpar mensagens de spam de user_id=%d", author.id)

    # =========================================================
    # helper de convite
    # =========================================================

    def _find_invite_channel(
        self,
        guild: discord.Guild,
        preferred: discord.abc.GuildChannel | None,
    ) -> discord.TextChannel | None:
        me = self._get_me(guild)
        if me is None:
            return None
        if preferred is not None and preferred.permissions_for(me).create_instant_invite:
            return preferred  # type: ignore[return-value]
        return next(
            (ch for ch in guild.text_channels if ch.permissions_for(me).create_instant_invite),
            None,
        )

    # =========================================================
    # slash commands — canal geral
    # =========================================================

    @app_commands.command(
        name="set_geral",
        description="Define o canal onde o bot posta avisos de moderação",
    )
    @app_commands.describe(canal="Canal que será monitorado")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def set_geral(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
    ) -> None:
        assert interaction.guild is not None
        self._settings(interaction.guild.id)["geral"] = canal.id
        self._persist()
        await interaction.response.send_message(
            f"Canal de avisos definido: {canal.mention}", ephemeral=True
        )

    # =========================================================
    # slash commands — permanência mínima
    # =========================================================

    @app_commands.command(
        name="set_permanencia",
        description="Define o tempo mínimo (em minutos) que um novo membro deve ficar no servidor",
    )
    @app_commands.describe(minutos="Tempo mínimo em minutos (ex: 10)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def set_permanencia(
        self,
        interaction: discord.Interaction,
        minutos: app_commands.Range[int, 1, 10080],  # 1 min até 7 dias
    ) -> None:
        assert interaction.guild is not None
        self._settings(interaction.guild.id)["permanencia_minutos"] = minutos
        self._persist()
        await interaction.response.send_message(
            f"Permanência mínima definida: **{minutos} minuto(s)**.", ephemeral=True
        )

    # =========================================================
    # slash commands — convite
    # =========================================================

    @app_commands.command(
        name="set_convite",
        description="Configura os parâmetros do convite gerado pelo /convite",
    )
    @app_commands.describe(
        max_age="Validade em horas (0 = nunca expira)",
        max_uses="Número máximo de usos (0 = ilimitado)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def set_convite(
        self,
        interaction: discord.Interaction,
        max_age: app_commands.Range[int, 0, 720],   # 0 até 30 dias em horas
        max_uses: app_commands.Range[int, 0, 1000],
    ) -> None:
        assert interaction.guild is not None
        cfg = self._settings(interaction.guild.id)
        cfg["invite_max_age"] = max_age * 3600  # Discord usa segundos
        cfg["invite_max_uses"] = max_uses
        self._persist()

        age_str = f"{max_age}h" if max_age > 0 else "nunca expira"
        uses_str = str(max_uses) if max_uses > 0 else "ilimitado"
        await interaction.response.send_message(
            f"Configuração de convite atualizada:\n"
            f"• Validade: **{age_str}**\n"
            f"• Usos máximos: **{uses_str}**",
            ephemeral=True,
        )

    @app_commands.command(
        name="convite",
        description="Gera um convite privado com as configurações definidas",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def convite(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        cfg = self._settings(interaction.guild.id)

        preferred = (
            interaction.channel
            if isinstance(interaction.channel, discord.abc.GuildChannel)
            else None
        )
        invite_channel = self._find_invite_channel(interaction.guild, preferred)

        if invite_channel is None:
            await interaction.response.send_message(
                "Não encontrei canal com permissão para criar convite.", ephemeral=True
            )
            return

        try:
            invite = await invite_channel.create_invite(
                max_age=cfg["invite_max_age"],
                max_uses=cfg["invite_max_uses"],
                unique=True,
                reason=f"Convite solicitado por {interaction.user}",
            )

            age_val = cfg["invite_max_age"]
            uses_val = cfg["invite_max_uses"]
            age_str = f"{age_val // 3600}h" if age_val > 0 else "permanente"
            uses_str = str(uses_val) if uses_val > 0 else "ilimitado"

            await interaction.response.send_message(
                f"🔗 {invite.url}\n"
                f"-# Validade: {age_str} · Usos: {uses_str}",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Sem permissão para criar convite.", ephemeral=True
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                "Erro na API do Discord ao criar convite.", ephemeral=True
            )

    # =========================================================
    # slash commands — anti-spam (grupo)
    # =========================================================

    antispam_group = app_commands.Group(
        name="antispam",
        description="Gerencia o anti-spam do servidor",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @antispam_group.command(name="toggle", description="Ativa ou desativa o anti-spam")
    @app_commands.checks.has_permissions(administrator=True)
    async def antispam_toggle(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        cfg = self._settings(interaction.guild.id)
        cfg["antispam_enabled"] = not cfg["antispam_enabled"]
        self._persist()

        estado = "✅ ativado" if cfg["antispam_enabled"] else "🔴 desativado"
        await interaction.response.send_message(
            f"Anti-spam **{estado}**.", ephemeral=True
        )

    @antispam_group.command(name="status", description="Mostra o estado atual do anti-spam")
    @app_commands.checks.has_permissions(administrator=True)
    async def antispam_status(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        cfg = self._settings(interaction.guild.id)
        enabled = cfg["antispam_enabled"]

        estado = "✅ Ativo" if enabled else "🔴 Inativo"
        await interaction.response.send_message(
            f"Anti-spam: **{estado}**\n"
            f"-# Limite: {SPAM_MESSAGE_LIMIT} msgs em {SPAM_WINDOW_SECONDS}s → kick",
            ephemeral=True,
        )

    # =========================================================
    # tratamento de erros
    # =========================================================

    @set_geral.error
    @set_permanencia.error
    @set_convite.error
    @convite.error
    async def _command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
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
    cog = ModerationCog(bot)
    await bot.add_cog(cog)
    bot.tree.add_command(cog.antispam_group)