from __future__ import annotations

import pytest

from panem_bot.errors import NotAllowed, NotFound
from panem_bot.services import housing as housing_svc
from panem_shared import constants
from panem_shared.db.models import ApartmentLease, Character, Property
from panem_shared.enums import CharacterStatus, DayPhase, OwnerKind, PropertyKind


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
        money=10_000,
        reputation=0.0,
        shifts_completed=0,
        fatigue=100.0,
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = 1
    return character


def make_property(**overrides: object) -> Property:
    defaults: dict[str, object] = dict(
        district_id=1,
        kind=PropertyKind.HOUSE.value,
        tier="apprentice",
        owner_kind=OwnerKind.NPC.value,
        for_sale=True,
        suggested_price=500.0,
    )
    defaults.update(overrides)
    property_ = Property(**defaults)  # type: ignore[arg-type]
    property_.id = 1
    return property_


class TestCheckCanBuyProperty:
    def test_refuses_a_dead_character(self):
        character = make_character(status=CharacterStatus.DEAD.value)
        property_ = make_property()
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_buy_property(character=character, property_=property_)
        assert exc_info.value.reason_key == "character_dead"

    def test_refuses_a_property_not_for_sale(self):
        character = make_character()
        property_ = make_property(for_sale=False)
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_buy_property(character=character, property_=property_)
        assert exc_info.value.reason_key == "housing_not_for_sale"

    def test_refuses_an_already_owned_property(self):
        character = make_character()
        property_ = make_property(owner_kind=OwnerKind.CHARACTER.value)
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_buy_property(character=character, property_=property_)
        assert exc_info.value.reason_key == "housing_already_owned"

    def test_refuses_a_house_outside_the_buyers_home_district(self):
        character = make_character(district_id=1)
        property_ = make_property(district_id=2)
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_buy_property(character=character, property_=property_)
        assert exc_info.value.reason_key == "housing_wrong_district"

    def test_refuses_a_house_above_the_buyers_job_level(self):
        character = make_character(shifts_completed=0)
        property_ = make_property(tier="expert")
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_buy_property(character=character, property_=property_)
        assert exc_info.value.reason_key == "housing_tier_too_low"

    def test_allows_a_house_at_the_buyers_exact_tier(self):
        character = make_character(shifts_completed=0)
        property_ = make_property(tier="apprentice")
        housing_svc.check_can_buy_property(character=character, property_=property_)

    def test_allows_a_house_below_the_buyers_job_level(self):
        character = make_character(shifts_completed=constants.JOB_LEVEL_SHIFT_THRESHOLDS["expert"])
        property_ = make_property(tier="apprentice")
        housing_svc.check_can_buy_property(character=character, property_=property_)

    def test_inn_ignores_district_and_tier(self):
        character = make_character(district_id=1, shifts_completed=0)
        property_ = make_property(kind=PropertyKind.INN.value, district_id=2, tier="expert")
        housing_svc.check_can_buy_property(character=character, property_=property_)


class TestCheckCanRent:
    def test_refuses_a_non_apartment(self):
        character = make_character()
        property_ = make_property(kind=PropertyKind.HOUSE.value)
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_rent(
                character=character, property_=property_, existing_lease=None
            )
        assert exc_info.value.reason_key == "housing_not_an_apartment"

    def test_refuses_an_already_leased_unit(self):
        character = make_character()
        property_ = make_property(kind=PropertyKind.APARTMENT.value)
        lease = ApartmentLease(
            property_id=1,
            tenant_character_id=2,
            rent_price=40.0,
            started_tick=0,
            next_rent_due_tick=24,
        )
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_rent(
                character=character, property_=property_, existing_lease=lease
            )
        assert exc_info.value.reason_key == "housing_unit_already_leased"

    def test_allows_a_vacant_unit(self):
        character = make_character()
        property_ = make_property(kind=PropertyKind.APARTMENT.value)
        housing_svc.check_can_rent(character=character, property_=property_, existing_lease=None)


