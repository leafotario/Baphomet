from __future__ import annotations

import unittest
from types import SimpleNamespace

from cogs.vinculos import MIN_COMMON_INTEREST_ROLES, VinculosCog


class VinculoInterestRoleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cog = VinculosCog(SimpleNamespace(), SimpleNamespace())

    def test_exactly_two_common_interest_roles_passes_minimum_gate(self) -> None:
        everyone = _FakeRole(1, 0, default=True)
        music = _FakeRole(10, 10)
        games = _FakeRole(20, 20)
        art = _FakeRole(30, 30)
        shared_non_interest = _FakeRole(40, 40)
        guild = _FakeGuild(123, [everyone, music, games, art, shared_non_interest])
        requester = _FakeMember(100, guild, [everyone, music, games, art, shared_non_interest])
        target = _FakeMember(200, guild, [everyone, music, games, shared_non_interest])

        match = self.cog._build_interest_role_match(
            configured_role_ids=["10", 20, 20, 30, 999, 1],
            requester=requester,
            target=target,
        )

        self.assertEqual(match.common_role_ids, (20, 10))
        self.assertEqual(len(match.common_role_ids), MIN_COMMON_INTEREST_ROLES)
        self.assertFalse(len(match.common_role_ids) < MIN_COMMON_INTEREST_ROLES)
        self.assertEqual(match.missing_configured_role_ids, (999,))
        self.assertEqual(match.ignored_default_role_ids, (1,))
        self.assertEqual(match.requester_interest_role_ids, (10, 20, 30))
        self.assertEqual(match.target_interest_role_ids, (10, 20))
        self.assertEqual(match.shared_non_interest_role_ids, (1, 40))

    def test_duplicate_configured_roles_do_not_inflate_common_count(self) -> None:
        interest = _FakeRole(10, 10)
        guild = _FakeGuild(123, [interest])
        requester = _FakeMember(100, guild, [interest])
        target = _FakeMember(200, guild, [interest])

        common_role_ids = self.cog._common_interest_role_ids(
            configured_role_ids=[10, "10", 10],
            requester=requester,
            target=target,
        )

        self.assertEqual(common_role_ids, [10])
        self.assertEqual(len(common_role_ids), 1)

    async def test_resolve_member_for_role_check_prefers_fetched_member(self) -> None:
        music = _FakeRole(10, 10)
        games = _FakeRole(20, 20)
        guild = _FakeGuild(123, [music, games])
        stale_member = _FakeMember(100, guild, [music])
        fresh_member = _FakeMember(100, guild, [music, games])
        guild.members[100] = fresh_member

        resolved = await self.cog._resolve_member_for_role_check(guild, 100, fallback=stale_member)

        self.assertIs(resolved, fresh_member)
        self.assertEqual([role.id for role in resolved.roles], [10, 20])


class _FakeRole:
    def __init__(self, role_id: int, position: int, *, default: bool = False) -> None:
        self.id = role_id
        self.position = position
        self.mention = f"<@&{role_id}>"
        self._default = default

    def is_default(self) -> bool:
        return self._default


class _FakeGuild:
    def __init__(self, guild_id: int, roles: list[_FakeRole]) -> None:
        self.id = guild_id
        self.roles = {role.id: role for role in roles}
        self.members: dict[int, _FakeMember] = {}

    def get_role(self, role_id: int) -> _FakeRole | None:
        return self.roles.get(role_id)

    def get_member(self, user_id: int) -> _FakeMember | None:
        return self.members.get(user_id)

    async def fetch_member(self, user_id: int) -> _FakeMember:
        return self.members[user_id]


class _FakeMember:
    def __init__(self, user_id: int, guild: _FakeGuild, roles: list[_FakeRole]) -> None:
        self.id = user_id
        self.guild = guild
        self.roles = roles
        self.bot = False
        self.mention = f"<@{user_id}>"
