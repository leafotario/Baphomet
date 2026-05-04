from __future__ import annotations

import tempfile
import unittest
import asyncio
import importlib.util
import threading
from pathlib import Path

AIOSQLITE_AVAILABLE = importlib.util.find_spec("aiosqlite") is not None

if AIOSQLITE_AVAILABLE:
    from cogs.tierlist_templates.models import TemplateVisibility
    from cogs.tierlist_templates.repository import TierListTemplateRepository


async def asyncio_threadsafe_callbacks_work() -> bool:
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def worker() -> None:
        loop.call_soon_threadsafe(future.set_result, True)

    threading.Thread(target=worker).start()
    try:
        return bool(await asyncio.wait_for(future, 0.5))
    except TimeoutError:
        return False


@unittest.skipIf(not AIOSQLITE_AVAILABLE, "aiosqlite não está instalado neste Python")
class TierListTemplateRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        if not await asyncio_threadsafe_callbacks_work():
            self.skipTest("asyncio.call_soon_threadsafe não acorda o loop neste sandbox")
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "templates.sqlite3")
        self.repo = TierListTemplateRepository(self.db_path)
        await self.repo.connect()

    async def asyncTearDown(self) -> None:
        await self.repo.close()
        self.tmp.cleanup()

    async def test_publish_creates_immutable_version_snapshot(self) -> None:
        template = await self.repo.create_template(
            name="Álbuns",
            description="",
            creator_id=10,
            guild_id=20,
            visibility=TemplateVisibility.GUILD,
        )
        await self.repo.add_draft_item(
            template_id=template.id,
            name="Item original",
            source_type="text",
            source_query=None,
            asset_sha256=None,
            metadata={},
        )

        version_one = await self.repo.publish_template(template_id=template.id, published_by=10)
        snapshot_one = await self.repo.get_version_snapshot(version_one.id)
        self.assertIsNotNone(snapshot_one)
        self.assertEqual([tier.name for tier in snapshot_one.tiers], ["S", "A", "B", "C", "D"])
        self.assertEqual([item.name for item in snapshot_one.items], ["Item original"])

        await self.repo.set_draft_tiers(template_id=template.id, tiers=[("God", None), ("Ok", None)])
        await self.repo.add_draft_item(
            template_id=template.id,
            name="Item novo",
            source_type="text",
            source_query=None,
            asset_sha256=None,
            metadata={},
        )
        version_two = await self.repo.publish_template(template_id=template.id, published_by=10)

        snapshot_one_again = await self.repo.get_version_snapshot(version_one.id)
        snapshot_two = await self.repo.get_version_snapshot(version_two.id)
        self.assertEqual([tier.name for tier in snapshot_one_again.tiers], ["S", "A", "B", "C", "D"])
        self.assertEqual([item.name for item in snapshot_one_again.items], ["Item original"])
        self.assertEqual([tier.name for tier in snapshot_two.tiers], ["God", "Ok"])
        self.assertEqual([item.name for item in snapshot_two.items], ["Item original", "Item novo"])


if __name__ == "__main__":
    unittest.main()
