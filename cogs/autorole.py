from __future__ import annotations

"""Cog de autorole por mensagem para discord.py 2.x.

O arquivo foi desenhado para ser autocontido: banco SQLite assíncrono,
Persistent Views, comandos administrativos e wizard de configuração vivem aqui.
"""

import asyncio
import logging
import pathlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Sequence

import discord
from discord import app_commands
from discord.ext import commands


LOGGER = logging.getLogger("baphomet.autorole")

DATA_DIR = pathlib.Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "autorole.sqlite3"

DEFAULT_COLOR = 0x5865F2
CUSTOM_EMOJI_RE = re.compile(r"^<a?:[A-Za-z0-9_]{2,32}:\d{17,22}>$")

# Self-assign de Administrator/Manage Roles quase sempre é um acidente grave.
# Troque para False se o seu servidor realmente precisar permitir isso.
BLOCK_PRIVILEGED_AUTOROLES = True

AutoroleMode = Literal["unico", "multi", "deci"]
SelectionMode = Literal["single", "multiple"]


class AutoroleUserError(Exception):
    """Erro esperado que pode ser mostrado ao usuário sem detalhes técnicos."""


@dataclass(slots=True)
class AutoroleRoleOption:
    role_id: int
    label: str
    emoji: str | None = None
    description: str | None = None
    order_index: int = 0


@dataclass(slots=True)
class AutoroleConfig:
    guild_id: int
    channel_id: int
    message_id: int
    mode: AutoroleMode
    title: str
    description: str
    color: int
    selection_mode: SelectionMode
    remove_on_click: bool
    created_by: int
    created_at: str
    placeholder: str | None = None


@dataclass(slots=True)
class AutoroleRecord:
    config: AutoroleConfig
    roles: list[AutoroleRoleOption]


@dataclass(slots=True)
class AutoroleListEntry:
    config: AutoroleConfig
    role_count: int


