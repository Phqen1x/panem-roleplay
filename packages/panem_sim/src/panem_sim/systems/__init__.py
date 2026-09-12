"""The tick loop's system order (Spec FR-TCK-2): fixed, never reordered.

`tick.py` runs `FIXED_ORDER` in sequence against one `WorldState`, inside
one DB transaction, collecting every returned `WorldEvent` to publish once
the transaction commits.
"""

from __future__ import annotations

from collections.abc import Callable

from panem_shared.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import crisis, economy, games, jobs, memory, needs, schedule, social, time

SystemFn = Callable[[WorldState, TickContext], list[AnyWorldEvent]]

FIXED_ORDER: list[SystemFn] = [
    time.run,
    schedule.run,
    needs.run,
    jobs.run,
    economy.run,
    social.run,
    memory.run,
    crisis.run,
    games.run,
]
