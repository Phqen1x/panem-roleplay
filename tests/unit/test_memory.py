from __future__ import annotations

from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.db.models import Memory
from panem_shared.enums import DayPhase
from panem_sim.rng import tick_rng
from panem_sim.state import NotableEvent, TickContext, WorldState
from panem_sim.systems import memory


def make_content() -> ContentBundle:
    return ContentBundle(districts={}, goods={}, jobs={}, routes=[])


def make_ctx(*, tick: int) -> TickContext:
    return TickContext(
        tick=tick,
        phase=DayPhase.MORNING,
        day=1,
        month=1,
        rng=tick_rng("seed", tick),
        content=make_content(),
    )


def make_state(**overrides: object) -> WorldState:
    defaults: dict[str, object] = dict(
        districts={}, npcs={}, npc_schedules={}, characters={}, open_shifts=[]
    )
    defaults.update(overrides)
    return WorldState(**defaults)  # type: ignore[arg-type]


def make_memory_row(id_: int, **overrides: object) -> Memory:
    defaults: dict[str, object] = dict(
        owner_kind="npc",
        owner_id="npc1",
        tick=0,
        kind="misc",
        importance=1,
        text="something happened",
        tags=[],
        expires_tick=None,
    )
    defaults.update(overrides)
    row = Memory(**defaults)  # type: ignore[arg-type]
    row.id = id_
    return row


class TestFormMemories:
    def test_notable_event_becomes_a_memory_row(self):
        state = make_state(
            notable_events=[
                NotableEvent(
                    owner_kind="character", owner_id="1", kind="fired", importance=3, text="Fired."
                )
            ]
        )

        memory.run(state, make_ctx(tick=10))

        assert len(state.new_memories) == 1
        row = state.new_memories[0]
        assert row.owner_kind == "character"
        assert row.owner_id == "1"
        assert row.tick == 10
        assert row.text == "Fired."

    def test_low_importance_memory_gets_a_finite_expiry(self):
        state = make_state(
            notable_events=[
                NotableEvent(owner_kind="npc", owner_id="a", kind="misc", importance=1, text="...")
            ]
        )

        memory.run(state, make_ctx(tick=10))

        row = state.new_memories[0]
        assert row.expires_tick == 10 + memory.DEFAULT_MEMORY_TTL_TICKS

    def test_high_importance_memory_never_expires(self):
        state = make_state(
            notable_events=[
                NotableEvent(
                    owner_kind="npc",
                    owner_id="a",
                    kind="milestone",
                    importance=memory.HIGH_IMPORTANCE,
                    text="...",
                )
            ]
        )

        memory.run(state, make_ctx(tick=10))

        assert state.new_memories[0].expires_tick is None

    def test_no_notable_events_creates_no_memories(self):
        state = make_state()

        memory.run(state, make_ctx(tick=10))

        assert state.new_memories == []


class TestPruning:
    def test_expired_memory_is_deleted(self):
        expired = make_memory_row(1, expires_tick=5)
        state = make_state(memories={1: expired})

        memory.run(state, make_ctx(tick=10))

        assert 1 in state.deleted_memory_ids

    def test_unexpired_memory_is_kept(self):
        alive = make_memory_row(1, expires_tick=100)
        state = make_state(memories={1: alive})

        memory.run(state, make_ctx(tick=10))

        assert state.deleted_memory_ids == []

    def test_never_expiring_memory_is_kept(self):
        alive = make_memory_row(1, expires_tick=None)
        state = make_state(memories={1: alive})

        memory.run(state, make_ctx(tick=10))

        assert state.deleted_memory_ids == []

    def test_over_cap_drops_least_important_first(self):
        rows = {
            i: make_memory_row(i, owner_id="npc1", importance=i % 3, tick=i)
            for i in range(1, constants.MEMORY_CAP_PER_NPC + 3)
        }
        state = make_state(memories=rows)

        memory.run(state, make_ctx(tick=10_000))

        assert len(state.deleted_memory_ids) == 2
        kept = set(rows) - set(state.deleted_memory_ids)
        assert len(kept) == constants.MEMORY_CAP_PER_NPC
        # everything dropped is <= everything kept, by (importance, tick)
        dropped_keys = [(rows[i].importance, rows[i].tick) for i in state.deleted_memory_ids]
        kept_keys = [(rows[i].importance, rows[i].tick) for i in kept]
        assert max(dropped_keys) <= min(kept_keys)

    def test_new_memories_this_tick_count_toward_the_cap(self):
        rows = {
            i: make_memory_row(i, owner_id="npc1", importance=1, tick=i)
            for i in range(1, constants.MEMORY_CAP_PER_NPC + 1)
        }
        state = make_state(
            memories=rows,
            notable_events=[
                NotableEvent(owner_kind="npc", owner_id="npc1", kind="misc", importance=1, text="x")
            ],
        )

        memory.run(state, make_ctx(tick=10_000))

        assert len(state.deleted_memory_ids) == 1

    def test_different_owners_are_pruned_independently(self):
        rows = {
            1: make_memory_row(1, owner_id="npc1", expires_tick=5),
            2: make_memory_row(2, owner_id="npc2", expires_tick=100),
        }
        state = make_state(memories=rows)

        memory.run(state, make_ctx(tick=10))

        assert state.deleted_memory_ids == [1]


class TestRetrieve:
    def test_returns_only_the_requested_owners_memories(self):
        rows = [
            make_memory_row(1, owner_id="npc1", importance=1, tick=1),
            make_memory_row(2, owner_id="npc2", importance=5, tick=1),
        ]

        result = memory.retrieve(rows, "npc", "npc1")

        assert [m.id for m in result] == [1]

    def test_orders_by_importance_then_recency(self):
        rows = [
            make_memory_row(1, owner_id="npc1", importance=1, tick=100),
            make_memory_row(2, owner_id="npc1", importance=5, tick=1),
            make_memory_row(3, owner_id="npc1", importance=5, tick=50),
        ]

        result = memory.retrieve(rows, "npc", "npc1")

        assert [m.id for m in result] == [3, 2, 1]

    def test_respects_k(self):
        rows = [make_memory_row(i, owner_id="npc1") for i in range(1, 10)]

        result = memory.retrieve(rows, "npc", "npc1", k=3)

        assert len(result) == 3
