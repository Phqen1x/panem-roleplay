"""NPC (and character) memory formation, decay, and cap enforcement
(Spec §6, `MEMORY_CAP_PER_NPC`, `RETRIEVAL_K`).

Formation turns this tick's `state.notable_events` -- appended by
whichever earlier system decided something was worth remembering (a
firing in `jobs.py`, a relationship crossing into a new stance in
`social.py`) -- into real `Memory` rows. Pruning expires anything past
its `expires_tick`, then trims each owner back to `MEMORY_CAP_PER_NPC`
by dropping the least important (oldest as the tiebreak) first; it runs
every tick over `state.memories` (loaded once per tick, no extra
queries) rather than only for owners touched this tick, since expiry
shouldn't wait on that owner doing something else memorable first.

Retrieval (`RETRIEVAL_K`, the *consumer*-side query) lives in
`panem_shared.memory.retrieve` instead of here, since `panem_bot`'s
dialogue service needs it without depending on `panem_sim`.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from panem_shared import constants
from panem_shared.db.models import Memory
from panem_shared.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState

DEFAULT_MEMORY_TTL_TICKS = constants.TICKS_PER_DAY * 90
"""~a season. Spec §6 doesn't give a concrete memory lifetime in this
session's context; ordinary memories fade after this, high-importance
ones (`importance >= HIGH_IMPORTANCE`) never auto-expire."""
HIGH_IMPORTANCE = 4


def _expiry_for(ctx: TickContext, importance: int) -> int | None:
    if importance >= HIGH_IMPORTANCE:
        return None
    return ctx.tick + DEFAULT_MEMORY_TTL_TICKS


def _form_memories(state: WorldState, ctx: TickContext) -> None:
    for note in state.notable_events:
        state.new_memories.append(
            Memory(
                owner_kind=note.owner_kind,
                owner_id=note.owner_id,
                tick=ctx.tick,
                kind=note.kind,
                importance=note.importance,
                subject_kind=note.subject_kind,
                subject_id=note.subject_id,
                text=note.text,
                tags=note.tags,
                expires_tick=_expiry_for(ctx, note.importance),
            )
        )


def _prune(state: WorldState, ctx: TickContext) -> None:
    by_owner: dict[tuple[str, str], list[Memory]] = defaultdict(list)
    for row in state.memories.values():
        by_owner[(row.owner_kind, row.owner_id)].append(row)

    new_counts = Counter((note.owner_kind, note.owner_id) for note in state.notable_events)

    for owner, rows in by_owner.items():
        alive: list[Memory] = []
        for row in rows:
            if row.expires_tick is not None and row.expires_tick <= ctx.tick:
                state.deleted_memory_ids.append(row.id)
            else:
                alive.append(row)

        overflow = len(alive) + new_counts.get(owner, 0) - constants.MEMORY_CAP_PER_NPC
        if overflow > 0:
            alive.sort(key=lambda m: (m.importance, m.tick))
            for row in alive[:overflow]:
                state.deleted_memory_ids.append(row.id)


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    _form_memories(state, ctx)
    _prune(state, ctx)
    return []
