from __future__ import annotations

from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import District, DistrictCulture, DistrictMap, Location
from panem_shared.db.models import Shipment
from panem_shared.enums import DayPhase
from panem_sim.rng import tick_rng
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import shipments


def make_district(district_id: int = 1) -> District:
    # Every district's real content authors a `kind: station` location
    # (FR-LOC-7 enforces it schema-wide), so there's no "no rail station"
    # case to test here -- shipments.py's own `station is None` guard
    # exists purely as defense, not a reachable branch.
    locations = [
        Location(id="home", name="Home", kind="residential"),
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
    ]
    coords = {loc.id: (0, 0) for loc in locations}
    return District(
        id=district_id,
        name=f"District {district_id}",
        industry="luxury",
        produces=[],
        imports=[],
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=500, height=500, location_coords=coords),
    )


def make_content(*districts: District) -> ContentBundle:
    return ContentBundle(districts={d.id: d for d in districts}, goods={}, jobs={}, routes=[])


def make_ctx(content: ContentBundle, *, tick: int = 1) -> TickContext:
    return TickContext(
        tick=tick,
        phase=DayPhase.MORNING,
        day=1,
        month=1,
        rng=tick_rng("test-seed", tick),
        content=content,
    )


def make_state(*existing_shipments: Shipment) -> WorldState:
    return WorldState(
        districts={},
        npcs={},
        npc_schedules={},
        characters={},
        open_shifts=[],
        shipments={s.id: s for s in existing_shipments},
    )


class TestShipmentSpawn:
    def test_no_spawn_when_the_odds_never_hit(self, monkeypatch):
        monkeypatch.setattr(constants, "SHIPMENT_SPAWN_CHANCE_PER_TICK", 0.0)
        content = make_content(make_district())
        state = make_state()
        shipments.run(state, make_ctx(content))
        assert state.new_shipments == []

    def test_spawns_with_certainty_at_the_rail_station(self, monkeypatch):
        monkeypatch.setattr(constants, "SHIPMENT_SPAWN_CHANCE_PER_TICK", 1.0)
        content = make_content(make_district())
        state = make_state()
        events = shipments.run(state, make_ctx(content, tick=5))
        assert len(state.new_shipments) == 1
        spawned = state.new_shipments[0]
        assert spawned.district_id == 1
        assert spawned.location_id == "station"
        assert spawned.good_id in constants.SHIPMENT_LOOT_GOOD_IDS
        assert spawned.spawned_tick == 5
        assert spawned.expires_tick == 5 + constants.SHIPMENT_WINDOW_TICKS
        assert len(events) == 1
        assert events[0].kind == "Bulletin"

    def test_no_second_spawn_while_one_is_already_active(self, monkeypatch):
        monkeypatch.setattr(constants, "SHIPMENT_SPAWN_CHANCE_PER_TICK", 1.0)
        content = make_content(make_district())
        existing = Shipment(
            id=1,
            district_id=1,
            location_id="station",
            good_id=constants.SHIPMENT_LOOT_GOOD_IDS[0],
            qty=1,
            spawned_tick=0,
            expires_tick=100,
        )
        state = make_state(existing)
        shipments.run(state, make_ctx(content))
        assert state.new_shipments == []

    def test_each_district_rolls_its_own_spawn_independently(self, monkeypatch):
        monkeypatch.setattr(constants, "SHIPMENT_SPAWN_CHANCE_PER_TICK", 1.0)
        content = make_content(make_district(1), make_district(2))
        state = make_state()
        shipments.run(state, make_ctx(content))
        assert {s.district_id for s in state.new_shipments} == {1, 2}


class TestShipmentExpiry:
    def test_expired_shipment_is_marked_for_deletion(self, monkeypatch):
        monkeypatch.setattr(constants, "SHIPMENT_SPAWN_CHANCE_PER_TICK", 0.0)
        content = make_content(make_district())
        expired = Shipment(
            id=1,
            district_id=1,
            location_id="station",
            good_id=constants.SHIPMENT_LOOT_GOOD_IDS[0],
            qty=1,
            spawned_tick=0,
            expires_tick=5,
        )
        state = make_state(expired)
        shipments.run(state, make_ctx(content, tick=5))
        assert state.deleted_shipment_ids == [1]

    def test_unexpired_shipment_is_left_alone(self, monkeypatch):
        monkeypatch.setattr(constants, "SHIPMENT_SPAWN_CHANCE_PER_TICK", 0.0)
        content = make_content(make_district())
        active = Shipment(
            id=1,
            district_id=1,
            location_id="station",
            good_id=constants.SHIPMENT_LOOT_GOOD_IDS[0],
            qty=1,
            spawned_tick=0,
            expires_tick=100,
        )
        state = make_state(active)
        shipments.run(state, make_ctx(content, tick=5))
        assert state.deleted_shipment_ids == []
