"""Tick/phase/day/month advancement (Spec §1.3, FR-TCK-2).

The actual math lives in `panem_shared.simtime` (the bot needs it too, to
display the current time and shift windows to players); re-exported here
so existing `from panem_sim.systems import time` / `time.advance(...)`
call sites don't need to change. `run()` is a no-op included in
`FIXED_ORDER` purely so the tick loop's system list matches FR-TCK-2's
fixed order exactly -- time advancement itself happens before systems
run (the tick loop needs the new `TickContext` to build `WorldState` for
the other systems), not as a side effect of this function.
"""

from __future__ import annotations

from panem_shared.events import AnyWorldEvent
from panem_shared.simtime import advance, is_phase_boundary
from panem_sim.state import TickContext, WorldState

__all__ = ["advance", "is_phase_boundary", "run"]


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    return []
