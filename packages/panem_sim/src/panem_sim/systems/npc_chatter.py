"""Random NPC-NPC ambient chatter: two co-located, unengaged NPCs
occasionally strike up a short conversation on their own, purely as world
flavor -- never involving a player, and never as frequent or as long as a
player-started `/engage`.

This system only *decides* that a conversation is happening and who's in
it (`NpcChatter`, `panem_shared.events`) -- it never generates the actual
lines, since nothing in `panem_sim` calls the LLM anywhere in this
codebase. `panem_bot.narrator` is what turns the decision into real
dialogue, generated and posted the same way ordinary ambient narration is.
"""

from __future__ import annotations

from collections import defaultdict

from panem_shared import constants
from panem_shared.events import AnyWorldEvent, NpcChatter
from panem_sim.state import TickContext, WorldState


def _co_located_free_npcs(state: WorldState) -> dict[tuple[int, str], list[str]]:
    """Groups NPC ids by `(district_id, location_id)`, excluding any NPC
    currently pulled into a player's engagement (`engagement_id is not
    None`) -- an engaged NPC is spoken for and shouldn't also be starting
    a side conversation."""
    groups: dict[tuple[int, str], list[str]] = defaultdict(list)
    for npc_id, npc in state.npcs.items():
        if npc.location_id is None or npc.engagement_id is not None:
            continue
        groups[(npc.district_id, npc.location_id)].append(npc_id)
    return groups


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    events: list[AnyWorldEvent] = []
    for (district_id, location_id), npc_ids in _co_located_free_npcs(state).items():
        if len(npc_ids) < 2:
            continue
        if ctx.rng.random() >= constants.NPC_CHATTER_CHANCE_PER_TICK:
            continue
        first, second = ctx.rng.sample(sorted(npc_ids), 2)
        events.append(
            NpcChatter(
                tick=ctx.tick,
                district_id=district_id,
                location_id=location_id,
                npc_ids=(first, second),
            )
        )
    return events
