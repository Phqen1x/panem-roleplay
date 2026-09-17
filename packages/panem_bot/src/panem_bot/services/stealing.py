"""`/steal` -- pickpocketing players and NPCs (contraband system).

A success is silent -- no reputation cost either way, undetected. A
failure has two further rolls, mirroring the spec's "successfully steal
without alerting them, or with alerting them... escape or get caught":
`STEAL_ALERT_PROB` decides whether the mark notices at all (a clean
miss otherwise), and `STEAL_ESCAPE_BASE_PROB` decides whether an
alerted character gets away before peacekeepers catch up. Only a real
catch carries any consequence -- jail (via `panem_bot.services.jail`),
a fine, a general reputation hit, and, for an NPC victim, an additional
`RelationshipRow.affinity` hit with that specific NPC.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot.errors import NotAllowed
from panem_bot.services import jail as jail_svc
from panem_shared import constants
from panem_shared.db.models import Character, DistrictState, Npc, RelationshipRow
from panem_shared.enums import CharacterStatus, OwnerKind
from panem_shared.relationships import relationship_key
from panem_shared.simtime import TICKS_PER_PHASE

STEAL_PRESSURE_DELTA = 0.05
"""Mirrors `panem_bot.services.market.ILLICIT_PRESSURE_DELTA` exactly --
same placeholder-weighting caveat (Spec §7's real number wasn't
available in this session's context)."""

StealVictim = Character | Npc


@dataclass(frozen=True, slots=True)
class StealResult:
    success: bool
    alerted: bool
    caught: bool
    amount: int


def check_can_steal(character: Character, victim: StealVictim, current_tick: int) -> None:
    """Raises `NotAllowed` unless `character` can attempt this steal:
    approved, physically at the same location as `victim`, and hasn't
    already tried once this day-phase (`Character.last_steal_tick`,
    compared via `tick // TICKS_PER_PHASE` -- the same boundary math
    `panem_shared.simtime.is_phase_boundary` uses)."""
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")
    if character.location_id is None or character.location_id != victim.location_id:
        raise NotAllowed("steal_not_here", name=character.name)
    if (
        character.last_steal_tick is not None
        and character.last_steal_tick // TICKS_PER_PHASE == current_tick // TICKS_PER_PHASE
    ):
        raise NotAllowed("steal_on_cooldown", name=character.name)


async def resolve_steal(
    session: AsyncSession,
    *,
    character: Character,
    victim: StealVictim,
    district_id: int,
    current_tick: int,
    rng: random.Random,
) -> StealResult:
    check_can_steal(character, victim, current_tick)
    character.last_steal_tick = current_tick

    is_npc = isinstance(victim, Npc)
    base_success = (
        constants.STEAL_FROM_NPC_BASE_SUCCESS
        if is_npc
        else constants.STEAL_FROM_PLAYER_BASE_SUCCESS
    )
    if rng.random() < base_success:
        amount = min(int(victim.money), rng.randint(*constants.STEAL_YIELD_MONEY_RANGE))
        victim.money -= amount
        character.money += amount
        return StealResult(success=True, alerted=False, caught=False, amount=amount)

    if rng.random() >= constants.STEAL_ALERT_PROB:
        return StealResult(success=False, alerted=False, caught=False, amount=0)  # a clean miss

    if rng.random() < constants.STEAL_ESCAPE_BASE_PROB:
        return StealResult(
            success=False, alerted=True, caught=False, amount=0
        )  # alerted, but got away

    character.money = max(0, character.money - constants.STEAL_FINE)
    jail_svc.commit_to_jail(character, constants.STEAL_JAIL_TICKS)
    character.reputation -= constants.REP_STEAL_CAUGHT_GENERAL_PENALTY
    if isinstance(victim, Npc):
        key = relationship_key(
            (OwnerKind.CHARACTER.value, str(character.id)), (OwnerKind.NPC.value, victim.id)
        )
        relationship = await session.get(RelationshipRow, key)
        if relationship is None:
            relationship = RelationshipRow(
                subject_kind=key[0],
                subject_id=key[1],
                object_kind=key[2],
                object_id=key[3],
                affinity=0,
                trust=0.0,
            )
            session.add(relationship)
        relationship.affinity -= constants.REP_STEAL_CAUGHT_VICTIM_PENALTY

    district_row = await session.get(DistrictState, district_id)
    if district_row is not None:
        district_row.peacekeeper_pressure = min(
            1.0, district_row.peacekeeper_pressure + STEAL_PRESSURE_DELTA
        )

    return StealResult(success=False, alerted=True, caught=True, amount=0)
