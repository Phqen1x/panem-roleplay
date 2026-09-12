"""District crisis thresholds and recovery (Spec §7, `CRISIS_THRESHOLDS`,
`CRISIS_RECOVERY_DAYS`).

`DistrictState.unrest` (0.0-1.0, clamped) is this system's own running
score. It's fed by two things: a chronic-hunger fraction computed here
each day (the same `HEALTH_DECAY_HUNGER_THRESHOLD` cutoff `needs.py`
already uses for health decay), and quota misses, which
`economy.py::_evaluate_quotas` bumps directly at the same point it
already adjusts `capitol_favor` -- an illicit-market catch
(`panem_bot.services.market`) bumps `DistrictState.peacekeeper_pressure`
the same way, bot-side, since that's a live trade action, not something
that waits for a tick. Both channels decay back toward baseline here
every day nothing new pushes them, over `CRISIS_RECOVERY_DAYS`.

`crisis_level` (0-4) is just how many of `CRISIS_THRESHOLDS`'s four cut
points `unrest` has cleared; `crisis_kind` is a single placeholder label
("unrest") rather than a real taxonomy, since Spec §7's actual crisis
categories weren't available in this session's context -- the mechanism
(escalate, recover, announce a level change) is what's meant to be
right here, not these exact numbers or names.

Runs once per day (a stable daily reading, not per-tick jitter); emits a
`Bulletin` only when a district's `crisis_level` actually changes.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared.events import AnyWorldEvent, Bulletin
from panem_sim.state import TickContext, WorldState

HUNGER_UNREST_WEIGHT = 0.05
"""Placeholder: the unrest added per day when 100% of a district's
population is chronically hungry. Spec §7's real weighting wasn't
available in this session's context."""
PEACEKEEPER_PRESSURE_BASELINE = 0.3
"""`DistrictState.peacekeeper_pressure`'s own column default -- pressure
relaxes back toward this, not to zero, absent new illicit-market catches."""


def _hunger_fraction(state: WorldState, district_id: int) -> float:
    population = [
        character
        for character in state.characters.values()
        if character.current_district_id == district_id
    ] + [npc for npc in state.npcs.values() if npc.district_id == district_id]
    if not population:
        return 0.0
    hungry = sum(
        1 for person in population if person.hunger >= constants.HEALTH_DECAY_HUNGER_THRESHOLD
    )
    return hungry / len(population)


def _crisis_level(unrest: float) -> int:
    return sum(1 for threshold in constants.CRISIS_THRESHOLDS if unrest >= threshold)


def _update_district(state: WorldState, ctx: TickContext, district_id: int) -> AnyWorldEvent | None:
    district_row = state.districts.get(district_id)
    if district_row is None:
        return None

    district_row.unrest = max(
        0.0, district_row.unrest - district_row.unrest / constants.CRISIS_RECOVERY_DAYS
    )
    district_row.unrest = min(
        1.0, district_row.unrest + _hunger_fraction(state, district_id) * HUNGER_UNREST_WEIGHT
    )
    district_row.peacekeeper_pressure += (
        PEACEKEEPER_PRESSURE_BASELINE - district_row.peacekeeper_pressure
    ) / constants.CRISIS_RECOVERY_DAYS

    old_level = district_row.crisis_level
    new_level = _crisis_level(district_row.unrest)
    if new_level == old_level:
        return None

    district_row.crisis_level = new_level
    district_row.crisis_kind = "unrest" if new_level > 0 else None
    verb = "escalates" if new_level > old_level else "eases"
    return Bulletin(
        tick=ctx.tick, district_id=district_id, text=f"Unrest {verb} to level {new_level}."
    )


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    if ctx.tick % constants.TICKS_PER_DAY != 0:
        return []
    events: list[AnyWorldEvent] = []
    for district_id in ctx.content.districts:
        event = _update_district(state, ctx, district_id)
        if event is not None:
            events.append(event)
    return events