@dataclass(slots=True)
class AutoroleSetupState:
    admin_id: int
    guild_id: int
    channel_id: int
    mode: AutoroleMode
    title: str
    description: str
    color: int
    selection_mode: SelectionMode
    remove_on_click: bool
    min_roles: int
    max_roles: int
    placeholder: str | None = None
    roles: list[AutoroleRoleOption] | None = None

    def __post_init__(self) -> None:
        if self.roles is None:
            self.roles = []


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip_text(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _sanitize_label(value: str | None, fallback: str) -> str:
    label = (value or fallback).strip()
    if not label:
        label = fallback
    return _clip_text(label, 80)


def _sanitize_description(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return _clip_text(value, 100)


def _sanitize_emoji(value: str | None) -> str | None:
    if value is None:
        return None

    value = value.strip()
    if not value:
        return None

    if CUSTOM_EMOJI_RE.fullmatch(value):
        return value

    # Emojis unicode costumam ter poucos codepoints e pelo menos um char nao ASCII.
    if len(value) <= 12 and any(ord(char) > 127 for char in value):
        return value

    raise AutoroleUserError(
        "Emoji inválido. Use um emoji unicode ou um emoji customizado no formato <:nome:id>."
    )


def _component_emoji(value: str | None) -> str | discord.PartialEmoji | None:
    if not value:
        return None
    if CUSTOM_EMOJI_RE.fullmatch(value):
        return discord.PartialEmoji.from_str(value)
    return value


def _parse_color(value: str | None) -> int:
    if not value:
        return DEFAULT_COLOR

    normalized = value.strip().lower()
    named_colors = {
        "azul": 0x5865F2,
        "blurple": 0x5865F2,
        "vermelho": 0xE74C3C,
        "verde": 0x2ECC71,
        "roxo": 0x9B59B6,
        "rosa": 0xEB459E,
        "amarelo": 0xF1C40F,
        "preto": 0x2F3136,
        "branco": 0xFFFFFF,
    }
    if normalized in named_colors:
        return named_colors[normalized]

    normalized = normalized.removeprefix("#").removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{6}", normalized):
        raise AutoroleUserError(
            "Cor inválida. Use hexadecimal como #5865F2 ou nomes como azul, vermelho, verde, roxo, rosa, amarelo, preto ou branco."
        )
    return int(normalized, 16)


def _format_bool(value: bool) -> str:
    return "Sim" if value else "Não"


def _format_selection_mode(value: SelectionMode) -> str:
    return "Apenas um cargo desta mensagem" if value == "single" else "Vários cargos ao mesmo tempo"


class AutoroleDatabase:
    def __init__(self, db_path: pathlib.Path | str) -> None:
        self.db_path = pathlib.Path(db_path)
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        await self.run_migrations()

    async def close(self) -> None:
        return None

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    async def run_migrations(self) -> None:
        def sync_run() -> None:
            conn = self._open_connection()
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = NORMAL")
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS autorole_messages (
                        guild_id INTEGER NOT NULL,
                        channel_id INTEGER NOT NULL,
                        message_id INTEGER PRIMARY KEY,
                        mode TEXT NOT NULL CHECK (mode IN ('unico', 'multi', 'deci')),
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        color INTEGER NOT NULL,
                        selection_mode TEXT NOT NULL CHECK (selection_mode IN ('single', 'multiple')),
                        remove_on_click INTEGER NOT NULL CHECK (remove_on_click IN (0, 1)),
                        created_by INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        placeholder TEXT NULL
                    );

                    CREATE TABLE IF NOT EXISTS autorole_roles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_id INTEGER NOT NULL,
                        role_id INTEGER NOT NULL,
                        label TEXT NOT NULL,
                        emoji TEXT NULL,
                        description TEXT NULL,
                        order_index INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY (message_id)
                            REFERENCES autorole_messages(message_id)
                            ON DELETE CASCADE,
                        UNIQUE (message_id, role_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_autorole_messages_guild
                        ON autorole_messages(guild_id);

                    CREATE INDEX IF NOT EXISTS idx_autorole_roles_message
                        ON autorole_roles(message_id, order_index);
                    """
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(sync_run)

    async def save_autorole(
        self,
        config: AutoroleConfig,
        roles: Sequence[AutoroleRoleOption],
    ) -> None:
        def sync_run() -> None:
            conn = self._open_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO autorole_messages (
                        guild_id, channel_id, message_id, mode, title, description,
                        color, selection_mode, remove_on_click, created_by,
                        created_at, placeholder
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        config.guild_id,
                        config.channel_id,
                        config.message_id,
                        config.mode,
                        config.title,
                        config.description,
                        config.color,
                        config.selection_mode,
                        int(config.remove_on_click),
                        config.created_by,
                        config.created_at,
                        config.placeholder,
                    ),
                )
                conn.execute(
                    "DELETE FROM autorole_roles WHERE message_id = ?",
                    (config.message_id,),
                )
                conn.executemany(
                    """
                    INSERT INTO autorole_roles (
                        message_id, role_id, label, emoji, description, order_index
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            config.message_id,
                            role.role_id,
                            role.label,
                            role.emoji,
                            role.description,
                            index,
                        )
                        for index, role in enumerate(roles)
                    ],
                )
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(sync_run)

    async def get_config(self, message_id: int) -> AutoroleConfig | None:
        def sync_run() -> sqlite3.Row | None:
            conn = self._open_connection()
            try:
                cur = conn.execute(
                    "SELECT * FROM autorole_messages WHERE message_id = ?",
                    (message_id,),
                )
                return cur.fetchone()
            finally:
                conn.close()

        row = await asyncio.to_thread(sync_run)
        return self._config_from_row(row) if row else None

    async def get_roles(self, message_id: int) -> list[AutoroleRoleOption]:
        def sync_run() -> list[sqlite3.Row]:
            conn = self._open_connection()
            try:
                cur = conn.execute(
                    """
                    SELECT role_id, label, emoji, description, order_index
                    FROM autorole_roles
                    WHERE message_id = ?
                    ORDER BY order_index ASC, id ASC
                    """,
                    (message_id,),
                )
                return cur.fetchall()
            finally:
                conn.close()

        rows = await asyncio.to_thread(sync_run)
        return [
            AutoroleRoleOption(
                role_id=int(row["role_id"]),
                label=str(row["label"]),
                emoji=row["emoji"],
                description=row["description"],
                order_index=int(row["order_index"]),
            )
            for row in rows
        ]

    async def get_record(self, message_id: int) -> AutoroleRecord | None:
        config = await self.get_config(message_id)
        if config is None:
            return None
        roles = await self.get_roles(message_id)
        return AutoroleRecord(config=config, roles=roles)

    async def list_records(self) -> list[AutoroleRecord]:
        def sync_run() -> list[sqlite3.Row]:
            conn = self._open_connection()
            try:
                cur = conn.execute("SELECT * FROM autorole_messages ORDER BY created_at ASC")
                return cur.fetchall()
            finally:
                conn.close()

        configs = [self._config_from_row(row) for row in await asyncio.to_thread(sync_run)]
        records: list[AutoroleRecord] = []
        for config in configs:
            records.append(AutoroleRecord(config=config, roles=await self.get_roles(config.message_id)))
        return records

    async def list_guild_entries(self, guild_id: int) -> list[AutoroleListEntry]:
        def sync_run() -> list[sqlite3.Row]:
            conn = self._open_connection()
            try:
                cur = conn.execute(
                    """
                    SELECT m.*, COUNT(r.id) AS role_count
                    FROM autorole_messages AS m
                    LEFT JOIN autorole_roles AS r ON r.message_id = m.message_id
                    WHERE m.guild_id = ?
                    GROUP BY m.message_id
                    ORDER BY m.created_at DESC
                    """,
                    (guild_id,),
                )
                return cur.fetchall()
            finally:
                conn.close()

        rows = await asyncio.to_thread(sync_run)
        return [
            AutoroleListEntry(
                config=self._config_from_row(row),
                role_count=int(row["role_count"]),
            )
            for row in rows
        ]

    async def delete_autorole(self, message_id: int) -> bool:
        def sync_run() -> int:
            conn = self._open_connection()
            try:
                cur = conn.execute(
                    "DELETE FROM autorole_messages WHERE message_id = ?",
                    (message_id,),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

        async with self._lock:
            return await asyncio.to_thread(sync_run) > 0

    async def delete_role_options(self, message_id: int, role_ids: Sequence[int]) -> int:
        if not role_ids:
            return 0

        def sync_run() -> int:
            conn = self._open_connection()
            try:
                total = 0
                for role_id in role_ids:
                    cur = conn.execute(
                        "DELETE FROM autorole_roles WHERE message_id = ? AND role_id = ?",
                        (message_id, role_id),
                    )
                    total += max(cur.rowcount, 0)
                conn.commit()
                return total
            finally:
                conn.close()

        async with self._lock:
            return await asyncio.to_thread(sync_run)

    async def prune_empty_messages(self) -> int:
        def sync_run() -> int:
            conn = self._open_connection()
            try:
                cur = conn.execute(
                    """
                    DELETE FROM autorole_messages
                    WHERE message_id IN (
                        SELECT m.message_id
                        FROM autorole_messages AS m
                        LEFT JOIN autorole_roles AS r ON r.message_id = m.message_id
                        GROUP BY m.message_id
                        HAVING COUNT(r.id) = 0
                    )
                    """
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

        async with self._lock:
            return await asyncio.to_thread(sync_run)

    @staticmethod
    def _config_from_row(row: sqlite3.Row) -> AutoroleConfig:
        return AutoroleConfig(
            guild_id=int(row["guild_id"]),
            channel_id=int(row["channel_id"]),
            message_id=int(row["message_id"]),
            mode=row["mode"],
            title=str(row["title"]),
            description=str(row["description"]),
            color=int(row["color"]),
            selection_mode=row["selection_mode"],
            remove_on_click=bool(row["remove_on_click"]),
            created_by=int(row["created_by"]),
            created_at=str(row["created_at"]),
            placeholder=row["placeholder"],
        )


class AutoroleButton(discord.ui.Button):
    def __init__(
        self,
        cog: AutoroleCog,
        message_id: int,
        option: AutoroleRoleOption,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    ) -> None:
        super().__init__(
            label=_clip_text(option.label, 80),
            emoji=_component_emoji(option.emoji),
            style=style,
            custom_id=f"autorole:button:{message_id}:{option.role_id}",
        )
        self.cog = cog
        self.message_id = message_id
        self.role_id = option.role_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_autorole_interaction(
            interaction,
            message_id=self.message_id,
            selected_role_ids=[self.role_id],
        )


class AutoroleButtonView(discord.ui.View):
    def __init__(
        self,
        cog: AutoroleCog,
        config: AutoroleConfig,
        roles: Sequence[AutoroleRoleOption],
    ) -> None:
        super().__init__(timeout=None)
        style = discord.ButtonStyle.primary if config.mode == "unico" else discord.ButtonStyle.secondary
        for option in roles[:5]:
            self.add_item(AutoroleButton(cog, config.message_id, option, style=style))


class AutoroleSelect(discord.ui.Select):
    def __init__(
        self,
        cog: AutoroleCog,
        config: AutoroleConfig,
        roles: Sequence[AutoroleRoleOption],
    ) -> None:
        options = [
            discord.SelectOption(
                label=_clip_text(role.label, 100),
                value=str(role.role_id),
                description=_sanitize_description(role.description),
                emoji=_component_emoji(role.emoji),
            )
            for role in roles[:25]
        ]
        max_values = 1 if config.selection_mode == "single" else max(1, min(len(options), 15))
        placeholder = config.placeholder or "Escolha seus cargos"
        super().__init__(
            custom_id=f"autorole:select:{config.message_id}",
            placeholder=_clip_text(placeholder, 100),
            min_values=1,
            max_values=max_values,
            options=options,
        )
        self.cog = cog
        self.message_id = config.message_id

    async def callback(self, interaction: discord.Interaction) -> None:
        role_ids = [int(value) for value in self.values]
        await self.cog.handle_autorole_interaction(
            interaction,
            message_id=self.message_id,
            selected_role_ids=role_ids,
        )


class AutoroleSelectView(discord.ui.View):
    def __init__(
        self,
        cog: AutoroleCog,
        config: AutoroleConfig,
        roles: Sequence[AutoroleRoleOption],
    ) -> None:
        super().__init__(timeout=None)
        self.add_item(AutoroleSelect(cog, config, roles))


class AutoroleSetupRoleSelect(discord.ui.RoleSelect):
    def __init__(self, setup_view: AutoroleSetupView) -> None:
        super().__init__(
            placeholder="Selecionar cargo para adicionar ou editar",
            min_values=1,
            max_values=1,
            row=0,
        )
        self.setup_view = setup_view

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.setup_view.ensure_owner(interaction):
            return

        role = self.values[0]
        try:
            await self.setup_view.cog.validate_role_for_setup(interaction, role)
            if (
                role.id not in {option.role_id for option in self.setup_view.state.roles or []}
                and len(self.setup_view.state.roles or []) >= self.setup_view.state.max_roles
            ):
                raise AutoroleUserError(
                    f"Essa configuração aceita no máximo {self.setup_view.state.max_roles} cargos."
                )
        except AutoroleUserError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        existing = self.setup_view.find_option(role.id)
        await interaction.response.send_modal(
            AutoroleRoleDetailsModal(
                setup_view=self.setup_view,
                role=role,
                existing=existing,
            )
        )


class AutoroleSetupRemoveSelect(discord.ui.Select):
    def __init__(self, setup_view: AutoroleSetupView) -> None:
        options = [
            discord.SelectOption(
                label=_clip_text(option.label, 100),
                value=str(option.role_id),
                description=f"Remover {option.label} da configuração",
                emoji=_component_emoji(option.emoji),
            )
            for option in (setup_view.state.roles or [])[:25]
        ]
        super().__init__(
            placeholder="Remover um cargo adicionado",
            min_values=1,
            max_values=1,
            options=options,
            row=1,
        )
        self.setup_view = setup_view

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.setup_view.ensure_owner(interaction):
            return

        role_id = int(self.values[0])
        self.setup_view.state.roles = [
            option for option in (self.setup_view.state.roles or []) if option.role_id != role_id
        ]
        self.setup_view.rebuild_items()
        await interaction.response.edit_message(
            embed=self.setup_view.build_panel_embed(),
            view=self.setup_view,
        )


class AutoroleRoleDetailsModal(discord.ui.Modal):
    def __init__(
        self,
        setup_view: AutoroleSetupView,
        role: discord.Role,
        existing: AutoroleRoleOption | None,
    ) -> None:
        super().__init__(title=f"Configurar {role.name}", timeout=300)
        self.setup_view = setup_view
        self.role = role

        self.label_input = discord.ui.TextInput(
            label="Texto exibido",
            default=(existing.label if existing else role.name)[:80],
            max_length=80,
            required=True,
        )
        self.emoji_input = discord.ui.TextInput(
            label="Emoji opcional",
            default=(existing.emoji if existing and existing.emoji else ""),
            max_length=80,
            required=False,
            placeholder="Ex.: 🔔 ou <:nome:123456789012345678>",
        )
        self.description_input = discord.ui.TextInput(
            label="Descrição curta opcional",
            default=(existing.description if existing and existing.description else ""),
            max_length=100,
            required=False,
            style=discord.TextStyle.short,
            placeholder="Aparece no select menu e na embed",
        )

        self.add_item(self.label_input)
        self.add_item(self.emoji_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.setup_view.ensure_owner(interaction):
            return

        try:
            await self.setup_view.cog.validate_role_for_setup(interaction, self.role)
            emoji = _sanitize_emoji(str(self.emoji_input.value))
            option = AutoroleRoleOption(
                role_id=self.role.id,
                label=_sanitize_label(str(self.label_input.value), self.role.name),
                emoji=emoji,
                description=_sanitize_description(str(self.description_input.value)),
                order_index=len(self.setup_view.state.roles or []),
            )
            self.setup_view.upsert_option(option)
        except AutoroleUserError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.setup_view.rebuild_items()
        if self.setup_view.message is not None:
            try:
                await self.setup_view.message.edit(
                    embed=self.setup_view.build_panel_embed(),
                    view=self.setup_view,
                )
            except discord.HTTPException:
                LOGGER.warning("Falha ao editar painel ephemeral de autorole", exc_info=True)

        await interaction.response.send_message(
            f"Cargo {self.role.mention} salvo no painel de configuração.",
            ephemeral=True,
        )


class AutoroleSetupView(discord.ui.View):
    def __init__(self, cog: AutoroleCog, state: AutoroleSetupState) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.state = state
        self.message: discord.InteractionMessage | None = None
        self.finished = False
        self.rebuild_items()

    async def ensure_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.state.admin_id:
            return True
        await interaction.response.send_message(
            "Apenas quem iniciou esta configuração pode usar este painel.",
            ephemeral=True,
        )
        return False

    def find_option(self, role_id: int) -> AutoroleRoleOption | None:
        for option in self.state.roles or []:
            if option.role_id == role_id:
                return option
        return None

    def upsert_option(self, option: AutoroleRoleOption) -> None:
        roles = self.state.roles or []
        for index, current in enumerate(roles):
            if current.role_id == option.role_id:
                option.order_index = current.order_index
                roles[index] = option
                return
        option.order_index = len(roles)
        roles.append(option)

    def rebuild_items(self) -> None:
        self.clear_items()
        self.add_item(AutoroleSetupRoleSelect(self))
        if self.state.roles:
            self.add_item(AutoroleSetupRemoveSelect(self))

        preview = discord.ui.Button(label="Preview", style=discord.ButtonStyle.secondary, row=2)
        preview.callback = self.preview_callback
        self.add_item(preview)

        publish = discord.ui.Button(label="Publicar", style=discord.ButtonStyle.success, row=2)
        publish.callback = self.publish_callback
        self.add_item(publish)

        clear = discord.ui.Button(label="Limpar", style=discord.ButtonStyle.secondary, row=2)
        clear.callback = self.clear_callback
        self.add_item(clear)

        cancel = discord.ui.Button(label="Cancelar", style=discord.ButtonStyle.danger, row=2)
        cancel.callback = self.cancel_callback
        self.add_item(cancel)

    def disable_all_items(self) -> None:
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True

    def build_panel_embed(self) -> discord.Embed:
        roles = self.state.roles or []
        channel = f"<#{self.state.channel_id}>"
        embed = discord.Embed(
            title=f"Configuração de autorole {self.state.mode}",
            description=(
                f"Canal de publicação: {channel}\n"
                f"Cargos adicionados: {len(roles)}/{self.state.max_roles}\n"
                f"Mínimo necessário: {self.state.min_roles}\n"
                f"Modo: {_format_selection_mode(self.state.selection_mode)}\n"
                f"Remover ao selecionar de novo: {_format_bool(self.state.remove_on_click)}"
            ),
            color=discord.Color(self.state.color),
        )
        if roles:
            lines = []
            for index, option in enumerate(roles, start=1):
                emoji = f"{option.emoji} " if option.emoji else ""
                desc = f" - {option.description}" if option.description else ""
                lines.append(f"{index}. {emoji}**{option.label}** (<@&{option.role_id}>{desc})")
            embed.add_field(name="Cargos", value="\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(
                name="Cargos",
                value="Use o seletor abaixo para adicionar cargos.",
                inline=False,
            )
        embed.set_footer(text="Você pode selecionar um cargo já adicionado para editar label, emoji ou descrição.")
        return embed

    async def preview_callback(self, interaction: discord.Interaction) -> None:
        if not await self.ensure_owner(interaction):
            return
        if not self.state.roles:
            await interaction.response.send_message(
                "Adicione pelo menos um cargo antes de pré-visualizar.",
                ephemeral=True,
            )
            return

        config = self.cog.config_from_setup_state(self.state, message_id=0)
        embed = self.cog.build_autorole_embed(config, self.state.roles or [], preview=True)
        view = self.cog.build_preview_view(config, self.state.roles or [])
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def publish_callback(self, interaction: discord.Interaction) -> None:
        if not await self.ensure_owner(interaction):
            return

        roles = self.state.roles or []
        if not (self.state.min_roles <= len(roles) <= self.state.max_roles):
            await interaction.response.send_message(
                f"Você precisa configurar entre {self.state.min_roles} e {self.state.max_roles} cargos.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = await self.cog.resolve_setup_channel(interaction, self.state.channel_id)
            await self.cog.ensure_setup_permissions(interaction, channel)
            for option in roles:
                role = interaction.guild.get_role(option.role_id) if interaction.guild else None
                if role is None:
                    raise AutoroleUserError(
                        f"O cargo `{option.label}` não existe mais. Remova-o do painel e tente novamente."
                    )
                await self.cog.validate_role_for_setup(interaction, role)

            message = await self.cog.create_autorole_message(
                interaction=interaction,
                channel=channel,
                mode=self.state.mode,
                title=self.state.title,
                description=self.state.description,
                color=self.state.color,
                selection_mode=self.state.selection_mode,
                remove_on_click=self.state.remove_on_click,
                roles=roles,
                placeholder=self.state.placeholder,
            )
        except AutoroleUserError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send(
                "Não tenho permissão para publicar ou editar a mensagem nesse canal.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            LOGGER.warning("Falha HTTP ao publicar autorole", exc_info=True)
            await interaction.followup.send(
                "O Discord recusou a publicação da mensagem. Revise emojis, permissões e tente novamente.",
                ephemeral=True,
            )
            return

        self.finished = True
        self.disable_all_items()
        if self.message is not None:
            try:
                await self.message.edit(embed=self.build_panel_embed(), view=self)
            except discord.HTTPException:
                LOGGER.warning("Falha ao finalizar painel ephemeral de autorole", exc_info=True)
        self.stop()
        await interaction.followup.send(
            f"Autorole publicado em {channel.mention}. ID da mensagem: `{message.id}`",
            ephemeral=True,
        )

    async def clear_callback(self, interaction: discord.Interaction) -> None:
        if not await self.ensure_owner(interaction):
            return
        self.state.roles = []
        self.rebuild_items()
        await interaction.response.edit_message(embed=self.build_panel_embed(), view=self)

    async def cancel_callback(self, interaction: discord.Interaction) -> None:
        if not await self.ensure_owner(interaction):
            return
        self.finished = True
        self.disable_all_items()
        await interaction.response.edit_message(
            content="Configuração de autorole cancelada.",
            embed=None,
            view=self,
        )
        self.stop()

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self.disable_all_items()
        if self.message is not None:
            try:
                await self.message.edit(content="Painel de autorole expirado.", embed=None, view=self)
            except discord.HTTPException:
                LOGGER.warning("Falha ao expirar painel ephemeral de autorole", exc_info=True)


class AutoroleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = AutoroleDatabase(DB_PATH)
        self._registered_views: set[int] = set()

    async def initialize(self) -> None:
        await self.db.connect()
        loaded = await self.load_persistent_views()
        LOGGER.info("Autorole carregou %s Persistent Views", loaded)

    def cog_unload(self) -> None:
        self.bot.loop.create_task(self.db.close())

    async def load_persistent_views(self) -> int:
        loaded = 0
        records = await self.db.list_records()
        for record in records:
            if not record.roles:
                await self.db.delete_autorole(record.config.message_id)
                continue
            if record.config.message_id in self._registered_views:
                continue
            try:
                view = self.build_persistent_view(record.config, record.roles)
                self.bot.add_view(view, message_id=record.config.message_id)
                self._registered_views.add(record.config.message_id)
                loaded += 1
            except Exception:
                LOGGER.warning(
                    "Falha ao registrar view persistente de autorole %s",
                    record.config.message_id,
                    exc_info=True,
                )
        return loaded

    def build_persistent_view(
        self,
        config: AutoroleConfig,
        roles: Sequence[AutoroleRoleOption],
    ) -> discord.ui.View:
        if config.mode == "deci" or len(roles) > 5:
            return AutoroleSelectView(self, config, roles)
        return AutoroleButtonView(self, config, roles)

    def build_preview_view(
        self,
        config: AutoroleConfig,
        roles: Sequence[AutoroleRoleOption],
    ) -> discord.ui.View:
        view = discord.ui.View(timeout=60)
        if config.mode == "deci" or len(roles) > 5:
            select = AutoroleSelect(self, config, roles)
            select.disabled = True
            view.add_item(select)
            return view

        style = discord.ButtonStyle.primary if config.mode == "unico" else discord.ButtonStyle.secondary
        for option in roles[:5]:
            button = discord.ui.Button(
                label=_clip_text(option.label, 80),
                emoji=_component_emoji(option.emoji),
                style=style,
                disabled=True,
            )
            view.add_item(button)
        return view

    def config_from_setup_state(self, state: AutoroleSetupState, message_id: int) -> AutoroleConfig:
        return AutoroleConfig(
            guild_id=state.guild_id,
            channel_id=state.channel_id,
            message_id=message_id,
            mode=state.mode,
            title=state.title,
            description=state.description,
            color=state.color,
            selection_mode=state.selection_mode,
            remove_on_click=state.remove_on_click,
            created_by=state.admin_id,
            created_at=_utc_now_iso(),
            placeholder=state.placeholder,
        )

    def build_autorole_embed(
        self,
        config: AutoroleConfig,
        roles: Sequence[AutoroleRoleOption],
        *,
        preview: bool = False,
    ) -> discord.Embed:
        title = config.title or "Autorole"
        description = config.description or "Use os componentes abaixo para gerenciar seus cargos."
        if preview:
            title = f"[Preview] {title}"

        embed = discord.Embed(
            title=_clip_text(title, 256),
            description=description[:4096],
            color=discord.Color(config.color),
        )

        if config.mode == "unico" and roles:
            option = roles[0]
            emoji = f"{option.emoji} " if option.emoji else ""
            extra = f"\n{option.description}" if option.description else ""
            embed.add_field(
                name="Cargo disponível",
                value=f"{emoji}<@&{option.role_id}>{extra}",
                inline=False,
            )
        elif roles:
            lines = []
            for index, option in enumerate(roles, start=1):
                emoji = f"{option.emoji} " if option.emoji else ""
                desc = f" - {option.description}" if option.description else ""
                lines.append(f"{index}. {emoji}**{option.label}**: <@&{option.role_id}>{desc}")
            embed.add_field(name="Cargos disponíveis", value="\n".join(lines)[:1024], inline=False)

        embed.add_field(name="Modo", value=_format_selection_mode(config.selection_mode), inline=True)
        embed.add_field(
            name="Remove ao repetir",
            value=_format_bool(config.remove_on_click),
            inline=True,
        )
        embed.set_footer(text="Clique abaixo para gerenciar seus cargos.")
        return embed

    async def resolve_setup_channel(
        self,
        interaction: discord.Interaction,
        channel_id: int,
    ) -> discord.TextChannel:
        if interaction.guild is None:
            raise AutoroleUserError("Esse comando só pode ser usado em servidor.")
        channel = interaction.guild.get_channel(channel_id)
        if channel is None:
            fetched = await self.bot.fetch_channel(channel_id)
            channel = fetched if isinstance(fetched, discord.TextChannel) else None
        if not isinstance(channel, discord.TextChannel):
            raise AutoroleUserError("O canal escolhido não existe mais ou não é um canal de texto.")
        return channel

    async def ensure_setup_permissions(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> discord.Member:
        if interaction.guild is None:
            raise AutoroleUserError("Esse comando só pode ser usado em servidor.")

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None:
            member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            member = await interaction.guild.fetch_member(interaction.user.id)

        if not (member.guild_permissions.manage_roles or member.guild_permissions.administrator):
            raise AutoroleUserError("Você precisa de Gerenciar Cargos ou Administrador para usar autorole.")

        me = interaction.guild.me
        if me is None:
            me = interaction.guild.get_member(self.bot.user.id) if self.bot.user else None
        if me is None:
            raise AutoroleUserError("Não consegui validar minhas permissões neste servidor.")
        if not me.guild_permissions.manage_roles:
            raise AutoroleUserError("Eu preciso da permissão Gerenciar Cargos para usar autorole.")

        if channel is not None:
            permissions = channel.permissions_for(me)
            if not permissions.view_channel or not permissions.send_messages or not permissions.embed_links:
                raise AutoroleUserError(
                    "Eu preciso ver o canal, enviar mensagens e incorporar links no canal escolhido."
                )
        return member

    async def validate_role_for_setup(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        if interaction.guild is None:
            raise AutoroleUserError("Esse comando só pode ser usado em servidor.")

        admin = await self.ensure_setup_permissions(interaction)
        me = interaction.guild.me
        if me is None:
            raise AutoroleUserError("Não consegui validar minha hierarquia de cargos.")

        if role == interaction.guild.default_role:
            raise AutoroleUserError("Não é possível configurar @everyone como autorole.")
        if role.managed:
            raise AutoroleUserError("Não é possível usar cargos gerenciados por integrações ou bots.")
        if role >= me.top_role:
            raise AutoroleUserError(
                f"Meu cargo mais alto precisa estar acima de {role.mention} para eu gerenciá-lo."
            )
        if interaction.guild.owner_id != admin.id and role >= admin.top_role:
            raise AutoroleUserError(
                f"Você só pode configurar cargos abaixo do seu maior cargo. O cargo {role.mention} está alto demais."
            )
        if BLOCK_PRIVILEGED_AUTOROLES and (
            role.permissions.administrator or role.permissions.manage_roles
        ):
            raise AutoroleUserError(
                "Por segurança, cargos com Administrador ou Gerenciar Cargos não podem ser autorole."
            )

    async def create_autorole_message(
        self,
        *,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        mode: AutoroleMode,
        title: str,
        description: str,
        color: int,
        selection_mode: SelectionMode,
        remove_on_click: bool,
        roles: Sequence[AutoroleRoleOption],
        placeholder: str | None = None,
    ) -> discord.Message:
        if interaction.guild is None:
            raise AutoroleUserError("Esse comando só pode ser usado em servidor.")

        temp_config = AutoroleConfig(
            guild_id=interaction.guild.id,
            channel_id=channel.id,
            message_id=0,
            mode=mode,
            title=title,
            description=description,
            color=color,
            selection_mode=selection_mode,
            remove_on_click=remove_on_click,
            created_by=interaction.user.id,
            created_at=_utc_now_iso(),
            placeholder=placeholder,
        )
        message = await channel.send(embed=self.build_autorole_embed(temp_config, roles))

        config = AutoroleConfig(
            guild_id=interaction.guild.id,
            channel_id=channel.id,
            message_id=message.id,
            mode=mode,
            title=title,
            description=description,
            color=color,
            selection_mode=selection_mode,
            remove_on_click=remove_on_click,
            created_by=interaction.user.id,
            created_at=_utc_now_iso(),
            placeholder=placeholder,
        )
        await self.db.save_autorole(config, roles)
        view = self.build_persistent_view(config, roles)
        await message.edit(embed=self.build_autorole_embed(config, roles), view=view)
        self.bot.add_view(view, message_id=message.id)
        self._registered_views.add(message.id)
        return message

    async def handle_autorole_interaction(
        self,
        interaction: discord.Interaction,
        *,
        message_id: int,
        selected_role_ids: Sequence[int],
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message = await self.apply_role_changes(interaction, message_id, selected_role_ids)
        except AutoroleUserError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send(
                "Não consegui alterar seus cargos por falta de permissão ou hierarquia.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            LOGGER.warning("Falha HTTP ao alternar autorole", exc_info=True)
            await interaction.followup.send(
                "O Discord recusou a alteração de cargo. Tente novamente em alguns segundos.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(message, ephemeral=True)

    async def apply_role_changes(
        self,
        interaction: discord.Interaction,
        message_id: int,
        selected_role_ids: Sequence[int],
    ) -> str:
        if interaction.guild is None:
            raise AutoroleUserError("Essa interação só funciona dentro de um servidor.")

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None:
            member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            member = await interaction.guild.fetch_member(interaction.user.id)

        record = await self.db.get_record(message_id)
        if record is None or not record.roles:
            raise AutoroleUserError(
                "Essa configuração de autorole não existe mais. Peça para um admin recriá-la."
            )
        if record.config.guild_id != interaction.guild.id:
            raise AutoroleUserError("Essa configuração pertence a outro servidor.")

        configured_ids = {option.role_id for option in record.roles}
        selected_ids = [role_id for role_id in selected_role_ids if role_id in configured_ids]
        if not selected_ids:
            raise AutoroleUserError("Esse cargo não faz mais parte desta configuração.")

        me = interaction.guild.me
        if me is None:
            raise AutoroleUserError("Não consegui validar minhas permissões neste servidor.")
        if not me.guild_permissions.manage_roles:
            raise AutoroleUserError("Eu preciso da permissão Gerenciar Cargos para alterar autoroles.")

        guild_roles: dict[int, discord.Role] = {}
        missing_role_ids: list[int] = []
        for option in record.roles:
            role = interaction.guild.get_role(option.role_id)
            if role is None:
                missing_role_ids.append(option.role_id)
            else:
                guild_roles[option.role_id] = role

        if missing_role_ids:
            await self.db.delete_role_options(message_id, missing_role_ids)
            await self.refresh_autorole_message(record.config.message_id)

        manageable_selected: list[discord.Role] = []
        for role_id in selected_ids:
            role = guild_roles.get(role_id)
            if role is None:
                continue
            self.validate_role_for_runtime(me, role)
            manageable_selected.append(role)

        if not manageable_selected:
            raise AutoroleUserError(
                "Esse cargo não existe mais ou minha hierarquia está abaixo dele. Peça para um admin revisar a mensagem."
            )

        if record.config.selection_mode == "single":
            return await self.apply_single_mode(member, me, record, guild_roles, manageable_selected[0])
        return await self.apply_multiple_mode(member, record, manageable_selected)

    def validate_role_for_runtime(self, me: discord.Member, role: discord.Role) -> None:
        if role == role.guild.default_role:
            raise AutoroleUserError("Não é possível gerenciar @everyone.")
        if role.managed:
            raise AutoroleUserError(f"O cargo {role.mention} é gerenciado por integração e não pode ser usado.")
        if role >= me.top_role:
            raise AutoroleUserError(
                f"Não consigo gerenciar {role.mention} porque minha hierarquia está abaixo."
            )
        if BLOCK_PRIVILEGED_AUTOROLES and (
            role.permissions.administrator or role.permissions.manage_roles
        ):
            raise AutoroleUserError(
                f"O cargo {role.mention} tem permissões sensíveis e não pode ser usado como autorole."
            )

    async def apply_single_mode(
        self,
        member: discord.Member,
        me: discord.Member,
        record: AutoroleRecord,
        guild_roles: dict[int, discord.Role],
        target_role: discord.Role,
    ) -> str:
        current_role_ids = {role.id for role in member.roles}
        had_target = target_role.id in current_role_ids

        other_roles = [
            role
            for role_id, role in guild_roles.items()
            if role_id != target_role.id and role_id in current_role_ids
        ]
        for role in other_roles:
            self.validate_role_for_runtime(me, role)

        reason = f"Autorole single na mensagem {record.config.message_id}"
        if had_target and record.config.remove_on_click:
            roles_to_remove = other_roles + [target_role]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason=reason)
            return f"Cargo {target_role.mention} removido com sucesso."

        if other_roles:
            await member.remove_roles(*other_roles, reason=reason)

        if had_target:
            return f"Você já possui {target_role.mention}. Outros cargos desta mensagem foram removidos."

        await member.add_roles(target_role, reason=reason)
        return f"Cargo {target_role.mention} adicionado com sucesso."

    async def apply_multiple_mode(
        self,
        member: discord.Member,
        record: AutoroleRecord,
        selected_roles: Sequence[discord.Role],
    ) -> str:
        # Em menus multi-select, o Discord envia apenas as opções escolhidas
        # naquela interação. Por isso, opções não selecionadas são ignoradas:
        # cada opção selecionada funciona como toggle quando remove_on_click=True.
        current_role_ids = {role.id for role in member.roles}
        to_add = [role for role in selected_roles if role.id not in current_role_ids]
        already = [role for role in selected_roles if role.id in current_role_ids]
        to_remove = already if record.config.remove_on_click else []

        reason = f"Autorole multiple na mensagem {record.config.message_id}"
        if to_remove:
            await member.remove_roles(*to_remove, reason=reason)
        if to_add:
            await member.add_roles(*to_add, reason=reason)

        parts: list[str] = []
        if to_add:
            parts.append("Adicionados: " + ", ".join(role.mention for role in to_add))
        if to_remove:
            parts.append("Removidos: " + ", ".join(role.mention for role in to_remove))
        if already and not record.config.remove_on_click:
            parts.append("Você já possui: " + ", ".join(role.mention for role in already))
        if not parts:
            return "Nenhuma alteração foi necessária."
        return "\n".join(parts)

    async def refresh_autorole_message(self, message_id: int) -> None:
        record = await self.db.get_record(message_id)
        if record is None:
            return
        if not record.roles:
            await self.db.delete_autorole(message_id)
            return

        guild = self.bot.get_guild(record.config.guild_id)
        channel = guild.get_channel(record.config.channel_id) if guild else None
        if channel is None:
            channel = self.bot.get_channel(record.config.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(message_id)
            await message.edit(
                embed=self.build_autorole_embed(record.config, record.roles),
                view=self.build_persistent_view(record.config, record.roles),
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            LOGGER.info("Não foi possível atualizar autorole %s", message_id, exc_info=True)

    async def _interaction_channel_or_default(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None,
    ) -> discord.TextChannel:
        if channel is not None:
            return channel
        if isinstance(interaction.channel, discord.TextChannel):
            return interaction.channel
        raise AutoroleUserError("Informe um canal de texto para publicar o autorole.")

    async def _send_error(self, interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name="autorole_unico",
        description="Cria uma mensagem de autorole com um único cargo.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(
        cargo="Cargo que o usuário receberá/removerá",
        canal="Canal onde a mensagem será publicada",
        titulo="Título da embed",
        descricao="Descrição da embed",
        texto_botao="Texto do botão",
        emoji="Emoji opcional do botão",
        cor="Cor hexadecimal (#5865F2) ou nome simples",
        remover_ao_clicar="Se clicar novamente remove o cargo",
    )
    async def autorole_unico(
        self,
        interaction: discord.Interaction,
        cargo: discord.Role,
        canal: discord.TextChannel | None = None,
        titulo: str | None = None,
        descricao: str | None = None,
        texto_botao: str | None = None,
        emoji: str | None = None,
        cor: str | None = None,
        remover_ao_clicar: bool = True,
    ) -> None:
        try:
            channel = await self._interaction_channel_or_default(interaction, canal)
            await self.ensure_setup_permissions(interaction, channel)
            await self.validate_role_for_setup(interaction, cargo)
            color = _parse_color(cor)
            clean_emoji = _sanitize_emoji(emoji)
            option = AutoroleRoleOption(
                role_id=cargo.id,
                label=_sanitize_label(texto_botao, cargo.name),
                emoji=clean_emoji,
                description=None,
                order_index=0,
            )
        except AutoroleUserError as exc:
            await self._send_error(interaction, str(exc))
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message = await self.create_autorole_message(
                interaction=interaction,
                channel=channel,
                mode="unico",
                title=titulo or f"Receber cargo: {cargo.name}",
                description=descricao or f"Clique no botão abaixo para receber ou gerenciar {cargo.mention}.",
                color=color,
                selection_mode="single",
                remove_on_click=remover_ao_clicar,
                roles=[option],
            )
        except AutoroleUserError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send(
                "Não tenho permissão para enviar ou editar mensagens nesse canal.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            LOGGER.warning("Falha ao criar autorole único", exc_info=True)
            await interaction.followup.send(
                "Não consegui criar a mensagem. Verifique permissões, emoji e tente novamente.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Autorole único criado em {channel.mention}. ID da mensagem: `{message.id}`",
            ephemeral=True,
        )

    @app_commands.command(
        name="autorole_multi",
        description="Abre um wizard para criar autorole com 2 a 5 cargos.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(
        canal="Canal onde a mensagem será publicada",
        titulo="Título da embed",
        descricao="Descrição da embed",
        modo="single = um cargo por vez; multiple = acumular cargos",
        remover_ao_clicar="Selecionar/clicar de novo remove o cargo",
        cor="Cor hexadecimal (#5865F2) ou nome simples",
    )
    @app_commands.choices(
        modo=[
            app_commands.Choice(name="single", value="single"),
            app_commands.Choice(name="multiple", value="multiple"),
        ]
    )
    async def autorole_multi(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel | None = None,
        titulo: str | None = None,
        descricao: str | None = None,
        modo: app_commands.Choice[str] | None = None,
        remover_ao_clicar: bool = True,
        cor: str | None = None,
    ) -> None:
        await self.start_setup_wizard(
            interaction=interaction,
            mode="multi",
            min_roles=2,
            max_roles=5,
            channel=canal,
            title=titulo or "Escolha seus cargos",
            description=descricao or "Clique nos botões abaixo para gerenciar os cargos desejados.",
            selection_mode=(modo.value if modo else "multiple"),
            remove_on_click=remover_ao_clicar,
            color_value=cor,
            placeholder=None,
        )

    @app_commands.command(
        name="autorole_deci",
        description="Abre um wizard para criar autorole com 6 a 15 cargos usando select menu.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(
        canal="Canal onde a mensagem será publicada",
        titulo="Título da embed",
        descricao="Descrição da embed",
        modo="single = um cargo por vez; multiple = acumular cargos",
        remover_ao_clicar="Selecionar de novo remove o cargo",
        cor="Cor hexadecimal (#5865F2) ou nome simples",
        placeholder="Texto exibido no select menu",
    )
    @app_commands.choices(
        modo=[
            app_commands.Choice(name="single", value="single"),
            app_commands.Choice(name="multiple", value="multiple"),
        ]
    )
    async def autorole_deci(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel | None = None,
        titulo: str | None = None,
        descricao: str | None = None,
        modo: app_commands.Choice[str] | None = None,
        remover_ao_clicar: bool = True,
        cor: str | None = None,
        placeholder: str | None = None,
    ) -> None:
        await self.start_setup_wizard(
            interaction=interaction,
            mode="deci",
            min_roles=6,
            max_roles=15,
            channel=canal,
            title=titulo or "Escolha seus cargos",
            description=descricao or "Use o menu abaixo para selecionar os cargos desejados.",
            selection_mode=(modo.value if modo else "multiple"),
            remove_on_click=remover_ao_clicar,
            color_value=cor,
            placeholder=placeholder or "Selecione um ou mais cargos",
        )

    async def start_setup_wizard(
        self,
        *,
        interaction: discord.Interaction,
        mode: AutoroleMode,
        min_roles: int,
        max_roles: int,
        channel: discord.TextChannel | None,
        title: str,
        description: str,
        selection_mode: str,
        remove_on_click: bool,
        color_value: str | None,
        placeholder: str | None,
    ) -> None:
        try:
            target_channel = await self._interaction_channel_or_default(interaction, channel)
            await self.ensure_setup_permissions(interaction, target_channel)
            color = _parse_color(color_value)
            if selection_mode not in {"single", "multiple"}:
                raise AutoroleUserError("Modo inválido. Use single ou multiple.")
        except AutoroleUserError as exc:
            await self._send_error(interaction, str(exc))
            return

        state = AutoroleSetupState(
            admin_id=interaction.user.id,
            guild_id=interaction.guild_id or 0,
            channel_id=target_channel.id,
            mode=mode,
            title=title,
            description=description,
            color=color,
            selection_mode=selection_mode,  # type: ignore[arg-type]
            remove_on_click=remove_on_click,
            min_roles=min_roles,
            max_roles=max_roles,
            placeholder=placeholder,
        )
        view = AutoroleSetupView(self, state)
        await interaction.response.send_message(
            embed=view.build_panel_embed(),
            view=view,
            ephemeral=True,
        )
        view.message = await interaction.original_response()

    @app_commands.command(
        name="autorole_remover",
        description="Remove uma configuração de autorole pelo ID da mensagem.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(
        mensagem_id="ID da mensagem de autorole",
        apagar_mensagem="Também apagar a mensagem do canal, se possível",
    )
    async def autorole_remover(
        self,
        interaction: discord.Interaction,
        mensagem_id: str,
        apagar_mensagem: bool = False,
    ) -> None:
        try:
            message_id = int(mensagem_id.strip())
        except ValueError:
            await self._send_error(interaction, "ID de mensagem inválido.")
            return

        try:
            await self.ensure_setup_permissions(interaction)
        except AutoroleUserError as exc:
            await self._send_error(interaction, str(exc))
            return

        record = await self.db.get_record(message_id)
        if record is None or record.config.guild_id != interaction.guild_id:
            await self._send_error(interaction, "Não encontrei autorole com esse ID neste servidor.")
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        deleted_message = False
        if apagar_mensagem:
            channel = interaction.guild.get_channel(record.config.channel_id) if interaction.guild else None
            if isinstance(channel, discord.TextChannel):
                try:
                    message = await channel.fetch_message(message_id)
                    await message.delete()
                    deleted_message = True
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    deleted_message = False
        else:
            await self.try_disable_autorole_message(record.config)

        await self.db.delete_autorole(message_id)
        self._registered_views.discard(message_id)
        suffix = " Mensagem apagada." if deleted_message else ""
        await interaction.followup.send(f"Configuração `{message_id}` removida do banco.{suffix}", ephemeral=True)

    @app_commands.command(
        name="autorole_listar",
        description="Lista as mensagens de autorole configuradas neste servidor.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    async def autorole_listar(self, interaction: discord.Interaction) -> None:
        try:
            await self.ensure_setup_permissions(interaction)
        except AutoroleUserError as exc:
            await self._send_error(interaction, str(exc))
            return

        entries = await self.db.list_guild_entries(interaction.guild_id or 0)
        if not entries:
            await interaction.response.send_message(
                "Nenhum autorole configurado neste servidor.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Autoroles configurados",
            color=discord.Color(DEFAULT_COLOR),
        )
        lines = []
        for entry in entries[:20]:
            channel = f"<#{entry.config.channel_id}>"
            lines.append(
                f"`{entry.config.message_id}` | {channel} | {entry.config.mode} | "
                f"{entry.config.selection_mode} | {entry.role_count} cargos"
            )
        if len(entries) > 20:
            lines.append(f"... e mais {len(entries) - 20} configurações.")
        embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="autorole_recarregar",
        description="Recarrega manualmente as Persistent Views de autorole.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    async def autorole_recarregar(self, interaction: discord.Interaction) -> None:
        try:
            await self.ensure_setup_permissions(interaction)
        except AutoroleUserError as exc:
            await self._send_error(interaction, str(exc))
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        loaded = await self.load_persistent_views()
        pruned = await self.db.prune_empty_messages()
        await interaction.followup.send(
            f"Persistent Views registradas: {loaded}. Registros vazios limpos: {pruned}.",
            ephemeral=True,
        )

    async def try_disable_autorole_message(self, config: AutoroleConfig) -> None:
        guild = self.bot.get_guild(config.guild_id)
        channel = guild.get_channel(config.channel_id) if guild else self.bot.get_channel(config.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(config.message_id)
            embed = message.embeds[0] if message.embeds else self.build_autorole_embed(config, [])
            embed.set_footer(text="Esta mensagem de autorole foi desativada.")
            await message.edit(embed=embed, view=None)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            LOGGER.info("Não foi possível desativar mensagem de autorole %s", config.message_id, exc_info=True)


async def setup(bot: commands.Bot) -> None:
    cog = AutoroleCog(bot)
    await cog.initialize()
    await bot.add_cog(cog)
