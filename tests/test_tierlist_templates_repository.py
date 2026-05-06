from __future__ import annotations

import asyncio
import hashlib
import io
import importlib.util
import tempfile
import threading
import unittest
from types import SimpleNamespace
from pathlib import Path

AIOSQLITE_AVAILABLE = importlib.util.find_spec("aiosqlite") is not None

from PIL import Image

from cogs.tierlist_templates.assets import TierTemplateAssetStore
from cogs.tierlist_templates.downloads import DownloadedImage, SafeImageDownloader
from cogs.tierlist_templates.exceptions import (
    AssetValidationError,
    ConflictingImageSourcesError,
    EmptyTemplateItemError,
    UnsupportedImageTypeError,
)
from cogs.tierlist_templates.item_resolver import (
    CONFLICTING_IMAGE_SOURCES_MESSAGE,
    TierTemplateItemResolver,
    get_filled_image_sources,
    normalize_caption,
)
from cogs.tierlist_templates.migrations import DEFAULT_TIERS_JSON
from cogs.tierlist_templates.messages import (
    EMPTY_ITEM_MESSAGE,
    SESSION_FINALIZED_MESSAGE,
    SESSION_PERMISSION_DENIED_MESSAGE,
    TEMPLATE_NOT_FOUND_MESSAGE,
    TEMPLATE_PRIVATE_MESSAGE,
    VERSION_LOCKED_MESSAGE,
)
from cogs.tierlist_wikipedia.wikipedia import WIKIPEDIA_SOURCE_TYPE
from cogs.tierlist_templates.models import (
    SessionStatus,
    TemplateItemType,
    TemplateVisibility,
    TierSession,
    TierSessionItem,
    TierTemplate,
    TierTemplateItem,
    TierTemplateVersion,
)
from cogs.tierlist_templates.session_renderer import SessionRenderSnapshot, TierSessionRenderer
from cogs.tierlist_templates.cog import TierTemplateCog

if AIOSQLITE_AVAILABLE:
    from cogs.tierlist_templates.asset_repository import TierAssetRepository
    from cogs.tierlist_templates.database import DatabaseManager
    from cogs.tierlist_templates.session_repository import TierSessionRepository
    from cogs.tierlist_templates.template_repository import TierTemplateRepository


async def asyncio_threadsafe_callbacks_work() -> bool:
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def worker() -> None:
        loop.call_soon_threadsafe(future.set_result, True)

    threading.Thread(target=worker, daemon=True).start()
    try:
        return bool(await asyncio.wait_for(future, 0.5))
    except TimeoutError:
        return False


def make_template_item(
    item_id: str,
    *,
    item_type: TemplateItemType,
    source_type: str | None,
    asset_id: str | None = None,
    render_caption: str | None = None,
    has_visible_caption: bool = False,
    internal_title: str | None = None,
) -> TierTemplateItem:
    return TierTemplateItem(
        id=item_id,
        template_version_id="version-1",
        item_type=item_type,
        source_type=source_type,
        asset_id=asset_id,
        user_caption=render_caption,
        render_caption=render_caption,
        has_visible_caption=has_visible_caption,
        internal_title=internal_title,
        source_query="busca-interna",
        metadata={
            "asset_hash": "a" * 64,
            "asset_mime_type": "image/webp",
        },
        sort_order=0,
        created_at="2026-01-01T00:00:00+00:00",
        deleted_at=None,
    )


