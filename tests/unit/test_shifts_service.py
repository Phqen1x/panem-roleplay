from __future__ import annotations

from panem_bot.services import shifts as shifts_svc
from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    DistrictQuota,
    Good,
    Location,
)
from panem_shared.db.models import Character, DistrictState, MarketPrice, Shift, User
from panem_shared.enums import CharacterStatus


class FixedRng:
    """A stand-in for `random.Random` that always returns a fixed draw."""

    def __init__(self, value: float) -> None:
        self._value = value

    def random(self) -> float:
        return self._value


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
        money=0,
        reputation=0.0,
        health=100.0,
        consecutive_missed=0,
        shifts_completed=0,
        positions=[],
    )
    defaults.update(overrides)
    return Character(**defaults)  # type: ignore[arg-type]


def make_district(*, quota_good: str | None = "coal") -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Station", kind="station"),
    ]
    coords = {loc.id: (0, 0) for loc in locations}
    return District(
        id=12,
        name="District Twelve",
        industry="coal",
        locations=locations,
        culture=DistrictCulture(),
        population_base=100,
        quota=DistrictQuota(good=quota_good, amount=100) if quota_good else None,
        map=DistrictMap(image="x.png", width=10, height=10, location_coords=coords),
    )


class TestHasJob:
    def test_true_with_both_fields_set(self):
        character = make_character(job_title="Miner", shift_phase="morning")
        assert shifts_svc.has_job(character)

    def test_false_without_a_job_title(self):
        character = make_character(job_title=None, shift_phase="morning")
        assert not shifts_svc.has_job(character)

    def test_false_without_a_shift_phase(self):
        character = make_character(job_title="Miner", shift_phase=None)
        assert not shifts_svc.has_job(character)


class TestStartShiftGame:
    def test_sets_started_at_tick(self):
        shift = Shift(character_id=1, job_id="Miner", tick_opened=0, tick_due=6)
        shifts_svc.start_shift_game(shift, 3)
        assert shift.started_at_tick == 3

    def test_is_idempotent(self):
        shift = Shift(character_id=1, job_id="Miner", tick_opened=0, tick_due=6)
        shifts_svc.start_shift_game(shift, 3)
        shifts_svc.start_shift_game(shift, 10)
        assert shift.started_at_tick == 3


class TestOpenAdhocShiftOverride:
    def test_none_without_a_job(self):
        character = make_character(job_title=None, shift_phase=None, positions=["gamemaker"])
        assert shifts_svc.open_adhoc_shift_override(character, 10) is None

    def test_none_without_the_gamemaker_position_or_staff_override(self):
        character = make_character(job_title="Miner", shift_phase="morning", positions=[])
        assert shifts_svc.open_adhoc_shift_override(character, 10) is None

    def test_synthesizes_a_shift_for_a_gamemaker_with_a_job(self):
        character = make_character(
            job_title="Miner", shift_phase="morning", positions=["gamemaker"]
        )
        character.id = 7
        shift = shifts_svc.open_adhoc_shift_override(character, 10)
        assert shift is not None
        assert shift.character_id == 7
        assert shift.job_id == "Miner"
        assert shift.tick_opened == 10
        assert shift.tick_due == 10 + shifts_svc.constants.SHIFT_DURATION_TICKS
        assert shift.result is None

    def test_none_for_staff_override_without_a_job(self):
        character = make_character(job_title=None, shift_phase=None, positions=[])
        assert shifts_svc.open_adhoc_shift_override(character, 10, is_staff=True) is None

    def test_synthesizes_a_shift_for_staff_without_the_gamemaker_position(self):
        character = make_character(job_title="Miner", shift_phase="morning", positions=[])
        character.id = 9
        shift = shifts_svc.open_adhoc_shift_override(character, 10, is_staff=True)
        assert shift is not None
        assert shift.character_id == 9
        assert shift.job_id == "Miner"


class TestAlreadyWorkedThisTick:
    def test_reexported_from_shared(self):
        shift = Shift(character_id=1, job_id="Miner", tick_opened=0, tick_due=6)
        assert shifts_svc.already_worked_this_tick(shift, 3) is False
        shift.last_worked_tick = 3
        assert shifts_svc.already_worked_this_tick(shift, 3) is True


