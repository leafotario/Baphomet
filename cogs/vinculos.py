from __future__ import annotations

"""Sistema de vínculos do Baphomet.

Os vínculos vivem no mesmo SQLite usado pelo XP para que o multiplicador seja
calculado a partir da fonte persistida, sem cache de bônus.
"""

import asyncio
import contextlib
import logging
import pathlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands


LOGGER = logging.getLogger("baphomet.vinculos")

DATA_DIR = pathlib.Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "baphomet_xp.sqlite3"

REQUEST_TIMEOUT_SECONDS = 120
REQUEST_COOLDOWN_SECONDS = 15
XP_BONUS_PER_VINCULO = 0.1

VINCULO_COLOR = discord.Color.from_rgb(93, 39, 126)
VINCULO_SUCCESS_COLOR = discord.Color.from_rgb(132, 48, 79)
VINCULO_WARNING_COLOR = discord.Color.from_rgb(173, 113, 38)

RequestCreateStatus = Literal["created", "active_exists", "pending_exists"]
RequestFinishStatus = Literal["accepted", "refused", "expired", "duplicate", "missing", "forbidden"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _normalize_pair(user_a_id: int, user_b_id: int) -> tuple[int, int]:
    return (user_a_id, user_b_id) if user_a_id < user_b_id else (user_b_id, user_a_id)


def _clip_embed_value(value: str, limit: int = 1024) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 18].rstrip()}... (cortado)"


@dataclass(slots=True)
class VinculoRequestCreation:
    status: RequestCreateStatus
    request_id: int | None = None


@dataclass(slots=True)
class VinculosRuntime:
    repository: "VinculoRepository"


