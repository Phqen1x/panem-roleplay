"""Character travel, both within a district (Spec FR-LOC-2/3, CMD-14/15)
and across districts by train (FR-LOC-7/8/9, CMD-14b).

Re-exported from `panem_bot.services.travel` for every existing call
site -- moved here (same reasoning as every other `panem_shared` move
this session) because the web dashboard's Travel tab needs this too and
`panem_api` cannot import `panem_bot`. Nothing here has ever depended on
discord.py; this was a package-boundary accident, not a real coupling.
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.constants import CAPITOL_DISTRICT_ID
from panem_shared.content.schemas import District, Location
from panem_shared.db.models import Character, Inventory
from panem_shared.enums import CharacterStatus, DayPhase, LocationKind, OwnerKind, Position, RpMode
from panem_shared.errors import NotAllowed, NotFound
from panem_shared.jail import check_not_jailed
from panem_shared.location_access import has_location_access
from panem_shared.simtime import current as current_time


def resolve_location(district: District, location_id: str) -> Location:
    location = next((loc for loc in district.locations if loc.id == location_id), None)
    if location is None:
        raise NotFound("location_not_found")
    return location


def check_can_travel(*, character: Character, location: Location, current_tick: int) -> None:
    """FR-LOC-2/3. Raises `NotAllowed` on refusal. Story-mode characters
    bypass the job/position-based location-access gate entirely ("do not
    need to travel to different locations in their district to RP in
    them") -- the within-district `/travel` call this backs is optional
    scenery for them anyway, not a requirement to physically be somewhere
    before RPing there (that's `proxy.check_can_proxy`'s job). They still
    can't reach the outskirts outside night, though -- unlike the job/
    position gate, that one isn't about who a character is, it's about
    when it is, so it applies to every mode alike."""
    if character.status == CharacterStatus.DEAD.value:
        raise NotAllowed("character_dead")
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")
    check_not_jailed(character, current_tick, "travel_jailed")
    if location.kind == LocationKind.OUTSKIRTS:
        _, phase, _, _ = current_time(current_tick)
        if phase != DayPhase.NIGHT:
            raise NotAllowed("outskirts_night_only", name=character.name)
    if character.rp_mode == RpMode.STORY.value:
        return
    if not has_location_access(
        job_title=character.job_title, has_position=bool(character.positions), location=location
    ):
        raise NotAllowed("location_restricted")


def place(district: District, location: Location) -> tuple[float, float] | None:
    """A map pixel position for `location`, jittered within its radius --
    the same placement `panem_sim.systems.schedule` uses for NPCs, so
    players and NPCs land on the same map consistently. `None` if the
    district's map doesn't have coordinates for this location."""
    coords = district.map.location_coords.get(location.id)
    if coords is None:
        return None
    cx, cy = coords
    radius = location.radius
    return cx + random.uniform(-radius, radius), cy + random.uniform(-radius, radius)


def resolve_station(district: District) -> Location:
    """The district's one `kind: station` location (`District` content
    validation already guarantees exactly one exists) -- cross-district
    travel departs from and arrives at this location specifically."""
    station = next((loc for loc in district.locations if loc.kind == LocationKind.STATION), None)
    if station is None:  # pragma: no cover -- guaranteed by content validation
        raise NotFound("location_not_found")
    return station


def is_free_victor_route(character: Character, origin_id: int, destination_id: int) -> bool:
    """A Victor's ticket between their home district and the Capitol is
    free in either direction -- Victors are expected to move between the
    two regularly (mentoring duties, Capitol appearances) without it
    costing them anything, unlike an ordinary cross-district trip. Any
    other route (e.g. a Victor visiting a third district) still costs the
    usual fare."""
    if Position.VICTOR.value not in character.positions:
        return False
    endpoints = {character.district_id, CAPITOL_DISTRICT_ID}
    return origin_id in endpoints and destination_id in endpoints


def is_free_route(character: Character, origin_id: int, destination_id: int) -> bool:
    """Train tickets are round-trip: the leg back to a character's own
    assigned district is always free, since it's already covered by
    whatever ticket got them away from home to begin with -- no matter
    which district they're returning from. A Victor's home<->Capitol
    route is free outright in both directions (`is_free_victor_route`),
    which for the outbound (home -> Capitol) leg is the only case this
    round-trip rule alone wouldn't already cover."""
    if destination_id == character.district_id:
        return True
    return is_free_victor_route(character, origin_id, destination_id)


