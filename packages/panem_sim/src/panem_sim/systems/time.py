"""Tick/phase/day/month advancement (Spec §1.3, FR-TCK-2).

`advance()` is the real clock math every other system's `TickContext` is
built from. `run()` is a no-op included in `FIXED_ORDER` purely so the
tick loop's system list matches FR-TCK-2's fixed order exactly -- time
advancement itself happens before systems run (the tick loop needs the
new `TickContext` to build `WorldState` for the other systems), not as a
side effect of this function.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared.enums import DayPhase
from panem_shared.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState

_PHASE_ORDER = (DayPhase.NIGHT, DayPhase.MORNING, DayPhase.AFTERNOON, DayPhase.EVENING)
_TICKS_PER_PHASE = constants.TICKS_PER_DAY // len(_PHASE_ORDER)


def advance(previous_tick: int) -> tuple[int, DayPhase, int, int]:
    """Given the previously-persisted tick, return the next
    `(tick, phase, day, month)`. `day` is 1-indexed within the month,
    `month` is 1-indexed within a 12-month year (Spec §1.1: 30-day months)."""
    tick = previous_tick + 1
    hour_of_day = tick % constants.TICKS_PER_DAY
    phase = _PHASE_ORDER[hour_of_day // _TICKS_PER_PHASE]
    day_index = tick // constants.TICKS_PER_DAY
    day = (day_index % constants.DAYS_PER_MONTH) + 1
    month = (day_index // constants.DAYS_PER_MONTH) % 12 + 1
    return tick, phase, day, month


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    return []
