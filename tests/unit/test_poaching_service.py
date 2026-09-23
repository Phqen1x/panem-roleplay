from __future__ import annotations

import pytest
from sqlalchemy import select

from panem_bot.errors import NotAllowed, NotFound
from panem_bot.services import poaching as poaching_svc
from panem_shared import constants
from panem_shared.content.schemas import District, DistrictCulture, DistrictMap, Good, Location
from panem_shared.db.models import Character, CrimeLog, DistrictState, Inventory
from panem_shared.enums import CharacterStatus, OwnerKind
from panem_shared.simtime import TICKS_PER_PHASE


class SequenceRng:
    """A stand-in for `random.Random` that returns a fixed sequence of
    draws in order -- `roll_and_apply_poach` makes exactly two calls (the
    archery hit/miss roll, then the detection roll), so tests need to
    control both independently rather than a single fixed value."""

    def __init__(self, values: list[float]) -> None:
        self._values = list(values)

    def random(self) -> float:
        return self._values.pop(0)


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
        last_poach_tick=None,
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
        good = poaching_svc.check_can_poach(character, district, make_goods(), 0)
        assert good.id == "grain"  # the only food-category good here

    def test_prefers_a_food_good_the_district_produces_over_one_it_imports(self):
        district = make_district(produces=["grain"], imports=["coal"])
        character = make_character()
        good = poaching_svc.check_can_poach(character, district, make_goods(), 0)
        assert good.id == "grain"

    def test_non_approved_character_refused(self):
        district = make_district()
        character = make_character(status=CharacterStatus.PENDING.value)
        with pytest.raises(NotAllowed) as exc_info:
            poaching_svc.check_can_poach(character, district, make_goods(), 0)
        assert exc_info.value.reason_key == "character_not_approved"

    def test_elsewhere_in_the_district_refuses(self):
        district = make_district()
        character = make_character(location_id="square")
        with pytest.raises(NotAllowed) as exc_info:
            poaching_svc.check_can_poach(character, district, make_goods(), 0)
        assert exc_info.value.reason_key == "poach_not_at_outskirts"

    def test_raises_on_cooldown_within_the_same_phase(self):
        district = make_district()
        character = make_character(last_poach_tick=0)
        with pytest.raises(NotAllowed) as exc_info:
            poaching_svc.check_can_poach(character, district, make_goods(), 1)
        assert exc_info.value.reason_key == "poach_on_cooldown"

    def test_allowed_once_a_new_phase_starts(self):
        district = make_district()
        character = make_character(last_poach_tick=0)
        # no raise
        poaching_svc.check_can_poach(character, district, make_goods(), TICKS_PER_PHASE)

    def test_raises_when_jailed(self):
        district = make_district()
        character = make_character(jailed_until_tick=100)
        with pytest.raises(NotAllowed) as exc_info:
            poaching_svc.check_can_poach(character, district, make_goods(), 10)
        assert exc_info.value.reason_key == "poach_jailed"

    def test_allowed_once_jail_has_expired(self):
        district = make_district()
        character = make_character(jailed_until_tick=5)
        # no raise
        poaching_svc.check_can_poach(character, district, make_goods(), 10)


class TestPoachDifficulty:
    def test_flat_and_derived_from_the_archery_base_success_constant(self):
        assert poaching_svc.poach_difficulty() == 1.0 - constants.POACH_ARCHERY_BASE_SUCCESS


