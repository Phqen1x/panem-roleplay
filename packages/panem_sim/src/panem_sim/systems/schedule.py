"""NPC movement (Spec FR-NPC-1/2/3).

Each NPC independently picks a location for the current day phase,
weighted by its `npc_schedule` rows (FR-NPC-1). Need modifiers (e.g.
hunger pulling an NPC toward a market) are Milestone C's needs system's
job to fold into those weights once it exists; until then this reads
the schedule weights as-is.

`npc.location_id`/`x`/`y` update every time an NPC's chosen location
changes, regardless of narration -- `/resident where` always reflects
where an NPC actually is. Narration is much narrower: only an arrival at
a job's workplace during its own shift phase, or an arrival home at
night, is worth announcing to a location thread. A resident popping into
the market or square mid-afternoon is mundane background noise a
district's ambient thread doesn't need to see every few minutes; without
this filter, 300-odd NPCs re-rolling a weighted choice every tick made
that thread nearly unreadable. Movement into each *narrated* location is
still batched into at most one `NarrationLine` per location/reason per
tick (FR-NPC-2/3), rather than one per NPC.

`npc.x`/`npc.y` are set from the district map's `location_coords` (the
pixel position the Activity's live map, Phase 5, will render), jittered
within the location's radius so NPCs at the same location don't all
land on the exact same pixel.
"""

from __future__ import annotations

import random

from panem_shared.content.schemas import District, Location
from panem_shared.enums import DayPhase
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


def _join_names(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _narration_text(names: list[str], reason: str) -> str:
    verb = "arrives" if len(names) == 1 else "arrive"
    who = _join_names(names)
    if reason == "work":
        return f"{who} {verb} for their shift."
    return f"{who} {'returns' if len(names) == 1 else 'return'} home for the night."


def _arrival_reason(
    npc_id: str, new_location_id: str, ctx: TickContext, state: WorldState
) -> str | None:
    """Only two kinds of arrival are worth announcing (see module
    docstring): showing up to work a shift, or coming home for the
    night. Everything else -- an NPC's schedule sending them to the
    market, square, or wherever else during the day -- moves them
    silently."""
    npc = state.npcs[npc_id]
    job = ctx.content.jobs.get(npc.job_id) if npc.job_id else None
    if job is not None and new_location_id == job.workplace and ctx.phase == job.shift_phase:
        return "work"
    if new_location_id == npc.home_location_id and ctx.phase == DayPhase.NIGHT:
        return "home"
    return None


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    arrivals: dict[tuple[int, str, str], list[str]] = {}
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

        reason = _arrival_reason(npc_id, new_location_id, ctx, state)

        district = ctx.content.district(npc.district_id)
        by_id = locations_by_district.setdefault(
            npc.district_id, {loc.id: loc for loc in district.locations}
        )
        location = by_id.get(new_location_id)

        npc.location_id = new_location_id
        placed = _place(district, location, ctx.rng)
        if placed is not None:
            npc.x, npc.y = placed

        if reason is not None:
            arrivals.setdefault((npc.district_id, new_location_id, reason), []).append(npc.name)

    return [
        NarrationLine(
            tick=ctx.tick,
            district_id=district_id,
            location_id=location_id,
            text=_narration_text(names, reason),
        )
        for (district_id, location_id, reason), names in arrivals.items()
    ]
