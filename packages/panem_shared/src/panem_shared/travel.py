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
from panem_shared.enums import CharacterStatus, LocationKind, OwnerKind, Position
from panem_shared.errors import NotAllowed, NotFound
from panem_shared.location_access import has_location_access


def resolve_location(district: District, location_id: str) -> Location:
    location = next((loc for loc in district.locations if loc.id == location_id), None)
    if location is None:
        raise NotFound("location_not_found")
    return location


def check_can_travel(*, character: Character, location: Location) -> None:
    """FR-LOC-2/3. Raises `NotAllowed` on refusal."""
    if character.status == CharacterStatus.DEAD.value:
        raise NotAllowed("character_dead")
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")
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
    below rather than duplicated here."""
    if character.status == CharacterStatus.DEAD.value:
        raise NotAllowed("character_dead")
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")
    if character.jailed_until_tick is not None and character.jailed_until_tick > current_tick:
        raise NotAllowed("travel_jailed", name=character.name)
    if (
        character.in_transit_until_tick is not None
        and character.in_transit_until_tick > current_tick
    ):
        raise NotAllowed("travel_already_in_transit", name=character.name)
    if destination_id == district.id:
        raise NotAllowed("travel_same_district", name=character.name)
    station = resolve_station(district)
    if character.location_id != station.id:
        raise NotAllowed("travel_not_at_station", name=character.name, station=station.name)


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
