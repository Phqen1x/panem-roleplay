from __future__ import annotations

import pytest

from panem_shared import block_models, housing3d
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    Location,
    PetSpecies,
)
from panem_shared.db.models import Character, User
from panem_shared.enums import PropertyKind
from panem_shared.errors import NotAllowed, ValidationFailed
from panem_shared.map3d_layout import GRID_RADIUS, build_city_layout, npc_scatter_offset
from panem_shared.pets import purchase_pet, validate_pet_name


def make_district() -> District:
    locations = [
        Location(id="square", name="Town Square", kind="public"),
        Location(id="mill", name="The Mill", kind="workplace"),
        Location(id="hall", name="Government Hall", kind="government"),
        Location(id="homes", name="Homes", kind="residential"),
        Location(id="market", name="The Market", kind="market"),
        Location(id="rail", name="Rail Station", kind="station"),
    ]
    coords = {
        "square": (500, 500),
        "mill": (200, 200),
        "hall": (800, 800),
        "homes": (200, 800),
        "market": (800, 200),
        "rail": (900, 900),
    }
    return District(
        id=7,
        name="District Seven",
        industry="lumber",
        produces=["lumber"],
        imports=[],
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=1000, height=1000, location_coords=coords),
    )


GRID_PLOT_COUNT = (2 * GRID_RADIUS + 1) ** 2


class TestBuildCityLayout:
    def test_hub_is_the_most_central_location(self):
        layout = build_city_layout(make_district())
        assert layout.hub_location_id == "square"

    def test_hub_building_has_no_model(self):
        layout = build_city_layout(make_district())
        hub = next(b for b in layout.buildings if b.location_id == "square")
        assert hub.model is None

    def test_every_located_location_gets_a_building_entry(self):
        layout = build_city_layout(make_district())
        named = {b.location_id for b in layout.buildings if b.location_id is not None}
        assert named == {"square", "mill", "hall", "homes", "market", "rail"}

    def test_non_hub_located_locations_get_a_model(self):
        layout = build_city_layout(make_district())
        for loc_id in ("mill", "hall", "homes", "market", "rail"):
            building = next(b for b in layout.buildings if b.location_id == loc_id)
            assert building.model is not None

    def test_grid_is_filled_with_more_than_just_named_locations(self):
        """The whole point of the grid rewrite: a city reads as a city, not
        six buildings scattered on empty ground."""
        layout = build_city_layout(make_district())
        filler = [b for b in layout.buildings if b.location_id is None]
        assert len(filler) > 10
        # Every filler building is still a real, placeable model.
        assert all(b.model is not None for b in filler)

    def test_no_two_buildings_share_a_plot(self):
        layout = build_city_layout(make_district())
        positions = [(b.x, b.z) for b in layout.buildings]
        assert len(positions) == len(set(positions))

    def test_building_count_never_exceeds_the_grid(self):
        layout = build_city_layout(make_district())
        assert len(layout.buildings) <= GRID_PLOT_COUNT

    def test_roads_form_a_grid_not_just_a_few_spokes(self):
        layout = build_city_layout(make_district())
        assert len(layout.roads) > 100

    def test_deterministic_across_calls(self):
        district = make_district()
        first = build_city_layout(district)
        second = build_city_layout(district)
        assert [(b.location_id, b.model, b.x, b.z) for b in first.buildings] == [
            (b.location_id, b.model, b.x, b.z) for b in second.buildings
        ]
        assert len(first.roads) == len(second.roads)

    def test_district_with_no_located_locations_still_gets_a_filled_grid(self):
        """No named locations at all is an edge case (bad content), not a
        reason to render an empty field -- the hub plaza is always there
        and filler still fills the rest of the grid."""
        district = make_district()
        district = district.model_copy(
            update={"map": district.map.model_copy(update={"location_coords": {}})}
        )
        layout = build_city_layout(district)
        assert layout.hub_location_id is None
        assert all(b.location_id is None for b in layout.buildings)
        assert len(layout.buildings) > 10
        assert len(layout.roads) > 100


