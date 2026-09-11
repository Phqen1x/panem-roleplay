"""Shift lifecycle -- opening shifts, NPC auto-completion, miss tracking
(Spec FR-JOB-2/6/7/10). Stub for Milestone A -- real logic lands in
Milestone C (Phase 2)."""

from __future__ import annotations

from panem_shared.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    return []
