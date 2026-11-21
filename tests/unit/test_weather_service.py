from __future__ import annotations

import random

import pytest

from panem_bot.errors import NotAllowed, NotFound, ValidationFailed
from panem_shared import constants
from panem_shared import weather as weather_svc
from panem_shared.content.schemas import DistrictClimate, SeasonRange
from panem_shared.db.models import Character, Inventory, Property, User, WeatherEvent
from panem_shared.enums import OwnerKind, Season, WeatherEventStatus


def make_climate(**inclement: list[str]) -> DistrictClimate:
    return DistrictClimate(
        district_id=1,
        spring=SeasonRange(low=40, high=60),
        summer=SeasonRange(low=60, high=90),
        autumn=SeasonRange(low=40, high=60),
        winter=SeasonRange(low=10, high=30),
        inclement=inclement,
    )


class TestGenerateForecastValues:
    def test_high_always_exceeds_low(self):
        climate = make_climate()
        rng = random.Random(0)
        for _ in range(50):
            low, high, _ = weather_svc.generate_forecast_values(
                climate, season=Season.SUMMER, rng=rng
            )
            assert low < high
            assert 60 <= low < 90
            assert 60 < high <= 90

    def test_winter_without_a_roll_is_snow_or_clear_never_rain(self):
        climate = make_climate()
        rng = random.Random(1)
        seen = set()
        for _ in range(200):
            _, _, condition = weather_svc.generate_forecast_values(
                climate, season=Season.WINTER, rng=rng
            )
            seen.add(condition)
        assert seen <= {"snow", "clear"}


class TestPropertyDamageChance:
    def test_higher_severity_is_more_likely_to_damage(self):
        low = weather_svc.property_damage_chance(1, prep_level=0)
        high = weather_svc.property_damage_chance(6, prep_level=0)
        assert high > low

    def test_prep_reduces_chance(self):
        unprepared = weather_svc.property_damage_chance(6, prep_level=0)
        prepared = weather_svc.property_damage_chance(
            6, prep_level=constants.WEATHER_PREP_MAX_LEVEL
        )
        assert prepared < unprepared

    def test_max_prep_never_exceeds_full_reduction(self):
        chance = weather_svc.property_damage_chance(6, prep_level=100)
        assert chance == 0.0


class TestIsExposed:
    def test_in_transit_is_always_exposed(self):
        character = Character(
            user_id=1,
            district_id=1,
            current_district_id=1,
            name="A",
            age=20,
            in_transit_until_tick=10,
        )
        assert weather_svc.is_exposed(character, location=None) is True

    def test_residential_location_is_sheltered(self):
        class FakeLocation:
            kind = "residential"

        character = Character(user_id=1, district_id=1, current_district_id=1, name="A", age=20)
        assert weather_svc.is_exposed(character, FakeLocation()) is False

    def test_public_location_is_exposed(self):
        class FakeLocation:
            kind = "public"

        character = Character(user_id=1, district_id=1, current_district_id=1, name="A", age=20)
        assert weather_svc.is_exposed(character, FakeLocation()) is True


async def _persist_character_and_property(db_session, **property_overrides):
    user = User(discord_id=999)
    db_session.add(user)
    await db_session.flush()
    character = Character(
        user_id=user.id,
        district_id=1,
        current_district_id=1,
        name="Prepper",
        age=25,
        money=1000,
    )
    db_session.add(character)
    await db_session.flush()
    defaults = dict(
        district_id=1,
        kind="house",
        tier="apprentice",
        owner_id=character.id,
        created_at_tick=0,
    )
    defaults.update(property_overrides)
    property_row = Property(**defaults)
    db_session.add(property_row)
    await db_session.flush()
    return character, property_row