class TestAssignNpcAppearance:
    def test_deterministic(self):
        first = block_models.assign_npc_appearance("d0_npc_001", gender="female")
        second = block_models.assign_npc_appearance("d0_npc_001", gender="female")
        assert first == second

    def test_returns_every_expected_key(self):
        appearance = block_models.assign_npc_appearance("npc_x", gender=None)
        assert set(appearance.keys()) == set(block_models.DEFAULT_APPEARANCE.keys())

    def test_different_npcs_can_differ(self):
        outcomes = {
            tuple(block_models.assign_npc_appearance(f"npc_{i}", gender=None).values())
            for i in range(20)
        }
        assert len(outcomes) > 1

    def test_different_parts_of_the_same_npc_can_differ(self):
        """A real regression this feature fixed: the old single-outfit
        system gave every part of a character the same letter by
        construction; four independent parts should actually vary."""
        appearance = block_models.assign_npc_appearance("npc_varied", gender=None)
        shapes = {appearance["head"], appearance["torso"], appearance["arms"], appearance["legs"]}
        assert len(shapes) > 1

    def test_gendered_shapes_stay_within_their_pool(self):
        appearance = block_models.assign_npc_appearance("some_npc", gender="male")
        for part in block_models.BODY_PARTS:
            assert appearance[part] in block_models._GENDER_POOLS["male"]
        appearance = block_models.assign_npc_appearance("some_npc", gender="female")
        for part in block_models.BODY_PARTS:
            assert appearance[part] in block_models._GENDER_POOLS["female"]

    def test_unknown_gender_uses_full_range(self):
        appearance = block_models.assign_npc_appearance("some_npc", gender="unspecified")
        for part in block_models.BODY_PARTS:
            assert appearance[part] in block_models.LETTERS
            assert appearance[f"{part}_color"] in block_models.LETTERS


class TestNpcScatterOffset:
    def test_deterministic(self):
        assert npc_scatter_offset("npc_1") == npc_scatter_offset("npc_1")

    def test_stays_within_radius(self):
        x, z = npc_scatter_offset("npc_1", radius=2.5)
        assert (x**2 + z**2) ** 0.5 <= 2.5 + 1e-9

    def test_different_npcs_scatter_differently(self):
        offsets = {npc_scatter_offset(f"npc_{i}") for i in range(10)}
        assert len(offsets) > 1


class TestIsValidLetter:
    @pytest.mark.parametrize("letter", list("abcdefghijklmnopqr"))
    def test_accepts_every_valid_letter(self, letter):
        assert block_models.is_valid_letter(letter)

    @pytest.mark.parametrize("value", ["", "s", "aa", "A", "1", None, 1])
    def test_rejects_everything_else(self, value):
        assert not block_models.is_valid_letter(value)


class TestIsValidAppearance:
    def test_accepts_the_default_appearance(self):
        assert block_models.is_valid_appearance(block_models.DEFAULT_APPEARANCE)

    def test_rejects_a_missing_key(self):
        parts = dict(block_models.DEFAULT_APPEARANCE)
        del parts["legs_color"]
        assert not block_models.is_valid_appearance(parts)

    def test_rejects_an_extra_key(self):
        parts = dict(block_models.DEFAULT_APPEARANCE)
        parts["hat"] = "a"
        assert not block_models.is_valid_appearance(parts)

    def test_rejects_an_invalid_letter(self):
        parts = dict(block_models.DEFAULT_APPEARANCE)
        parts["head"] = "zz"
        assert not block_models.is_valid_appearance(parts)

    def test_rejects_a_non_dict(self):
        assert not block_models.is_valid_appearance("a")
        assert not block_models.is_valid_appearance(None)


