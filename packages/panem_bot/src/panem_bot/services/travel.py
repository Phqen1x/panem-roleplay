"""Character travel within a district (Spec FR-LOC-2/3, CMD-14/15).

Cross-district travel (ticket purchase, transit ticks, visitor roles) is
Milestone D scope (Plan §5.5, FR-LOC-7/8/9) -- this only covers moving a
character between locations already inside `current_district_id`.
"""

from __future__ import annotations

import random

from panem_bot.errors import NotAllowed, NotFound
from panem_bot.services.proxy import has_location_access
from panem_shared.content.schemas import District, Location
from panem_shared.db.models import Character
from panem_shared.enums import CharacterStatus


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
