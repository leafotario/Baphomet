from __future__ import annotations

import sqlite3
from typing import Any

from .database import DatabaseManager
from .migrations import DEFAULT_STYLE_JSON, DEFAULT_TIERS_JSON
from .models import (
    TemplateItemType,
    TemplateVisibility,
    TierTemplate,
    TierTemplateItem,
    TierTemplateVersion,
    utc_now_iso,
)
from .repository_utils import (
    coerce_item_type,
    coerce_visibility,
    fetch_one,
    load_json_dict,
    metadata_to_json,
    new_uuid,
    normalize_optional_text,
    normalize_slug,
)


def _template_from_row(row: Any | None) -> TierTemplate | None:
    if row is None:
        return None
    return TierTemplate(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        description=row["description"],
        creator_id=int(row["creator_id"]),
        guild_id=row["guild_id"],
        visibility=TemplateVisibility(row["visibility"]),
        current_version_id=row["current_version_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
    )


def _version_from_row(row: Any | None) -> TierTemplateVersion | None:
    if row is None:
        return None
    return TierTemplateVersion(
        id=row["id"],
        template_id=row["template_id"],
        version_number=int(row["version_number"]),
        default_tiers_json=row["default_tiers_json"],
        style_json=row["style_json"],
        is_locked=bool(row["is_locked"]),
        created_by=int(row["created_by"]),
        created_at=row["created_at"],
        published_at=row["published_at"],
        deleted_at=row["deleted_at"],
    )


def _item_from_row(row: Any | None) -> TierTemplateItem | None:
    if row is None:
        return None
    return TierTemplateItem(
        id=row["id"],
        template_version_id=row["template_version_id"],
        item_type=TemplateItemType(row["item_type"]),
        source_type=row["source_type"],
        asset_id=row["asset_id"],
        user_caption=row["user_caption"],
        render_caption=row["render_caption"],
        has_visible_caption=bool(row["has_visible_caption"]),
        internal_title=row["internal_title"],
        source_query=row["source_query"],
        metadata=load_json_dict(row["metadata_json"]),
        sort_order=int(row["sort_order"]),
        created_at=row["created_at"],
        deleted_at=row["deleted_at"],
    )


class TierTemplateRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    async def create_template(
        self,
        *,
        name: str,
        creator_id: int,
        guild_id: int | None = None,
        visibility: TemplateVisibility | str = TemplateVisibility.GUILD,
        description: str | None = None,
        slug: str | None = None,
        default_tiers_json: str = DEFAULT_TIERS_JSON,
        style_json: str | None = DEFAULT_STYLE_JSON,
    ) -> tuple[TierTemplate, TierTemplateVersion]:
        clean_name = normalize_optional_text(name)
        if clean_name is None:
            raise ValueError("Não é possível criar template sem nome.")
        visibility_value = coerce_visibility(visibility)
        slug_value = normalize_slug(slug or clean_name)
        now = utc_now_iso()
        template_id = new_uuid()
        version_id = new_uuid()

        try:
            async with self.db.immediate_transaction() as conn:
                await conn.execute(
                    """
                    INSERT INTO tier_templates(
                        id, slug, name, description, creator_id, guild_id, visibility,
                        current_version_id, created_at, updated_at, deleted_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
                    """,
                    (
                        template_id,
                        slug_value,
                        clean_name,
                        normalize_optional_text(description),
                        int(creator_id),
                        guild_id,
                        visibility_value.value,
                        now,
                        now,
                    ),
                )
                await conn.execute(
                    """
                    INSERT INTO tier_template_versions(
                        id, template_id, version_number, default_tiers_json, style_json,
                        is_locked, created_by, created_at, published_at, deleted_at
                    )
                    VALUES(?, ?, 1, ?, ?, 0, ?, ?, NULL, NULL)
                    """,
                    (version_id, template_id, default_tiers_json, style_json, int(creator_id), now),
                )
                await conn.execute(
                    """
                    UPDATE tier_templates
                    SET current_version_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (version_id, now, template_id),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Já existe um template com esse slug.") from exc

        template = await self.get_template_by_id(template_id)
        version = await self.get_template_version(version_id)
        if template is None or version is None:
            raise RuntimeError("Template criado não pôde ser recuperado.")
        return template, version

    async def get_template_by_id(self, template_id: str, *, include_deleted: bool = False) -> TierTemplate | None:
        query = "SELECT * FROM tier_templates WHERE id = ?"
        params: tuple[Any, ...] = (template_id,)
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        row = await fetch_one(self.db.conn, query, params)
        return _template_from_row(row)

    async def get_template_by_slug(self, slug: str, *, include_deleted: bool = False) -> TierTemplate | None:
        query = "SELECT * FROM tier_templates WHERE slug = ?"
        params: tuple[Any, ...] = (normalize_slug(slug),)
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        row = await fetch_one(self.db.conn, query, params)
        return _template_from_row(row)

    async def list_templates_for_user(
        self,
        user_id: int,
        *,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TierTemplate]:
        query = """
            SELECT * FROM tier_templates
            WHERE creator_id = ?
        """
        params: list[Any] = [int(user_id)]
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        rows = await self.db.conn.execute_fetchall(query, tuple(params))
        return [template for row in rows if (template := _template_from_row(row)) is not None]

    async def list_templates_for_guild(
        self,
        guild_id: int,
        *,
        include_global: bool = True,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TierTemplate]:
        if include_global:
            query = """
                SELECT * FROM tier_templates
                WHERE ((visibility = ? AND guild_id = ?) OR visibility = ?)
            """
            params: list[Any] = [TemplateVisibility.GUILD.value, int(guild_id), TemplateVisibility.GLOBAL.value]
        else:
            query = """
                SELECT * FROM tier_templates
                WHERE visibility = ? AND guild_id = ?
            """
            params = [TemplateVisibility.GUILD.value, int(guild_id)]
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        rows = await self.db.conn.execute_fetchall(query, tuple(params))
        return [template for row in rows if (template := _template_from_row(row)) is not None]

    async def create_template_version(
        self,
        *,
        template_id: str,
        created_by: int,
        default_tiers_json: str | None = None,
        style_json: str | None = None,
        clone_from_version_id: str | None = None,
    ) -> TierTemplateVersion:
        now = utc_now_iso()
        version_id = new_uuid()
        async with self.db.immediate_transaction() as conn:
            template_row = await fetch_one(
                conn,
                "SELECT id FROM tier_templates WHERE id = ? AND deleted_at IS NULL",
                (template_id,),
            )
            if template_row is None:
                raise ValueError("Template não encontrado.")

            if clone_from_version_id:
                source = await fetch_one(
                    conn,
                    """
                    SELECT * FROM tier_template_versions
                    WHERE id = ? AND template_id = ? AND deleted_at IS NULL
                    """,
                    (clone_from_version_id, template_id),
                )
                if source is None:
                    raise ValueError("Versão de origem não encontrada.")
                default_tiers_json = default_tiers_json or source["default_tiers_json"]
                style_json = style_json if style_json is not None else source["style_json"]

            row = await fetch_one(
                conn,
                """
                SELECT COALESCE(MAX(version_number), 0) + 1 AS next_number
                FROM tier_template_versions
                WHERE template_id = ?
                """,
                (template_id,),
            )
            next_number = int(row["next_number"])
            await conn.execute(
                """
                INSERT INTO tier_template_versions(
                    id, template_id, version_number, default_tiers_json, style_json,
                    is_locked, created_by, created_at, published_at, deleted_at
                )
                VALUES(?, ?, ?, ?, ?, 0, ?, ?, NULL, NULL)
                """,
                (
                    version_id,
                    template_id,
                    next_number,
                    default_tiers_json or DEFAULT_TIERS_JSON,
                    style_json if style_json is not None else DEFAULT_STYLE_JSON,
                    int(created_by),
                    now,
                ),
            )
            await conn.execute(
                "UPDATE tier_templates SET updated_at = ? WHERE id = ?",
                (now, template_id),
            )

        version = await self.get_template_version(version_id)
        if version is None:
            raise RuntimeError("Versão criada não pôde ser recuperada.")
        return version

    async def get_template_version(
        self,
        version_id: str,
        *,
        include_deleted: bool = False,
    ) -> TierTemplateVersion | None:
        query = "SELECT * FROM tier_template_versions WHERE id = ?"
        params: tuple[Any, ...] = (version_id,)
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        row = await fetch_one(self.db.conn, query, params)
        return _version_from_row(row)

    async def get_current_version(self, template_id: str) -> TierTemplateVersion | None:
        row = await fetch_one(
            self.db.conn,
            """
            SELECT v.*
            FROM tier_templates t
            JOIN tier_template_versions v ON v.id = t.current_version_id
            WHERE t.id = ? AND t.deleted_at IS NULL AND v.deleted_at IS NULL
            """,
            (template_id,),
        )
        return _version_from_row(row)

    async def lock_version(self, version_id: str, *, published_by: int | None = None) -> TierTemplateVersion:
        now = utc_now_iso()
        async with self.db.immediate_transaction() as conn:
            row = await fetch_one(
                conn,
                """
                SELECT * FROM tier_template_versions
                WHERE id = ? AND deleted_at IS NULL
                """,
                (version_id,),
            )
            if row is None:
                raise ValueError("Versão não encontrada.")
            await conn.execute(
                """
                UPDATE tier_template_versions
                SET is_locked = 1, published_at = COALESCE(published_at, ?)
                WHERE id = ?
                """,
                (now, version_id),
            )
            await conn.execute(
                """
                UPDATE tier_templates
                SET current_version_id = ?, updated_at = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (version_id, now, row["template_id"]),
            )
        version = await self.get_template_version(version_id)
        if version is None:
            raise RuntimeError("Versão publicada não pôde ser recuperada.")
        return version

    async def clone_version_for_editing(self, version_id: str, *, created_by: int) -> TierTemplateVersion:
        now = utc_now_iso()
        new_version_id = new_uuid()
        async with self.db.immediate_transaction() as conn:
            source = await fetch_one(
                conn,
                "SELECT * FROM tier_template_versions WHERE id = ? AND deleted_at IS NULL",
                (version_id,),
            )
            if source is None:
                raise ValueError("Versão de origem não encontrada.")
            next_row = await fetch_one(
                conn,
                """
                SELECT COALESCE(MAX(version_number), 0) + 1 AS next_number
                FROM tier_template_versions
                WHERE template_id = ?
                """,
                (source["template_id"],),
            )
            await conn.execute(
                """
                INSERT INTO tier_template_versions(
                    id, template_id, version_number, default_tiers_json, style_json,
                    is_locked, created_by, created_at, published_at, deleted_at
                )
                VALUES(?, ?, ?, ?, ?, 0, ?, ?, NULL, NULL)
                """,
                (
                    new_version_id,
                    source["template_id"],
                    int(next_row["next_number"]),
                    source["default_tiers_json"],
                    source["style_json"],
                    int(created_by),
                    now,
                ),
            )
            item_rows = await conn.execute_fetchall(
                """
                SELECT * FROM tier_template_items
                WHERE template_version_id = ? AND deleted_at IS NULL
                ORDER BY sort_order ASC
                """,
                (version_id,),
            )
            for item in item_rows:
                await conn.execute(
                    """
                    INSERT INTO tier_template_items(
                        id, template_version_id, item_type, source_type, asset_id,
                        user_caption, render_caption, has_visible_caption, internal_title,
                        source_query, metadata_json, sort_order, created_at, deleted_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        new_uuid(),
                        new_version_id,
                        item["item_type"],
                        item["source_type"],
                        item["asset_id"],
                        item["user_caption"],
                        item["render_caption"],
                        int(item["has_visible_caption"]),
                        item["internal_title"],
                        item["source_query"],
                        item["metadata_json"],
                        int(item["sort_order"]),
                        now,
                    ),
                )
            await conn.execute(
                "UPDATE tier_templates SET updated_at = ? WHERE id = ?",
                (now, source["template_id"]),
            )
        version = await self.get_template_version(new_version_id)
        if version is None:
            raise RuntimeError("Clone de versão não pôde ser recuperado.")
        return version

    async def add_template_item(
        self,
        *,
        template_version_id: str,
        item_type: TemplateItemType | str,
        source_type: str | None = None,
        asset_id: str | None = None,
        user_caption: str | None = None,
        render_caption: str | None = None,
        internal_title: str | None = None,
        source_query: str | None = None,
        metadata: dict[str, Any] | None = None,
        sort_order: int | None = None,
    ) -> TierTemplateItem:
        kind = coerce_item_type(item_type)
        clean_render_caption = normalize_optional_text(render_caption)
        clean_user_caption = normalize_optional_text(user_caption)
        if kind is TemplateItemType.TEXT_ONLY and clean_render_caption is None:
            raise ValueError("TEXT_ONLY precisa ter render_caption não vazio.")
        if kind is TemplateItemType.IMAGE and not asset_id:
            raise ValueError("IMAGE precisa ter asset_id resolvido.")
        has_visible_caption = clean_render_caption is not None
        now = utc_now_iso()
        item_id = new_uuid()

        async with self.db.immediate_transaction() as conn:
            version = await fetch_one(
                conn,
                "SELECT * FROM tier_template_versions WHERE id = ? AND deleted_at IS NULL",
                (template_version_id,),
            )
            if version is None:
                raise ValueError("Versão de template não encontrada.")
            if bool(version["is_locked"]):
                raise ValueError("Versões publicadas/congeladas não podem ser editadas.")
            if asset_id:
                asset = await fetch_one(
                    conn,
                    "SELECT id FROM tier_assets WHERE id = ? AND deleted_at IS NULL",
                    (asset_id,),
                )
                if asset is None:
                    raise ValueError("Asset não encontrado.")
            if sort_order is None:
                order_row = await fetch_one(
                    conn,
                    """
                    SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order
                    FROM tier_template_items
                    WHERE template_version_id = ? AND deleted_at IS NULL
                    """,
                    (template_version_id,),
                )
                sort_order = int(order_row["next_order"])
            await conn.execute(
                """
                INSERT INTO tier_template_items(
                    id, template_version_id, item_type, source_type, asset_id,
                    user_caption, render_caption, has_visible_caption, internal_title,
                    source_query, metadata_json, sort_order, created_at, deleted_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    item_id,
                    template_version_id,
                    kind.value,
                    normalize_optional_text(source_type),
                    asset_id,
                    clean_user_caption,
                    clean_render_caption,
                    int(has_visible_caption),
                    normalize_optional_text(internal_title),
                    normalize_optional_text(source_query),
                    metadata_to_json(metadata),
                    int(sort_order),
                    now,
                ),
            )
            await conn.execute(
                """
                UPDATE tier_templates
                SET updated_at = ?
                WHERE id = ?
                """,
                (now, version["template_id"]),
            )

        item = await self.get_template_item(item_id)
        if item is None:
            raise RuntimeError("Item criado não pôde ser recuperado.")
        return item

    async def get_template_item(self, item_id: str, *, include_deleted: bool = False) -> TierTemplateItem | None:
        query = "SELECT * FROM tier_template_items WHERE id = ?"
        params: tuple[Any, ...] = (item_id,)
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        row = await fetch_one(self.db.conn, query, params)
        return _item_from_row(row)

    async def remove_template_item(self, item_id: str) -> None:
        now = utc_now_iso()
        async with self.db.immediate_transaction() as conn:
            row = await fetch_one(
                conn,
                """
                SELECT i.id, i.template_version_id, v.is_locked, v.template_id
                FROM tier_template_items i
                JOIN tier_template_versions v ON v.id = i.template_version_id
                WHERE i.id = ? AND i.deleted_at IS NULL AND v.deleted_at IS NULL
                """,
                (item_id,),
            )
            if row is None:
                raise ValueError("Item de template não encontrado.")
            if bool(row["is_locked"]):
                raise ValueError("Versões publicadas/congeladas não podem ser editadas.")
            await conn.execute(
                "UPDATE tier_template_items SET deleted_at = ? WHERE id = ?",
                (now, item_id),
            )
            await self._compact_sort_order(conn, row["template_version_id"])
            await conn.execute(
                "UPDATE tier_templates SET updated_at = ? WHERE id = ?",
                (now, row["template_id"]),
            )

    async def list_template_items(
        self,
        template_version_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[TierTemplateItem]:
        query = """
            SELECT * FROM tier_template_items
            WHERE template_version_id = ?
        """
        params: tuple[Any, ...] = (template_version_id,)
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        query += " ORDER BY sort_order ASC, created_at ASC"
        rows = await self.db.conn.execute_fetchall(query, params)
        return [item for row in rows if (item := _item_from_row(row)) is not None]

    async def update_template_current_version(self, template_id: str, version_id: str) -> TierTemplate:
        now = utc_now_iso()
        async with self.db.immediate_transaction() as conn:
            version = await fetch_one(
                conn,
                """
                SELECT id FROM tier_template_versions
                WHERE id = ? AND template_id = ? AND deleted_at IS NULL
                """,
                (version_id, template_id),
            )
            if version is None:
                raise ValueError("Versão não pertence ao template informado.")
            cursor = await conn.execute(
                """
                UPDATE tier_templates
                SET current_version_id = ?, updated_at = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (version_id, now, template_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Template não encontrado.")
        template = await self.get_template_by_id(template_id)
        if template is None:
            raise RuntimeError("Template atualizado não pôde ser recuperado.")
        return template

    async def move_template_item(self, item_id: str, *, direction: int) -> TierTemplateItem:
        if direction not in {-1, 1}:
            raise ValueError("direction deve ser -1 ou 1.")
        now = utc_now_iso()
        async with self.db.immediate_transaction() as conn:
            row = await fetch_one(
                conn,
                """
                SELECT i.id, i.template_version_id, v.is_locked, v.template_id
                FROM tier_template_items i
                JOIN tier_template_versions v ON v.id = i.template_version_id
                WHERE i.id = ? AND i.deleted_at IS NULL AND v.deleted_at IS NULL
                """,
                (item_id,),
            )
            if row is None:
                raise ValueError("Item de template não encontrado.")
            if bool(row["is_locked"]):
                raise ValueError("Versões publicadas/congeladas não podem ser editadas.")

            items = await conn.execute_fetchall(
                """
                SELECT id, sort_order
                FROM tier_template_items
                WHERE template_version_id = ? AND deleted_at IS NULL
                ORDER BY sort_order ASC, created_at ASC
                """,
                (row["template_version_id"],),
            )
            ids = [str(item["id"]) for item in items]
            current_index = ids.index(item_id)
            target_index = current_index + direction
            if target_index < 0 or target_index >= len(ids):
                raise ValueError("Esse item já está no limite da lista.")

            current_id = ids[current_index]
            target_id = ids[target_index]
            current_order = int(items[current_index]["sort_order"])
            target_order = int(items[target_index]["sort_order"])
            await conn.execute(
                "UPDATE tier_template_items SET sort_order = ? WHERE id = ?",
                (target_order, current_id),
            )
            await conn.execute(
                "UPDATE tier_template_items SET sort_order = ? WHERE id = ?",
                (current_order, target_id),
            )
            await conn.execute(
                "UPDATE tier_templates SET updated_at = ? WHERE id = ?",
                (now, row["template_id"]),
            )

        item = await self.get_template_item(item_id)
        if item is None:
            raise RuntimeError("Item movido não pôde ser recuperado.")
        return item

    async def _compact_sort_order(self, conn: Any, template_version_id: str | None) -> None:
        if template_version_id is None:
            return
        rows = await conn.execute_fetchall(
            """
            SELECT id FROM tier_template_items
            WHERE template_version_id = ? AND deleted_at IS NULL
            ORDER BY sort_order ASC, created_at ASC
            """,
            (template_version_id,),
        )
        for index, row in enumerate(rows):
            await conn.execute(
                "UPDATE tier_template_items SET sort_order = ? WHERE id = ?",
                (index, row["id"]),
            )