class TestHousing3D:
    def test_default_layout_stays_within_room_bounds(self):
        for kind in (PropertyKind.HOUSE.value, PropertyKind.APARTMENT.value):
            half_width, half_depth = housing3d.room_bounds(kind)
            for item in housing3d.default_layout(kind):
                # Structural pieces (walls) legitimately sit right at the
                # room's edge -- only non-structural furniture is expected
                # to stay comfortably inside it.
                if item.item_key in ("wall", "wallCorner", "wallDoorway", "floorFull"):
                    continue
                assert abs(item.pos_x) <= half_width + 0.5
                assert abs(item.pos_z) <= half_depth + 0.5

    def test_house_gets_a_bigger_room_than_apartment(self):
        house_w, house_d = housing3d.room_bounds(PropertyKind.HOUSE.value)
        apt_w, apt_d = housing3d.room_bounds(PropertyKind.APARTMENT.value)
        assert house_w > apt_w
        assert house_d > apt_d

    def test_is_resident_house_owner(self):
        assert housing3d.is_resident(
            property_kind=PropertyKind.HOUSE.value,
            owner_kind="character",
            owner_id=5,
            character_id=5,
            lease_tenant_id=None,
        )

    def test_is_resident_house_non_owner(self):
        assert not housing3d.is_resident(
            property_kind=PropertyKind.HOUSE.value,
            owner_kind="character",
            owner_id=5,
            character_id=6,
            lease_tenant_id=None,
        )

    def test_is_resident_apartment_tenant(self):
        assert housing3d.is_resident(
            property_kind=PropertyKind.APARTMENT.value,
            owner_kind="npc",
            owner_id=None,
            character_id=9,
            lease_tenant_id=9,
        )

    def test_is_resident_inn_is_never_a_residence(self):
        assert not housing3d.is_resident(
            property_kind=PropertyKind.INN.value,
            owner_kind="character",
            owner_id=9,
            character_id=9,
            lease_tenant_id=9,
        )


def make_pet_species(**overrides: object) -> PetSpecies:
    defaults: dict[str, object] = dict(
        id="cat", name="Cat", model="cat", price=30.0, description=""
    )
    defaults.update(overrides)
    return PetSpecies(**defaults)  # type: ignore[arg-type]


class TestValidatePetName:
    def test_strips_whitespace(self):
        assert validate_pet_name("  Mittens  ") == "Mittens"

    def test_rejects_empty(self):
        with pytest.raises(ValidationFailed):
            validate_pet_name("   ")

    def test_rejects_too_long(self):
        with pytest.raises(ValidationFailed):
            validate_pet_name("x" * 33)


class TestPurchasePet:
    async def test_happy_path_debits_money_and_inserts_row(self, db_session):
        user = User(discord_id=424242)
        db_session.add(user)
        await db_session.flush()
        character = Character(
            user_id=user.id,
            district_id=0,
            current_district_id=0,
            name="Test Buyer",
            age=20,
            money=100,
        )
        db_session.add(character)
        await db_session.flush()

        pet = await purchase_pet(
            db_session,
            character=character,
            species=make_pet_species(price=30.0),
            name="Mittens",
            current_tick=5,
        )
        assert pet.id is not None
        assert pet.species_id == "cat"
        assert pet.name == "Mittens"
        assert character.money == 70

    async def test_insufficient_funds_refused(self, db_session):
        user = User(discord_id=424243)
        db_session.add(user)
        await db_session.flush()
        character = Character(
            user_id=user.id,
            district_id=0,
            current_district_id=0,
            name="Poor Buyer",
            age=20,
            money=10,
        )
        db_session.add(character)
        await db_session.flush()

        with pytest.raises(NotAllowed) as exc_info:
            await purchase_pet(
                db_session,
                character=character,
                species=make_pet_species(price=30.0),
                name="Mittens",
                current_tick=5,
            )
        assert exc_info.value.reason_key == "pets_insufficient_funds"
        assert character.money == 10
