from __future__ import annotations

import pytest
from sqlalchemy import select

from panem_bot.errors import NotAllowed, NotFound
from panem_bot.services import shipments as shipments_svc
from panem_shared import constants
from panem_shared.content.schemas import Good
from panem_shared.db.models import Character, CrimeLog, DistrictState, Inventory, Shipment
from panem_shared.enums import CharacterStatus, OwnerKind, RpMode


class SequenceRng:
    """A stand-in for `random.Random` that returns a fixed sequence of
    values, one per call -- mirrors `test_stealing_service.py`'s own fake
    for the same reason (a shipment attempt can roll up to three
    independent probabilities in a row on a failure)."""

    def __init__(self, values: list[float]) -> None:
        self._values = list(values)

    def random(self) -> float:
        return self._values.pop(0)

    def randint(self, a: int, b: int) -> int:
        return a

    def choice(self, seq):
        return seq[0]


def make_goods() -> dict[str, Good]:
    return {
        good_id: Good(id=good_id, name=good_id.replace("_", " ").title(), base_price=10.0, category="stolen")
        for good_id in constants.SHIPMENT_LOOT_GOOD_IDS
    }


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Thief",
        age=20,
        status=CharacterStatus.APPROVED.value,
        money=100,
        health=100.0,
        reputation=0.0,
        location_id="station",
        jail_count=0,
        jailed_until_tick=None,
        last_steal_tick=None,
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = 1
    return character


def make_shipment(**overrides: object) -> Shipment:
    defaults: dict[str, object] = dict(
        district_id=1,
        location_id="station",
        good_id=constants.SHIPMENT_LOOT_GOOD_IDS[0],
        qty=2,
        spawned_tick=0,
        expires_tick=100,
    )
    defaults.update(overrides)
    shipment = Shipment(**defaults)  # type: ignore[arg-type]
    shipment.id = 1
    return shipment


class TestFindShipmentHere:
    async def test_finds_an_unexpired_shipment_at_the_character_s_location(self, db_session):
        character = make_character(current_district_id=1, location_id="station")
        db_session.add(make_shipment(district_id=1, location_id="station", expires_tick=100))
        await db_session.flush()
        found = await shipments_svc.find_shipment_here(db_session, character, 10)
        assert found is not None

    async def test_returns_none_when_expired(self, db_session):
        character = make_character(current_district_id=1, location_id="station")
        db_session.add(make_shipment(district_id=1, location_id="station", expires_tick=5))
        await db_session.flush()
        found = await shipments_svc.find_shipment_here(db_session, character, 10)
        assert found is None

    async def test_returns_none_at_a_different_location(self, db_session):
        character = make_character(current_district_id=1, location_id="square")
        db_session.add(make_shipment(district_id=1, location_id="station", expires_tick=100))
        await db_session.flush()
        found = await shipments_svc.find_shipment_here(db_session, character, 10)
        assert found is None


class TestCheckCanStealShipment:
    def test_raises_when_not_at_the_shipment_s_location(self):
        character = make_character(location_id="square")
        shipment = make_shipment(location_id="station")
        with pytest.raises(NotAllowed) as exc_info:
            shipments_svc.check_can_steal_shipment(character, shipment, 10)
        assert exc_info.value.reason_key == "shipment_not_here"

    def test_raises_on_cooldown_within_the_same_phase(self):
        character = make_character(last_steal_tick=0)
        shipment = make_shipment()
        with pytest.raises(NotAllowed) as exc_info:
            shipments_svc.check_can_steal_shipment(character, shipment, 1)
        assert exc_info.value.reason_key == "steal_on_cooldown"

    def test_allowed_once_a_new_phase_starts(self):
        character = make_character(last_steal_tick=0)
        shipment = make_shipment()
        from panem_shared.simtime import TICKS_PER_PHASE

        shipments_svc.check_can_steal_shipment(character, shipment, TICKS_PER_PHASE)  # no raise

    def test_raises_when_jailed(self):
        character = make_character(jailed_until_tick=100)
        shipment = make_shipment()
        with pytest.raises(NotAllowed) as exc_info:
            shipments_svc.check_can_steal_shipment(character, shipment, 10)
        assert exc_info.value.reason_key == "shipment_jailed"

    def test_raises_when_expired(self):
        character = make_character()
        shipment = make_shipment(expires_tick=5)
        with pytest.raises(NotFound) as exc_info:
            shipments_svc.check_can_steal_shipment(character, shipment, 10)
        assert exc_info.value.reason_key == "shipment_gone"

    def test_refuses_a_story_mode_actor(self):
        character = make_character(rp_mode=RpMode.STORY.value)
        shipment = make_shipment()
        with pytest.raises(NotAllowed) as exc_info:
            shipments_svc.check_can_steal_shipment(character, shipment, 10)
        assert exc_info.value.reason_key == "crime_mode_forbidden"

    def test_refuses_an_actor_who_disabled_crime(self):
        character = make_character(crime_enabled=False)
        shipment = make_shipment()
        with pytest.raises(NotAllowed) as exc_info:
            shipments_svc.check_can_steal_shipment(character, shipment, 10)
        assert exc_info.value.reason_key == "crime_disabled_by_actor"

    def test_allowed_when_everything_checks_out(self):
        character = make_character()
        shipment = make_shipment()
        shipments_svc.check_can_steal_shipment(character, shipment, 10)  # no raise


