"""In-memory tick state (Plan §4.1).

`TickContext` is the read-only per-tick environment (the tick number,
day phase, the seeded RNG, and loaded content); `WorldState` holds the
mutable DB-backed rows systems read and write. Both are loaded once at
the start of a tick inside that tick's own DB session -- systems mutate
the ORM rows on `WorldState` directly, matching how `panem_bot`'s
service layer already works (session + ORM objects in, mutate in place,
caller commits), rather than introducing a second snapshot/sync layer.

Fields are added here as the systems that need them land (Milestone A/B
need only `districts`/`npcs`/`npc_schedules`; later milestones add
`characters`, `inventories`, etc. when their systems are built).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from panem_shared.content.loader import ContentBundle
from panem_shared.db.models import Character, DistrictState, JobHistory, Npc, NpcSchedule, Shift
from panem_shared.enums import DayPhase


@dataclass(slots=True)
class TickContext:
    tick: int
    phase: DayPhase
    day: int
    """Day of the current month, 1-indexed (Spec §1.1: 30-day months)."""
    month: int
    """1-indexed, 1-12."""
    rng: random.Random
    content: ContentBundle


@dataclass(slots=True)
class WorldState:
    districts: dict[int, DistrictState]
    npcs: dict[str, Npc]
    npc_schedules: dict[str, list[NpcSchedule]]
    """Keyed by `npc_id`; each NPC's full set of `(phase, location_id,
    weight)` rows."""
    characters: dict[int, Character]
    open_shifts: list[Shift]
    """Every `Shift` row with `result IS NULL` -- the ones `jobs.py` still
    needs to either see resolved (RP credit / `/work`, both outside the
    tick loop) or mark missed once `tick_due` passes."""
    new_shifts: list[Shift] = field(default_factory=list)
    """`Shift` rows `jobs.py` creates this tick (not yet in the session --
    `tick.py` adds them after the systems loop, same as new `WorldEvent`
    rows)."""
    new_job_history: list[JobHistory] = field(default_factory=list)
    """`JobHistory` rows `jobs.py` creates this tick (e.g. on firing);
    persisted by `tick.py` the same way as `new_shifts`."""
