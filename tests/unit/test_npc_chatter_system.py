from __future__ import annotations

from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.db.models import Npc
from panem_shared.enums import DayPhase
from panem_sim.rng import tick_rng
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import npc_chatter


def make_content() -> ContentBundle:
    return ContentBundle(districts={}, goods={}, jobs={}, routes=[])


def make_npc(
    npc_id: str, *, district_id: int = 1, location_id: str = "square", **overrides: object
) -> Npc:
    defaults: dict[str, object] = dict(
        id=npc_id,
        district_id=district_id,
        name=npc_id,
        age=30,
        location_id=location_id,
    )
    defaults.update(overrides)
    return Npc(**defaults)  # type: ignore[arg-type]


def make_ctx(*, tick: int = 1) -> TickContext:
    return TickContext(
        tick=tick,
        phase=DayPhase.MORNING,
        day=1,
        month=1,
        rng=tick_rng("test-seed", tick),
        content=make_content(),
    )


def make_state(*npcs: Npc) -> WorldState:
    return WorldState(
        districts={},
        npcs={n.id: n for n in npcs},
        npc_schedules={},
        characters={},
        open_shifts=[],
    )


class TestNpcChatter:
    def test_no_event_with_fewer_than_two_npcs_present(self, monkeypatch):
        monkeypatch.setattr(constants, "NPC_CHATTER_CHANCE_PER_TICK", 1.0)
        state = make_state(make_npc("npc1"))
        assert npc_chatter.run(state, make_ctx()) == []

    def test_no_event_when_the_odds_never_hit(self, monkeypatch):
        monkeypatch.setattr(constants, "NPC_CHATTER_CHANCE_PER_TICK", 0.0)
        state = make_state(make_npc("npc1"), make_npc("npc2"))
        assert npc_chatter.run(state, make_ctx()) == []

    def test_event_fires_with_certainty_and_names_two_npcs(self, monkeypatch):
        monkeypatch.setattr(constants, "NPC_CHATTER_CHANCE_PER_TICK", 1.0)
        state = make_state(make_npc("npc1"), make_npc("npc2"))
        events = npc_chatter.run(state, make_ctx())
        assert len(events) == 1
        event = events[0]
        assert event.district_id == 1
        assert event.location_id == "square"
        assert set(event.npc_ids) == {"npc1", "npc2"}

    def test_engaged_npcs_never_selected(self, monkeypatch):
        monkeypatch.setattr(constants, "NPC_CHATTER_CHANCE_PER_TICK", 1.0)
        engaged = make_npc("npc1")
        engaged.engagement_id = 42
        free = make_npc("npc2")
        state = make_state(engaged, free)
        # Only one free NPC remains at the location -- never enough to chat.
        assert npc_chatter.run(state, make_ctx()) == []

    def test_npcs_at_different_locations_do_not_chat(self, monkeypatch):
        monkeypatch.setattr(constants, "NPC_CHATTER_CHANCE_PER_TICK", 1.0)
        state = make_state(
            make_npc("npc1", location_id="square"), make_npc("npc2", location_id="market")
        )
        assert npc_chatter.run(state, make_ctx()) == []

    def test_npc_with_no_location_is_ignored(self, monkeypatch):
        monkeypatch.setattr(constants, "NPC_CHATTER_CHANCE_PER_TICK", 1.0)
        state = make_state(make_npc("npc1", location_id=None), make_npc("npc2"))
        assert npc_chatter.run(state, make_ctx()) == []
