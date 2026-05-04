from __future__ import annotations

import tempfile
import unittest
import asyncio
import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace

AIOSQLITE_AVAILABLE = importlib.util.find_spec("aiosqlite") is not None

if AIOSQLITE_AVAILABLE:
    from cogs.tierlist_templates.assets import TierListAssetStore
    from cogs.tierlist_templates.models import TemplateVisibility
    from cogs.tierlist_templates.repository import TierListTemplateRepository
    from cogs.tierlist_templates.services import TierListTemplateService
    from cogs.tierlist_templates.sources import TemplateSourceResolver


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
class TierListTemplateServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        if not await asyncio_threadsafe_callbacks_work():
            self.skipTest("asyncio.call_soon_threadsafe não acorda o loop neste sandbox")
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = TierListTemplateRepository(str(root / "templates.sqlite3"))
        await self.repo.connect()
        self.service = TierListTemplateService(
            repository=self.repo,
            asset_store=TierListAssetStore(root / "assets"),
            source_resolver=TemplateSourceResolver(),
        )

    async def asyncTearDown(self) -> None:
        await self.repo.close()
        self.tmp.cleanup()

    async def test_session_uses_published_version_with_unallocated_items(self) -> None:
        actor = SimpleNamespace(id=123)
        template = await self.service.create_template(
            name="Jogos",
            description="",
            creator_id=actor.id,
            guild_id=456,
            visibility=TemplateVisibility.GUILD,
        )
        await self.service.add_draft_item_from_fields(
            template_id=template.id,
            actor=actor,
            client=SimpleNamespace(),
            guild_id=456,
            raw_name="Minecraft",
        )
        await self.service.publish_template(template_id=template.id, actor_id=actor.id)

        session = await self.service.create_session_from_template(
            template_id=template.id,
            actor_id=actor.id,
            guild_id=456,
            channel_id=789,
        )
        snapshot = await self.service.get_session_snapshot(session_id=session.id, actor_id=actor.id)
        self.assertEqual(len(snapshot.items), 1)
        self.assertIsNone(snapshot.items[0].tier_name)

        await self.service.move_session_item(
            session_id=session.id,
            item_id=snapshot.items[0].id,
            tier_name="S",
            actor_id=actor.id,
        )
        moved_snapshot = await self.service.get_session_snapshot(session_id=session.id, actor_id=actor.id)
        self.assertEqual(moved_snapshot.items[0].tier_name, "S")

        if importlib.util.find_spec("discord") is not None:
            rendered = self.service.render_session_snapshot(moved_snapshot)
            self.assertGreater(len(rendered.getvalue()), 0)


if __name__ == "__main__":
    unittest.main()
