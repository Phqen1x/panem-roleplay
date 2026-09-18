from __future__ import annotations

import pytest

from panem_bot.errors import NotAllowed
from panem_bot.services import stealing as stealing_svc
from panem_shared import constants
from panem_shared.db.models import Character, DistrictState, Npc, Property, RelationshipRow
from panem_shared.enums import CharacterStatus, OwnerKind, PropertyKind


class SequenceRng:
    """A stand-in for `random.Random` that returns a fixed sequence of
    values, one per call -- `resolve_steal` rolls up to three independent
    probabilities in a row, so a single fixed value (as `market.py`'s
    `FixedRng` uses) can't drive every branch."""

    def __init__(self, values: list[float]) -> None:
        self._values = list(values)

    def random(self) -> float:
        return self._values.pop(0)

    def randint(self, a: int, b: int) -> int:
        return a


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Thief",
        age=20,
        status=CharacterStatus.APPROVED.value,
        money=100,
        reputation=0.0,
        location_id="square",
        jail_count=0,
        jailed_until_tick=None,
        last_steal_tick=None,
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = 1
    return character


def make_house(**overrides: object) -> Property:
    defaults: dict[str, object] = dict(
        district_id=1,
        kind=PropertyKind.HOUSE.value,
        tier="apprentice",
        owner_kind=OwnerKind.CHARACTER.value,
        owner_id=2,
        for_sale=False,
        suggested_price=1000.0,
        created_at_tick=0,
    )
    defaults.update(overrides)
    house = Property(**defaults)  # type: ignore[arg-type]
    house.id = 1
    return house


def make_npc(**overrides: object) -> Npc:
    defaults: dict[str, object] = dict(
        id="d1_npc_001",
        district_id=1,
        name="Mark",
        age=30,
        money=50.0,
        location_id="square",
    )
    defaults.update(overrides)
    return Npc(**defaults)  # type: ignore[arg-type]


class TestCheckCanSteal:
    def test_raises_when_not_at_the_same_location(self):
        character = make_character(location_id="square")
        victim = make_npc(location_id="market")
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_steal(character, victim, 10)
        assert exc_info.value.reason_key == "steal_not_here"

    def test_raises_on_cooldown_within_the_same_phase(self):
        character = make_character(last_steal_tick=0)
        victim = make_npc()
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_steal(character, victim, 1)
        assert exc_info.value.reason_key == "steal_on_cooldown"

    def test_allowed_once_a_new_phase_starts(self):
        character = make_character(last_steal_tick=0)
        victim = make_npc()
        from panem_shared.simtime import TICKS_PER_PHASE

        stealing_svc.check_can_steal(character, victim, TICKS_PER_PHASE)  # no raise


class TestResolveSteal:
    async def test_success_moves_money_with_no_consequence(self, db_session):
        character = make_character(money=0)
        victim = make_npc(money=50.0)
        result = await stealing_svc.resolve_steal(
            db_session,
            character=character,
            victim=victim,
            district_id=1,
            current_tick=10,
            rng=SequenceRng([0.0]),
        )
        assert result.success is True
        assert result.amount == constants.STEAL_YIELD_MONEY_RANGE[0]
        assert character.money == result.amount
        assert victim.money == 50.0 - result.amount
        assert character.jailed_until_tick is None

    async def test_success_capped_by_what_the_victim_has(self, db_session):
        character = make_character(money=0)
        victim = make_npc(money=2.0)
        result = await stealing_svc.resolve_steal(
            db_session,
            character=character,
            victim=victim,
            district_id=1,
            current_tick=10,
            rng=SequenceRng([0.0]),
        )
        assert result.amount == 2
        assert victim.money == 0.0

    async def test_clean_miss_changes_nothing(self, db_session):
        character = make_character(money=100)
        victim = make_npc(money=50.0)
        result = await stealing_svc.resolve_steal(
            db_session,
            character=character,
            victim=victim,
            district_id=1,
            current_tick=10,
            rng=SequenceRng([0.99, 0.99]),
        )
        assert result.success is False
        assert result.alerted is False
        assert result.caught is False
        assert character.money == 100
        assert victim.money == 50.0

    async def test_alerted_but_escapes_changes_nothing(self, db_session):
        character = make_character(money=100)
        victim = make_npc(money=50.0)
        result = await stealing_svc.resolve_steal(
            db_session,
            character=character,
            victim=victim,
            district_id=1,
            current_tick=10,
            rng=SequenceRng([0.99, 0.0, 0.0]),
        )
        assert result.success is False
        assert result.alerted is True
        assert result.caught is False
        assert character.money == 100
        assert character.jailed_until_tick is None

    async def test_caught_stealing_from_an_npc_applies_full_consequence(self, db_session):
        character = make_character(money=100, jailed_until_tick=None)
        victim = make_npc(money=50.0)
        db_session.add(DistrictState(district_id=1, peacekeeper_pressure=0.3))
        await db_session.flush()

        result = await stealing_svc.resolve_steal(
            db_session,
            character=character,
            victim=victim,
            district_id=1,
            current_tick=10,
            rng=SequenceRng([0.99, 0.0, 0.99]),
        )

        assert result.caught is True
        assert character.money == 100 - constants.STEAL_FINE
        assert character.jailed_until_tick == constants.STEAL_JAIL_TICKS
        assert character.reputation == -constants.REP_STEAL_CAUGHT_GENERAL_PENALTY

        relationship = await db_session.get(
            RelationshipRow,
            (OwnerKind.CHARACTER.value, "1", OwnerKind.NPC.value, victim.id),
        )
        assert relationship.affinity == -constants.REP_STEAL_CAUGHT_VICTIM_PENALTY

        district_row = await db_session.get(DistrictState, 1)
        assert district_row.peacekeeper_pressure == 0.3 + stealing_svc.STEAL_PRESSURE_DELTA

    async def test_caught_stealing_from_a_player_skips_the_relationship_hit(self, db_session):
        character = make_character(money=100, jailed_until_tick=None)
        victim = make_character(name="Victim", money=50)
        victim.id = 2

        result = await stealing_svc.resolve_steal(
            db_session,
            character=character,
            victim=victim,
            district_id=1,
            current_tick=10,
            rng=SequenceRng([0.99, 0.0, 0.99]),
        )

        assert result.caught is True
        assert character.reputation == -constants.REP_STEAL_CAUGHT_GENERAL_PENALTY

    async def test_crackdown_makes_success_harder(self, db_session):
        character = make_character(money=0)
        victim = make_npc(money=50.0)
        db_session.add(
            DistrictState(district_id=1, peacekeeper_pressure=0.3, crackdown_until_tick=100)
        )
        await db_session.flush()
        # A roll that clears the base success prob but not the crackdown-scaled one.
        roll = (
            constants.STEAL_FROM_NPC_BASE_SUCCESS
            + constants.STEAL_FROM_NPC_BASE_SUCCESS / constants.CRACKDOWN_DETECTION_MULTIPLIER
        ) / 2

        result = await stealing_svc.resolve_steal(
            db_session,
            character=character,
            victim=victim,
            district_id=1,
            current_tick=10,
            rng=SequenceRng([roll, 0.99]),
        )
        assert result.success is False

    async def test_sets_the_cooldown_on_every_attempt(self, db_session):
        character = make_character(last_steal_tick=None)
        victim = make_npc()
        await stealing_svc.resolve_steal(
            db_session,
            character=character,
            victim=victim,
            district_id=1,
            current_tick=42,
            rng=SequenceRng([0.99, 0.99]),
        )
        assert character.last_steal_tick == 42