def make_session_item(
    item_id: str,
    template_item_id: str,
    *,
    tier_id: str | None,
    position: int,
    is_unused: bool = False,
) -> TierSessionItem:
    return TierSessionItem(
        id=item_id,
        session_id="session-1",
        template_item_id=template_item_id,
        current_tier_id=tier_id,
        position=position,
        is_unused=is_unused,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def make_render_snapshot(
    *,
    template_items: list[TierTemplateItem],
    session_items: list[TierSessionItem],
    tiers: list[dict],
) -> SessionRenderSnapshot:
    return SessionRenderSnapshot(
        template=TierTemplate(
            id="template-1",
            slug="template-teste",
            name="Template Teste",
            description=None,
            creator_id=10,
            guild_id=None,
            visibility=TemplateVisibility.GLOBAL,
            current_version_id="version-1",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            deleted_at=None,
        ),
        version=TierTemplateVersion(
            id="version-1",
            template_id="template-1",
            version_number=1,
            default_tiers_json=DEFAULT_TIERS_JSON,
            style_json=None,
            is_locked=True,
            created_by=10,
            created_at="2026-01-01T00:00:00+00:00",
            published_at="2026-01-01T00:00:00+00:00",
            deleted_at=None,
        ),
        session=TierSession(
            id="session-1",
            template_version_id="version-1",
            owner_id=99,
            guild_id=None,
            channel_id=None,
            message_id=None,
            status=SessionStatus.ACTIVE,
            selected_item_id=None,
            selected_tier_id=None,
            current_inventory_page=0,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            finalized_at=None,
            expires_at=None,
        ),
        session_items=session_items,
        template_items_by_id={item.id: item for item in template_items},
        tiers=tiers,
    )


class FakeCanonicalTierListRenderer:
    def __init__(self) -> None:
        self.call: dict[str, object] = {}

    def generate_tierlist_image(
        self,
        title: str,
        tiers_dict: object,
        *,
        author: object | None = None,
        guild_icon_bytes: bytes | None = None,
        tier_colors: object | None = None,
    ) -> io.BytesIO:
        self.call = {
            "title": title,
            "tiers_dict": tiers_dict,
            "author": author,
            "guild_icon_bytes": guild_icon_bytes,
            "tier_colors": tier_colors,
        }
        return io.BytesIO(b"canonical-render")


class FakeRenderAssetRepository:
    async def get_asset(self, asset_id: str) -> SimpleNamespace:
        return SimpleNamespace(id=asset_id)


class FakeRenderAssetStore:
    async def load_asset_bytes(self, asset: object) -> bytes:
        return b"asset-bytes"


class TierTemplateSchemaConstantTests(unittest.TestCase):
    def test_required_user_messages_are_exact(self) -> None:
        self.assertEqual(
            EMPTY_ITEM_MESSAGE,
            "⚠️ Esse item veio tão vazio que nem o abismo respondeu. Preencha um nome ou escolha uma fonte de imagem.",
        )
        self.assertEqual(
            CONFLICTING_IMAGE_SOURCES_MESSAGE,
            "⚠️ Honra e proveito não cabem no mesmo saco estreito.\n\n"
            "Você preencheu mais de uma fonte de imagem ao mesmo tempo. Eu preciso saber qual imagem usar: "
            "avatar de usuário, link direto, Wikipedia, Spotify ou outra fonte, mas não tudo junto no mesmo item.\n\n"
            "Escolha só uma fonte de imagem e tente de novo.",
        )
        self.assertEqual(
            SESSION_PERMISSION_DENIED_MESSAGE,
            "⚠️ Essa tierlist não é sua, beldade. Crie sua própria sessão com /tierlist-template usar.",
        )
        self.assertEqual(SESSION_FINALIZED_MESSAGE, "🏁 Essa tierlist já foi finalizada. Ela agora é relíquia histórica.")
        self.assertEqual(TEMPLATE_PRIVATE_MESSAGE, "🔒 Esse template é privado e só o criador pode usar.")
        self.assertEqual(TEMPLATE_NOT_FOUND_MESSAGE, "🔎 Não encontrei esse template. Confere o nome/slug e tenta de novo.")
        self.assertEqual(VERSION_LOCKED_MESSAGE, "🔒 Esse template já foi publicado. Para editar, vou criar uma nova versão em rascunho.")

    def test_default_tiers_use_expected_ids(self) -> None:
        self.assertIn('"id":"S"', DEFAULT_TIERS_JSON)
        self.assertIn('"id":"D"', DEFAULT_TIERS_JSON)

    def test_normalize_caption_never_turns_empty_values_visible(self) -> None:
        self.assertIsNone(normalize_caption(None))
        self.assertIsNone(normalize_caption(""))
        self.assertIsNone(normalize_caption("  null  "))
        self.assertIsNone(normalize_caption("None"))
        self.assertEqual(normalize_caption("  Nome  "), "Nome")

    def test_filled_image_sources_ignore_caption_and_find_conflicts(self) -> None:
        sources = get_filled_image_sources(image_url="https://example.com/a.png", spotify_input="album")
        self.assertEqual([source.key for source in sources], ["image_url", "spotify_input"])

    def test_asset_processor_rejects_invalid_payload(self) -> None:
        store = TierTemplateAssetStore(repository=SimpleNamespace())
        with self.assertRaises((AssetValidationError, UnsupportedImageTypeError)):
            store._process_image_sync(b"not an image")

    def test_asset_processor_outputs_hash_path_shape(self) -> None:
        store = TierTemplateAssetStore(repository=SimpleNamespace())
        image = Image.new("RGBA", (16, 16), (255, 0, 0, 128))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        processed = store._process_image_sync(buffer.getvalue())
        relative = store._relative_path_for_hash(processed.asset_hash, processed.extension)
        self.assertEqual(relative.parts[0], processed.asset_hash[:2])
        self.assertEqual(relative.parts[1], processed.asset_hash[2:4])
        self.assertTrue(relative.name.startswith(processed.asset_hash))

    def test_svg_content_type_is_rejected_before_download_body_processing(self) -> None:
        downloader = SafeImageDownloader()
        with self.assertRaises(UnsupportedImageTypeError):
            downloader._validate_content_type("image/svg+xml")

    @unittest.skipIf(not AIOSQLITE_AVAILABLE, "aiosqlite não está instalado neste Python")
    def test_sqlite_busy_error_detection_is_conservative(self) -> None:
        self.assertTrue(DatabaseManager._is_busy_error(Exception("database is locked")))
        self.assertTrue(DatabaseManager._is_busy_error(Exception("database is busy")))
        self.assertFalse(DatabaseManager._is_busy_error(Exception("foreign key constraint failed")))

    def test_renderer_safe_text_suppresses_empty_sentinel_values(self) -> None:
        renderer = TierSessionRenderer(
            template_repository=SimpleNamespace(),
            session_repository=SimpleNamespace(),
            asset_repository=SimpleNamespace(),
            asset_store=SimpleNamespace(),
        )
        self.assertIsNone(renderer._safe_text(None))
        self.assertIsNone(renderer._safe_text(""))
        self.assertIsNone(renderer._safe_text("  None "))
        self.assertIsNone(renderer._safe_text("null"))
        self.assertEqual(renderer._safe_text("  Nome limpo  "), "Nome limpo")

    def test_session_snapshot_conversion_uses_canonical_item_rules(self) -> None:
        renderer = TierSessionRenderer(
            template_repository=SimpleNamespace(),
            session_repository=SimpleNamespace(),
            asset_repository=SimpleNamespace(),
            asset_store=SimpleNamespace(),
        )
        spotify_item = make_template_item(
            "template-image-1",
            item_type=TemplateItemType.IMAGE,
            source_type="SPOTIFY",
            asset_id="asset-1",
            render_caption=None,
            has_visible_caption=False,
            internal_title="Album que nao deve aparecer",
        )
        wikipedia_item = make_template_item(
            "template-image-2",
            item_type=TemplateItemType.IMAGE,
            source_type="WIKIPEDIA",
            asset_id="asset-2",
            render_caption="None",
            has_visible_caption=True,
            internal_title="Artigo que nao deve aparecer",
        )
        text_item = make_template_item(
            "template-text-1",
            item_type=TemplateItemType.TEXT_ONLY,
            source_type="TEXT",
            render_caption="Texto visivel",
            has_visible_caption=True,
        )
        unused_item = make_template_item(
            "template-unused-1",
            item_type=TemplateItemType.TEXT_ONLY,
            source_type="TEXT",
            render_caption="Inventario",
            has_visible_caption=True,
        )
        snapshot = make_render_snapshot(
            template_items=[spotify_item, wikipedia_item, text_item, unused_item],
            session_items=[
                make_session_item("session-image-1", spotify_item.id, tier_id="S", position=0),
                make_session_item("session-image-2", wikipedia_item.id, tier_id="S", position=1),
                make_session_item("session-text-1", text_item.id, tier_id="A", position=0),
                make_session_item("session-unused-1", unused_item.id, tier_id=None, position=0, is_unused=True),
            ],
            tiers=[
                {"id": "S", "label": "S", "color": "#FF0000"},
                {"id": "A", "label": "A", "color": "#00FF00"},
            ],
        )

        tiers_dict, tier_colors = renderer.session_snapshot_to_tier_items(
            snapshot,
            {"session-image-1": b"spotify-bytes", "session-image-2": b"wiki-bytes"},
        )

        self.assertEqual(list(tiers_dict.keys()), ["S", "A"])
        self.assertEqual(tier_colors, {"S": (255, 0, 0), "A": (0, 255, 0)})
        self.assertEqual(len(tiers_dict["S"]), 2)
        self.assertEqual(len(tiers_dict["A"]), 1)

        spotify_render_item = tiers_dict["S"][0]
        self.assertEqual(spotify_render_item.name, "")
        self.assertIsNone(spotify_render_item.render_caption)
        self.assertFalse(spotify_render_item.has_visible_caption)
        self.assertEqual(spotify_render_item.image_bytes, b"spotify-bytes")
        self.assertEqual(spotify_render_item.source_type, "spotify")

        wikipedia_render_item = tiers_dict["S"][1]
        self.assertEqual(wikipedia_render_item.name, "")
        self.assertIsNone(wikipedia_render_item.render_caption)
        self.assertFalse(wikipedia_render_item.has_visible_caption)
        self.assertEqual(wikipedia_render_item.source_type, WIKIPEDIA_SOURCE_TYPE)

        text_render_item = tiers_dict["A"][0]
        self.assertEqual(text_render_item.name, "Texto visivel")
        self.assertEqual(text_render_item.render_caption, "Texto visivel")
        self.assertTrue(text_render_item.has_visible_caption)
        self.assertEqual(text_render_item.source_type, "text")
        self.assertNotIn("Inventario", [item.name for items in tiers_dict.values() for item in items])

    def test_render_session_snapshot_calls_canonical_renderer(self) -> None:
        fake_renderer = FakeCanonicalTierListRenderer()
        renderer = TierSessionRenderer(
            template_repository=SimpleNamespace(),
            session_repository=SimpleNamespace(),
            asset_repository=FakeRenderAssetRepository(),
            asset_store=FakeRenderAssetStore(),
            renderer=fake_renderer,
        )
        image_item = make_template_item(
            "template-image-1",
            item_type=TemplateItemType.IMAGE,
            source_type="IMAGE_URL",
            asset_id="asset-1",
            render_caption="Capa",
            has_visible_caption=True,
        )
        snapshot = make_render_snapshot(
            template_items=[image_item],
            session_items=[make_session_item("session-image-1", image_item.id, tier_id="S", position=0)],
            tiers=[{"id": "S", "label": "S", "color": "#FF0000"}],
        )

        original_to_thread = asyncio.to_thread

        async def immediate_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        try:
            asyncio.to_thread = immediate_to_thread  # type: ignore[assignment]
            output = asyncio.run(
                renderer.render_session_snapshot(
                    snapshot,
                    author=SimpleNamespace(name="Autor"),
                    guild_icon_bytes=b"guild-icon",
                )
            )
        finally:
            asyncio.to_thread = original_to_thread  # type: ignore[assignment]

        self.assertEqual(output.getvalue(), b"canonical-render")
        self.assertEqual(fake_renderer.call["title"], "Template Teste")
        self.assertEqual(fake_renderer.call["author"].name, "Autor")
        self.assertEqual(fake_renderer.call["guild_icon_bytes"], b"guild-icon")
        self.assertEqual(fake_renderer.call["tier_colors"], {"S": (255, 0, 0)})
        self.assertEqual(fake_renderer.call["tiers_dict"]["S"][0].image_bytes, b"asset-bytes")
        self.assertEqual(fake_renderer.call["tiers_dict"]["S"][0].render_caption, "Capa")

    def test_session_and_editor_labels_do_not_fallback_to_internal_title_for_images(self) -> None:
        cog = object.__new__(TierTemplateCog)
        item = TierTemplateItem(
            id="item-1",
            template_version_id="version-1",
            item_type=TemplateItemType.IMAGE,
            source_type="SPOTIFY",
            asset_id="asset-1",
            user_caption=None,
            render_caption=None,
            has_visible_caption=False,
            internal_title="Album que nao deve aparecer",
            source_query="https://example.com/nao-renderizar",
            metadata={},
            sort_order=0,
            created_at="2026-01-01T00:00:00+00:00",
            deleted_at=None,
        )

        self.assertEqual(TierTemplateCog.session_item_label(cog, item, 0), "Item com imagem #1")
        self.assertEqual(TierTemplateCog.item_display_label(cog, item, 0), "Imagem sem legenda: Item com imagem #1")


class FakeDownloader:
    async def download(self, url: str) -> DownloadedImage:
        return DownloadedImage(url=url, final_url=url, content_type="image/png", data=b"image")


class FakeAssetStore:
    async def store_image_bytes(self, raw_bytes: bytes, *, source_type: str | None = None, metadata: dict | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            asset=SimpleNamespace(id="asset-1"),
            asset_id="asset-1",
            asset_hash="a" * 64,
            storage_path="aa/aa/" + ("a" * 64) + ".webp",
            width=16,
            height=16,
            size_bytes=123,
            mime_type="image/webp",
            created_new_file=True,
        )


class TierTemplateItemResolverUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_item_requires_caption(self) -> None:
        resolver = TierTemplateItemResolver(asset_store=FakeAssetStore(), downloader=FakeDownloader())
        with self.assertRaises(EmptyTemplateItemError):
            await resolver.resolve_item(user_caption_raw=" ")

    async def test_conflicting_sources_use_required_message(self) -> None:
        resolver = TierTemplateItemResolver(asset_store=FakeAssetStore(), downloader=FakeDownloader())
        with self.assertRaises(ConflictingImageSourcesError) as ctx:
            await resolver.resolve_item(image_url="https://example.com/a.png", spotify_input="album")
        self.assertEqual(ctx.exception.user_message, CONFLICTING_IMAGE_SOURCES_MESSAGE)

    async def test_image_without_caption_keeps_render_caption_none(self) -> None:
        resolver = TierTemplateItemResolver(asset_store=FakeAssetStore(), downloader=FakeDownloader())
        item = await resolver.resolve_item(user_caption_raw="", image_url="https://example.com/a.png")
        self.assertEqual(item.item_type, TemplateItemType.IMAGE)
        self.assertIsNone(item.user_caption)
        self.assertIsNone(item.render_caption)
        self.assertFalse(item.has_visible_caption)
        self.assertEqual(item.asset_id, "asset-1")


@unittest.skipIf(not AIOSQLITE_AVAILABLE, "aiosqlite não está instalado neste Python")
class TierTemplateRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        if not await asyncio_threadsafe_callbacks_work():
            self.skipTest("asyncio.call_soon_threadsafe não acorda o loop neste sandbox")
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(Path(self.tmp.name) / "templates.sqlite3")
        await self.db.connect()
        self.templates = TierTemplateRepository(self.db)
        self.assets = TierAssetRepository(self.db)
        self.sessions = TierSessionRepository(self.db)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_template_version_item_asset_and_session_flow(self) -> None:
        template, version = await self.templates.create_template(
            name="Álbuns",
            creator_id=10,
            guild_id=20,
            visibility=TemplateVisibility.GUILD,
        )
        self.assertEqual(template.slug, "albuns")
        self.assertFalse(version.is_locked)

        payload_hash = hashlib.sha256(b"optimized-image").hexdigest()
        asset = await self.assets.create_asset(
            asset_hash=payload_hash,
            storage_path="sha256/ab/optimized-image.webp",
            mime_type="image/webp",
            width=128,
            height=128,
            size_bytes=15,
            source_type="IMAGE_URL",
            metadata={"source": "test"},
        )
        same_asset = await self.assets.create_asset(
            asset_hash=payload_hash,
            storage_path="sha256/ab/optimized-image.webp",
            mime_type="image/webp",
            width=128,
            height=128,
            size_bytes=15,
        )
        self.assertEqual(asset.id, same_asset.id)

        with tempfile.TemporaryDirectory() as asset_tmp:
            store = TierTemplateAssetStore(repository=self.assets, root_dir=asset_tmp)
            image = Image.new("RGB", (24, 24), (0, 128, 255))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            first = await store.store_image_bytes(buffer.getvalue(), source_type="IMAGE_URL")
            second = await store.store_image_bytes(buffer.getvalue(), source_type="IMAGE_URL")
            self.assertEqual(first.asset_hash, second.asset_hash)
            self.assertEqual(first.asset_id, second.asset_id)
            self.assertTrue((Path(asset_tmp) / first.storage_path).exists())

        text_item = await self.templates.add_template_item(
            template_version_id=version.id,
            item_type=TemplateItemType.TEXT_ONLY,
            source_type="TEXT",
            user_caption="Texto",
            render_caption="Texto",
        )
        image_item = await self.templates.add_template_item(
            template_version_id=version.id,
            item_type=TemplateItemType.IMAGE,
            source_type="IMAGE_URL",
            asset_id=asset.id,
            internal_title="não renderizar",
        )
        self.assertEqual([item.id for item in await self.templates.list_template_items(version.id)], [text_item.id, image_item.id])

        published = await self.templates.lock_version(version.id, published_by=10)
        self.assertTrue(published.is_locked)
        with self.assertRaises(ValueError):
            await self.templates.add_template_item(
                template_version_id=version.id,
                item_type=TemplateItemType.TEXT_ONLY,
                render_caption="bloqueado",
            )

        clone = await self.templates.clone_version_for_editing(version.id, created_by=10)
        self.assertFalse(clone.is_locked)
        self.assertEqual(len(await self.templates.list_template_items(clone.id)), 2)

        session = await self.sessions.create_session(
            template_version_id=version.id,
            owner_id=99,
            guild_id=20,
            channel_id=30,
        )
        session_items = await self.sessions.list_session_items(session.id)
        self.assertEqual(len(session_items), 2)
        moved = await self.sessions.move_item_to_tier(
            session_id=session.id,
            session_item_id=session_items[0].id,
            tier_id="S",
            owner_id=99,
        )
        self.assertEqual(moved.current_tier_id, "S")
        self.assertFalse(moved.is_unused)

        finalized = await self.sessions.finalize_session(session.id, owner_id=99)
        self.assertEqual(finalized.status.value, "FINALIZED")
        with self.assertRaises(ValueError):
            await self.sessions.move_item_to_inventory(
                session_id=session.id,
                session_item_id=session_items[0].id,
                owner_id=99,
            )


if __name__ == "__main__":
    unittest.main()
