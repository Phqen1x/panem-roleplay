"""NPC movement (Spec FR-NPC-1/2/3).

Each NPC independently picks a location for the current day phase,
weighted by its `npc_schedule` rows (FR-NPC-1). Need modifiers (e.g.
hunger pulling an NPC toward a market) are Milestone C's needs system's
job to fold into those weights once it exists; until then this reads
the schedule weights as-is.

Movement into each location is batched into at most one `NarrationLine`
per location per tick (FR-NPC-2/3), rather than one per NPC, so several
NPCs converging on the same market in one tick doesn't spam the ambient
post. NPCs who stay put produce no narration.

`npc.x`/`npc.y` are set from the district map's `location_coords` (the
pixel position the Activity's live map, Phase 5, will render), jittered
within the location's radius so NPCs at the same location don't all
land on the exact same pixel.
"""

from __future__ import annotations

import random

from panem_shared.content.schemas import District, Location
from panem_shared.events import AnyWorldEvent, NarrationLine
from panem_sim.state import TickContext, WorldState


def _choose_location(rng: random.Random, weights: dict[str, float]) -> str:
    locations = list(weights)
    return rng.choices(locations, weights=list(weights.values()), k=1)[0]


def _place(
    district: District, location: Location | None, rng: random.Random
) -> tuple[float, float] | None:
    if location is None:
        return None
    coords = district.map.location_coords.get(location.id)
    if coords is None:
        return None
    cx, cy = coords
    radius = location.radius
    return cx + rng.uniform(-radius, radius), cy + rng.uniform(-radius, radius)


def _narration_text(names: list[str]) -> str:
    if len(names) == 1:
        return f"{names[0]} arrives."
    if len(names) == 2:
        return f"{names[0]} and {names[1]} arrive."
    return f"{', '.join(names[:-1])}, and {names[-1]} arrive."


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    arrivals: dict[tuple[int, str], list[str]] = {}
    locations_by_district: dict[int, dict[str, Location]] = {}

    for npc_id, npc in state.npcs.items():
        weights = {
            row.location_id: row.weight
            for row in state.npc_schedules.get(npc_id, [])
            if row.phase == ctx.phase.value
        }
        if not weights:
            continue

        new_location_id = _choose_location(ctx.rng, weights)
        if new_location_id == npc.location_id:
            continue

        district = ctx.content.district(npc.district_id)
        by_id = locations_by_district.setdefault(
            npc.district_id, {loc.id: loc for loc in district.locations}
        )
        location = by_id.get(new_location_id)

        npc.location_id = new_location_id
        placed = _place(district, location, ctx.rng)
        if placed is not None:
            npc.x, npc.y = placed

        arrivals.setdefault((npc.district_id, new_location_id), []).append(npc.name)

    return [
        NarrationLine(
            tick=ctx.tick,
            district_id=district_id,
            location_id=location_id,
            text=_narration_text(names),
        )
        for (district_id, location_id), names in arrivals.items()
    ]
