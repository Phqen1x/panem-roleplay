"""Illegal hunting/gathering at a district's outskirts -- the coping
mechanism for a market allocation (`panem_sim.systems.economy`'s national
redistribution) too thin to live on: if a district's cut of a necessity
leaves someone unable to afford or find enough of it, they can risk
poaching some instead. Reuses the exact detection/consequence shape
`panem_bot.services.market` already applies to a caught illicit-market
trade (a probability roll, then a fine, jail time, a reputation hit, and
a district `peacekeeper_pressure` bump) -- getting caught poaching is no
better or worse than getting caught buying off the books.

Re-exported from `panem_bot.services.poaching` for every existing call
site -- moved here (same reasoning as every other `panem_shared` move
this session) because the web dashboard's Crime tab needs this too and
`panem_api` cannot import `panem_bot`. Nothing here has ever depended on
discord.py; this was a package-boundary accident, not a real coupling.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.content.schemas import District, Good, Location
from panem_shared.crime_log import record_crime_log
from panem_shared.db.models import Character, DistrictState, Inventory
from panem_shared.enums import CharacterStatus, LocationKind, OwnerKind, RpMode
from panem_shared.errors import NotAllowed, NotFound
from panem_shared.jail import check_not_jailed, commit_to_jail
from panem_shared.simtime import TICKS_PER_PHASE

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


def check_can_poach(
    character: Character, district: District, goods: dict[str, Good], current_tick: int
) -> Good:
    """Raises `NotAllowed`/`NotFound` on refusal; otherwise returns the
    good a successful attempt would yield. Gates a once-per-day-phase
    cooldown off `Character.last_poach_tick`, the same shape (and
    boundary math) `panem_shared.stealing.check_can_steal` uses for
    `last_steal_tick` -- unlimited poaching would make it a strictly
    better market allocation instead of the occasional coping mechanism
    it's meant to be. Story mode has no crime access at all -- "no ...
    crime" -- since poaching has no player victim, only the actor's own
    mode matters here."""
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")
    if character.rp_mode == RpMode.STORY.value:
        raise NotAllowed("crime_mode_forbidden", name=character.name)
    check_not_jailed(character, current_tick, "poach_jailed")
    location = resolve_outskirts(district)
    if character.location_id != location.id:
        raise NotAllowed("poach_not_at_outskirts", name=character.name, location=location.name)
    if (
        character.last_poach_tick is not None
        and character.last_poach_tick // TICKS_PER_PHASE == current_tick // TICKS_PER_PHASE
    ):
        raise NotAllowed("poach_on_cooldown", name=character.name)
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
    session: AsyncSession, character: Character, district_id: int, current_tick: int
) -> None:
    character.money = max(0, character.money - constants.POACH_FINE)
    commit_to_jail(character, constants.POACH_JAIL_TICKS, current_tick)
    character.reputation -= constants.POACH_REP_PENALTY

    district_row = await session.get(DistrictState, district_id)
    if district_row is not None:
        district_row.peacekeeper_pressure = min(
            1.0, district_row.peacekeeper_pressure + PEACEKEEPER_PRESSURE_DELTA
        )


def poach_difficulty() -> float:
    """0..1 difficulty for the archery minigame -- flat, like
    `panem_shared.stealing.burgle_difficulty()` (no per-target tiering
    the way `/steal` has), derived from the same `POACH_ARCHERY_BASE_
    SUCCESS` the RNG-fallback path rolls against, so a harder-looking
    client-side target genuinely tracks a lower auto-resolve odds."""
    return 1.0 - constants.POACH_ARCHERY_BASE_SUCCESS


async def apply_poach_outcome(
    session: AsyncSession,
    *,
    character: Character,
    good: Good,
    district_id: int,
    current_tick: int,
    success: bool,
    rng: random.Random,
) -> PoachResult:
    """Given whether the archery minigame itself was won (3+ hits out of
    5 arrows in 30 seconds) -- or the RNG-fallback roll standing in for
    it -- resolves the rest: a peacekeeper can still notice regardless of
    how the hunt itself went (`POACH_DETECTION_PROB`, unchanged from
    before the minigame existed), and only a clean, unnoticed attempt
    that also landed its shots brings anything home. Logs the attempt to
    `CrimeLog` either way, the same as `apply_steal_outcome`/`apply_
    burgle_outcome`."""
    if rng.random() < constants.POACH_DETECTION_PROB:
        await _apply_caught_consequence(session, character, district_id, current_tick)
        result = PoachResult(caught=True, good=None)
    elif not success:
        result = PoachResult(caught=False, good=None)
    else:
        await _grant_good(session, character, good.id, constants.POACH_YIELD_QTY)
        result = PoachResult(caught=False, good=good)
    await record_crime_log(
        session,
        character_id=character.id,
        kind="poach",
        tick=current_tick,
        success=result.good is not None,
        caught=result.caught,
        good_name=result.good.name if result.good is not None else None,
        amount=constants.POACH_YIELD_QTY if result.good is not None else 0,
    )
    return result


async def roll_and_apply_poach(
    session: AsyncSession,
    *,
    character: Character,
    good: Good,
    district_id: int,
    current_tick: int,
    rng: random.Random,
) -> PoachResult:
    """The RNG-fallback skill check (no Activity configured, or the
    player hits Skip) -- split out from `resolve_poach` so `/poach`'s
    cog can call this directly for an already-validated, already-
    cooldown-set attempt, the same split `panem_shared.stealing.roll_
    and_apply_steal` makes for `/steal`."""
    success = rng.random() < constants.POACH_ARCHERY_BASE_SUCCESS
    return await apply_poach_outcome(
        session,
        character=character,
        good=good,
        district_id=district_id,
        current_tick=current_tick,
        success=success,
        rng=rng,
    )


async def resolve_poach(
    session: AsyncSession,
    *,
    character: Character,
    district: District,
    goods: dict[str, Good],
    current_tick: int,
    rng: random.Random,
) -> PoachResult:
    """Top-level convenience wrapper: gate, set the cooldown, then the
    RNG-fallback roll -- mirrors `panem_shared.stealing.resolve_steal`."""
    good = check_can_poach(character, district, goods, current_tick)
    character.last_poach_tick = current_tick
    return await roll_and_apply_poach(
        session,
        character=character,
        good=good,
        district_id=district.id,
        current_tick=current_tick,
        rng=rng,
    )
