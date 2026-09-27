from __future__ import annotations

import pytest
from sqlalchemy import select

from panem_bot.errors import NotAllowed
from panem_bot.services import stealing as stealing_svc
from panem_shared import constants
from panem_shared.content.schemas import Good
from panem_shared.db.models import (
    Character,
    CrimeLog,
    DistrictState,
    Inventory,
    Npc,
    Property,
    RelationshipRow,
    User,
)
from panem_shared.enums import CharacterStatus, OwnerKind, PropertyKind, RpMode


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

    def choice(self, seq):  # noqa: ANN001, ANN201 -- matches random.Random's own loose typing
        """Deterministic stand-in for `rng.choice(STEAL_LOOT_GOOD_IDS)`/
        `rng.choice(BURGLE_LOOT_GOOD_IDS)` -- always the first entry, the
        same "always the low end" determinism `randint` above already
        gives every other roll in this fake."""
        return seq[0]


def make_goods() -> dict[str, Good]:
    """A minimal, self-contained goods catalog covering every id
    `STEAL_LOOT_GOOD_IDS`/`BURGLE_LOOT_GOOD_IDS` can pick -- these tests
    build their own content the same way `test_api_app.py`'s `make_content`
    does, rather than depending on `data/goods.yaml`."""
    good_ids = set(constants.STEAL_LOOT_GOOD_IDS) | set(constants.BURGLE_LOOT_GOOD_IDS)
    return {
        good_id: Good(
            id=good_id, name=good_id.replace("_", " ").title(), base_price=10.0, category="stolen"
        )
        for good_id in good_ids
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

    def test_raises_when_jailed(self):
        character = make_character(jailed_until_tick=100)
        victim = make_npc()
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_steal(character, victim, 10)
        assert exc_info.value.reason_key == "steal_jailed"

    def test_allowed_once_jail_has_expired(self):
        character = make_character(jailed_until_tick=5)
        victim = make_npc()
        stealing_svc.check_can_steal(character, victim, 10)  # no raise

    def test_refuses_a_story_mode_actor(self):
        character = make_character(rp_mode=RpMode.STORY.value)
        victim = make_npc()
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_steal(character, victim, 10)
        assert exc_info.value.reason_key == "crime_mode_forbidden"

    def test_refuses_an_actor_who_disabled_crime(self):
        character = make_character(crime_enabled=False)
        victim = make_npc()
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_steal(character, victim, 10)
        assert exc_info.value.reason_key == "crime_disabled_by_actor"

    def test_refuses_a_story_mode_character_victim(self):
        character = make_character()
        victim = make_character(name="Victim", rp_mode=RpMode.STORY.value, location_id="square")
        victim.id = 2
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_steal(character, victim, 10)
        assert exc_info.value.reason_key == "victim_is_story_mode"

    def test_refuses_a_character_victim_who_disabled_crime(self):
        character = make_character()
        victim = make_character(name="Victim", crime_enabled=False, location_id="square")
        victim.id = 2
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_steal(character, victim, 10)
        assert exc_info.value.reason_key == "victim_crime_disabled"

    def test_allows_an_npc_victim_with_no_crime_enabled_field(self):
        character = make_character()
        victim = make_npc(location_id="square")
        stealing_svc.check_can_steal(character, victim, 10)  # no raise


class TestResolveSteal:
    async def test_success_grants_a_random_loot_good_not_money(self, db_session):
        character = make_character(money=50)
        victim = make_npc(money=50.0)
        result = await stealing_svc.resolve_steal(
            db_session,
            character=character,
            victim=victim,
            district_id=1,
            current_tick=10,
            rng=SequenceRng([0.0]),
            goods=make_goods(),
        )
        assert result.success is True
        assert result.amount == constants.STEAL_LOOT_QTY
        assert result.good_name is not None
        # Neither wallet moves -- the payout is a good, never cash.
        assert character.money == 50
        assert victim.money == 50.0
        assert character.jailed_until_tick is None

        good_id = constants.STEAL_LOOT_GOOD_IDS[0]  # SequenceRng.choice always picks the first
        row = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", good_id))
        assert row is not None
        assert row.qty == constants.STEAL_LOOT_QTY

        # A clean, undetected success leaves the NPC's opinion of the
        # thief untouched -- they never noticed, so there's nothing for
        # it to react to. Only actually getting caught crashes it.
        relationship = await db_session.get(
            RelationshipRow,
            (OwnerKind.CHARACTER.value, "1", OwnerKind.NPC.value, victim.id),
        )
        assert relationship is None

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
            goods=make_goods(),
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
            goods=make_goods(),
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
            goods=make_goods(),
        )

        assert result.caught is True
        assert character.money == 100 - constants.STEAL_FINE
        # Sentence runs from the current tick (10), not from absolute
        # tick 0 -- see test_market_service.py's identical regression note.
        assert character.jailed_until_tick == 10 + constants.STEAL_JAIL_TICKS
        assert character.reputation == -constants.REP_STEAL_CAUGHT_GENERAL_PENALTY

        relationship = await db_session.get(
            RelationshipRow,
            (OwnerKind.CHARACTER.value, "1", OwnerKind.NPC.value, victim.id),
        )
        # Caught red-handed crashes the relationship to the same floor a
        # clean, undetected success does (`crash_to_hated`) -- being
        # caught is at least as damning as a theft they never noticed.
        assert relationship.affinity == constants.AFFINITY_FLOOR
        assert relationship.stance == "hates"

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
            goods=make_goods(),
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
            goods=make_goods(),
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
            goods=make_goods(),
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

    def test_raises_when_jailed(self):
        character = make_character(current_district_id=1, jailed_until_tick=100)
        character.id = 1
        house = make_house(district_id=1)
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_burgle(character, house, 10)
        assert exc_info.value.reason_key == "burgle_jailed"

    def test_allowed_for_a_stranger_s_house(self):
        character = make_character(current_district_id=1)
        character.id = 1
        house = make_house(district_id=1, owner_id=2)
        stealing_svc.check_can_burgle(character, house, 10)  # no raise

    def test_raises_when_the_owner_is_home(self):
        character = make_character(current_district_id=1)
        character.id = 1
        house = make_house(district_id=1, owner_id=2, location_id="home")
        owner = make_character(name="Owner", location_id="home")
        owner.id = 2
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_burgle(character, house, 10, owner=owner)
        assert exc_info.value.reason_key == "burgle_owner_home"

    def test_allowed_when_the_owner_is_elsewhere(self):
        character = make_character(current_district_id=1)
        character.id = 1
        house = make_house(district_id=1, owner_id=2, location_id="home")
        owner = make_character(name="Owner", location_id="market")
        owner.id = 2
        stealing_svc.check_can_burgle(character, house, 10, owner=owner)  # no raise

    def test_allowed_when_the_house_has_no_location(self):
        """Every property seeded before `location_id` existed (or a
        non-house kind) has `location_id=None` -- the occupancy check
        just skips rather than refusing every burglary."""
        character = make_character(current_district_id=1)
        character.id = 1
        house = make_house(district_id=1, owner_id=2, location_id=None)
        owner = make_character(name="Owner", location_id="home")
        owner.id = 2
        stealing_svc.check_can_burgle(character, house, 10, owner=owner)  # no raise

    def test_refuses_a_story_mode_actor(self):
        character = make_character(current_district_id=1, rp_mode=RpMode.STORY.value)
        character.id = 1
        house = make_house(district_id=1, owner_id=2)
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_burgle(character, house, 10)
        assert exc_info.value.reason_key == "crime_mode_forbidden"

    def test_refuses_a_life_mode_actor(self):
        """Life mode has no housing access at all -- distinct from the
        Story-mode block, and refused before the owner-mode check."""
        character = make_character(current_district_id=1, rp_mode=RpMode.LIFE.value)
        character.id = 1
        house = make_house(district_id=1, owner_id=2)
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_burgle(character, house, 10)
        assert exc_info.value.reason_key == "burgle_mode_forbidden"

    def test_refuses_a_story_mode_owner(self):
        character = make_character(current_district_id=1)
        character.id = 1
        house = make_house(district_id=1, owner_id=2)
        owner = make_character(name="Owner", rp_mode=RpMode.STORY.value)
        owner.id = 2
        with pytest.raises(NotAllowed) as exc_info:
            stealing_svc.check_can_burgle(character, house, 10, owner=owner)
        assert exc_info.value.reason_key == "victim_is_story_mode"