class TestCanEarnRpCreditAnywhere:
    def test_true_for_a_gamemaker(self):
        character = make_character(positions=["gamemaker"])
        assert shifts_svc.can_earn_rp_credit_anywhere(character)

    def test_false_without_the_position(self):
        character = make_character(positions=["victor"])
        assert not shifts_svc.can_earn_rp_credit_anywhere(character)


class TestMeetsRpCredit:
    def test_below_threshold_fails(self):
        assert (
            shifts_svc.meets_rp_credit("x" * (shifts_svc.constants.RP_CREDIT_MIN_CHARS - 1))
            is False
        )

    def test_at_threshold_passes(self):
        assert shifts_svc.meets_rp_credit("x" * shifts_svc.constants.RP_CREDIT_MIN_CHARS) is True


class TestMarketMultiplierForDistrict:
    async def test_no_quota_good_gives_unit_multiplier(self, db_session):
        district = make_district(quota_good=None)
        content = ContentBundle(districts={}, goods={}, jobs={}, routes=[])
        multiplier = await shifts_svc.market_multiplier_for_district(db_session, content, district)
        assert multiplier == 1.0

    async def test_no_price_row_yet_gives_unit_multiplier(self, db_session):
        district = make_district(quota_good="coal")
        good = Good(id="coal", name="Coal", base_price=10.0, category="fuel")
        content = ContentBundle(districts={}, goods={"coal": good}, jobs={}, routes=[])
        multiplier = await shifts_svc.market_multiplier_for_district(db_session, content, district)
        assert multiplier == 1.0

    async def test_uses_the_live_market_price(self, db_session):
        district = make_district(quota_good="coal")
        good = Good(id="coal", name="Coal", base_price=10.0, category="fuel")
        content = ContentBundle(districts={}, goods={"coal": good}, jobs={}, routes=[])
        db_session.add(MarketPrice(district_id=12, good_id="coal", price=20.0, tick=0))
        await db_session.flush()

        multiplier = await shifts_svc.market_multiplier_for_district(db_session, content, district)

        assert multiplier == 2.0


class TestResolveIllicitHeat:
    async def test_looks_up_the_district_row_and_bumps_pressure_on_arrest(self, db_session):
        character = make_character(illicit_heat=99.0, jail_count=0, jailed_until_tick=None)
        db_session.add(DistrictState(district_id=1, peacekeeper_pressure=0.3))
        await db_session.flush()

        arrested = await shifts_svc.resolve_illicit_heat(
            db_session,
            character=character,
            district_id=1,
            current_tick=0,
            lost=False,
            rng=FixedRng(0.99),
        )

        assert arrested is True
        assert character.jailed_until_tick == constants.ILLICIT_ARREST_JAIL_TICKS
        district_row = await db_session.get(DistrictState, 1)
        assert district_row.peacekeeper_pressure == 0.3 + 0.05

    async def test_below_threshold_never_touches_jail(self, db_session):
        character = make_character(illicit_heat=0.0, jail_count=0, jailed_until_tick=None)

        arrested = await shifts_svc.resolve_illicit_heat(
            db_session,
            character=character,
            district_id=1,
            current_tick=0,
            lost=False,
            rng=FixedRng(0.99),
        )

        assert arrested is False
        assert character.jailed_until_tick is None

    async def test_missing_district_row_does_not_raise(self, db_session):
        character = make_character(illicit_heat=99.0, jail_count=0, jailed_until_tick=None)

        arrested = await shifts_svc.resolve_illicit_heat(
            db_session,
            character=character,
            district_id=999,
            current_tick=0,
            lost=False,
            rng=FixedRng(0.99),
        )

        assert arrested is True


