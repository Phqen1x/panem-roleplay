from __future__ import annotations

from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import District, DistrictCulture, DistrictMap, Location
from panem_shared.db.models import Character
from panem_shared.enums import CharacterStatus, DayPhase
from panem_shared.events import CharacterArrived, NarrationLine
from panem_sim.rng import tick_rng
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import time as time_system


def make_district(id_: int, name: str) -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
    ]
    coords = {"square": (10, 10), "station": (20, 20)}
    return District(
        id=id_,
        name=name,
        industry="x",
        produces=[],
        imports=[],
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=100, height=100, location_coords=coords),
    )


def make_content() -> ContentBundle:
    return ContentBundle(
        districts={
            1: make_district(1, "District 1"),
            2: make_district(2, "District 2"),
        },
        goods={},
        jobs={},
        routes=[],
    )


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Traveler",
        age=20,
        status=CharacterStatus.APPROVED.value,
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = 1
    return character


def make_ctx(content: ContentBundle, *, tick: int) -> TickContext:
    return TickContext(
        tick=tick,
        phase=DayPhase.MORNING,
        day=1,
        month=1,
        rng=tick_rng("seed", tick),
        content=content,
    )


def make_state(character: Character) -> WorldState:
    return WorldState(
        districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
    )


class TestResolveArrivals:
    def test_not_yet_in_transit_is_untouched(self):
        content = make_content()
        character = make_character()
        state = make_state(character)

        events = time_system.run(state, make_ctx(content, tick=5))

        assert events == []
        assert character.current_district_id == 1

    def test_still_in_transit_before_the_due_tick_is_untouched(self):
        content = make_content()
        character = make_character(
            current_district_id=1, in_transit_until_tick=10, transit_destination_id=2
        )
        state = make_state(character)

        events = time_system.run(state, make_ctx(content, tick=9))

        assert events == []
        assert character.in_transit_until_tick == 10
        assert character.current_district_id == 1

    def test_arrival_moves_character_to_destination_station(self):
        content = make_content()
        character = make_character(
            district_id=1,
            current_district_id=1,
            location_id="station",
            in_transit_until_tick=10,
            transit_destination_id=2,
            away_since_tick=10,
        )
        state = make_state(character)

        time_system.run(state, make_ctx(content, tick=10))

        assert character.current_district_id == 2
        assert character.location_id == "station"
        assert character.x == 20
        assert character.y == 20
        assert character.in_transit_until_tick is None
        assert character.transit_destination_id is None

    def test_arrival_emits_character_arrived_and_narration(self):
        content = make_content()
        character = make_character(
            current_district_id=1, in_transit_until_tick=10, transit_destination_id=2
        )
        state = make_state(character)

        events = time_system.run(state, make_ctx(content, tick=10))

        arrived = [e for e in events if isinstance(e, CharacterArrived)]
        narrated = [e for e in events if isinstance(e, NarrationLine)]
        assert len(arrived) == 1
        assert arrived[0].character_id == 1
        assert arrived[0].district_id == 2
        assert arrived[0].origin_district_id == 1
        assert len(narrated) == 1
        assert narrated[0].district_id == 2
        assert narrated[0].location_id == "station"

    def test_arrival_home_clears_away_since_tick(self):
        content = make_content()
        character = make_character(
            district_id=2,
            current_district_id=1,
            in_transit_until_tick=10,
            transit_destination_id=2,
            away_since_tick=3,
        )
        state = make_state(character)

        time_system.run(state, make_ctx(content, tick=10))

        assert character.away_since_tick is None

    def test_arrival_elsewhere_keeps_away_since_tick(self):
        """Multi-hop travel (home -> A -> B) keeps tracking continuously
        from when the character first left home, not from the latest hop."""
        content = make_content()
        character = make_character(
            district_id=1,
            current_district_id=1,
            in_transit_until_tick=10,
            transit_destination_id=2,
            away_since_tick=3,
        )
        state = make_state(character)

        time_system.run(state, make_ctx(content, tick=10))

        assert character.away_since_tick == 3