class TestApplyPoachOutcome:
    """`success` is given directly here (the archery minigame's own win/
    lose, or the RNG-fallback roll `roll_and_apply_poach` makes on its
    behalf) -- only the detection roll happens inside this function."""

    async def test_success_and_not_caught_grants_the_good(self, db_session):
        character = make_character(money=100)
        good = make_goods()["grain"]

        result = await poaching_svc.apply_poach_outcome(
            db_session,
            character=character,
            good=good,
            district_id=12,
            current_tick=0,
            success=True,
            rng=SequenceRng([0.99]),  # above the detection threshold
        )

        assert result.caught is False
        assert result.good is not None
        assert result.good.id == "grain"
        assert character.money == 100
        inv = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "grain"))
        assert inv.qty == constants.POACH_YIELD_QTY

    async def test_logs_a_successful_attempt(self, db_session):
        character = make_character(money=100)
        good = make_goods()["grain"]

        await poaching_svc.apply_poach_outcome(
            db_session,
            character=character,
            good=good,
            district_id=12,
            current_tick=7,
            success=True,
            rng=SequenceRng([0.99]),
        )

        row = (await db_session.execute(select(CrimeLog))).scalar_one()
        assert row.character_id == character.id
        assert row.kind == "poach"
        assert row.tick == 7
        assert row.success is True
        assert row.caught is False
        assert row.target_name is None
        assert row.good_name == "Grain"
        assert row.amount == constants.POACH_YIELD_QTY

    async def test_logs_a_caught_attempt_with_no_good(self, db_session):
        character = make_character(money=100, jailed_until_tick=None)
        good = make_goods()["grain"]

        await poaching_svc.apply_poach_outcome(
            db_session,
            character=character,
            good=good,
            district_id=12,
            current_tick=0,
            success=True,
            rng=SequenceRng([0.0]),  # below the detection threshold
        )

        row = (await db_session.execute(select(CrimeLog))).scalar_one()
        assert row.success is False
        assert row.caught is True
        assert row.good_name is None
        assert row.amount == 0

    async def test_success_adds_to_existing_inventory(self, db_session):
        db_session.add(
            Inventory(owner_kind=OwnerKind.CHARACTER.value, owner_id="1", good_id="grain", qty=2)
        )
        await db_session.flush()
        character = make_character()
        good = make_goods()["grain"]

        await poaching_svc.apply_poach_outcome(
            db_session,
            character=character,
            good=good,
            district_id=12,
            current_tick=0,
            success=True,
            rng=SequenceRng([0.99]),
        )

        inv = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "grain"))
        assert inv.qty == 2 + constants.POACH_YIELD_QTY

    async def test_missed_shots_but_not_caught_grants_nothing(self, db_session):
        character = make_character(money=100)
        good = make_goods()["grain"]

        result = await poaching_svc.apply_poach_outcome(
            db_session,
            character=character,
            good=good,
            district_id=12,
            current_tick=0,
            success=False,
            rng=SequenceRng([0.99]),  # not caught either
        )

        assert result.caught is False
        assert result.good is None
        assert character.money == 100
        inv = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "grain"))
        assert inv is None

    async def test_caught_applies_fine_jail_and_reputation_penalty_regardless_of_success(
        self, db_session
    ):
        character = make_character(money=100, jailed_until_tick=None)
        good = make_goods()["grain"]
        db_session.add(DistrictState(district_id=12, peacekeeper_pressure=0.3))
        await db_session.flush()

        result = await poaching_svc.apply_poach_outcome(
            db_session,
            character=character,
            good=good,
            district_id=12,
            current_tick=0,
            success=True,  # landed the shots, but a peacekeeper still notices
            rng=SequenceRng([0.0]),  # below the detection threshold
        )

        assert result.caught is True
        assert result.good is None
        assert character.money == 100 - constants.POACH_FINE
        assert character.jailed_until_tick == constants.POACH_JAIL_TICKS
        assert character.reputation == -constants.POACH_REP_PENALTY
        inv = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "grain"))
        assert inv is None

        district_row = await db_session.get(DistrictState, 12)
        assert district_row.peacekeeper_pressure == 0.3 + poaching_svc.PEACEKEEPER_PRESSURE_DELTA

    async def test_caught_at_a_non_zero_world_tick_jails_from_now_not_from_zero(self, db_session):
        # Regression test for the "still says I'm free when I'm jailed"
        # bug: commit_to_jail used to anchor a fresh sentence at absolute
        # tick `POACH_JAIL_TICKS` regardless of the actual world tick, so
        # getting caught at any real (non-zero) tick left
        # `jailed_until_tick` already in the past the instant it was set.
        character = make_character(money=100, jailed_until_tick=None)
        good = make_goods()["grain"]

        result = await poaching_svc.apply_poach_outcome(
            db_session,
            character=character,
            good=good,
            district_id=12,
            current_tick=5000,
            success=True,
            rng=SequenceRng([0.0]),
        )

        assert result.caught is True
        assert character.jailed_until_tick == 5000 + constants.POACH_JAIL_TICKS
        assert character.jailed_until_tick > 5000  # actually jailed, not already free

    async def test_caught_without_a_district_state_row_does_not_raise(self, db_session):
        character = make_character(money=100, jailed_until_tick=None)
        good = make_goods()["grain"]

        result = await poaching_svc.apply_poach_outcome(
            db_session,
            character=character,
            good=good,
            district_id=12,
            current_tick=0,
            success=True,
            rng=SequenceRng([0.0]),
        )
        assert result.caught is True


class TestResolvePoach:
    """The top-level convenience wrapper: gate, set the cooldown, then the
    RNG-fallback roll -- two draws per call (archery, then detection)."""

    async def test_success_adds_inventory_and_sets_the_cooldown(self, db_session):
        district = make_district()
        character = make_character(money=100)

        result = await poaching_svc.resolve_poach(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            current_tick=7,
            rng=SequenceRng([0.0, 0.99]),  # archery hit, not caught
        )

        assert result.caught is False
        assert result.good is not None
        assert result.good.id == "grain"
        assert character.money == 100
        assert character.last_poach_tick == 7
        inv = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "grain"))
        assert inv.qty == constants.POACH_YIELD_QTY

    async def test_caught_applies_consequences(self, db_session):
        district = make_district()
        character = make_character(money=100, jailed_until_tick=None)

        result = await poaching_svc.resolve_poach(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            current_tick=0,
            rng=SequenceRng([0.0, 0.0]),  # archery hit, but still caught
        )

        assert result.caught is True
        assert result.good is None
        assert character.money == 100 - constants.POACH_FINE

    async def test_refusal_raised_before_any_roll_or_mutation(self, db_session):
        district = make_district()
        character = make_character(location_id="square", money=100)

        with pytest.raises(NotAllowed):
            await poaching_svc.resolve_poach(
                db_session,
                character=character,
                district=district,
                goods=make_goods(),
                current_tick=0,
                rng=SequenceRng([]),  # any roll at all would raise IndexError
            )
        assert character.money == 100
        assert character.last_poach_tick is None
