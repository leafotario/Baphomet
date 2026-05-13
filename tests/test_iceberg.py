from __future__ import annotations

import asyncio
import importlib.util
import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
from cogs.iceberg.renderer import IcebergRenderer, IcebergTemplateError
from cogs.iceberg.repository import IcebergDatabaseManager, IcebergRepository
from cogs.iceberg.service import IcebergService
from cogs.iceberg.sources.providers import AttachmentImageProvider, IcebergUserError, TextItemProvider
from cogs.iceberg.themes import get_theme
from cogs.iceberg.constants import ICEBERG_MAX_LAYERS, ICEBERG_TEMPLATE_TIER_COUNT


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


TEMPLATE_TIER_COLORS = (
    (10, 20, 30, 255),
    (20, 40, 60, 255),
    (30, 60, 90, 255),
    (40, 80, 120, 255),
    (50, 100, 150, 255),
    (60, 120, 180, 255),
    (70, 140, 210, 255),
    (80, 160, 220, 255),
    (90, 180, 230, 255),
    (100, 200, 240, 255),
)


def template_bounds(height: int) -> list[int]:
    return [round(index * height / ICEBERG_TEMPLATE_TIER_COUNT) for index in range(ICEBERG_TEMPLATE_TIER_COUNT + 1)]


def write_test_template(path: Path, *, width: int = 100, height: int = 101) -> None:
    bounds = template_bounds(height)
    image = Image.new("RGBA", (width, height), TEMPLATE_TIER_COLORS[0])
    for index, color in enumerate(TEMPLATE_TIER_COLORS):
        image.paste(color, (0, bounds[index], width, bounds[index + 1]))
    image.save(path, format="PNG")