class TestResolveBurgle:
    async def test_success_grants_a_random_loot_good_not_money(self, db_session):
        character = make_character(current_district_id=1, money=0)
        character.id = 1
        house = make_house(district_id=1, owner_id=2, suggested_price=1000.0)
        result = await stealing_svc.resolve_burgle(
            db_session,
            character=character,
            house=house,
            current_tick=10,
            rng=SequenceRng([0.0]),
            goods=make_goods(),
        )
        assert result.success is True
        assert result.amount in range(
            constants.BURGLE_LOOT_QTY_RANGE[0], constants.BURGLE_LOOT_QTY_RANGE[1] + 1
        )
        assert result.good_name is not None
        assert character.money == 0  # never touched -- the payout is a good, not cash

        good_id = constants.BURGLE_LOOT_GOOD_IDS[0]  # SequenceRng.choice always picks the first
        row = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", good_id))
        assert row is not None
        assert row.qty == result.amount

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
            goods=make_goods(),
        )

        assert result.caught is True
        assert character.money == 100 - constants.STEAL_FINE
        # Sentence runs from the current tick (10), not from absolute
        # tick 0 -- see test_market_service.py's identical regression note.
        assert character.jailed_until_tick == 10 + constants.STEAL_JAIL_TICKS
        district_row = await db_session.get(DistrictState, 1)
        assert district_row.peacekeeper_pressure == 0.3 + stealing_svc.STEAL_PRESSURE_DELTA


