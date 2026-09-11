"""Market pricing, quotas, exports, shopkeepers (Spec FR-ECO-1/2/5/6/8/9).
Stub for Milestone A -- real logic lands in Milestone D (Phase 2)."""

from __future__ import annotations

from panem_sim.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    return []