class TestQuotedPrice:
    def test_uses_asking_price_override_when_set(self):
        character = make_character()
        property_ = make_property(suggested_price=500.0, asking_price=999.0, tier="apprentice")
        assert housing_svc.quoted_price(property_, character) == pytest.approx(999.0)

    def test_uses_suggested_price_when_no_override(self):
        character = make_character(shifts_completed=0)
        property_ = make_property(suggested_price=500.0, tier="apprentice")
        # Buyer and NPC-seller (house tier) are both apprentice -- parity.
        assert housing_svc.quoted_price(property_, character) == pytest.approx(500.0)

    def test_higher_mastery_buyer_gets_a_discount_on_a_house(self):
        character = make_character(
            shifts_completed=constants.JOB_LEVEL_SHIFT_THRESHOLDS["expert"], reputation=0.0
        )
        property_ = make_property(suggested_price=500.0, tier="apprentice")
        price = housing_svc.quoted_price(property_, character)
        assert price < 500.0

    def test_lower_mastery_buyer_pays_more_for_a_house(self):
        character = make_character(shifts_completed=0)
        property_ = make_property(suggested_price=500.0, tier="expert")
        # Buying below their level would be refused by check_can_buy_property,
        # but quoted_price itself is pure math -- confirm the direction.
        price = housing_svc.quoted_price(property_, character)
        assert price > 500.0

    def test_better_reputation_lowers_a_house_price(self):
        good_rep = make_character(reputation=100.0)
        bad_rep = make_character(reputation=-100.0)
        property_ = make_property(suggested_price=500.0, tier="apprentice")
        assert housing_svc.quoted_price(property_, good_rep) < housing_svc.quoted_price(
            property_, bad_rep
        )

    def test_reputation_does_not_affect_apartment_or_inn_pricing(self):
        good_rep = make_character(reputation=100.0, shifts_completed=0)
        bad_rep = make_character(reputation=-100.0, shifts_completed=0)
        property_ = make_property(kind=PropertyKind.INN.value, suggested_price=15.0)
        assert housing_svc.quoted_price(property_, good_rep) == housing_svc.quoted_price(
            property_, bad_rep
        )

    def test_real_seller_mastery_used_over_npc_default(self):
        buyer = make_character(shifts_completed=0)
        expert_seller = make_character(
            shifts_completed=constants.JOB_LEVEL_SHIFT_THRESHOLDS["expert"]
        )
        property_ = make_property(kind=PropertyKind.INN.value, suggested_price=15.0)
        price_vs_npc = housing_svc.quoted_price(property_, buyer)
        price_vs_expert = housing_svc.quoted_price(property_, buyer, seller=expert_seller)
        assert price_vs_expert > price_vs_npc

    def test_price_multiplier_is_clamped(self):
        buyer = make_character(shifts_completed=0, reputation=1_000_000.0)
        property_ = make_property(tier="expert", suggested_price=500.0)
        price = housing_svc.quoted_price(property_, buyer)
        assert price == pytest.approx(500.0 * constants.HOUSING_PRICE_MULT_MIN)


class TestComplexPurchasePrice:
    def test_scales_with_unit_count(self):
        units = [make_property(kind=PropertyKind.APARTMENT.value) for _ in range(6)]
        assert housing_svc.complex_purchase_price(units) == constants.APARTMENT_UNIT_BASE_PRICE * 6


class TestCheckCanBuyComplex:
    def test_refuses_an_empty_complex(self):
        character = make_character()
        with pytest.raises(NotFound):
            housing_svc.check_can_buy_complex(character=character, units=[])

    def test_refuses_a_partially_owned_complex(self):
        character = make_character()
        units = [
            make_property(kind=PropertyKind.APARTMENT.value, owner_kind=OwnerKind.NPC.value),
            make_property(kind=PropertyKind.APARTMENT.value, owner_kind=OwnerKind.CHARACTER.value),
        ]
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_buy_complex(character=character, units=units)
        assert exc_info.value.reason_key == "housing_complex_partially_owned"

    def test_allows_a_fully_npc_owned_complex(self):
        character = make_character()
        units = [make_property(kind=PropertyKind.APARTMENT.value) for _ in range(3)]
        housing_svc.check_can_buy_complex(character=character, units=units)


class TestFatigue:
    def test_has_a_bed_true_when_housing_property_set(self):
        character = make_character(housing_property_id=1)
        assert housing_svc.has_a_bed(character) is True

    def test_has_a_bed_false_without_a_home(self):
        character = make_character(housing_property_id=None)
        assert housing_svc.has_a_bed(character) is False

    def test_fatigue_restored_full_rate_with_a_bed(self):
        assert (
            housing_svc.fatigue_restored(2, has_bed=True) == 2 * constants.FATIGUE_RESTORE_PER_TICK
        )

    def test_fatigue_restored_halved_on_the_ground(self):
        assert housing_svc.fatigue_restored(2, has_bed=False) == pytest.approx(
            2 * constants.FATIGUE_RESTORE_PER_TICK * constants.FATIGUE_GROUND_SLEEP_MULT
        )

    def test_apply_fatigue_restoration_caps_at_max(self):
        character = make_character(fatigue=95.0)
        restored = housing_svc.apply_fatigue_restoration(character, 5, has_bed=True)
        assert character.fatigue == constants.FATIGUE_MAX
        assert restored == pytest.approx(constants.FATIGUE_MAX - 95.0)

    def test_dock_fatigue_floors_at_min(self):
        character = make_character(fatigue=2.0)
        housing_svc.dock_fatigue(character, 10.0)
        assert character.fatigue == constants.FATIGUE_MIN


class TestCheckCanSleep:
    def test_allows_night_phase(self):
        housing_svc.check_can_sleep(DayPhase.NIGHT)

    def test_refuses_other_phases(self):
        for phase in (DayPhase.MORNING, DayPhase.AFTERNOON, DayPhase.EVENING):
            with pytest.raises(NotAllowed) as exc_info:
                housing_svc.check_can_sleep(phase)
            assert exc_info.value.reason_key == "sleep_wrong_phase"
