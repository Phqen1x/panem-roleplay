"""Illegal hunting/gathering at a district's outskirts -- the coping
mechanism for a market allocation (`panem_sim.systems.economy`'s national
redistribution) too thin to live on: if a district's cut of a necessity
leaves someone unable to afford or find enough of it, they can risk
poaching some instead. Reuses the exact detection/consequence shape
`panem_bot.services.market` already applies to a caught illicit-market
trade (a probability roll, then a fine, jail time, a reputation hit, and
a district `peacekeeper_pressure` bump) -- getting caught poaching is no
better or worse than getting caught buying off the books.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot.errors import NotAllowed, NotFound
from panem_bot.services import jail as jail_svc
from panem_shared import constants
from panem_shared.content.schemas import District, Good, Location
from panem_shared.db.models import Character, DistrictState, Inventory
from panem_shared.enums import CharacterStatus, LocationKind, OwnerKind

PEACEKEEPER_PRESSURE_DELTA = 0.05
"""Mirrors `panem_bot.services.market.ILLICIT_PRESSURE_DELTA` exactly --
same placeholder-weighting caveat (Spec §7's real number wasn't available
in this session's context)."""


@dataclass(frozen=True, slots=True)
class PoachResult:
    caught: bool
    good: Good | None
    """The good gained, or `None` when caught -- nothing was actually
    taken home."""


def _primary_food_good(district: District, goods: dict[str, Good]) -> Good | None:
    """The one food-category good a successful attempt yields -- whatever
    the district itself produces, falling back to whatever food it
    imports (a non-agricultural district still has hungry people). `None`
    only for a district with no food-category good at all in either list,
    which no real district content ships (every district produces or
    imports at least one food good)."""
    for good_id in (*district.produces, *district.imports):
        good = goods.get(good_id)
        if good is not None and good.category == "food":
            return good
    return None


def resolve_outskirts(district: District) -> Location:
    """The district's `kind: outskirts` location, if it has one -- the
    Capitol doesn't (no district content authors one), so poaching is
    simply unavailable there."""
    location = next((loc for loc in district.locations if loc.kind == LocationKind.OUTSKIRTS), None)
    if location is None:
        raise NotFound("poach_no_outskirts")
    return location


def check_can_poach(character: Character, district: District, goods: dict[str, Good]) -> Good:
    """Raises `NotAllowed`/`NotFound` on refusal; otherwise returns the
    good a successful attempt would yield."""
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")
    location = resolve_outskirts(district)
    if character.location_id != location.id:
        raise NotAllowed("poach_not_at_outskirts", name=character.name, location=location.name)
    good = _primary_food_good(district, goods)
    if good is None:  # pragma: no cover -- no shipped district lacks a food good
        raise NotFound("poach_nothing_to_poach")
    return good


async def _grant_good(session: AsyncSession, character: Character, good_id: str, qty: int) -> None:
    owner_id = str(character.id)
    row = await session.get(Inventory, (OwnerKind.CHARACTER.value, owner_id, good_id))
    if row is None:
        session.add(
            Inventory(
                owner_kind=OwnerKind.CHARACTER.value, owner_id=owner_id, good_id=good_id, qty=qty
            )
        )
    else:
        row.qty += qty


async def _apply_caught_consequence(
    session: AsyncSession, character: Character, district_id: int
) -> None:
    character.money = max(0, character.money - constants.POACH_FINE)
    jail_svc.commit_to_jail(character, constants.POACH_JAIL_TICKS)
    character.reputation -= constants.POACH_REP_PENALTY

    district_row = await session.get(DistrictState, district_id)
    if district_row is not None:
        district_row.peacekeeper_pressure = min(
            1.0, district_row.peacekeeper_pressure + PEACEKEEPER_PRESSURE_DELTA
        )


async def resolve_poach(
    session: AsyncSession,
    *,
    character: Character,
    district: District,
    goods: dict[str, Good],
    rng: random.Random,
) -> PoachResult:
    good = check_can_poach(character, district, goods)
    if rng.random() < constants.POACH_DETECTION_PROB:
        await _apply_caught_consequence(session, character, district.id)
        return PoachResult(caught=True, good=None)
    await _grant_good(session, character, good.id, constants.POACH_YIELD_QTY)
    return PoachResult(caught=False, good=good)
