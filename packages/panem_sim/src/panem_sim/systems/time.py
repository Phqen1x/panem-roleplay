"""Tick/phase/day/month advancement (Spec §1.3, FR-TCK-2).

The actual math lives in `panem_shared.simtime` (the bot needs it too, to
display the current time and shift windows to players); re-exported here
so existing `from panem_sim.systems import time` / `time.advance(...)`
call sites don't need to change. Time advancement itself happens before
systems run (the tick loop needs the new `TickContext` to build
`WorldState` for the other systems), not as a side effect of `run()`.

`run()` does carry one piece of real logic despite that: resolving
cross-district transit arrivals (FR-LOC-9). A character's journey is
just "wait until `in_transit_until_tick`" -- no schedule/needs/economy
system has a more natural claim to "is it time yet", and this keeps
`FIXED_ORDER`'s existing member list untouched rather than adding a new
system for one `Character`-mutating step.
"""

from __future__ import annotations

from panem_shared.enums import LocationKind
from panem_shared.events import AnyWorldEvent, CharacterArrived, NarrationLine
from panem_shared.simtime import advance, is_phase_boundary
from panem_sim.state import TickContext, WorldState

__all__ = ["advance", "is_phase_boundary", "run"]


def _resolve_arrivals(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    events: list[AnyWorldEvent] = []
    for character in state.characters.values():
        if character.in_transit_until_tick is None or character.in_transit_until_tick > ctx.tick:
            continue

        origin_id = character.current_district_id
        destination_id = character.transit_destination_id
        character.in_transit_until_tick = None
        character.transit_destination_id = None
        if destination_id is None:
            continue

        destination = ctx.content.districts.get(destination_id)
        station = (
            next((loc for loc in destination.locations if loc.kind == LocationKind.STATION), None)
            if destination is not None
            else None
        )
        character.current_district_id = destination_id
        character.location_id = station.id if station is not None else None
        coords = (
            destination.map.location_coords.get(station.id)
            if destination is not None and station is not None
            else None
        )
        character.x, character.y = coords if coords is not None else (None, None)
        if destination_id == character.district_id:
            character.away_since_tick = None

        events.append(
            CharacterArrived(
                tick=ctx.tick,
                character_id=character.id,
                district_id=destination_id,
                origin_district_id=origin_id,
            )
        )
        if station is not None and destination is not None:
            origin = ctx.content.districts.get(origin_id)
            origin_name = origin.name if origin is not None else "afar"
            events.append(
                NarrationLine(
                    tick=ctx.tick,
                    district_id=destination_id,
                    location_id=station.id,
                    text=f"{character.name} steps off the train from {origin_name}.",
                )
            )
    return events


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    return _resolve_arrivals(state, ctx)
