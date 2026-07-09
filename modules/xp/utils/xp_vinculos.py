from __future__ import annotations

"""Cálculo de XP explicado por vínculos."""

from collections.abc import Iterable

from .xp_models import BondContribution, PenaltyContribution, VinculoXpContext

VINCULO_RESONANCE_WINDOW_SECONDS = 86_400


def calculate_vinculo_xp_context(
    *,
    base_xp: int,
    bonds: Iterable[BondContribution],
    penalties: Iterable[PenaltyContribution],
    source: str,
) -> VinculoXpContext:
    base_xp = max(0, int(base_xp))
    bond_list = tuple(bonds)
    penalty_list = tuple(penalties)
    positive_rate = sum(
        max(0.0, float(bond.bonus_rate))
        for bond in bond_list
        if bond.resonance_active
    )
    penalty_rate = sum(
        abs(float(penalty.multiplier_delta))
        for penalty in penalty_list
        if penalty.multiplier_delta < 0
    )
    final_multiplier = 1.0 + positive_rate - penalty_rate
    final_xp = max(0, int(round(base_xp * final_multiplier)))
    penalty_pool = max(0, int(round(base_xp * penalty_rate)))
    positive_bonus_pool = max(0, final_xp - base_xp + penalty_pool)
    eligible = [
        bond
        for bond in bond_list
        if bond.resonance_active and bond.bonus_rate > 0
    ]
    allocations = _allocate_largest_remainder(positive_bonus_pool, eligible)
    allocated_total = sum(allocations.values())
    distributed_bonds = tuple(
        BondContribution(
            vinculo_id=bond.vinculo_id,
            partner_id=bond.partner_id,
            bond_type=bond.bond_type,
            affinity_level=bond.affinity_level,
            bonus_rate=bond.bonus_rate,
            resonance_active=bond.resonance_active,
            partner_last_seen_at=bond.partner_last_seen_at,
            resonance_window_seconds=bond.resonance_window_seconds,
            allocated_bonus_xp=allocations.get(bond.vinculo_id, 0),
        )
        for bond in bond_list
    )
    return VinculoXpContext(
        base_xp=base_xp,
        final_xp=final_xp,
        final_multiplier=final_multiplier,
        positive_bonus_rate=positive_rate,
        penalty_rate=penalty_rate,
        positive_bonus_pool=positive_bonus_pool,
        penalty_pool=penalty_pool,
        bond_contributions=distributed_bonds,
        penalty_contributions=penalty_list,
        source=source,
        unallocated_bonus_xp=max(0, positive_bonus_pool - allocated_total),
    )


def _allocate_largest_remainder(pool: int, bonds: list[BondContribution]) -> dict[int, int]:
    if pool <= 0 or not bonds:
        return {}
    total_rate = sum(max(0.0, float(bond.bonus_rate)) for bond in bonds)
    if total_rate <= 0:
        return {}

    allocations: dict[int, int] = {}
    remainders: list[tuple[float, float, int, int]] = []
    allocated = 0
    for bond in bonds:
        raw_share = pool * (max(0.0, float(bond.bonus_rate)) / total_rate)
        whole = int(raw_share)
        allocations[bond.vinculo_id] = whole
        allocated += whole
        remainders.append((raw_share - whole, bond.bonus_rate, -bond.vinculo_id, bond.vinculo_id))

    remaining = pool - allocated
    for _, _, _, vinculo_id in sorted(remainders, reverse=True)[:remaining]:
        allocations[vinculo_id] += 1
    return allocations
