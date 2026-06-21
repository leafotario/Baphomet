from __future__ import annotations

import unittest
import io
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

import discord
from cogs.vinculos import VinculosCog, ActiveVinculo
from modules.vinculos.rendering.renderer import VinculoCardRenderer


class MockAsset:
    async def read(self) -> bytes:
        return b""

    def replace(self, **kwargs):
        return self


class MockMember:
    def __init__(self, user_id: int, display_name: str) -> None:
        self.id = user_id
        self.display_name = display_name
        self.mention = f"<@{user_id}>"
        self.display_avatar = MockAsset()


class VinculoAnniversaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = MagicMock()
        self.bot = MagicMock()
        self.bot.loop = asyncio.get_event_loop()
        self.cog = VinculosCog(self.bot, self.repository)
        self.cog.vinculo_card_renderer = MagicMock(spec=VinculoCardRenderer)

    async def test_anniversary_check_ignores_non_anniversary_candidates(self) -> None:
        now_mock = datetime(2026, 5, 23, 15, 0, tzinfo=timezone.utc)
        
        # Candidate created today has diff = 0 months, must be ignored
        v1 = ActiveVinculo(
            id=1,
            guild_id=123,
            user_low_id=100,
            user_high_id=200,
            bond_type="pacto_sangue",
            created_at="2026-05-23T15:00:00+00:00",
            ended_at=None,
            active=1,
            last_announced_affinity_level=1,
        )
        self.repository.get_anniversary_candidates = AsyncMock(return_value=[v1])

        with patch("cogs.vinculos.datetime") as mock_datetime:
            mock_datetime.now.return_value = now_mock
            mock_datetime.fromisoformat = datetime.fromisoformat
            
            await self.cog.check_vinculo_anniversaries()

        self.cog.vinculo_card_renderer.render_vinculo_anniversary_card.assert_not_called()

    async def test_anniversary_check_notifies_successfully(self) -> None:
        now_mock = datetime(2026, 5, 23, 15, 0, tzinfo=timezone.utc)
        
        # 1 year ago (12 months diff)
        v = ActiveVinculo(
            id=1,
            guild_id=123,
            user_low_id=100,
            user_high_id=200,
            bond_type="pacto_sangue",
            created_at="2025-05-23T15:00:00+00:00",
            ended_at=None,
            active=1,
            last_announced_affinity_level=1,
        )
        self.repository.get_anniversary_candidates = AsyncMock(return_value=[v])
        
        guild = MagicMock(spec=discord.Guild)
        guild.id = 123
        self.bot.get_guild.return_value = guild
        
        settings = SimpleNamespace(gossip_channel_id=456)
        self.repository.get_guild_settings = AsyncMock(return_value=settings)
        
        channel = MagicMock(spec=discord.TextChannel)
        guild.get_channel.return_value = channel
        
        permissions = MagicMock()
        permissions.send_messages = True
        permissions.embed_links = True
        permissions.attach_files = True
        channel.permissions_for.return_value = permissions
        
        user_a = MockMember(100, "Alice")
        user_b = MockMember(200, "Bob")
        guild.fetch_member = AsyncMock(side_effect=lambda uid: user_a if uid == 100 else user_b)
        
        dummy_file = io.BytesIO(b"dummy image bytes")
        self.cog.vinculo_card_renderer.render_vinculo_anniversary_card = AsyncMock(return_value=dummy_file)
        
        channel.send = AsyncMock()

        with patch("cogs.vinculos.datetime") as mock_datetime:
            mock_datetime.now.return_value = now_mock
            mock_datetime.fromisoformat = datetime.fromisoformat
            
            await self.cog.check_vinculo_anniversaries()

        channel.send.assert_called_once()
        args, kwargs = channel.send.call_args
        self.assertIn("1 Ano!", kwargs["embed"].description)
        self.assertEqual(kwargs["content"], "<@100> <@200>")

    async def test_anniversary_check_handles_missing_member_gracefully(self) -> None:
        now_mock = datetime(2026, 5, 23, 15, 0, tzinfo=timezone.utc)
        
        v = ActiveVinculo(
            id=1,
            guild_id=123,
            user_low_id=100,
            user_high_id=200,
            bond_type="pacto_sangue",
            created_at="2025-05-23T15:00:00+00:00",
            ended_at=None,
            active=1,
            last_announced_affinity_level=1,
        )
        self.repository.get_anniversary_candidates = AsyncMock(return_value=[v])
        
        guild = MagicMock(spec=discord.Guild)
        guild.id = 123
        self.bot.get_guild.return_value = guild
        
        settings = SimpleNamespace(gossip_channel_id=456)
        self.repository.get_guild_settings = AsyncMock(return_value=settings)
        
        channel = MagicMock(spec=discord.TextChannel)
        guild.get_channel.return_value = channel
        
        permissions = MagicMock()
        permissions.send_messages = True
        permissions.embed_links = True
        permissions.attach_files = True
        channel.permissions_for.return_value = permissions
        
        mock_response = MagicMock()
        mock_response.status = 404
        mock_response.reason = "Not Found"
        guild.fetch_member = AsyncMock(side_effect=discord.NotFound(mock_response, "User not found"))

        with patch("cogs.vinculos.datetime") as mock_datetime:
            mock_datetime.now.return_value = now_mock
            mock_datetime.fromisoformat = datetime.fromisoformat
            
            await self.cog.check_vinculo_anniversaries()

        self.cog.vinculo_card_renderer.render_vinculo_anniversary_card.assert_not_called()
        channel.send.assert_not_called()

    async def test_anniversary_card_rendering_success(self) -> None:
        renderer = VinculoCardRenderer()
        user_a = MockMember(100, "Alice")
        user_b = MockMember(200, "Bob")
        
        user_a.display_avatar = None
        user_b.display_avatar = None

        image_fp = await renderer.render_vinculo_anniversary_card(
            participant_a=user_a,
            participant_b=user_b,
            accent=(132, 48, 79),
            time_text="2 Anos!",
            fallback_name_a="Alice",
            fallback_name_b="Bob",
        )
        self.assertIsInstance(image_fp, io.BytesIO)
        self.assertGreater(len(image_fp.getvalue()), 0)
