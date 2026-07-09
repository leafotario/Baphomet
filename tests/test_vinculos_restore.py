from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord

from cogs.vinculos import VINCULO_TYPE_METADATA, VinculoType, VinculosCog


def _message(
    *,
    author_id: int,
    embed: discord.Embed,
    created_at: datetime,
    content: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        author=SimpleNamespace(id=author_id),
        content=content,
        embeds=[embed],
        created_at=created_at,
    )


class VinculoRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        bot = SimpleNamespace(user=SimpleNamespace(id=999))
        self.cog = VinculosCog(bot, MagicMock())

    def test_extract_recovery_event_ignores_unrecognized_embed(self) -> None:
        embed = discord.Embed(
            title="🎉 Aniversário de Vínculo! 🎉",
            description="1 Ano!",
        )
        message = _message(
            author_id=999,
            embed=embed,
            created_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
            content="<@10> <@20>",
        )

        self.assertIsNone(self.cog._extract_recovery_event(message))

    def test_collect_recovery_records_removes_pairs_broken_later(self) -> None:
        accepted_meta = VINCULO_TYPE_METADATA[VinculoType.PACTO_SANGUE]
        broken_meta = VINCULO_TYPE_METADATA[VinculoType.PACTO_SANGUE]
        active_meta = VINCULO_TYPE_METADATA[VinculoType.PACTO_SERVIDAO]

        accepted = discord.Embed(
            title=accepted_meta.accepted_title,
            description="<@10> e <@20> agora caminham sob o mesmo selo rubro.",
        )
        broken = discord.Embed(
            title=broken_meta.rupture_title,
            description="<@10> quebrou o vínculo com <@20>.",
        )
        still_active = discord.Embed(
            title=active_meta.accepted_title,
            description="<@30> e <@40> aceitaram o peso ritual das correntes.",
        )

        records = self.cog._collect_recovery_records(
            guild_id=123,
            messages=[
                _message(author_id=999, embed=accepted, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
                _message(author_id=999, embed=broken, created_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),
                _message(author_id=999, embed=still_active, created_at=datetime(2026, 1, 3, tzinfo=timezone.utc)),
            ],
        )

        self.assertEqual(len(records), 1)
        self.assertEqual((records[0].user_low_id, records[0].user_high_id), (30, 40))
        self.assertEqual(records[0].bond_type, VinculoType.PACTO_SERVIDAO)

    def test_collect_recovery_records_preserves_type_created_at_and_affinity(self) -> None:
        metadata = VINCULO_TYPE_METADATA[VinculoType.FIO_RIVALIDADE]
        accepted = discord.Embed(
            title=metadata.accepted_title,
            description="<@55> e <@77> agora brilham melhor quando um desafia o outro.",
        )
        affinity = discord.Embed(
            title=f"{metadata.emoji} O pacto amadureceu",
            description="<@55> e <@77> alcançaram **laço da alma** no fio de rivalidade.",
        )
        affinity.add_field(name="Afinidade", value="Nível **3** | bônus **+15%**", inline=False)

        created_at = datetime(2026, 2, 1, 15, 30, tzinfo=timezone.utc)
        records = self.cog._collect_recovery_records(
            guild_id=123,
            messages=[
                _message(author_id=999, embed=accepted, created_at=created_at),
                _message(author_id=999, embed=affinity, created_at=datetime(2026, 4, 1, tzinfo=timezone.utc)),
            ],
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].bond_type, VinculoType.FIO_RIVALIDADE)
        self.assertEqual(records[0].created_at, created_at.isoformat())
        self.assertEqual(records[0].last_announced_affinity_level, 3)

    def test_collect_recovery_records_can_infer_active_pair_from_affinity(self) -> None:
        metadata = VINCULO_TYPE_METADATA[VinculoType.PACTO_SERVIDAO]
        affinity = discord.Embed(
            title=f"{metadata.emoji} O pacto amadureceu",
            description="<@88> e <@99> alcançaram **fio de sangue** no pacto de servidão.",
        )
        affinity.add_field(name="Afinidade", value="Nível **2** | bônus **+10%**", inline=False)
        affinity_time = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

        records = self.cog._collect_recovery_records(
            guild_id=123,
            messages=[_message(author_id=999, embed=affinity, created_at=affinity_time)],
        )

        self.assertEqual(len(records), 1)
        self.assertEqual((records[0].user_low_id, records[0].user_high_id), (88, 99))
        self.assertEqual(records[0].bond_type, VinculoType.PACTO_SERVIDAO)
        self.assertEqual(records[0].created_at, affinity_time.isoformat())
        self.assertEqual(records[0].last_announced_affinity_level, 2)
