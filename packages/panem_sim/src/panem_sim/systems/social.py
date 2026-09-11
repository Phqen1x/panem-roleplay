"""NPC-NPC and NPC-character social interaction, affinity/stance changes
(Spec §6). Stub for Milestone A -- real logic lands in Phase 3."""

from __future__ import annotations

from panem_sim.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    return []
