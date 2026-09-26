"""Contraband shipments arriving at each district's Rail Station -- a
discoverable, time-boxed heist opportunity (`panem_sim.systems.shipments`
spawns and expires the `Shipment` row; this module resolves an attempt
against one that's still sitting there). Shares the alert/escape/caught
chain `panem_shared.stealing` already established for `/steal`/`/burgle`,
with its own tuned odds (`constants.SHIPMENT_*`) and one addition neither
of those has: a caught thief also takes a `Character.health` hit
(`SHIPMENT_HEALTH_PENALTY`) -- armed guards, not just peacekeepers giving
chase.

Reuses `Character.last_steal_tick` for its own once-per-phase cooldown --
the same slot `/steal`/`/burgle` already share, so a character gets one
big daring crime attempt per day-phase, not three independent ones.

A shipment is single-use: `apply_shipment_outcome` deletes its row the
moment anyone attempts it, success or failure alike -- the win/loss is
what happened when the attempt was made, not a partial depletion of some
larger stockpile the way `MarketPrice.supply` is. Unlike `/poach`, which
only ever yields `constants.POACH_GOOD_ID`, a shipment's own `good_id`/
`qty` (set once at spawn) decide the payout, so different shipments are
worth different amounts.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.content.schemas import Good
from panem_shared.crime_log import record_crime_log
from panem_shared.db.models import Character, DistrictState, Inventory, Shipment
from panem_shared.enums import CharacterStatus, OwnerKind, RpMode
from panem_shared.errors import NotAllowed, NotFound
from panem_shared.jail import (
    check_not_jailed,
    commit_to_jail,
    crackdown_bad_odds,
    crackdown_good_odds,
)
from panem_shared.simtime import TICKS_PER_PHASE


@dataclass(frozen=True, slots=True)
class ShipmentResult:
    success: bool
    alerted: bool
    caught: bool
    amount: int
    """Units of `good_name` taken home on a success (0 on a miss/catch) --
    never money, same as `stealing.StealResult`."""
    good_name: str | None = None


async def find_shipment_here(
    session: AsyncSession, character: Character, current_tick: int
) -> Shipment | None:
    """The unexpired `Shipment` (if any) sitting at `character`'s current
    district+location -- what `/shipment` and the dashboard's Crime tab
    both need before they can even attempt a steal. `None` covers both
    "nothing ever spawned here" and "it expired" identically; either way
    there's nothing to hit right now."""
    rows = (
        await session.execute(
            select(Shipment).where(
                Shipment.district_id == character.current_district_id,
                Shipment.location_id == character.location_id,
                Shipment.expires_tick > current_tick,
            )
        )
    ).scalars()
    return next(iter(rows), None)


def check_can_steal_shipment(character: Character, shipment: Shipment, current_tick: int) -> None:
    """Raises `NotAllowed`/`NotFound` unless `character` can attempt this
    shipment: approved, not currently jailed, crime-capable (Story mode
    is blocked outright, a Life-mode character can opt out via `/character
    crime`), physically at the shipment's district+location, hasn't
    already tried a steal/burgle/shipment this day-phase (`last_steal_
    tick`, the same shared slot `check_can_burgle` reuses), and the
    shipment hasn't expired out from under them since they looked."""
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")
    if character.rp_mode == RpMode.STORY.value:
        raise NotAllowed("crime_mode_forbidden", name=character.name)
    if character.crime_enabled is False:
        raise NotAllowed("crime_disabled_by_actor", name=character.name)
    check_not_jailed(character, current_tick, "shipment_jailed")
    if (
        character.current_district_id != shipment.district_id
        or character.location_id != shipment.location_id
    ):
        raise NotAllowed("shipment_not_here", name=character.name)
    if shipment.expires_tick <= current_tick:
        raise NotFound("shipment_gone")
    if (
        character.last_steal_tick is not None
        and character.last_steal_tick // TICKS_PER_PHASE == current_tick // TICKS_PER_PHASE
    ):
        raise NotAllowed("steal_on_cooldown", name=character.name)


def shipment_difficulty() -> float:
    """0..1 difficulty for the pickpocket minigame, reused here the same
    way `/steal` uses it -- a guarded shipment sits between `burgle_
    difficulty()` and an NPC pickpocket target, matching `SHIPMENT_BASE_
    SUCCESS`'s own placement."""
    return 1.0 - constants.SHIPMENT_BASE_SUCCESS


async def _grant_good(session: AsyncSession, character: Character, good_id: str, qty: int) -> None:
    """Mirrors `panem_shared.poaching._grant_good`/`panem_shared.stealing.
    _grant_good` exactly -- same per-file-self-contained convention."""
    owner_id = str(character.id)
    row = await session.get(Inventory, (OwnerKind.CHARACTER.value, owner_id, good_id))
    if row is None:
        session.add(
            Inventory(owner_kind=OwnerKind.CHARACTER.value, owner_id=owner_id, good_id=good_id, qty=qty)
        )
    else:
        row.qty += qty