def check_can_travel_district(
    *,
    character: Character,
    district: District,
    destination_id: int,
    current_tick: int,
) -> None:
    """FR-LOC-7/8/9. `district` is the character's current district
    (`current_district_id`'s content), not their home. Raises
    `NotAllowed`/`NotFound` on refusal; doesn't check transport stock --
    that's a DB-backed `Inventory` lookup, so it stays in `spend_transport`
    below rather than duplicated here. Story-mode characters skip the
    "must be at the station" check -- "no travel wait time or cost to
    travel between districts for these characters" means there's nothing
    to physically catch a train for."""
    if character.status == CharacterStatus.DEAD.value:
        raise NotAllowed("character_dead")
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")
    check_not_jailed(character, current_tick, "travel_jailed")
    if (
        character.in_transit_until_tick is not None
        and character.in_transit_until_tick > current_tick
    ):
        raise NotAllowed("travel_already_in_transit", name=character.name)
    if destination_id == district.id:
        raise NotAllowed("travel_same_district", name=character.name)
    if character.rp_mode == RpMode.STORY.value:
        return
    station = resolve_station(district)
    if character.location_id != station.id:
        raise NotAllowed("travel_not_at_station", name=character.name, station=station.name)


def transit_ticks_for(character: Character) -> int:
    """How many ticks a cross-district trip takes for this character's
    mode: instant (0) for Story, a flat `LIFE_MODE_TRANSIT_TICKS` (1) for
    Life regardless of route, and the full `TRANSIT_TICKS` for Simulation
    -- the only mode still using the sim's own `_resolve_arrivals` delay
    mechanism as originally designed."""
    if character.rp_mode == RpMode.STORY.value:
        return 0
    if character.rp_mode == RpMode.LIFE.value:
        return constants.LIFE_MODE_TRANSIT_TICKS
    return constants.TRANSIT_TICKS


def should_charge_transport(character: Character, origin_id: int, destination_id: int) -> bool:
    """Story-mode trips never cost transport goods, on top of the usual
    free-route exemptions everyone gets (`is_free_route`)."""
    if character.rp_mode == RpMode.STORY.value:
        return False
    return not is_free_route(character, origin_id, destination_id)


def apply_instant_arrival(character: Character, destination: District) -> None:
    """Applies a Story-mode cross-district trip immediately, mirroring
    `panem_sim.systems.time._resolve_arrivals`'s own arrival-application
    logic exactly (station placement, `away_since_tick` clearing) but
    synchronously -- Story mode never sets `in_transit_until_tick` at all,
    so there's nothing for the sim tick to resolve later; "no travel wait
    time" means the destination takes effect the instant the command
    runs, not up to one tick-interval later."""
    station = resolve_station(destination)
    character.current_district_id = destination.id
    character.location_id = station.id
    placed = place(destination, station)
    character.x, character.y = placed if placed is not None else (None, None)
    if destination.id == character.district_id:
        character.away_since_tick = None


async def spend_transport(session: AsyncSession, character: Character) -> None:
    """Deducts `TRANSPORT_UNITS_PER_TRIP` units of the `transport` good
    from `character`'s `Inventory` to cover one cross-district round trip
    -- the replacement for the old flat per-destination cash ticket price.
    Raises `NotAllowed` if they haven't banked enough (bought at a
    district market like any other good, via `/market buy transport`).
    The caller skips calling this entirely for a free route (`is_free_
    route`/`is_free_victor_route`), same as it used to skip the cash
    deduction."""
    row = await session.get(
        Inventory,
        (OwnerKind.CHARACTER.value, str(character.id), constants.TRANSPORT_GOOD_ID),
    )
    if row is None or row.qty < constants.TRANSPORT_UNITS_PER_TRIP:
        raise NotAllowed(
            "travel_insufficient_transport",
            name=character.name,
            qty=constants.TRANSPORT_UNITS_PER_TRIP,
        )
    row.qty -= constants.TRANSPORT_UNITS_PER_TRIP
