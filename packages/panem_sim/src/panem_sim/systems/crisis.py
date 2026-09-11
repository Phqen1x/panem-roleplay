"""District crisis thresholds and recovery (Spec §7, `CRISIS_THRESHOLDS`,
`CRISIS_RECOVERY_DAYS`). Stub for Milestone A -- real logic lands in
Phase 4."""

from __future__ import annotations

from panem_sim.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    return []
