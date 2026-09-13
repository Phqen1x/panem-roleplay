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
from panem_shared.db.models import (
    ApartmentLease,
    Character,
    DistrictState,
    JobHistory,
    MarketPrice,
    Memory,
    Npc,
    NpcSchedule,
    Property,
    PropertyAuction,
    RelationshipRow,
    Shift,
)
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
class NotableEvent:
    """A system's note that something worth remembering happened to
    `owner_kind`/`owner_id` this tick -- kept as plain data (not a
    `Memory` row) so a system as early as `jobs.py` can flag one without
    needing `Memory`'s full column set or a DB session; `memory.py`
    (last in `FIXED_ORDER` before `crisis.py`/`games.py`) is what
    actually turns these into persisted rows."""

    owner_kind: str
    owner_id: str
    kind: str
    importance: int
    text: str
    subject_kind: str | None = None
    subject_id: str | None = None
    tags: list[str] = field(default_factory=list)


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
    completed_shifts: list[Shift] = field(default_factory=list)
    """`Shift` rows completed in the trailing `TICKS_PER_DAY` window (a
    fixed lookback, not a "since last aggregation" marker -- simpler than
    adding an aggregated flag, and `economy.py` only reads this on a day
    boundary anyway). `economy.py`'s supply side sums `.output` from these
    for player-driven production."""
    market_prices: dict[tuple[int, str], MarketPrice] = field(default_factory=dict)
    """Existing `MarketPrice` rows, keyed by `(district_id, good_id)`."""
    new_market_prices: list[MarketPrice] = field(default_factory=list)
    """`MarketPrice` rows `economy.py` creates this tick for a district/good
    with no prior price row; persisted by `tick.py` like `new_shifts`."""
    relationships: dict[tuple[str, str, str, str], RelationshipRow] = field(default_factory=dict)
    """Existing `RelationshipRow`s, keyed by `(subject_kind, subject_id,
    object_kind, object_id)` -- `social.py`'s canonical pair ordering."""
    new_relationships: list[RelationshipRow] = field(default_factory=list)
    """`RelationshipRow`s `social.py` creates this tick for a pair with no
    prior row; persisted by `tick.py` like `new_market_prices`."""
    notable_events: list[NotableEvent] = field(default_factory=list)
    """Appended by any system this tick that wants `memory.py` to persist
    a `Memory` row for it (a firing, a relationship crossing into a new
    stance, ...)."""
    memories: dict[int, Memory] = field(default_factory=dict)
    """Every persisted `Memory` row, keyed by id -- loaded once per tick
    so `memory.py` can expire old ones and enforce `MEMORY_CAP_PER_NPC`
    without a DB session of its own."""
    new_memories: list[Memory] = field(default_factory=list)
    """`Memory` rows `memory.py` creates this tick from `notable_events`;
    persisted by `tick.py` like `new_shifts`."""
    deleted_memory_ids: list[int] = field(default_factory=list)
    """`Memory` row ids `memory.py` wants pruned this tick (expired, or
    over `MEMORY_CAP_PER_NPC` for their owner); deleted by `tick.py`."""
    properties: dict[int, Property] = field(default_factory=dict)
    """Every `Property` row, keyed by id -- `panem_sim.systems.housing`
    mutates these in place (payment collection, foreclosure) the same way
    `jobs.py` mutates `characters`."""
    apartment_leases: dict[int, ApartmentLease] = field(default_factory=dict)
    """Every `ApartmentLease` row, keyed by id."""
    property_auctions: dict[int, PropertyAuction] = field(default_factory=dict)
    """Every open `PropertyAuction` row, keyed by id."""
    new_property_auctions: list[PropertyAuction] = field(default_factory=list)
    """`PropertyAuction` rows `housing.py` creates this tick (a
    foreclosure auto-listing a repossessed property); persisted by
    `tick.py` like `new_shifts`."""
