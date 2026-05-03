"""Sistema de Prune por Inatividade Textual — Seguro, Persistente e à Prova de Desastres."""

from __future__ import annotations

import asyncio
import logging
import pathlib
from datetime import datetime, timedelta, timezone

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

LOGGER = logging.getLogger("baphomet.prune")

DATA_DIR = pathlib.Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "activity_tracker.sqlite3"


# ── Camada de Persistência ──────────────────────────────────────────────────
class ActivityRepository:
    """Armazena a última mensagem de cada membro por servidor."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA busy_timeout = 5000")
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS last_activity (
                guild_id   INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                last_seen  TEXT    NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );
        """)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("ActivityRepository não conectado.")
        return self._conn

    async def upsert_activity(self, guild_id: int, user_id: int, timestamp: datetime) -> None:
        """Registra ou atualiza o timestamp da última mensagem do membro."""
        await self.conn.execute(
            """
            INSERT INTO last_activity (guild_id, user_id, last_seen)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                last_seen = excluded.last_seen
            """,
            (guild_id, user_id, timestamp.isoformat()),
        )
        await self.conn.commit()

    async def get_activity(self, guild_id: int, user_id: int) -> datetime | None:
        """Retorna o datetime da última atividade ou None se nunca registrado."""
        rows = await self.conn.execute_fetchall(
            "SELECT last_seen FROM last_activity WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        if not rows:
            return None
        return datetime.fromisoformat(rows[0]["last_seen"])


# ── View de Confirmação ─────────────────────────────────────────────────────
class _ConfirmPruneView(discord.ui.View):
    """Botões de confirmação para a expulsão em massa."""

    def __init__(self, cog: InactivityPruneCog, targets: list[discord.Member], days: int, author_id: int) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.targets = targets
        self.days = days
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Apenas quem iniciou o comando pode usar esses botões.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirmar Expulsão", style=discord.ButtonStyle.danger, custom_id="prune_confirm")
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Desabilita botões imediatamente
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        await self.cog.execute_prune(interaction, self.targets, self.days)
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary, custom_id="prune_cancel")
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="🛑 Operação de Prune cancelada.", view=self)
        self.stop()