class VinculoRepository:
    def __init__(self, db_path: str | pathlib.Path) -> None:
        self.db_path = pathlib.Path(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._tx_lock = asyncio.Lock()

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Repositório de vínculos ainda não foi conectado")
        return self._conn

    async def connect(self) -> None:
        if self._conn is not None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._apply_pragmas()
        await self.run_migrations()
        await self.expire_pending_requests()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _apply_pragmas(self) -> None:
        conn = self.connection
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA synchronous = FULL")
        await conn.execute("PRAGMA busy_timeout = 5000")

    async def run_migrations(self) -> None:
        await self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS vinculo_interest_roles (
                guild_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, role_id)
            );

            CREATE TABLE IF NOT EXISTS vinculos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_low_id INTEGER NOT NULL,
                user_high_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                ended_at TEXT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                CHECK (user_low_id < user_high_id),
                CHECK (active IN (0, 1))
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_vinculos_active_pair
                ON vinculos(guild_id, user_low_id, user_high_id)
                WHERE active = 1;

            CREATE INDEX IF NOT EXISTS idx_vinculos_active_low
                ON vinculos(guild_id, user_low_id, active);

            CREATE INDEX IF NOT EXISTS idx_vinculos_active_high
                ON vinculos(guild_id, user_high_id, active);

            CREATE TABLE IF NOT EXISTS vinculo_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                requester_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                user_low_id INTEGER NOT NULL,
                user_high_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                completed_at TEXT NULL,
                status TEXT NOT NULL,
                CHECK (user_low_id < user_high_id),
                CHECK (status IN ('pending', 'accepted', 'refused', 'expired'))
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_vinculo_pending_pair
                ON vinculo_requests(guild_id, user_low_id, user_high_id)
                WHERE status = 'pending';

            CREATE INDEX IF NOT EXISTS idx_vinculo_requests_guild_status
                ON vinculo_requests(guild_id, status, expires_at);
            """
        )
        await self.connection.commit()

    async def expire_stale_requests(self) -> int:
        now_iso = _utc_now_iso()
        async with self._tx_lock:
            cur = await self.connection.execute(
                """
                UPDATE vinculo_requests
                SET status = 'expired',
                    completed_at = ?
                WHERE status = 'pending'
                  AND expires_at <= ?
                """,
                (now_iso, now_iso),
            )
            await self.connection.commit()
            return cur.rowcount

    async def expire_pending_requests(self) -> int:
        now_iso = _utc_now_iso()
        async with self._tx_lock:
            cur = await self.connection.execute(
                """
                UPDATE vinculo_requests
                SET status = 'expired',
                    completed_at = ?
                WHERE status = 'pending'
                """,
                (now_iso,),
            )
            await self.connection.commit()
            return cur.rowcount

    async def list_interest_role_ids(self, guild_id: int) -> list[int]:
        rows = await self.connection.execute_fetchall(
            """
            SELECT role_id
            FROM vinculo_interest_roles
            WHERE guild_id = ?
            ORDER BY created_at ASC, role_id ASC
            """,
            (guild_id,),
        )
        return [int(row["role_id"]) for row in rows]

    async def add_interest_role(self, guild_id: int, role_id: int) -> bool:
        async with self._tx_lock:
            cur = await self.connection.execute(
                """
                INSERT OR IGNORE INTO vinculo_interest_roles(guild_id, role_id, created_at)
                VALUES(?, ?, ?)
                """,
                (guild_id, role_id, _utc_now_iso()),
            )
            await self.connection.commit()
            return cur.rowcount > 0

    async def remove_interest_role(self, guild_id: int, role_id: int) -> bool:
        async with self._tx_lock:
            cur = await self.connection.execute(
                "DELETE FROM vinculo_interest_roles WHERE guild_id = ? AND role_id = ?",
                (guild_id, role_id),
            )
            await self.connection.commit()
            return cur.rowcount > 0

    async def clear_interest_roles(self, guild_id: int) -> int:
        async with self._tx_lock:
            cur = await self.connection.execute(
                "DELETE FROM vinculo_interest_roles WHERE guild_id = ?",
                (guild_id,),
            )
            await self.connection.commit()
            return cur.rowcount

    async def create_request(
        self,
        *,
        guild_id: int,
        requester_id: int,
        target_id: int,
        expires_at: datetime,
    ) -> VinculoRequestCreation:
        user_low_id, user_high_id = _normalize_pair(requester_id, target_id)
        now_iso = _utc_now_iso()
        expires_iso = expires_at.astimezone(timezone.utc).isoformat()

        async with self._tx_lock:
            conn = self.connection
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    """
                    UPDATE vinculo_requests
                    SET status = 'expired',
                        completed_at = ?
                    WHERE status = 'pending'
                      AND expires_at <= ?
                    """,
                    (now_iso, now_iso),
                )
                active_rows = await conn.execute_fetchall(
                    """
                    SELECT id
                    FROM vinculos
                    WHERE guild_id = ?
                      AND user_low_id = ?
                      AND user_high_id = ?
                      AND active = 1
                    LIMIT 1
                    """,
                    (guild_id, user_low_id, user_high_id),
                )
                if active_rows:
                    await conn.commit()
                    return VinculoRequestCreation(status="active_exists")

                pending_rows = await conn.execute_fetchall(
                    """
                    SELECT id
                    FROM vinculo_requests
                    WHERE guild_id = ?
                      AND user_low_id = ?
                      AND user_high_id = ?
                      AND status = 'pending'
                    LIMIT 1
                    """,
                    (guild_id, user_low_id, user_high_id),
                )
                if pending_rows:
                    await conn.commit()
                    return VinculoRequestCreation(status="pending_exists", request_id=int(pending_rows[0]["id"]))

                cur = await conn.execute(
                    """
                    INSERT INTO vinculo_requests(
                        guild_id,
                        requester_id,
                        target_id,
                        user_low_id,
                        user_high_id,
                        created_at,
                        expires_at,
                        status
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        guild_id,
                        requester_id,
                        target_id,
                        user_low_id,
                        user_high_id,
                        now_iso,
                        expires_iso,
                    ),
                )
                await conn.commit()
                return VinculoRequestCreation(status="created", request_id=int(cur.lastrowid))
            except aiosqlite.IntegrityError:
                await conn.rollback()
                return VinculoRequestCreation(status="pending_exists")
            except Exception:
                await conn.rollback()
                raise

    async def accept_request(self, request_id: int, guild_id: int, target_id: int) -> RequestFinishStatus:
        now_iso = _utc_now_iso()
        async with self._tx_lock:
            conn = self.connection
            await conn.execute("BEGIN IMMEDIATE")
            try:
                rows = await conn.execute_fetchall(
                    "SELECT * FROM vinculo_requests WHERE id = ? AND guild_id = ?",
                    (request_id, guild_id),
                )
                if not rows:
                    await conn.rollback()
                    return "missing"

                request = rows[0]
                if int(request["target_id"]) != target_id:
                    await conn.rollback()
                    return "forbidden"
                if request["status"] != "pending":
                    await conn.rollback()
                    return str(request["status"])  # type: ignore[return-value]
                if str(request["expires_at"]) <= now_iso:
                    await conn.execute(
                        "UPDATE vinculo_requests SET status = 'expired', completed_at = ? WHERE id = ?",
                        (now_iso, request_id),
                    )
                    await conn.commit()
                    return "expired"

                guild_id = int(request["guild_id"])
                user_low_id = int(request["user_low_id"])
                user_high_id = int(request["user_high_id"])
                active_rows = await conn.execute_fetchall(
                    """
                    SELECT id
                    FROM vinculos
                    WHERE guild_id = ?
                      AND user_low_id = ?
                      AND user_high_id = ?
                      AND active = 1
                    LIMIT 1
                    """,
                    (guild_id, user_low_id, user_high_id),
                )
                if active_rows:
                    await conn.execute(
                        "UPDATE vinculo_requests SET status = 'expired', completed_at = ? WHERE id = ?",
                        (now_iso, request_id),
                    )
                    await conn.commit()
                    return "duplicate"

                await conn.execute(
                    """
                    INSERT INTO vinculos(guild_id, user_low_id, user_high_id, created_at, active)
                    VALUES(?, ?, ?, ?, 1)
                    """,
                    (guild_id, user_low_id, user_high_id, now_iso),
                )
                await conn.execute(
                    "UPDATE vinculo_requests SET status = 'accepted', completed_at = ? WHERE id = ?",
                    (now_iso, request_id),
                )
                await conn.commit()
                return "accepted"
            except aiosqlite.IntegrityError:
                await conn.rollback()
                return "duplicate"
            except Exception:
                await conn.rollback()
                raise

    async def refuse_request(self, request_id: int, guild_id: int, target_id: int) -> RequestFinishStatus:
        now_iso = _utc_now_iso()
        async with self._tx_lock:
            conn = self.connection
            await conn.execute("BEGIN IMMEDIATE")
            try:
                rows = await conn.execute_fetchall(
                    "SELECT * FROM vinculo_requests WHERE id = ? AND guild_id = ?",
                    (request_id, guild_id),
                )
                if not rows:
                    await conn.rollback()
                    return "missing"

                request = rows[0]
                if int(request["target_id"]) != target_id:
                    await conn.rollback()
                    return "forbidden"
                if request["status"] != "pending":
                    await conn.rollback()
                    return str(request["status"])  # type: ignore[return-value]

                status = "expired" if str(request["expires_at"]) <= now_iso else "refused"
                await conn.execute(
                    "UPDATE vinculo_requests SET status = ?, completed_at = ? WHERE id = ?",
                    (status, now_iso, request_id),
                )
                await conn.commit()
                return status  # type: ignore[return-value]
            except Exception:
                await conn.rollback()
                raise

    async def expire_request(self, request_id: int) -> bool:
        now_iso = _utc_now_iso()
        async with self._tx_lock:
            cur = await self.connection.execute(
                """
                UPDATE vinculo_requests
                SET status = 'expired',
                    completed_at = ?
                WHERE id = ?
                  AND status = 'pending'
                """,
                (now_iso, request_id),
            )
            await self.connection.commit()
            return cur.rowcount > 0

    async def end_vinculo(self, guild_id: int, user_a_id: int, user_b_id: int) -> bool:
        user_low_id, user_high_id = _normalize_pair(user_a_id, user_b_id)
        async with self._tx_lock:
            cur = await self.connection.execute(
                """
                UPDATE vinculos
                SET active = 0,
                    ended_at = ?
                WHERE guild_id = ?
                  AND user_low_id = ?
                  AND user_high_id = ?
                  AND active = 1
                """,
                (_utc_now_iso(), guild_id, user_low_id, user_high_id),
            )
            await self.connection.commit()
            return cur.rowcount > 0

    async def count_active_vinculos(self, guild_id: int, user_id: int) -> int:
        rows = await self.connection.execute_fetchall(
            """
            SELECT COUNT(*) AS total
            FROM vinculos
            WHERE guild_id = ?
              AND active = 1
              AND (user_low_id = ? OR user_high_id = ?)
            """,
            (guild_id, user_id, user_id),
        )
        return int(rows[0]["total"])

    async def get_xp_multiplier(self, guild_id: int, user_id: int) -> float:
        count = await self.count_active_vinculos(guild_id, user_id)
        return 1.0 + (count * XP_BONUS_PER_VINCULO)

    async def count_active_guild_vinculos(self, guild_id: int) -> int:
        rows = await self.connection.execute_fetchall(
            "SELECT COUNT(*) AS total FROM vinculos WHERE guild_id = ? AND active = 1",
            (guild_id,),
        )
        return int(rows[0]["total"])

    async def count_pending_requests(self, guild_id: int) -> int:
        await self.expire_stale_requests()
        rows = await self.connection.execute_fetchall(
            "SELECT COUNT(*) AS total FROM vinculo_requests WHERE guild_id = ? AND status = 'pending'",
            (guild_id,),
        )
        return int(rows[0]["total"])


