from __future__ import annotations

import asyncio
import importlib.util
import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

import discord
from discord.ext import commands
from PIL import Image

from cogs.iceberg.commands import IcebergCog
from cogs.iceberg.models import (
    IcebergProject,
    ItemConfig,
    ItemDisplayStyle,
    ItemSource,
    ItemSourceType,
    LayerConfig,
    PlacementConfig,
)
from cogs.iceberg.renderer import IcebergRenderer
from cogs.iceberg.repository import IcebergDatabaseManager, IcebergRepository
from cogs.iceberg.service import IcebergService
from cogs.iceberg.sources.providers import AttachmentImageProvider, IcebergUserError, TextItemProvider
from cogs.iceberg.themes import get_theme


AIOSQLITE_AVAILABLE = importlib.util.find_spec("aiosqlite") is not None


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


def png_bytes(color: tuple[int, int, int, int] = (220, 40, 90, 255)) -> bytes:
    image = Image.new("RGBA", (64, 64), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def make_project() -> IcebergProject:
    theme = get_theme()
    layers = [
        LayerConfig(id="layer-1", name="Ponta", order=0, height_weight=0.8),
        LayerConfig(id="layer-2", name="Raso", order=1, height_weight=1.2),
        LayerConfig(id="layer-3", name="Abismo", order=2, height_weight=1.8),
    ]
    return IcebergProject(
        id="project-1",
        owner_id=42,
        guild_id=99,
        name="Iceberg Teste",
        theme_id=theme.id,
        theme=theme,
        layers=layers,
        items=[
            ItemConfig(
                id="text-1",
                layer_id="layer-1",
                title="Texto visivel",
                source=ItemSource(type=ItemSourceType.TEXT, value="Texto visivel"),
                display_style=ItemDisplayStyle.CHIP,
                sort_order=0,
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            ),
            ItemConfig(
                id="image-1",
                layer_id="layer-2",
                title="Imagem",
                source=ItemSource(type=ItemSourceType.ATTACHMENT, value="imagem.png", asset_id="asset-1"),
                display_style=ItemDisplayStyle.CARD,
                placement=PlacementConfig(x=0.5, y=0.1, scale=1.0),
                sort_order=0,
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            ),
        ],
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


class IcebergRendererSnapshotTests(unittest.TestCase):
    def test_default_renderer_snapshot_signature(self) -> None:
        project = make_project()
        buffer = IcebergRenderer().render_project(project, asset_bytes_by_item_id={"image-1": png_bytes()})
        image = Image.open(buffer).convert("RGBA")
        self.assertEqual(image.size, (1200, 1600))
        snapshot = {
            "sky": image.getpixel((10, 10)),
            "ocean": image.getpixel((10, 620)),
            "iceberg_top": image.getpixel((600, 222)),
            "footer": image.getpixel((600, 1570)),
        }
        self.assertEqual(snapshot["sky"], (164, 218, 241, 255))
        self.assertNotEqual(snapshot["ocean"], snapshot["sky"])
        self.assertGreater(snapshot["iceberg_top"][0], 180)
        self.assertGreater(snapshot["footer"][3], 240)

    def test_project_json_roundtrip_keeps_domain_types(self) -> None:
        project = make_project()
        imported = IcebergProject.from_dict(project.to_dict())
        self.assertEqual(imported.name, project.name)
        self.assertEqual(imported.items[0].source.type, ItemSourceType.TEXT)
        self.assertEqual(imported.items[1].display_style, ItemDisplayStyle.CARD)
        self.assertEqual(imported.layers[2].height_weight, 1.8)


class IcebergSourceProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_provider_requires_real_text(self) -> None:
        provider = TextItemProvider()
        with self.assertRaises(IcebergUserError):
            await provider.resolve(title=" ", value=" ")
        resolved = await provider.resolve(value="  Conteudo  ")
        self.assertEqual(resolved.title, "Conteudo")
        self.assertEqual(resolved.source.type, ItemSourceType.TEXT)

    async def test_attachment_provider_stores_image_asset(self) -> None:
        class FakeAssetStore:
            async def store_image_bytes(self, raw_bytes: bytes, *, source_type: str | None = None, metadata: dict | None = None) -> SimpleNamespace:
                self.raw_bytes = raw_bytes
                self.source_type = source_type
                self.metadata = metadata
                return SimpleNamespace(
                    asset_id="asset-1",
                    asset_hash="a" * 64,
                    storage_path="aa/aa/" + ("a" * 64) + ".webp",
                    mime_type="image/webp",
                    width=64,
                    height=64,
                    size_bytes=123,
                )

        class FakeAttachment:
            filename = "misterio.png"
            content_type = "image/png"
            size = 256
            url = "https://cdn.discordapp.test/misterio.png"

            async def read(self, *, use_cached: bool = True) -> bytes:
                return png_bytes()

        store = FakeAssetStore()
        provider = AttachmentImageProvider(downloader=SimpleNamespace(), asset_store=store)
        resolved = await provider.resolve(title="", attachment=FakeAttachment())
        self.assertEqual(resolved.title, "misterio")
        self.assertEqual(resolved.source.asset_id, "asset-1")
        self.assertEqual(resolved.source.type, ItemSourceType.ATTACHMENT)
        self.assertEqual(store.source_type, ItemSourceType.ATTACHMENT.value)


@unittest.skipIf(not AIOSQLITE_AVAILABLE, "aiosqlite não está instalado neste Python")
class IcebergRepositoryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        if not await asyncio_threadsafe_callbacks_work():
            self.skipTest("asyncio.call_soon_threadsafe não acorda o loop neste sandbox")
        self.tmp = tempfile.TemporaryDirectory()
        self.db = IcebergDatabaseManager(Path(self.tmp.name) / "icebergs.sqlite3")
        try:
            await asyncio.wait_for(self.db.connect(), timeout=2.0)
        except TimeoutError:
            self.tmp.cleanup()
            self.skipTest("aiosqlite.connect travou neste sandbox")
        self.repository = IcebergRepository(self.db)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_repository_saves_and_lists_project_json(self) -> None:
        project = make_project()
        saved = await self.repository.save_project(project)
        self.assertEqual(saved.id, "project-1")
        listed = await self.repository.list_projects_for_user(42, guild_id=99)
        self.assertEqual([item.id for item in listed], ["project-1"])
        fetched = await self.repository.get_project("project-1", owner_id=42)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.items[1].source.asset_id, "asset-1")

    async def test_service_layer_update_preserves_items_by_name_and_moves_orphans(self) -> None:
        class FakeAssetRepository:
            pass

        class FakeAssetStore:
            pass

        class FakeRegistry:
            pass

        service = IcebergService(
            repository=self.repository,
            asset_repository=FakeAssetRepository(),
            asset_store=FakeAssetStore(),
            source_registry=FakeRegistry(),
            renderer=IcebergRenderer(),
        )
        await self.repository.save_project(make_project())
        updated = await service.update_layers(
            "project-1",
            owner_id=42,
            names=["Raso", "Novo"],
            weights=[2.0, 1.0],
        )
        self.assertEqual([layer.name for layer in updated.layers], ["Raso", "Novo"])
        self.assertTrue(all(item.layer_id in {layer.id for layer in updated.layers} for item in updated.items))


class IcebergCommandRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())

    async def asyncTearDown(self) -> None:
        await self.bot.close()

    async def test_iceberg_commands_register_without_tierlist_collisions(self) -> None:
        await self.bot.load_extension("cogs.tierlist")
        await self.bot.add_cog(IcebergCog(self.bot))
        command_names = [command.qualified_name for command in self.bot.tree.walk_commands()]
        self.assertIn("tierlist criar", command_names)
        self.assertIn("iceberg criar", command_names)
        self.assertIn("iceberg anexar", command_names)
        self.assertIn("iceberg importar", command_names)
        self.assertEqual(sorted(name for name in command_names if command_names.count(name) > 1), [])


if __name__ == "__main__":
    unittest.main()
