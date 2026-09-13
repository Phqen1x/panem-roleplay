from __future__ import annotations

from panem_shared import memory
from panem_shared.db.models import Memory


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