class TestResolveShipment:
    async def test_success_grants_the_shipment_s_own_good_and_qty(self, db_session):
        character = make_character(money=50)
        shipment = make_shipment(good_id=constants.SHIPMENT_LOOT_GOOD_IDS[0], qty=3)
        db_session.add(shipment)
        await db_session.flush()
        result = await shipments_svc.resolve_shipment(
            db_session,
            character=character,
            shipment=shipment,
            current_tick=10,
            rng=SequenceRng([0.0]),
            goods=make_goods(),
        )
        assert result.success is True
        assert result.amount == 3
        assert result.good_name is not None
        assert character.money == 50  # never touched -- the payout is a good, not cash

        row = await db_session.get(
            Inventory, (OwnerKind.CHARACTER.value, "1", constants.SHIPMENT_LOOT_GOOD_IDS[0])
        )
        assert row is not None
        assert row.qty == 3

    async def test_success_deletes_the_shipment_row(self, db_session):
        character = make_character(money=0)
        shipment = make_shipment()
        db_session.add(shipment)
        await db_session.flush()
        shipment_id = shipment.id
        await shipments_svc.resolve_shipment(
            db_session,
            character=character,
            shipment=shipment,
            current_tick=10,
            rng=SequenceRng([0.0]),
            goods=make_goods(),
        )
        await db_session.flush()
        assert await db_session.get(Shipment, shipment_id) is None

    async def test_clean_miss_changes_nothing_and_deletes_the_shipment(self, db_session):
        character = make_character(money=100)
        shipment = make_shipment()
        db_session.add(shipment)
        await db_session.flush()
        shipment_id = shipment.id
        result = await shipments_svc.resolve_shipment(
            db_session,
            character=character,
            shipment=shipment,
            current_tick=10,
            rng=SequenceRng([0.99, 0.99]),
            goods=make_goods(),
        )
        assert result.success is False
        assert result.alerted is False
        assert result.caught is False
        assert character.money == 100
        assert character.health == 100.0
        await db_session.flush()
        assert await db_session.get(Shipment, shipment_id) is None

    async def test_alerted_but_escapes_changes_nothing(self, db_session):
        character = make_character(money=100)
        shipment = make_shipment()
        db_session.add(shipment)
        await db_session.flush()
        result = await shipments_svc.resolve_shipment(
            db_session,
            character=character,
            shipment=shipment,
            current_tick=10,
            rng=SequenceRng([0.99, 0.0, 0.0]),
            goods=make_goods(),
        )
        assert result.success is False
        assert result.alerted is True
        assert result.caught is False
        assert character.money == 100
        assert character.health == 100.0
        assert character.jailed_until_tick is None

    async def test_caught_applies_fine_jail_health_and_reputation(self, db_session):
        character = make_character(money=100, health=100.0, jailed_until_tick=None)
        shipment = make_shipment()
        db_session.add(shipment)
        db_session.add(DistrictState(district_id=1, peacekeeper_pressure=0.3))
        await db_session.flush()

        result = await shipments_svc.resolve_shipment(
            db_session,
            character=character,
            shipment=shipment,
            current_tick=10,
            rng=SequenceRng([0.99, 0.0, 0.99]),
            goods=make_goods(),
        )

        assert result.caught is True
        assert character.money == 100 - constants.SHIPMENT_FINE
        assert character.health == 100.0 - constants.SHIPMENT_HEALTH_PENALTY
        assert character.jailed_until_tick == 10 + constants.SHIPMENT_JAIL_TICKS
        assert character.reputation == -constants.REP_SHIPMENT_CAUGHT_PENALTY

        district_row = await db_session.get(DistrictState, 1)
        assert district_row.peacekeeper_pressure == 0.3 + constants.SHIPMENT_PRESSURE_DELTA

    async def test_sets_the_cooldown_on_every_attempt(self, db_session):
        character = make_character(last_steal_tick=None)
        shipment = make_shipment()
        db_session.add(shipment)
        await db_session.flush()
        await shipments_svc.resolve_shipment(
            db_session,
            character=character,
            shipment=shipment,
            current_tick=42,
            rng=SequenceRng([0.99, 0.99]),
            goods=make_goods(),
        )
        assert character.last_steal_tick == 42

    async def test_logs_the_attempt(self, db_session):
        character = make_character(money=0)
        shipment = make_shipment(good_id=constants.SHIPMENT_LOOT_GOOD_IDS[0], qty=2)
        db_session.add(shipment)
        await db_session.flush()
        await shipments_svc.resolve_shipment(
            db_session,
            character=character,
            shipment=shipment,
            current_tick=10,
            rng=SequenceRng([0.0]),
            goods=make_goods(),
        )
        row = (await db_session.execute(select(CrimeLog))).scalar_one()
        assert row.character_id == character.id
        assert row.kind == "shipment"
        assert row.tick == 10
        assert row.success is True
        assert row.caught is False
        assert row.good_name is not None
        assert row.amount == 2


class TestRollAndApplyShipment:
    """The Activity-launch skip/fallback path: unlike `resolve_shipment`,
    this never re-validates or re-touches `last_steal_tick` -- an Activity
    launch already did both once, up front."""

    async def test_does_not_touch_the_cooldown(self, db_session):
        character = make_character(money=0, last_steal_tick=42)
        shipment = make_shipment()
        db_session.add(shipment)
        await db_session.flush()
        await shipments_svc.roll_and_apply_shipment(
            db_session,
            character=character,
            shipment=shipment,
            district_row=None,
            current_tick=42,
            rng=SequenceRng([0.0]),
            goods=make_goods(),
        )
        assert character.last_steal_tick == 42