# ── O Cog ───────────────────────────────────────────────────────────────────
class InactivityPruneCog(commands.Cog):
    """Rastreia atividade textual e expulsa membros inativos sob demanda."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.repo = ActivityRepository(str(DB_PATH))

    async def cog_load(self) -> None:
        await self.repo.connect()
        LOGGER.info("InactivityPruneCog carregado.")

    async def cog_unload(self) -> None:
        await self.repo.close()

    # ── Rastreio Silencioso ─────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Ignorar bots e DMs
        if message.author.bot:
            return
        if message.guild is None:
            return

        await self.repo.upsert_activity(
            message.guild.id,
            message.author.id,
            message.created_at.replace(tzinfo=timezone.utc) if message.created_at.tzinfo is None else message.created_at,
        )

    # ── Comando Principal ───────────────────────────────────────────────────
    @app_commands.command(
        name="expulsar_inativos",
        description="Expulsa membros que não enviam mensagens há X dias ⚠️",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(dias="Quantidade mínima de dias de inatividade para expulsão")
    async def expulsar_inativos(self, interaction: discord.Interaction, dias: app_commands.Range[int, 1, 365]) -> None:
        # ┌─────────────────────────────────────────────────────────────────┐
        # │  DEFER IMEDIATO: O processamento pode demorar em servidores    │
        # │  grandes. Sem isso, o Discord cancela a interação em 3s.       │
        # └─────────────────────────────────────────────────────────────────┘
        await interaction.response.defer(thinking=True)

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=dias)
        guild = interaction.guild
        bot_member = guild.me
        targets: list[discord.Member] = []

        # ┌─────────────────────────────────────────────────────────────────┐
        # │              LÓGICA DE FILTRAGEM (O CORAÇÃO)                   │
        # │                                                                 │
        # │  Cada membro passa por uma cascata de verificações de          │
        # │  imunidade. Ele só entra na lista de alvos se FALHAR em        │
        # │  todas elas.                                                   │
        # └─────────────────────────────────────────────────────────────────┘
        for member in guild.members:

            # ── IMUNIDADE 1: Bots jamais são expulsos. ──────────────────
            if member.bot:
                continue

            # ── IMUNIDADE 2: Dono do servidor. ──────────────────────────
            if member.id == guild.owner_id:
                continue

            # ── IMUNIDADE 3: Administradores. ───────────────────────────
            if member.guild_permissions.administrator:
                continue

            # ── IMUNIDADE 4: Cargo superior ao do bot. ──────────────────
            # Se o membro tem um cargo mais alto que o bot na hierarquia,
            # o kick falharia com Forbidden de qualquer forma.
            if member.top_role >= bot_member.top_role:
                continue

            # ── DETERMINAÇÃO DA ÚLTIMA ATIVIDADE ────────────────────────
            # Busca no banco de dados. Se o membro nunca enviou uma
            # mensagem desde que o bot começou a rastrear, usamos o
            # `joined_at` como data de referência (Cold Start).
            last_activity = await self.repo.get_activity(guild.id, member.id)

            if last_activity is None:
                # ── COLD START: Membro sem registro no banco. ───────────
                # Usamos a data de entrada como substituto.
                if member.joined_at is None:
                    # Dado corrompido ou indisponível — poupar.
                    continue
                last_activity = member.joined_at.replace(tzinfo=timezone.utc) if member.joined_at.tzinfo is None else member.joined_at

            # ── IMUNIDADE 5: Grace Period para Novatos. ─────────────────
            # Se o membro entrou há MENOS dias do que o filtro exige,
            # ele é poupado mesmo que nunca tenha falado.
            # Exemplo: /expulsar_inativos dias:30
            #   - Membro entrou há 5 dias e nunca falou → POUPADO
            #   - Membro entrou há 45 dias e nunca falou → ALVO
            if member.joined_at:
                joined = member.joined_at.replace(tzinfo=timezone.utc) if member.joined_at.tzinfo is None else member.joined_at
                if joined > cutoff:
                    continue

            # ── VERIFICAÇÃO FINAL: Inatividade confirmada. ──────────────
            if last_activity < cutoff:
                targets.append(member)

        # ── Resultado da análise ────────────────────────────────────────
        if not targets:
            await interaction.followup.send(
                f"✅ Nenhum membro inativo há mais de **{dias} dias** foi encontrado. Seu servidor está ativo!",
            )
            return

        # ── Etapa de Confirmação (Prevenção de Desastres) ───────────────
        view = _ConfirmPruneView(self, targets, dias, interaction.user.id)

        # Montar preview dos primeiros alvos
        preview_lines = [f"`{i}.` **{m.display_name}** (`{m.id}`)" for i, m in enumerate(targets[:15], 1)]
        if len(targets) > 15:
            preview_lines.append(f"_...e mais {len(targets) - 15} membros._")

        preview = "\n".join(preview_lines)

        await interaction.followup.send(
            f"🚨 **ANÁLISE CONCLUÍDA**\n\n"
            f"Encontrei **{len(targets)} membros** que não enviam mensagens há mais de **{dias} dias**.\n\n"
            f"{preview}\n\n"
            f"Deseja prosseguir com a expulsão em massa?",
            view=view,
        )

    # ── Execução Segura do Prune ────────────────────────────────────────
    async def execute_prune(self, interaction: discord.Interaction, targets: list[discord.Member], days: int) -> None:
        """Executa os kicks com rate-limit handling e feedback progressivo."""
        total = len(targets)
        kicked = 0
        failed = 0
        reason = f"Prune por inatividade: sem mensagens há mais de {days} dias."

        # Mensagem de progresso inicial
        progress_msg = await interaction.followup.send(
            f"⏳ Iniciando expulsão de **{total}** membros...\n`[{'░' * 20}]` 0%",
        )

        for i, member in enumerate(targets, 1):
            try:
                await member.kick(reason=reason)
                kicked += 1
            except (discord.Forbidden, discord.HTTPException) as exc:
                LOGGER.warning(f"Falha ao expulsar {member} ({member.id}): {exc}")
                failed += 1

            # ┌─────────────────────────────────────────────────────────────┐
            # │  RATE LIMIT HANDLING (OBRIGATÓRIO)                          │
            # │                                                             │
            # │  O Discord impõe limites de requisições por segundo.        │
            # │  Sem esse delay, o bot toma HTTP 429 (Too Many Requests)    │
            # │  e pode ser temporariamente banido da API.                  │
            # │                                                             │
            # │  1.5s entre kicks é conservador o suficiente para não       │
            # │  disparar o rate limiter mesmo em servidores grandes.       │
            # └─────────────────────────────────────────────────────────────┘
            await asyncio.sleep(1.5)

            # Atualizar progresso a cada 10 expulsões
            if i % 10 == 0 or i == total:
                pct = int((i / total) * 100)
                filled = pct // 5
                bar = f"{'█' * filled}{'░' * (20 - filled)}"
                try:
                    await progress_msg.edit(
                        content=(
                            f"⏳ Progresso: **{i}/{total}** membros processados...\n"
                            f"`[{bar}]` {pct}%\n"
                            f"✅ Expulsos: {kicked} | ❌ Falhas: {failed}"
                        )
                    )
                except discord.HTTPException:
                    pass

        # ── Relatório Final ─────────────────────────────────────────────
        try:
            await progress_msg.edit(
                content=(
                    f"🏁 **Prune Concluído!**\n\n"
                    f"📊 **Resultados:**\n"
                    f"✅ Expulsos com sucesso: **{kicked}**\n"
                    f"❌ Falhas (cargo superior ou erro): **{failed}**\n"
                    f"📅 Critério: inatividade de **{days}+ dias**"
                )
            )
        except discord.HTTPException:
            pass

        LOGGER.info(f"[Guild {interaction.guild.id}] Prune finalizado: {kicked} expulsos, {failed} falhas.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InactivityPruneCog(bot))
