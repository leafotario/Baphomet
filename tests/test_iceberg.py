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


class IcebergManageItemsViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_pagination_options_slice(self) -> None:
        from cogs.iceberg.views import IcebergManageItemsView
        project = make_project()

        project.items = []
        # Populate project with 30 items
        for i in range(30):
            item = ItemConfig(
                id=f"item-{i}",
                layer_id="layer-1",
                title=f"Item Title {i}",
                source=ItemSource(type=ItemSourceType.TEXT, value=f"text {i}", metadata={}),
                display_style=ItemDisplayStyle.CHIP,
                placement=PlacementConfig(),
                sort_order=i,
                created_at="now",
                updated_at="now"
            )
            project.items.append(item)

        view = IcebergManageItemsView(cog=SimpleNamespace(), project=project, panel_message=None)

        # Page 0
        view.current_page = 0
        options_page0 = view.options()
        self.assertEqual(len(options_page0), 25)
        self.assertEqual(options_page0[0].label, "Item Title 0")

        # Page 1
        view.current_page = 1
        options_page1 = view.options()
        self.assertEqual(len(options_page1), 5)
        self.assertEqual(options_page1[0].label, "Item Title 25")


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



    def test_renderer_layout_modes(self) -> None:
        from cogs.iceberg.models import LayerLayoutMode
        project = make_project()

        # Test SCATTER layout deterministic bounds
        project.layers[0].layout_mode = LayerLayoutMode.SCATTER
        buffer_scatter1 = IcebergRenderer().render_project(project, asset_bytes_by_item_id={"image-1": png_bytes()})
        buffer_scatter2 = IcebergRenderer().render_project(project, asset_bytes_by_item_id={"image-1": png_bytes()})
        self.assertEqual(buffer_scatter1.getvalue(), buffer_scatter2.getvalue())

        # Test GRID layout
        project.layers[0].layout_mode = LayerLayoutMode.GRID
        buffer_grid = IcebergRenderer().render_project(project, asset_bytes_by_item_id={"image-1": png_bytes()})
        self.assertIsNotNone(buffer_grid)

        # Test MASONRY layout
        project.layers[0].layout_mode = LayerLayoutMode.MASONRY
        buffer_masonry = IcebergRenderer().render_project(project, asset_bytes_by_item_id={"image-1": png_bytes()})
        self.assertIsNotNone(buffer_masonry)

    def test_renderer_overflow_raises_error(self) -> None:
        from cogs.iceberg.models import LayerLayoutMode, ItemConfig, ItemSource, ItemSourceType, ItemDisplayStyle
        project = make_project()
        project.layers[0].layout_mode = LayerLayoutMode.GRID

        # Add 1000 large items to the first layer to cause an overflow
        items = []
        for i in range(1000):
            items.append(
                ItemConfig(
                    id=f"item-{i}",
                    layer_id="layer-1",
                    title="Muito grande para caber",
                    source=ItemSource(type=ItemSourceType.TEXT, value="Enorme"),
                    display_style=ItemDisplayStyle.CARD,
                    sort_order=i,
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="2026-01-01T00:00:00+00:00",
                )
            )
        project.items = items

        renderer = IcebergRenderer()
        with self.assertRaisesRegex(ValueError, "A camada não comporta os itens"):
            renderer.render_project(project)

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



    async def test_image_url_invalid(self) -> None:
        from cogs.iceberg.sources.providers import ImageUrlProvider
        from cogs.tierlist_templates.exceptions import AssetDownloadError

        class FakeDownloader:
            async def download(self, url: str) -> None:
                raise AssetDownloadError("Acesso bloqueado por segurança", code="ssrf_blocked")

        provider = ImageUrlProvider(downloader=FakeDownloader(), asset_store=SimpleNamespace())
        with self.assertRaisesRegex(IcebergUserError, "Acesso bloqueado"):
            await provider.resolve(value="http://127.0.0.1/image.png")

    async def test_discord_avatar_user_not_found(self) -> None:
        from cogs.iceberg.sources.providers import DiscordAvatarProvider

        class FakeClient:
            async def fetch_user(self, user_id: int):
                raise discord.HTTPException(SimpleNamespace(status=404, reason="Not Found"), "Not Found")

        provider = DiscordAvatarProvider(downloader=SimpleNamespace(), asset_store=SimpleNamespace())
        with self.assertRaises(IcebergUserError) as cm:
            await provider.resolve(value="123456789012345678", client=FakeClient())
        self.assertEqual(cm.exception.code, "avatar_user_not_found")

    async def test_wikipedia_without_thumbnail(self) -> None:
        from cogs.iceberg.sources.providers import WikipediaImageProvider
        from cogs.tierlist_wikipedia.wikipedia import WikipediaUserError

        class FakeWikipediaService:
            async def resolve(self, query: str, **kwargs):
                raise WikipediaUserError("Nenhuma imagem encontrada", code="wikipedia_no_image")

        provider = WikipediaImageProvider(asset_store=SimpleNamespace(), wikipedia_service=FakeWikipediaService())
        with self.assertRaises(IcebergUserError) as cm:
            await provider.resolve(value="Teste sem imagem")
        self.assertEqual(cm.exception.code, "wikipedia_no_image")

    async def test_attachment_too_large(self) -> None:
        from cogs.iceberg.sources.providers import AttachmentImageProvider

        class FakeAttachment:
            size = 10 * 1024 * 1024 # 10MB

        provider = AttachmentImageProvider(downloader=SimpleNamespace(), asset_store=SimpleNamespace())
        with self.assertRaises(IcebergUserError) as cm:
            await provider.resolve(attachment=FakeAttachment())
        self.assertEqual(cm.exception.code, "attachment_too_large")

    async def test_attachment_fake_content_type(self) -> None:
        from cogs.iceberg.sources.providers import AttachmentImageProvider

        class FakeAttachment:
            size = 1024
            content_type = "application/pdf"

        provider = AttachmentImageProvider(downloader=SimpleNamespace(), asset_store=SimpleNamespace())
        with self.assertRaises(IcebergUserError) as cm:
            await provider.resolve(attachment=FakeAttachment())
        self.assertEqual(cm.exception.code, "attachment_not_image")

    async def test_attachment_invalid_truncated_image(self) -> None:
        from cogs.iceberg.sources.providers import AttachmentImageProvider
        from cogs.tierlist_templates.exceptions import AssetValidationError

        class FakeAssetStore:
            async def store_image_bytes(self, raw_bytes: bytes, **kwargs):
                raise AssetValidationError("Imagem corrompida", code="image_invalid")

        class FakeAttachment:
            size = 1024
            content_type = "image/png"
            filename = "test.png"
            url = "http://test.com/test.png"
            async def read(self, **kwargs):
                return b"not an image"

        provider = AttachmentImageProvider(downloader=SimpleNamespace(), asset_store=FakeAssetStore())
        with self.assertRaises(IcebergUserError) as cm:
            await provider.resolve(attachment=FakeAttachment())
        self.assertEqual(cm.exception.code, "image_invalid")


    async def test_wikipedia_modal_returns_candidate_view(self) -> None:
        from cogs.iceberg.modals import IcebergAddItemModal
        from cogs.iceberg.views import IcebergWikipediaCandidateView

        async def mock_search(q):
            return [{"title": "Teste", "pageid": 1, "description": "", "thumbnail_url": ""}]

        class FakeCog:
            class FakeRegistry:
                providers = {
                    ItemSourceType.WIKIPEDIA: SimpleNamespace(
                        search_candidates=mock_search
                    )
                }
            service = SimpleNamespace(source_registry=FakeRegistry)

        cog = FakeCog()
        modal = IcebergAddItemModal(cog, project_id="p1", owner_id=1, source_type=ItemSourceType.WIKIPEDIA, panel_message=None)

        class FakeInteraction:
            class Response:
                async def defer(self, **kwargs): pass
            class Followup:
                async def send(self, content, view=None, ephemeral=True):
                    self.content = content
                    self.view = view
            response = Response()
            followup = Followup()

        modal.title_input._value = "My Title"
        modal.source_input._value = "Teste"
        modal.layer_input._value = "layer-1"

        interaction = FakeInteraction()
        await modal.on_submit(interaction)
        self.assertIsInstance(interaction.followup.view, IcebergWikipediaCandidateView)
        self.assertEqual(interaction.followup.view.candidates[0]["title"], "Teste")


    async def test_wikipedia_search_candidates(self) -> None:
        from cogs.iceberg.sources.providers import WikipediaImageProvider

        class FakeCandidate:
            def __init__(self, pageid, title, description, thumbnail_url):
                self.pageid = pageid
                self.title = title
                self.description = description
                self.thumbnail_url = thumbnail_url

        class FakeResults:
            def __init__(self, candidates):
                self.candidates = candidates

        class FakeWikipediaService:
            async def search(self, query: str, language: str):
                if language == "pt":
                    return FakeResults([FakeCandidate(1, "Teste", "Desc pt", "http://pt")])
                elif language == "en":
                    return FakeResults([FakeCandidate(2, "Test", "Desc en", "http://en")])
                return None

        provider = WikipediaImageProvider(asset_store=SimpleNamespace(), wikipedia_service=FakeWikipediaService())

        # Test pt-BR fallback to pt
        candidates = await provider.search_candidates("teste", locale="pt-BR")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"], "Teste")

        # Test en fallback if pt doesn't exist
        class FakeWikipediaServiceEnFallback:
            async def search(self, query: str, language: str):
                if language == "en":
                    return FakeResults([FakeCandidate(2, "Test", "Desc en", "http://en")])
                return None

        provider_en = WikipediaImageProvider(asset_store=SimpleNamespace(), wikipedia_service=FakeWikipediaServiceEnFallback())
        candidates_en = await provider_en.search_candidates("teste", locale="pt-BR")
        self.assertEqual(len(candidates_en), 1)
        self.assertEqual(candidates_en[0]["title"], "Test")

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
