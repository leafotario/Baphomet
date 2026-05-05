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
from cogs.tierlist_templates.models import TemplateItemType, TemplateVisibility

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


class TierTemplateSchemaConstantTests(unittest.TestCase):
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