def make_project(layer_count: int = 7) -> IcebergProject:
    theme = get_theme()
    layer_names = [
        "Ponta",
        "Raso",
        "Submerso",
        "Profundo",
        "Abismo",
        "Camada 6",
        "Camada 7",
        "Camada 8",
        "Camada 9",
        "Camada 10",
    ]
    height_weights = [0.8, 1.2, 1.8, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    layers = [
        LayerConfig(id=f"layer-{index + 1}", name=layer_names[index], order=index, height_weight=height_weights[index])
        for index in range(layer_count)
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


class MemoryIcebergRepository:
    def __init__(self) -> None:
        self.projects: dict[str, IcebergProject] = {}

    async def save_project(self, project: IcebergProject) -> IcebergProject:
        self.projects[project.id] = project
        return project

    async def get_project(self, project_id: str, *, owner_id: int) -> IcebergProject | None:
        project = self.projects.get(project_id)
        if project is None or project.owner_id != owner_id:
            return None
        return project

    async def list_projects_for_user(self, owner_id: int, *, guild_id: int | None = None, limit: int = 10) -> list[IcebergProject]:
        return [
            project
            for project in self.projects.values()
            if project.owner_id == owner_id and (guild_id is None or project.guild_id == guild_id)
        ][:limit]

    async def mark_deleted(self, project_id: str, *, owner_id: int) -> None:
        self.projects.pop(project_id, None)


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
    def render_with_template(self, project: IcebergProject, *, width: int = 100, height: int = 101) -> Image.Image:
        with tempfile.TemporaryDirectory() as tmp:
            template_path = Path(tmp) / "icebergtemplate.png"
            write_test_template(template_path, width=width, height=height)
            buffer = IcebergRenderer(template_path=template_path).render_project(
                project,
                asset_bytes_by_item_id={"image-1": png_bytes()},
            )
            return Image.open(buffer).convert("RGBA")

    def test_renderer_crops_template_for_7_layers(self) -> None:
        project = make_project(layer_count=7)
        project.items = []
        image = self.render_with_template(project)
        bounds = template_bounds(101)
        self.assertEqual(image.size, (100, bounds[7]))
        self.assertEqual(image.getpixel((1, image.height - 1)), TEMPLATE_TIER_COLORS[6])

    def test_renderer_crops_template_for_8_layers(self) -> None:
        project = make_project(layer_count=8)
        project.items = []
        image = self.render_with_template(project)
        bounds = template_bounds(101)
        present_colors = {image.getpixel((1, y)) for y in range(image.height)}
        self.assertEqual(image.size, (100, bounds[8]))
        self.assertIn(TEMPLATE_TIER_COLORS[7], present_colors)
        self.assertNotIn(TEMPLATE_TIER_COLORS[8], present_colors)
        self.assertNotIn(TEMPLATE_TIER_COLORS[9], present_colors)

    def test_renderer_uses_full_template_for_10_layers(self) -> None:
        project = make_project(layer_count=10)
        project.items = []
        image = self.render_with_template(project)
        self.assertEqual(image.size, (100, 101))
        self.assertEqual(image.getpixel((1, image.height - 1)), TEMPLATE_TIER_COLORS[9])

    def test_renderer_preview_resizes_after_crop(self) -> None:
        project = make_project(layer_count=8)
        project.items = []
        with tempfile.TemporaryDirectory() as tmp:
            template_path = Path(tmp) / "icebergtemplate.png"
            write_test_template(template_path, width=1000, height=1001)
            buffer = IcebergRenderer(template_path=template_path).render_project(project, preview=True)
            image = Image.open(buffer).convert("RGBA")
        cropped_height = template_bounds(1001)[8]
        self.assertEqual(image.size, (900, round(cropped_height * 900 / 1000)))

    def test_renderer_accepts_manual_rotation_opacity_and_placement(self) -> None:
        project = make_project(layer_count=7)
        project.items = [
            ItemConfig(
                id="manual-1",
                layer_id="layer-3",
                title="Manual",
                source=ItemSource(type=ItemSourceType.TEXT, value="Manual"),
                display_style=ItemDisplayStyle.CHIP,
                placement=PlacementConfig(x=0.4, y=0.3, scale=1.2, rotation=14.0, opacity=0.55),
                sort_order=0,
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        ]
        image = self.render_with_template(project, width=1200, height=2000)
        self.assertEqual(image.size, (1200, template_bounds(2000)[7]))

    def test_renderer_errors_when_template_is_missing(self) -> None:
        project = make_project(layer_count=7)
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = Path(tmp) / "icebergtemplate.png"
            with self.assertRaisesRegex(IcebergTemplateError, "icebergtemplate.png"):
                IcebergRenderer(template_path=missing_path).render_project(project)

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

        with tempfile.TemporaryDirectory() as tmp:
            template_path = Path(tmp) / "icebergtemplate.png"
            write_test_template(template_path, width=1200, height=2000)
            renderer = IcebergRenderer(template_path=template_path)

            project.layers[0].layout_mode = LayerLayoutMode.SCATTER
            buffer_scatter1 = renderer.render_project(project, asset_bytes_by_item_id={"image-1": png_bytes()})
            buffer_scatter2 = renderer.render_project(project, asset_bytes_by_item_id={"image-1": png_bytes()})
            self.assertEqual(buffer_scatter1.getvalue(), buffer_scatter2.getvalue())

            project.layers[0].layout_mode = LayerLayoutMode.GRID
            buffer_grid = renderer.render_project(project, asset_bytes_by_item_id={"image-1": png_bytes()})
            self.assertIsNotNone(buffer_grid)

            project.layers[0].layout_mode = LayerLayoutMode.MASONRY
            buffer_masonry = renderer.render_project(project, asset_bytes_by_item_id={"image-1": png_bytes()})
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

        with tempfile.TemporaryDirectory() as tmp:
            template_path = Path(tmp) / "icebergtemplate.png"
            write_test_template(template_path, width=1200, height=2000)
            renderer = IcebergRenderer(template_path=template_path)
            with self.assertRaisesRegex(ValueError, "A camada não comporta os itens"):
                renderer.render_project(project)


class IcebergServiceValidationTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, *, renderer: object | None = None) -> IcebergService:
        return IcebergService(
            repository=MemoryIcebergRepository(),
            asset_repository=SimpleNamespace(),
            asset_store=SimpleNamespace(),
            source_registry=SimpleNamespace(),
            renderer=renderer or IcebergRenderer(),
        )

    async def test_create_project_defaults_to_7_layers_and_rejects_outside_range(self) -> None:
        service = self.make_service()
        project = await service.create_project(owner_id=42, guild_id=99, name="Novo")
        self.assertEqual(len(project.layers), 7)

        with self.assertRaisesRegex(IcebergUserError, "mínimo 7"):
            await service.create_project(owner_id=42, guild_id=99, name="Poucas", layer_count=6)

        with self.assertRaisesRegex(IcebergUserError, "máximo 10"):
            await service.create_project(owner_id=42, guild_id=99, name="Muitas", layer_count=ICEBERG_MAX_LAYERS + 1)

    async def test_render_converts_template_error_to_user_error_without_sqlite(self) -> None:
        class BrokenRenderer:
            def render_project(self, *args, **kwargs):
                raise IcebergTemplateError("❌ Não encontrei a template local `icebergtemplate.png`.")

        async def inline_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        service = self.make_service(renderer=BrokenRenderer())
        project = make_project()
        project.items = []
        await service.repository.save_project(project)

        with patch("cogs.iceberg.service.asyncio.to_thread", inline_to_thread):
            with self.assertRaises(IcebergUserError) as cm:
                await service.render_project("project-1", owner_id=42)
        self.assertEqual(cm.exception.code, "template_unavailable")
        self.assertIn("icebergtemplate.png", cm.exception.user_message)


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
            names=["Raso", "Novo", "Submerso", "Profundo", "Abismo", "Camada 6", "Camada 7"],
            weights=[2.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        )
        self.assertEqual([layer.name for layer in updated.layers], ["Raso", "Novo", "Submerso", "Profundo", "Abismo", "Camada 6", "Camada 7"])
        self.assertTrue(all(item.layer_id in {layer.id for layer in updated.layers} for item in updated.items))

    async def test_service_rejects_layer_update_outside_template_range(self) -> None:
        service = IcebergService(
            repository=self.repository,
            asset_repository=SimpleNamespace(),
            asset_store=SimpleNamespace(),
            source_registry=SimpleNamespace(),
            renderer=IcebergRenderer(),
        )
        await self.repository.save_project(make_project())

        with self.assertRaisesRegex(IcebergUserError, "mínimo 7"):
            await service.update_layers("project-1", owner_id=42, names=[f"Camada {index}" for index in range(1, 7)])

        with self.assertRaisesRegex(IcebergUserError, "máximo 10"):
            await service.update_layers("project-1", owner_id=42, names=[f"Camada {index}" for index in range(1, ICEBERG_MAX_LAYERS + 2)])

    async def test_service_converts_template_error_to_user_error(self) -> None:
        class BrokenRenderer:
            def render_project(self, *args, **kwargs):
                raise IcebergTemplateError("❌ Não encontrei a template local `icebergtemplate.png`.")

        service = IcebergService(
            repository=self.repository,
            asset_repository=SimpleNamespace(),
            asset_store=SimpleNamespace(),
            source_registry=SimpleNamespace(),
            renderer=BrokenRenderer(),
        )
        project = make_project()
        project.items = []
        await self.repository.save_project(project)

        with self.assertRaises(IcebergUserError) as cm:
            await service.render_project("project-1", owner_id=42)
        self.assertEqual(cm.exception.code, "template_unavailable")
        self.assertIn("icebergtemplate.png", cm.exception.user_message)


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
