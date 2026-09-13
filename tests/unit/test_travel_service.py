from __future__ import annotations

import pytest

from panem_bot.errors import NotAllowed, NotFound
from panem_bot.services import travel as travel_svc
from panem_shared.content.schemas import District, DistrictCulture, DistrictMap, Location
from panem_shared.db.models import Character
from panem_shared.enums import CharacterStatus


def make_district() -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
        Location(id="labs", name="Gamemaker Labs", kind="workplace", restricted=True),
    ]
    coords = {"square": (10, 10), "station": (20, 20), "labs": (30, 30)}
    return District(
        id=12,
        name="District 12",
        industry="coal",
        produces=["coal"],
        imports=[],
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=100, height=100, location_coords=coords),
    )


def make_character(**overrides: object) -> Character:
    defaults = dict(
        user_id=1,
        district_id=12,
        current_district_id=12,
        name="Katniss",
        age=16,
        status=CharacterStatus.APPROVED.value,
    )
    defaults.update(overrides)
    return Character(**defaults)  # type: ignore[arg-type]


class TestResolveLocation:
    def test_finds_existing_location(self):
        district = make_district()
        location = travel_svc.resolve_location(district, "square")
        assert location.id == "square"

    def test_raises_not_found_for_unknown_location(self):
        district = make_district()
        with pytest.raises(NotFound) as exc_info:
            travel_svc.resolve_location(district, "nowhere")
        assert exc_info.value.reason_key == "location_not_found"


class TestCheckCanTravel:
    def test_allows_approved_character_to_unrestricted_location(self):
        district = make_district()
        character = make_character()
        location = travel_svc.resolve_location(district, "square")
        travel_svc.check_can_travel(character=character, location=location)  # no raise

    def test_refuses_dead_character(self):
        district = make_district()
        character = make_character(status=CharacterStatus.DEAD.value)
        location = travel_svc.resolve_location(district, "square")
        with pytest.raises(NotAllowed) as exc_info:
            travel_svc.check_can_travel(character=character, location=location)
        assert exc_info.value.reason_key == "character_dead"

    def test_refuses_non_approved_character(self):
        district = make_district()
        character = make_character(status=CharacterStatus.PENDING.value)
        location = travel_svc.resolve_location(district, "square")
        with pytest.raises(NotAllowed) as exc_info:
            travel_svc.check_can_travel(character=character, location=location)
        assert exc_info.value.reason_key == "character_not_approved"

    def test_refuses_restricted_location_without_access(self):
        district = make_district()
        character = make_character(job_title=None, positions=[])
        location = travel_svc.resolve_location(district, "labs")
        with pytest.raises(NotAllowed) as exc_info:
            travel_svc.check_can_travel(character=character, location=location)
        assert exc_info.value.reason_key == "location_restricted"

    def test_allows_victor_into_restricted_location(self):
        district = make_district()
        character = make_character(positions=["victor"])
        location = travel_svc.resolve_location(district, "labs")
        travel_svc.check_can_travel(character=character, location=location)  # no raise


class TestResolveStation:
    def test_finds_the_station_location(self):
        district = make_district()
        station = travel_svc.resolve_station(district)
        assert station.id == "station"


class TestTicketGoodId:
    def test_builds_the_per_district_ticket_id(self):
        assert travel_svc.ticket_good_id(0) == "train_ticket_d0"
        assert travel_svc.ticket_good_id(12) == "train_ticket_d12"


class TestIsFreeVictorRoute:
    def test_victor_home_to_capitol_is_free(self):
        character = make_character(district_id=12, positions=["victor"])
        assert travel_svc.is_free_victor_route(character, 12, 0)

    def test_victor_capitol_to_home_is_free(self):
        character = make_character(district_id=12, positions=["victor"])
        assert travel_svc.is_free_victor_route(character, 0, 12)

    def test_victor_to_a_third_district_is_not_free(self):
        character = make_character(district_id=12, positions=["victor"])
        assert not travel_svc.is_free_victor_route(character, 12, 5)

    def test_non_victor_never_free(self):
        character = make_character(district_id=12, positions=[])
        assert not travel_svc.is_free_victor_route(character, 12, 0)


