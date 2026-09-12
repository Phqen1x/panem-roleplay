"""Tick/phase/day/month math (Spec §1.3, FR-TCK-2).

Lives in `panem_shared`, not `panem_sim`, because both the sim (to advance
the clock each tick) and the bot (to display the current in-world time and
`is_phase_boundary` shift-window math to players) need it -- the same
reason `events.py` lives here rather than in `panem_sim`.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared.enums import DayPhase

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


def current(persisted_tick: int) -> tuple[int, DayPhase, int, int]:
    """`(tick, phase, day, month)` for the tick already persisted in
    `WorldClock.tick` -- i.e. the state as of the last completed tick,
    matching exactly what that tick's systems saw (`advance` itself
    describes the *next* tick from a previous one, so this is `advance`
    one step back)."""
    return advance(persisted_tick - 1)


def is_phase_boundary(tick: int) -> bool:
    """True on the exact tick a day phase begins (Spec §1.3's 4 phases
    divide `TICKS_PER_DAY` evenly, so this is phase-agnostic: every
    `_TICKS_PER_PHASE`-th tick starts *some* phase). Used by `jobs.py` to
    open a shift/run NPC job completion once per phase window, not once
    per tick within it."""
    return tick % _TICKS_PER_PHASE == 0


def ticks_until_next_phase(tick: int) -> int:
    """How many more ticks until the phase after `tick`'s begins -- 0 if
    `tick` itself is a boundary."""
    return (-tick) % _TICKS_PER_PHASE