class TestShiftPhaseMath:
    def test_is_in_shift_phase_matches(self):
        char = make_character(job_title="Miner", shift_phase="evening")
        # Tick 18 is 6:00 PM (Evening)
        assert shifts_svc.is_in_shift_phase(char, 18) is True
        # Tick 19 is 7:00 PM (Evening)
        assert shifts_svc.is_in_shift_phase(char, 19) is True
        # Tick 12 is 12:00 PM (Afternoon)
        assert shifts_svc.is_in_shift_phase(char, 12) is False

    def test_is_in_shift_phase_unemployed(self):
        char = make_character(job_title=None, shift_phase="evening")
        assert shifts_svc.is_in_shift_phase(char, 18) is False
        char2 = make_character(job_title="Miner", shift_phase=None)
        assert shifts_svc.is_in_shift_phase(char2, 18) is False

    def test_phase_window_for_tick(self):
        assert shifts_svc.phase_window_for_tick(18) == (18, 24)
        assert shifts_svc.phase_window_for_tick(19) == (18, 24)
        assert shifts_svc.phase_window_for_tick(23) == (18, 24)
        assert shifts_svc.phase_window_for_tick(0) == (0, 6)
        assert shifts_svc.phase_window_for_tick(6) == (6, 12)
        assert shifts_svc.phase_window_for_tick(12) == (12, 18)


class TestGetOrOpenShiftForCharacter:
    async def _setup_user(self, db_session):
        user = await db_session.get(User, 1)
        if user is None:
            user = User(id=1, discord_id=1)
            db_session.add(user)
            await db_session.flush()

    async def test_returns_existing_open_shift(self, db_session):
        await self._setup_user(db_session)
        char = make_character(id=1, job_title="Miner", shift_phase="evening")
        db_session.add(char)
        shift = Shift(character_id=1, job_id="Miner", tick_opened=18, tick_due=24, result=None)
        db_session.add(shift)
        await db_session.flush()

        result = await shifts_svc.get_or_open_shift_for_character(db_session, char, 18)
        assert result is not None
        assert result.id == shift.id

    async def test_opens_shift_during_shift_phase_when_none_exists(self, db_session):
        await self._setup_user(db_session)
        char = make_character(id=2, job_title="Barkeep", shift_phase="evening")
        db_session.add(char)
        await db_session.flush()

        # At tick 18 (6:00 PM), no shift existed yet -> opens one due at tick 24 (12:00 AM)
        shift = await shifts_svc.get_or_open_shift_for_character(db_session, char, 18)
        assert shift is not None
        assert shift.character_id == 2
        assert shift.job_id == "Barkeep"
        assert shift.tick_opened == 18
        assert shift.tick_due == 24
        assert shift.result is None

    async def test_opens_shift_mid_phase_when_none_exists(self, db_session):
        await self._setup_user(db_session)
        char = make_character(id=3, job_title="Barkeep", shift_phase="evening")
        db_session.add(char)
        await db_session.flush()

        # At tick 20 (8:00 PM), opens shift with window (18, 24)
        shift = await shifts_svc.get_or_open_shift_for_character(db_session, char, 20)
        assert shift is not None
        assert shift.tick_opened == 18
        assert shift.tick_due == 24

    async def test_returns_none_outside_shift_phase(self, db_session):
        await self._setup_user(db_session)
        char = make_character(id=4, job_title="Barkeep", shift_phase="evening")
        db_session.add(char)
        await db_session.flush()

        # At tick 12 (Afternoon), not in shift phase -> None
        shift = await shifts_svc.get_or_open_shift_for_character(db_session, char, 12)
        assert shift is None

    async def test_returns_none_if_already_completed_this_phase(self, db_session):
        await self._setup_user(db_session)
        char = make_character(id=5, job_title="Barkeep", shift_phase="evening")
        db_session.add(char)
        completed_shift = Shift(
            character_id=5, job_id="Barkeep", tick_opened=18, tick_due=24, result="completed"
        )
        db_session.add(completed_shift)
        await db_session.flush()

        # Shift already completed for the window -> None
        shift = await shifts_svc.get_or_open_shift_for_character(db_session, char, 19)
        assert shift is None

    async def test_opens_adhoc_for_staff_outside_shift_phase(self, db_session):
        await self._setup_user(db_session)
        char = make_character(id=6, job_title="Barkeep", shift_phase="evening")
        db_session.add(char)
        await db_session.flush()

        # At tick 12 (Afternoon), is_staff=True creates adhoc shift
        shift = await shifts_svc.get_or_open_shift_for_character(
            db_session, char, 12, is_staff=True
        )
        assert shift is not None
        assert shift.character_id == 6
