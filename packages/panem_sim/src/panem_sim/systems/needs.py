"""NPC/character needs -- hunger, health, living cost (Spec FR-NDS-1/2/3).
Stub for Milestone A -- real logic lands in Milestone C (Phase 2)."""

from __future__ import annotations

from panem_sim.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    return []
