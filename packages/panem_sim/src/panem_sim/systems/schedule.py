"""NPC movement (Spec FR-NPC-1/2/3). Stub for Milestone A -- real schedule-
weight-driven movement and narration batching lands in Milestone B."""

from __future__ import annotations

from panem_sim.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    return []
