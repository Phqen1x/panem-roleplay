"""Character travel, both within a district (Spec FR-LOC-2/3, CMD-14/15)
and across districts by train (FR-LOC-7/8/9, CMD-14b).
"""

from __future__ import annotations

import random

from panem_bot.errors import NotAllowed, NotFound
from panem_bot.services.proxy import has_location_access
from panem_shared.content.schemas import District, Location
from panem_shared.db.models import Character
from panem_shared.enums import CharacterStatus, LocationKind


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
        job_id=character.job_id, is_victor=character.is_victor, location=location
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


def ticket_good_id(destination_id: int) -> str:
    return f"train_ticket_d{destination_id}"


def check_can_travel_district(
    *,
    character: Character,
    district: District,
    destination_id: int,
    current_tick: int,
) -> None:
    """FR-LOC-7/8/9. `district` is the character's current district
    (`current_district_id`'s content), not their home. Raises
    `NotAllowed`/`NotFound` on refusal; doesn't check money -- the ticket
    price depends on content the caller already has to look up
    separately (`ticket_good_id` + `ContentBundle.goods`), so that check
    stays in the cog alongside the actual deduction."""
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
