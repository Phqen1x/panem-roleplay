"""NPC memory formation/decay/retrieval (Spec §6, `MEMORY_CAP_PER_NPC`,
`RETRIEVAL_K`). Stub for Milestone A -- real logic lands in Phase 3."""

from __future__ import annotations

from panem_shared.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    return []