class TestRollAndApplySteal:
    """The Activity-launch skip/fallback path: unlike `resolve_steal`,
    this never re-validates or re-touches `last_steal_tick` -- an
    Activity launch already did both once, up front."""

    async def test_does_not_touch_the_cooldown(self, db_session):
        character = make_character(money=0, last_steal_tick=42)
        victim = make_npc(money=50.0)
        await stealing_svc.roll_and_apply_steal(
            db_session,
            character=character,
            victim=victim,
            district_row=None,
            current_tick=42,
            rng=SequenceRng([0.0]),
            goods=make_goods(),
        )
        assert character.last_steal_tick == 42

    async def test_success_grants_loot_not_money(self, db_session):
        character = make_character(money=0)
        victim = make_npc(money=50.0)
        result = await stealing_svc.roll_and_apply_steal(
            db_session,
            character=character,
            victim=victim,
            district_row=None,
            current_tick=10,
            rng=SequenceRng([0.0]),
            goods=make_goods(),
        )
        assert result.success is True
        assert character.money == 0
        assert result.good_name is not None

    async def test_logs_the_attempt(self, db_session):
        character = make_character(money=0)
        victim = make_npc(name="Mark", money=50.0)
        await stealing_svc.roll_and_apply_steal(
            db_session,
            character=character,
            victim=victim,
            district_row=None,
            current_tick=10,
            rng=SequenceRng([0.0]),
            goods=make_goods(),
        )
        row = (await db_session.execute(select(CrimeLog))).scalar_one()
        assert row.character_id == character.id
        assert row.kind == "steal"
        assert row.tick == 10
        assert row.success is True
        assert row.caught is False
        assert row.target_name == "Mark"
        assert row.good_name is not None
        assert row.amount > 0


class TestRollAndApplyBurgle:
    async def test_does_not_touch_the_cooldown(self, db_session):
        character = make_character(money=0, last_steal_tick=42)
        await stealing_svc.roll_and_apply_burgle(
            db_session,
            character=character,
            house=make_house(suggested_price=1000.0),
            district_row=None,
            current_tick=42,
            rng=SequenceRng([0.0]),
            goods=make_goods(),
        )
        assert character.last_steal_tick == 42

    async def test_success_grants_loot_not_money(self, db_session):
        character = make_character(money=0)
        result = await stealing_svc.roll_and_apply_burgle(
            db_session,
            character=character,
            house=make_house(suggested_price=1000.0),
            district_row=None,
            current_tick=10,
            rng=SequenceRng([0.0]),
            goods=make_goods(),
        )
        assert result.success is True
        assert character.money == 0
        assert result.good_name is not None

    async def test_logs_the_attempt_with_the_owner_s_name(self, db_session):
        db_session.add(User(id=1, discord_id=1))
        await db_session.flush()
        character = make_character(money=0)
        character.id = 1
        owner = make_character(name="Peeta", money=0)
        owner.id = 2
        db_session.add(owner)
        await db_session.flush()
        house = make_house(suggested_price=1000.0, owner_id=owner.id)
        await stealing_svc.roll_and_apply_burgle(
            db_session,
            character=character,
            house=house,
            district_row=None,
            current_tick=10,
            rng=SequenceRng([0.0]),
            goods=make_goods(),
        )
        row = (await db_session.execute(select(CrimeLog))).scalar_one()
        assert row.kind == "burgle"
        assert row.target_name == "Peeta"

    async def test_logs_no_target_name_for_an_npc_owned_house(self, db_session):
        character = make_character(money=0)
        house = make_house(suggested_price=1000.0, owner_kind=OwnerKind.NPC.value, owner_id=None)
        await stealing_svc.roll_and_apply_burgle(
            db_session,
            character=character,
            house=house,
            district_row=None,
            current_tick=10,
            rng=SequenceRng([0.0]),
            goods=make_goods(),
        )
        row = (await db_session.execute(select(CrimeLog))).scalar_one()
        assert row.target_name is None
