from __future__ import annotations

import pytest

from panem_bot.errors import NotAllowed, NotFound
from panem_bot.services import poaching as poaching_svc
from panem_shared import constants
from panem_shared.content.schemas import District, DistrictCulture, DistrictMap, Good, Location
from panem_shared.db.models import Character, DistrictState, Inventory
from panem_shared.enums import CharacterStatus, OwnerKind


class FixedRng:
    """A stand-in for `random.Random` that always returns a fixed draw, so
    detection tests don't depend on the real threshold."""

    def __init__(self, value: float) -> None:
        self._value = value

    def random(self) -> float:
        return self._value


def make_district(*, with_outskirts: bool = True, produces=None, imports=None) -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
    ]
    if with_outskirts:
        locations.append(Location(id="outskirts", name="The Outskirts", kind="outskirts"))
    coords = {loc.id: (0, 0) for loc in locations}
    return District(
        id=12,
        name="District 12",
        industry="coal",
        produces=produces if produces is not None else ["coal"],
        imports=imports if imports is not None else ["grain"],
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=100, height=100, location_coords=coords),
    )


def make_goods() -> dict[str, Good]:
    return {
        "coal": Good(id="coal", name="Coal", base_price=4.0, category="fuel"),
        "grain": Good(id="grain", name="Grain", base_price=2.0, category="food"),
    }


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=12,
        current_district_id=12,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
        money=100,
        reputation=0.0,
        location_id="outskirts",
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = 1
    return character


class TestResolveOutskirts:
    def test_finds_the_outskirts_location(self):
        district = make_district()
        location = poaching_svc.resolve_outskirts(district)
        assert location.id == "outskirts"

    def test_no_outskirts_raises_not_found(self):
        district = make_district(with_outskirts=False)
        with pytest.raises(NotFound) as exc_info:
            poaching_svc.resolve_outskirts(district)
        assert exc_info.value.reason_key == "poach_no_outskirts"


class TestCheckCanPoach:
    def test_approved_character_at_outskirts_yields_the_primary_food_good(self):
        district = make_district()
        character = make_character()
        good = poaching_svc.check_can_poach(character, district, make_goods())
        assert good.id == "grain"  # the only food-category good here

    def test_prefers_a_food_good_the_district_produces_over_one_it_imports(self):
        district = make_district(produces=["grain"], imports=["coal"])
        character = make_character()
        good = poaching_svc.check_can_poach(character, district, make_goods())
        assert good.id == "grain"

    def test_non_approved_character_refused(self):
        district = make_district()
        character = make_character(status=CharacterStatus.PENDING.value)
        with pytest.raises(NotAllowed) as exc_info:
            poaching_svc.check_can_poach(character, district, make_goods())
        assert exc_info.value.reason_key == "character_not_approved"

    def test_elsewhere_in_the_district_refuses(self):
        district = make_district()
        character = make_character(location_id="square")
        with pytest.raises(NotAllowed) as exc_info:
            poaching_svc.check_can_poach(character, district, make_goods())
        assert exc_info.value.reason_key == "poach_not_at_outskirts"


class TestResolvePoach:
    async def test_success_adds_inventory_and_does_not_touch_money(self, db_session):
        district = make_district()
        character = make_character(money=100)

        result = await poaching_svc.resolve_poach(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            rng=FixedRng(0.99),  # always above the detection threshold
        )

        assert result.caught is False
        assert result.good is not None
        assert result.good.id == "grain"
        assert character.money == 100
        inv = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "grain"))
        assert inv.qty == constants.POACH_YIELD_QTY

    async def test_success_adds_to_existing_inventory(self, db_session):
        db_session.add(
            Inventory(owner_kind=OwnerKind.CHARACTER.value, owner_id="1", good_id="grain", qty=2)
        )
        await db_session.flush()
        district = make_district()
        character = make_character()

        await poaching_svc.resolve_poach(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            rng=FixedRng(0.99),
        )

        inv = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "grain"))
        assert inv.qty == 2 + constants.POACH_YIELD_QTY

    async def test_caught_applies_fine_jail_and_reputation_penalty(self, db_session):
        district = make_district()
        character = make_character(money=100, jailed_until_tick=None)
        db_session.add(DistrictState(district_id=district.id, peacekeeper_pressure=0.3))
        await db_session.flush()

        result = await poaching_svc.resolve_poach(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            rng=FixedRng(0.0),  # always below the detection threshold
        )

        assert result.caught is True
        assert result.good is None
        assert character.money == 100 - constants.POACH_FINE
        assert character.jailed_until_tick == constants.POACH_JAIL_TICKS
        assert character.reputation == -constants.POACH_REP_PENALTY
        inv = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "grain"))
        assert inv is None

        district_row = await db_session.get(DistrictState, district.id)
        assert district_row.peacekeeper_pressure == 0.3 + poaching_svc.PEACEKEEPER_PRESSURE_DELTA

    async def test_caught_without_a_district_state_row_does_not_raise(self, db_session):
        district = make_district()
        character = make_character(money=100, jailed_until_tick=None)

        result = await poaching_svc.resolve_poach(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            rng=FixedRng(0.0),
        )
        assert result.caught is True

    async def test_refusal_raised_before_any_roll_or_mutation(self, db_session):
        district = make_district()
        character = make_character(location_id="square", money=100)

        with pytest.raises(NotAllowed):
            await poaching_svc.resolve_poach(
                db_session,
                character=character,
                district=district,
                goods=make_goods(),
                rng=FixedRng(0.99),
            )
        assert character.money == 100