async def _apply_catch_consequences(
    session: AsyncSession,
    *,
    character: Character,
    district_row: DistrictState | None,
    current_tick: int,
) -> None:
    """The shared tail of a caught shipment attempt: fine, jail,
    reputation, a district pressure bump (all mirroring `stealing._apply_
    catch_consequences`), plus the one thing that's different here -- a
    `Character.health` hit, since a guarded shipment's peacekeepers don't
    just fine and march you off, they rough you up first."""
    character.money = max(0, character.money - constants.SHIPMENT_FINE)
    character.health = max(0.0, character.health - constants.SHIPMENT_HEALTH_PENALTY)
    commit_to_jail(character, constants.SHIPMENT_JAIL_TICKS, current_tick)
    character.reputation -= constants.REP_SHIPMENT_CAUGHT_PENALTY
    if district_row is not None:
        district_row.peacekeeper_pressure = min(
            1.0, district_row.peacekeeper_pressure + constants.SHIPMENT_PRESSURE_DELTA
        )


async def _apply_alert_escape_caught(
    session: AsyncSession,
    *,
    character: Character,
    district_row: DistrictState | None,
    current_tick: int,
    rng: random.Random,
) -> ShipmentResult:
    alert_prob = crackdown_bad_odds(constants.SHIPMENT_ALERT_PROB, district_row, current_tick)
    if rng.random() >= alert_prob:
        return ShipmentResult(False, False, False, 0)  # a clean miss

    escape_prob = crackdown_good_odds(
        constants.SHIPMENT_ESCAPE_BASE_PROB, district_row, current_tick
    )
    if rng.random() < escape_prob:
        return ShipmentResult(False, True, False, 0)  # alerted, but got away

    await _apply_catch_consequences(
        session, character=character, district_row=district_row, current_tick=current_tick
    )
    return ShipmentResult(False, True, True, 0)


async def apply_shipment_outcome(
    session: AsyncSession,
    *,
    character: Character,
    shipment: Shipment,
    district_row: DistrictState | None,
    current_tick: int,
    success: bool,
    rng: random.Random,
    goods: dict[str, Good],
) -> ShipmentResult:
    """Given whether the pickpocket-style skill check itself succeeded --
    a roll (`SHIPMENT_BASE_SUCCESS`), or the same pickpocket minigame
    `/steal` uses when launched via Activity -- resolves the rest: a
    clean, consequence-free heist (the shipment's own `good_id`/`qty`,
    not a re-roll -- different shipments are worth different amounts), or
    the alert/escape/caught chain. Deletes `shipment` either way -- a
    single opportunity, spent the moment it's attempted -- and logs to
    `CrimeLog` the same as `apply_steal_outcome`/`apply_burgle_outcome`/
    `apply_poach_outcome`."""
    if success:
        good = goods[shipment.good_id]
        await _grant_good(session, character, shipment.good_id, shipment.qty)
        result = ShipmentResult(True, False, False, shipment.qty, good.name)
    else:
        result = await _apply_alert_escape_caught(
            session, character=character, district_row=district_row, current_tick=current_tick, rng=rng
        )
    await record_crime_log(
        session,
        character_id=character.id,
        kind="shipment",
        tick=current_tick,
        success=result.success,
        caught=result.caught,
        good_name=result.good_name,
        amount=result.amount,
    )
    await session.delete(shipment)
    return result


async def roll_and_apply_shipment(
    session: AsyncSession,
    *,
    character: Character,
    shipment: Shipment,
    district_row: DistrictState | None,
    current_tick: int,
    rng: random.Random,
    goods: dict[str, Good],
) -> ShipmentResult:
    """The RNG-fallback skill check (no Activity configured, or the
    player hits Skip) -- split out from `resolve_shipment` the same way
    `stealing.roll_and_apply_steal` is split from `resolve_steal`."""
    success_prob = crackdown_good_odds(constants.SHIPMENT_BASE_SUCCESS, district_row, current_tick)
    success = rng.random() < success_prob
    return await apply_shipment_outcome(
        session,
        character=character,
        shipment=shipment,
        district_row=district_row,
        current_tick=current_tick,
        success=success,
        rng=rng,
        goods=goods,
    )


async def resolve_shipment(
    session: AsyncSession,
    *,
    character: Character,
    shipment: Shipment,
    current_tick: int,
    rng: random.Random,
    goods: dict[str, Good],
) -> ShipmentResult:
    """Top-level convenience wrapper: gate, set the shared cooldown, then
    the RNG-fallback roll -- mirrors `stealing.resolve_steal`/`poaching.
    resolve_poach`."""
    check_can_steal_shipment(character, shipment, current_tick)
    character.last_steal_tick = current_tick
    district_row = await session.get(DistrictState, shipment.district_id)
    return await roll_and_apply_shipment(
        session,
        character=character,
        shipment=shipment,
        district_row=district_row,
        current_tick=current_tick,
        rng=rng,
        goods=goods,
    )