class TestCheckCanBurgle:
    def test_raises_for_a_non_house_property(self):
        character = make_character(current_district_id=1)
        character.id = 1
        house = make_house(kind=PropertyKind.APARTMENT.value)
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_burgle(character, house, 10)
        assert exc_info.value.reason_key == "burgle_not_a_house"

    def test_raises_in_the_wrong_district(self):
        character = make_character(current_district_id=2)
        character.id = 1
        house = make_house(district_id=1)
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_burgle(character, house, 10)
        assert exc_info.value.reason_key == "burgle_wrong_district"

    def test_raises_on_your_own_house(self):
        character = make_character(current_district_id=1)
        character.id = 1
        house = make_house(district_id=1, owner_id=1)
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_burgle(character, house, 10)
        assert exc_info.value.reason_key == "burgle_own_house"

    def test_raises_on_cooldown(self):
        character = make_character(current_district_id=1, last_steal_tick=0)
        character.id = 1
        house = make_house(district_id=1)
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_burgle(character, house, 1)
        assert exc_info.value.reason_key == "steal_on_cooldown"

    def test_allowed_for_a_stranger_s_house(self):
        character = make_character(current_district_id=1)
        character.id = 1
        house = make_house(district_id=1, owner_id=2)
        stealing_svc.check_can_burgle(character, house, 10)  # no raise


class TestResolveBurgle:
    async def test_success_pays_a_fraction_of_the_house_value(self, db_session):
        character = make_character(current_district_id=1, money=0)
        character.id = 1
        house = make_house(district_id=1, owner_id=2, suggested_price=1000.0)
        result = await stealing_svc.resolve_burgle(
            db_session, character=character, house=house, current_tick=10, rng=SequenceRng([0.0])
        )
        assert result.success is True
        assert result.amount == round(1000.0 * constants.BURGLE_YIELD_FRACTION)
        assert character.money == result.amount

    async def test_payout_is_capped(self, db_session):
        character = make_character(current_district_id=1, money=0)
        character.id = 1
        house = make_house(district_id=1, owner_id=2, suggested_price=1_000_000.0)
        result = await stealing_svc.resolve_burgle(
            db_session, character=character, house=house, current_tick=10, rng=SequenceRng([0.0])
        )
        assert result.amount == constants.BURGLE_YIELD_CAP

    async def test_caught_applies_the_same_consequence_as_stealing(self, db_session):
        character = make_character(current_district_id=1, money=100, jailed_until_tick=None)
        character.id = 1
        house = make_house(district_id=1, owner_id=2)
        db_session.add(DistrictState(district_id=1, peacekeeper_pressure=0.3))
        await db_session.flush()

        result = await stealing_svc.resolve_burgle(
            db_session,
            character=character,
            house=house,
            current_tick=10,
            rng=SequenceRng([0.99, 0.0, 0.99]),
        )

        assert result.caught is True
        assert character.money == 100 - constants.STEAL_FINE
        assert character.jailed_until_tick == constants.STEAL_JAIL_TICKS
        district_row = await db_session.get(DistrictState, 1)
        assert district_row.peacekeeper_pressure == 0.3 + stealing_svc.STEAL_PRESSURE_DELTA