class VinculoRequestView(discord.ui.View):
    def __init__(
        self,
        *,
        cog: "VinculosCog",
        request_id: int,
        guild_id: int,
        requester_id: int,
        target_id: int,
        common_role_ids: list[int],
        timeout: float,
    ) -> None:
        super().__init__(timeout=timeout)
        self.cog = cog
        self.request_id = request_id
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.target_id = target_id
        self.common_role_ids = common_role_ids
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.target_id:
            return True
        await interaction.response.send_message(
            "👁️ Marionete intrusa... você não tem permissão para interferir neste pacto.",
            ephemeral=True,
        )
        return False

    def _disable_buttons(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    async def _edit_with_state(self, interaction: discord.Interaction, state: str) -> None:
        self._disable_buttons()
        guild = interaction.guild or self.cog.bot.get_guild(self.guild_id)
        embed = self.cog.build_request_embed(
            guild=guild,
            requester_id=self.requester_id,
            target_id=self.target_id,
            common_role_ids=self.common_role_ids,
            state=state,
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Aceitar vínculo", style=discord.ButtonStyle.success)
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            status = await self.cog.repository.accept_request(self.request_id, self.guild_id, interaction.user.id)
            if status == "accepted":
                await self._edit_with_state(interaction, "accepted")
                return
            if status == "duplicate":
                await interaction.response.send_message(
                    "🔗 Este fio já foi amarrado, criatura efêmera. O pacto duplicado foi consumido pelo abismo.",
                    ephemeral=True,
                )
                await self._edit_message_after_followup("duplicate")
                return
            if status == "expired":
                await self._edit_with_state(interaction, "expired")
                return
            await interaction.response.send_message(
                "⚰️ Este pacto já não está pendente. O destino se moveu sem pedir licença.",
                ephemeral=True,
            )
            await self._edit_message_after_followup("stale")
        except Exception:
            LOGGER.exception("falha ao aceitar vínculo request_id=%s", self.request_id)
            await self.cog.send_interaction_error(interaction)

    @discord.ui.button(label="Recusar vínculo", style=discord.ButtonStyle.danger)
    async def refuse_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            status = await self.cog.repository.refuse_request(self.request_id, self.guild_id, interaction.user.id)
            if status == "refused":
                await self._edit_with_state(interaction, "refused")
                return
            if status == "expired":
                await self._edit_with_state(interaction, "expired")
                return
            await interaction.response.send_message(
                "⚰️ Este pacto já foi decidido. Nem mesmo Baphomet costura o mesmo silêncio duas vezes.",
                ephemeral=True,
            )
            await self._edit_message_after_followup("stale")
        except Exception:
            LOGGER.exception("falha ao recusar vínculo request_id=%s", self.request_id)
            await self.cog.send_interaction_error(interaction)

    async def _edit_message_after_followup(self, state: str) -> None:
        self._disable_buttons()
        guild = self.cog.bot.get_guild(self.guild_id)
        embed = self.cog.build_request_embed(
            guild=guild,
            requester_id=self.requester_id,
            target_id=self.target_id,
            common_role_ids=self.common_role_ids,
            state=state,
        )
        if self.message is not None:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(embed=embed, view=self)
        self.stop()

    async def on_timeout(self) -> None:
        try:
            expired = await self.cog.repository.expire_request(self.request_id)
        except Exception:
            LOGGER.exception("falha ao expirar vínculo request_id=%s", self.request_id)
            return
        if not expired:
            return
        self._disable_buttons()
        guild = self.cog.bot.get_guild(self.guild_id)
        embed = self.cog.build_request_embed(
            guild=guild,
            requester_id=self.requester_id,
            target_id=self.target_id,
            common_role_ids=self.common_role_ids,
            state="expired",
        )
        if self.message is not None:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(embed=embed, view=self)
        self.stop()


class VinculosCog(commands.Cog):
    vinculo = app_commands.Group(name="vinculo", description="Pactos, fios e bônus de XP entre usuários.")
    config = app_commands.Group(name="config", description="Configura cargos que contam como interesses.")

    def __init__(self, bot: commands.Bot, repository: VinculoRepository) -> None:
        self.bot = bot
        self.repository = repository
        self._request_cooldowns: dict[tuple[int, int], datetime] = {}

    async def cog_load(self) -> None:
        await self.repository.connect()
        self.bot.vinculos_runtime = VinculosRuntime(repository=self.repository)

    def cog_unload(self) -> None:
        if getattr(self.bot, "vinculos_runtime", None) is not None:
            self.bot.vinculos_runtime = None
        self.bot.loop.create_task(self.repository.close())

    async def send_interaction_error(self, interaction: discord.Interaction) -> None:
        message = "🩸 Algo rangeu no mecanismo do altar. Tente novamente quando o sangue assentar."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            LOGGER.warning("não foi possível responder erro de vínculo ao usuário")

    def _format_role_ids(self, guild: discord.Guild | None, role_ids: list[int]) -> str:
        if not role_ids:
            return "Nenhum"
        lines: list[str] = []
        for role_id in role_ids:
            role = guild.get_role(role_id) if guild is not None else None
            lines.append(role.mention if role is not None else f"Cargo ausente (`{role_id}`)")
        return _clip_embed_value("\n".join(lines))

    def _common_interest_role_ids(
        self,
        *,
        configured_role_ids: list[int],
        requester: discord.Member,
        target: discord.Member,
    ) -> list[int]:
        requester_roles = {role.id for role in requester.roles}
        target_roles = {role.id for role in target.roles}
        common_ids = [role_id for role_id in configured_role_ids if role_id in requester_roles and role_id in target_roles]
        return sorted(
            common_ids,
            key=lambda role_id: (
                requester.guild.get_role(role_id).position if requester.guild.get_role(role_id) else -1,
                role_id,
            ),
            reverse=True,
        )

    def _check_request_cooldown(self, guild_id: int, user_id: int) -> int | None:
        key = (guild_id, user_id)
        until = self._request_cooldowns.get(key)
        now = _utc_now()
        if until is None or until <= now:
            self._request_cooldowns.pop(key, None)
            return None
        return max(1, int((until - now).total_seconds()) + 1)

    def _start_request_cooldown(self, guild_id: int, user_id: int) -> None:
        self._request_cooldowns[(guild_id, user_id)] = _utc_now() + timedelta(seconds=REQUEST_COOLDOWN_SECONDS)

    def build_request_embed(
        self,
        *,
        guild: discord.Guild | None,
        requester_id: int,
        target_id: int,
        common_role_ids: list[int],
        state: str,
    ) -> discord.Embed:
        requester = f"<@{requester_id}>"
        target = f"<@{target_id}>"
        interests = self._format_role_ids(guild, common_role_ids)

        if state == "pending":
            embed = discord.Embed(
                title="🕯️ Pedido de vínculo",
                description=(
                    f"{requester} ofereceu um pacto a {target}.\n"
                    "Agora resta saber se a outra marionete aceitará dividir o próprio destino."
                ),
                color=VINCULO_COLOR,
            )
            embed.add_field(name="Interesses em comum", value=interests, inline=False)
            embed.add_field(name="Expiração", value=f"{REQUEST_TIMEOUT_SECONDS} segundos", inline=True)
            embed.set_footer(text="Apenas o alvo do pacto pode aceitar ou recusar.")
            return embed

        states = {
            "accepted": (
                "🔗 Vínculo selado",
                f"O vínculo foi selado. {requester} e {target} agora sangram pela mesma linha.",
                VINCULO_SUCCESS_COLOR,
            ),
            "refused": (
                "⚰️ Pacto recusado",
                "O pacto foi recusado. Nem toda alma aceita outra sombra ao lado.",
                VINCULO_WARNING_COLOR,
            ),
            "expired": (
                "⌛ Pedido expirado",
                "O fio apodreceu antes de ser amarrado. O pedido de vínculo expirou.",
                VINCULO_WARNING_COLOR,
            ),
            "duplicate": (
                "🔗 Fio já existente",
                "Este pacto tentou nascer duas vezes, mas o abismo só reconhece um fio ativo.",
                VINCULO_WARNING_COLOR,
            ),
            "stale": (
                "📜 Pacto arquivado",
                "Este pedido já foi decidido. O altar encerrou a cerimônia.",
                VINCULO_WARNING_COLOR,
            ),
        }
        title, description, color = states.get(state, states["stale"])
        embed = discord.Embed(title=title, description=description, color=color)
        embed.add_field(name="Interesses que cruzaram os fios", value=interests, inline=False)
        return embed

    async def _send_embed(self, interaction: discord.Interaction, embed: discord.Embed, *, ephemeral: bool = True) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    async def _send_text(self, interaction: discord.Interaction, content: str, *, ephemeral: bool = True) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)

    @vinculo.command(name="criar", description="Oferece um vínculo a outro usuário.")
    @app_commands.guild_only()
    @app_commands.describe(usuario="Usuário que receberá o pedido de vínculo")
    async def criar(self, interaction: discord.Interaction, usuario: discord.Member) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await self._send_text(interaction, "🕯️ Este ritual só existe dentro de um servidor.")
            return

        requester = interaction.user
        target = usuario
        if requester.id == target.id:
            await self._send_text(
                interaction,
                "🎭 Herege curioso... tentar selar um pacto consigo mesmo é apenas solidão com efeitos especiais.",
            )
            return
        if target.bot:
            await self._send_text(interaction, "🩸 Bonecos de lata não assinam pactos de sangue, criatura.")
            return
        if target.guild.id != interaction.guild.id:
            await self._send_text(interaction, "👁️ Essa alma não pertence a este palco. O vínculo não pode ser iniciado.")
            return

        retry_after = self._check_request_cooldown(interaction.guild.id, requester.id)
        if retry_after is not None:
            await self._send_text(
                interaction,
                f"⌛ Respire, cultista. Aguarde **{retry_after}s** antes de oferecer outro pacto.",
            )
            return

        configured_role_ids = await self.repository.list_interest_role_ids(interaction.guild.id)
        if not configured_role_ids:
            await self._send_text(
                interaction,
                "📜 Nenhum interesse foi inscrito no grimório deste servidor. Um administrador precisa configurar os cargos primeiro.",
            )
            return

        common_role_ids = self._common_interest_role_ids(
            configured_role_ids=configured_role_ids,
            requester=requester,
            target=target,
        )
        if not common_role_ids:
            await self._send_text(
                interaction,
                "🧵 Criatura efêmera, os fios entre vocês sequer se tocaram. Nenhum interesse em comum foi encontrado.",
            )
            return

        creation = await self.repository.create_request(
            guild_id=interaction.guild.id,
            requester_id=requester.id,
            target_id=target.id,
            expires_at=_utc_now() + timedelta(seconds=REQUEST_TIMEOUT_SECONDS),
        )
        if creation.status == "active_exists":
            await self._send_text(interaction, "🔗 Este vínculo já está ativo. O abismo não duplica destinos.")
            return
        if creation.status == "pending_exists" or creation.request_id is None:
            await self._send_text(
                interaction,
                "🕯️ Já existe um pedido pendente entre vocês. Aguarde o fio ser aceito, recusado ou apodrecer.",
            )
            return

        self._start_request_cooldown(interaction.guild.id, requester.id)
        view = VinculoRequestView(
            cog=self,
            request_id=creation.request_id,
            guild_id=interaction.guild.id,
            requester_id=requester.id,
            target_id=target.id,
            common_role_ids=common_role_ids,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        embed = self.build_request_embed(
            guild=interaction.guild,
            requester_id=requester.id,
            target_id=target.id,
            common_role_ids=common_role_ids,
            state="pending",
        )
        await interaction.response.send_message(content=target.mention, embed=embed, view=view)
        view.message = await interaction.original_response()

    @vinculo.command(name="encerrar", description="Corta um vínculo ativo com outro usuário.")
    @app_commands.guild_only()
    @app_commands.describe(usuario="Usuário cujo vínculo será encerrado")
    async def encerrar(self, interaction: discord.Interaction, usuario: discord.Member) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await self._send_text(interaction, "🕯️ Este ritual só existe dentro de um servidor.")
            return
        if interaction.user.id == usuario.id:
            await self._send_text(interaction, "🧵 Cortar o próprio fio não encerra um vínculo, apenas faz cena.")
            return

        ended = await self.repository.end_vinculo(interaction.guild.id, interaction.user.id, usuario.id)
        if not ended:
            await self._send_text(interaction, "👁️ Nenhum vínculo ativo foi encontrado entre vocês. O altar não corta o que não existe.")
            return

        requester_multiplier = await self.repository.get_xp_multiplier(interaction.guild.id, interaction.user.id)
        target_multiplier = await self.repository.get_xp_multiplier(interaction.guild.id, usuario.id)
        embed = discord.Embed(
            title="🩸 Vínculo encerrado",
            description="O fio foi cortado. O vínculo não existe mais, e o abismo recolheu seu bônus.",
            color=VINCULO_SUCCESS_COLOR,
        )
        embed.add_field(
            name="Multiplicadores atuais",
            value=(
                f"{interaction.user.mention}: **{requester_multiplier:.1f}x**\n"
                f"{usuario.mention}: **{target_multiplier:.1f}x**"
            ),
            inline=False,
        )
        await self._send_embed(interaction, embed)

    @config.command(name="adicionar", description="Registra um cargo como interesse de vínculo.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    @app_commands.describe(cargo="Cargo que passará a contar como interesse")
    async def config_adicionar(self, interaction: discord.Interaction, cargo: discord.Role) -> None:
        if interaction.guild is None:
            await self._send_text(interaction, "🕯️ Este ritual só existe dentro de um servidor.")
            return
        if cargo.is_default():
            await self._send_text(interaction, "👁️ O @everyone é o chão do palco, não um interesse do grimório.")
            return
        added = await self.repository.add_interest_role(interaction.guild.id, cargo.id)
        if not added:
            await self._send_text(interaction, "📜 Este cargo já está inscrito no grimório de interesses.")
            return
        await self._send_text(interaction, f"📜 Interesse registrado no grimório: {cargo.mention}.")

    @config.command(name="remover", description="Remove um cargo da lista de interesses.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    @app_commands.describe(cargo="Cargo que deixará de contar como interesse")
    async def config_remover(self, interaction: discord.Interaction, cargo: discord.Role) -> None:
        if interaction.guild is None:
            await self._send_text(interaction, "🕯️ Este ritual só existe dentro de um servidor.")
            return
        removed = await self.repository.remove_interest_role(interaction.guild.id, cargo.id)
        if not removed:
            await self._send_text(interaction, "⚰️ Esse cargo não estava no grimório. Não há tinta para raspar.")
            return
        await self._send_text(interaction, f"🕯️ Interesse removido do grimório: {cargo.mention}.")

    @config.command(name="listar", description="Lista os cargos configurados como interesses.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def config_listar(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_text(interaction, "🕯️ Este ritual só existe dentro de um servidor.")
            return
        role_ids = await self.repository.list_interest_role_ids(interaction.guild.id)
        embed = discord.Embed(title="📜 Grimório de interesses", color=VINCULO_COLOR)
        embed.description = self._format_role_ids(interaction.guild, role_ids) if role_ids else "Nenhum cargo de interesse configurado ainda."
        await self._send_embed(interaction, embed)

    @config.command(name="limpar", description="Remove todos os cargos configurados como interesses.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def config_limpar(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_text(interaction, "🕯️ Este ritual só existe dentro de um servidor.")
            return
        removed = await self.repository.clear_interest_roles(interaction.guild.id)
        await self._send_text(interaction, f"⚰️ O grimório foi limpo. **{removed}** interesse(s) foram apagados deste servidor.")

    vinculo.add_command(config)

    @app_commands.command(name="vinculo_status", description="Mostra o relatório administrativo do altar de vínculos.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def vinculo_status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._send_text(interaction, "🕯️ Este ritual só existe dentro de um servidor.")
            return

        role_ids = await self.repository.list_interest_role_ids(interaction.guild.id)
        active_count = await self.repository.count_active_guild_vinculos(interaction.guild.id)
        pending_count = await self.repository.count_pending_requests(interaction.guild.id)
        xp_runtime = getattr(self.bot, "xp_runtime", None)
        xp_service = getattr(xp_runtime, "service", None)
        xp_provider = getattr(xp_service, "vinculos_provider", None)
        xp_status = "Integrado ao ganho de XP" if xp_provider is not None else "XP ainda não expôs o provedor de vínculos"

        warnings: list[str] = []
        if not role_ids:
            warnings.append("Nenhum cargo de interesse configurado ainda.")
        if role_ids and active_count == 0:
            warnings.append("O sistema está configurado, mas ainda não existem vínculos ativos.")
        if active_count > 0:
            warnings.append("Existem vínculos ativos e o multiplicador será aplicado no ganho de XP.")
        if xp_provider is None:
            warnings.append("A integração com XP não foi detectada neste instante.")

        embed = discord.Embed(
            title="👁️ Relatório do altar de vínculos",
            color=VINCULO_COLOR,
        )
        embed.add_field(name="Interesses configurados", value=str(len(role_ids)), inline=True)
        embed.add_field(name="Vínculos ativos", value=str(active_count), inline=True)
        embed.add_field(name="Pedidos pendentes", value=str(pending_count), inline=True)
        embed.add_field(
            name="Cargos de interesse",
            value=self._format_role_ids(interaction.guild, role_ids) if role_ids else "Nenhum",
            inline=False,
        )
        embed.add_field(
            name="Cálculo do multiplicador",
            value="`1.0 + (quantidade_de_vinculos_ativos * 0.1)`",
            inline=False,
        )
        embed.add_field(name="Integração XP", value=xp_status, inline=False)
        embed.add_field(name="Avisos do altar", value="\n".join(f"• {warning}" for warning in warnings), inline=False)
        await self._send_embed(interaction, embed)

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        original = getattr(error, "original", error)
        if isinstance(original, (app_commands.MissingPermissions, app_commands.CheckFailure)):
            await self._send_text(
                interaction,
                "👁️ Marionete ousada... este altar pertence aos que carregam as chaves do servidor.",
            )
            return
        if isinstance(original, app_commands.NoPrivateMessage):
            await self._send_text(interaction, "🕯️ Este ritual só existe dentro de um servidor.")
            return
        LOGGER.error(
            "erro em comando de vínculos: %s",
            original,
            exc_info=(type(original), original, original.__traceback__) if isinstance(original, BaseException) else None,
        )
        await self.send_interaction_error(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VinculosCog(bot, VinculoRepository(DB_PATH)))