class TestIsFreeRoute:
    def test_returning_home_is_free_from_anywhere(self):
        character = make_character(district_id=12, positions=[])
        assert travel_svc.is_free_route(character, 5, 12)
        assert travel_svc.is_free_route(character, 0, 12)

    def test_leaving_home_for_an_ordinary_district_still_costs(self):
        character = make_character(district_id=12, positions=[])
        assert not travel_svc.is_free_route(character, 12, 5)

    def test_victor_home_to_capitol_is_free(self):
        character = make_character(district_id=12, positions=["victor"])
        assert travel_svc.is_free_route(character, 12, 0)

    def test_non_victor_third_district_round_trip_only_home_leg_is_free(self):
        character = make_character(district_id=12, positions=[])
        assert not travel_svc.is_free_route(character, 12, 5)
        assert travel_svc.is_free_route(character, 5, 12)


class TestCheckCanTravelDistrict:
    def test_allows_approved_character_at_the_station(self):
        district = make_district()
        character = make_character(location_id="station")
        travel_svc.check_can_travel_district(
            character=character, district=district, destination_id=1, current_tick=100
        )  # no raise

    def test_refuses_dead_character(self):
        district = make_district()
        character = make_character(status=CharacterStatus.DEAD.value, location_id="station")
        with pytest.raises(NotAllowed) as exc_info:
            travel_svc.check_can_travel_district(
                character=character, district=district, destination_id=1, current_tick=100
            )
        assert exc_info.value.reason_key == "character_dead"

    def test_refuses_non_approved_character(self):
        district = make_district()
        character = make_character(status=CharacterStatus.PENDING.value, location_id="station")
        with pytest.raises(NotAllowed) as exc_info:
            travel_svc.check_can_travel_district(
                character=character, district=district, destination_id=1, current_tick=100
            )
        assert exc_info.value.reason_key == "character_not_approved"

    def test_refuses_jailed_character(self):
        district = make_district()
        character = make_character(location_id="station", jailed_until_tick=200)
        with pytest.raises(NotAllowed) as exc_info:
            travel_svc.check_can_travel_district(
                character=character, district=district, destination_id=1, current_tick=100
            )
        assert exc_info.value.reason_key == "travel_jailed"

    def test_allows_once_jail_sentence_has_passed(self):
        district = make_district()
        character = make_character(location_id="station", jailed_until_tick=50)
        travel_svc.check_can_travel_district(
            character=character, district=district, destination_id=1, current_tick=100
        )  # no raise

    def test_refuses_character_already_in_transit(self):
        district = make_district()
        character = make_character(location_id="station", in_transit_until_tick=150)
        with pytest.raises(NotAllowed) as exc_info:
            travel_svc.check_can_travel_district(
                character=character, district=district, destination_id=1, current_tick=100
            )
        assert exc_info.value.reason_key == "travel_already_in_transit"

    def test_refuses_traveling_to_current_district(self):
        district = make_district()
        character = make_character(location_id="station")
        with pytest.raises(NotAllowed) as exc_info:
            travel_svc.check_can_travel_district(
                character=character, district=district, destination_id=12, current_tick=100
            )
        assert exc_info.value.reason_key == "travel_same_district"

    def test_refuses_when_not_at_the_station(self):
        district = make_district()
        character = make_character(location_id="square")
        with pytest.raises(NotAllowed) as exc_info:
            travel_svc.check_can_travel_district(
                character=character, district=district, destination_id=1, current_tick=100
            )
        assert exc_info.value.reason_key == "travel_not_at_station"


class TestPlace:
    def test_places_within_location_radius_of_map_coords(self):
        district = make_district()
        location = travel_svc.resolve_location(district, "square")
        cx, cy = district.map.location_coords["square"]

        for _ in range(50):
            placed = travel_svc.place(district, location)
            assert placed is not None
            x, y = placed
            assert abs(x - cx) <= location.radius
            assert abs(y - cy) <= location.radius

    def test_returns_none_when_location_has_no_map_coords(self):
        district = make_district()
        unmapped = Location(id="unmapped", name="Nowhere Map", kind="public")
        result = travel_svc.place(district, unmapped)
        assert result is None