class TestPrepareProperty:
    async def test_raises_for_unknown_weather_kind(self, db_session):
        character, property_row = await _persist_character_and_property(db_session)
        with pytest.raises(NotFound):
            await weather_svc.prepare_property(
                db_session, character=character, property_row=property_row, kind="meteor_shower"
            )

    async def test_raises_without_enough_lumber(self, db_session):
        character, property_row = await _persist_character_and_property(db_session)
        with pytest.raises(ValidationFailed):
            await weather_svc.prepare_property(
                db_session, character=character, property_row=property_row, kind="blizzard"
            )

    async def test_spends_lumber_and_money_and_raises_the_level(self, db_session):
        character, property_row = await _persist_character_and_property(db_session)
        db_session.add(
            Inventory(
                owner_kind=OwnerKind.CHARACTER.value,
                owner_id=str(character.id),
                good_id=constants.WEATHER_PREP_MATERIAL_GOOD_ID,
                qty=constants.WEATHER_PREP_COST_PER_LEVEL,
            )
        )
        await db_session.flush()
        money_before = character.money

        level = await weather_svc.prepare_property(
            db_session, character=character, property_row=property_row, kind="blizzard"
        )
        assert level == 1
        assert property_row.weather_prep["blizzard"] == 1
        assert character.money == money_before - constants.WEATHER_PREP_COST_PER_LEVEL

    async def test_raises_once_already_at_the_cap(self, db_session):
        character, property_row = await _persist_character_and_property(
            db_session, weather_prep={"blizzard": constants.WEATHER_PREP_MAX_LEVEL}
        )
        with pytest.raises(NotAllowed):
            await weather_svc.prepare_property(
                db_session, character=character, property_row=property_row, kind="blizzard"
            )


class TestRepairProperty:
    async def test_raises_with_nothing_to_repair(self, db_session):
        character, property_row = await _persist_character_and_property(db_session, damage_level=0)
        with pytest.raises(ValidationFailed):
            await weather_svc.repair_property(
                db_session, character=character, property_row=property_row, points=10
            )

    async def test_repairs_and_charges_money(self, db_session):
        character, property_row = await _persist_character_and_property(db_session, damage_level=20)
        money_before = character.money

        cost = await weather_svc.repair_property(
            db_session, character=character, property_row=property_row, points=10
        )
        assert property_row.damage_level == 10
        assert cost == 10 * constants.WEATHER_REPAIR_COST_PER_POINT
        assert character.money == money_before - cost

    async def test_caps_repair_at_current_damage(self, db_session):
        character, property_row = await _persist_character_and_property(db_session, damage_level=5)
        cost = await weather_svc.repair_property(
            db_session, character=character, property_row=property_row, points=999
        )
        assert property_row.damage_level == 0
        assert cost == 5 * constants.WEATHER_REPAIR_COST_PER_POINT


class TestActivateAndEndEvents:
    async def test_due_forecast_event_activates_and_damages_properties(self, db_session):
        _character, property_row = await _persist_character_and_property(db_session)

        event = WeatherEvent(
            district_id=1,
            day_index=0,
            kind="blizzard",
            severity=6,
            predicted_start_tick=10,
            predicted_duration_ticks=5,
            actual_start_tick=10,
            actual_end_tick=15,
            status=WeatherEventStatus.FORECAST.value,
            announced_tick=0,
        )
        db_session.add(event)
        await db_session.flush()

        rng = random.Random()
        rng.random = lambda: 0.0  # type: ignore[method-assign]  # always "damage" and never fizzle
        newly_active, newly_ended = await weather_svc.activate_and_end_events(
            db_session, current_tick=10, rng=rng
        )
        assert event in newly_active
        assert event.status == WeatherEventStatus.ACTIVE.value
        assert property_row.damage_level > 0
        assert newly_ended == []

    async def test_active_event_past_its_end_tick_ends(self, db_session):
        event = WeatherEvent(
            district_id=1,
            day_index=0,
            kind="blizzard",
            severity=6,
            predicted_start_tick=10,
            predicted_duration_ticks=5,
            actual_start_tick=10,
            actual_end_tick=15,
            status=WeatherEventStatus.ACTIVE.value,
            announced_tick=0,
        )
        db_session.add(event)
        await db_session.flush()

        _, newly_ended = await weather_svc.activate_and_end_events(
            db_session, current_tick=15, rng=random.Random(0)
        )
        assert event in newly_ended
        assert event.status == WeatherEventStatus.ENDED.value
