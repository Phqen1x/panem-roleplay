"""Stealing/burglary outcome resolution (contraband system) shared by
`panem_bot` (`/steal`, `/burgle`, and their RNG-fallback path -- no
`ACTIVITY_PUBLIC_URL` configured, or the player hits Skip) and
`panem_api` (the pickpocket/lockpick Activity's result endpoint, which
needs the same alert/escape/caught consequence chain once the minigame
itself -- not a roll -- has already decided the initial skill check).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.db.models import Character, DistrictState, Npc, Property, RelationshipRow
from panem_shared.enums import CharacterStatus, OwnerKind, PropertyKind
from panem_shared.errors import NotAllowed
from panem_shared.jail import commit_to_jail, crackdown_bad_odds, crackdown_good_odds
from panem_shared.relationships import relationship_key
from panem_shared.simtime import TICKS_PER_PHASE

StealVictim = Character | Npc

STEAL_PRESSURE_DELTA = 0.05
"""Mirrors `panem_bot.services.market.ILLICIT_PRESSURE_DELTA` exactly --
same placeholder-weighting caveat (Spec §7's real number wasn't
available in this session's context)."""


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


def check_can_burgle(
    character: Character, house: Property, current_tick: int, *, owner: Character | None = None
) -> None:
    """Raises `NotAllowed` unless `character` can attempt this burglary:
    approved, physically in the house's district (`Property` carries no
    `location_id` the way a person does, so district presence is the
    closest match to "same location as you"), not its own owner, hasn't
    already stolen or burgled this day-phase -- the same `last_steal_
    tick` cooldown `/steal` uses -- and, when `owner` is given (the
    caller already looked it up to find the house), not currently home:
    `house.location_id` is only ever set for a `HOUSE` (seeded by
    `panem_sim.world.seed_properties`), so a `None` on either side just
    skips this check rather than refusing every burglary."""
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")
    if house.kind != PropertyKind.HOUSE.value:
        raise NotAllowed("burgle_not_a_house")
    if character.current_district_id != house.district_id:
        raise NotAllowed("burgle_wrong_district", name=character.name)
    if house.owner_kind == OwnerKind.CHARACTER.value and house.owner_id == character.id:
        raise NotAllowed("burgle_own_house", name=character.name)
    if (
        owner is not None
        and house.location_id is not None
        and owner.status == CharacterStatus.APPROVED.value
        and owner.location_id == house.location_id
    ):
        raise NotAllowed("burgle_owner_home", name=character.name, owner=owner.name)
    if (
        character.last_steal_tick is not None
        and character.last_steal_tick // TICKS_PER_PHASE == current_tick // TICKS_PER_PHASE
    ):
        raise NotAllowed("steal_on_cooldown", name=character.name)


def steal_difficulty(*, is_npc: bool) -> float:
    """0..1 difficulty for the pickpocket minigame -- an NPC mark is an
    easier target than a player, matching the spec's "different levels
    of difficulty" framing (same tiering `STEAL_FROM_*_BASE_SUCCESS`
    already gave the RNG-fallback path, just read as a minigame
    difficulty instead of a success probability)."""
    base_success = (
        constants.STEAL_FROM_NPC_BASE_SUCCESS
        if is_npc
        else constants.STEAL_FROM_PLAYER_BASE_SUCCESS
    )
    return 1.0 - base_success


def burgle_difficulty() -> float:
    """0..1 difficulty for the lockpick minigame when used on a house --
    a flat harder tier than either `/steal` target (no owner physically
    present to read a "same location" precision off, per
    `BURGLE_BASE_SUCCESS`'s own docstring)."""
    return 1.0 - constants.BURGLE_BASE_SUCCESS


async def _apply_catch_consequences(
    session: AsyncSession,
    *,
    character: Character,
    district_row: DistrictState | None,
    victim: StealVictim | None,
) -> None:
    """The shared tail of a caught steal or burglary: fine, jail,
    reputation, district pressure, and -- an NPC victim only -- the
    additional `RelationshipRow.affinity` hit (Spec §6's relationship
    model has no equivalent row for a player victim)."""
    character.money = max(0, character.money - constants.STEAL_FINE)
    commit_to_jail(character, constants.STEAL_JAIL_TICKS)
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
    if district_row is not None:
        district_row.peacekeeper_pressure = min(
            1.0, district_row.peacekeeper_pressure + STEAL_PRESSURE_DELTA
        )


async def _apply_alert_escape_caught(
    session: AsyncSession,
    *,
    character: Character,
    victim: StealVictim | None,
    district_row: DistrictState | None,
    current_tick: int,
    rng: random.Random,
) -> StealResult:
    alert_prob = crackdown_bad_odds(constants.STEAL_ALERT_PROB, district_row, current_tick)
    if rng.random() >= alert_prob:
        return StealResult(False, False, False, 0)  # a clean miss

    escape_prob = crackdown_good_odds(constants.STEAL_ESCAPE_BASE_PROB, district_row, current_tick)
    if rng.random() < escape_prob:
        return StealResult(False, True, False, 0)  # alerted, but got away

    await _apply_catch_consequences(
        session, character=character, district_row=district_row, victim=victim
    )
    return StealResult(False, True, True, 0)


async def apply_steal_outcome(
    session: AsyncSession,
    *,
    character: Character,
    victim: StealVictim,
    district_row: DistrictState | None,
    current_tick: int,
    success: bool,
    rng: random.Random,
) -> StealResult:
    """Given whether the pickpocket skill check itself succeeded -- a
    roll (`STEAL_FROM_*_BASE_SUCCESS`), or the pickpocket minigame's own
    result when `/steal` launched via Activity -- resolves the rest: a
    clean, consequence-free lift, or the alert/escape/caught chain."""
    if success:
        amount = min(int(victim.money), rng.randint(*constants.STEAL_YIELD_MONEY_RANGE))
        victim.money -= amount
        character.money += amount
        return StealResult(True, False, False, amount)
    return await _apply_alert_escape_caught(
        session,
        character=character,
        victim=victim,
        district_row=district_row,
        current_tick=current_tick,
        rng=rng,
    )


async def apply_burgle_outcome(
    session: AsyncSession,
    *,
    character: Character,
    house_value: float,
    district_row: DistrictState | None,
    current_tick: int,
    success: bool,
    rng: random.Random,
) -> StealResult:
    """The house-burglary counterpart to `apply_steal_outcome` -- same
    alert/escape/caught shape, a payout from the house's own value
    (`BURGLE_YIELD_FRACTION` of `suggested_price`, capped at
    `BURGLE_YIELD_CAP`) instead of debiting a victim, and no
    `RelationshipRow` to touch (a house has no single NPC standing)."""
    if success:
        amount = min(
            constants.BURGLE_YIELD_CAP, round(house_value * constants.BURGLE_YIELD_FRACTION)
        )
        character.money += amount
        return StealResult(True, False, False, amount)
    return await _apply_alert_escape_caught(
        session,
        character=character,
        victim=None,
        district_row=district_row,
        current_tick=current_tick,
        rng=rng,
    )
