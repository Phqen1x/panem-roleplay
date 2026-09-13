from __future__ import annotations

import pytest

from panem_bot.errors import NotAllowed, NotFound
from panem_bot.services import housing as housing_svc
from panem_shared import constants
from panem_shared.db.models import ApartmentLease, Character, Property, PropertyAuction
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
        mortgage_principal=0.0,
        mortgage_payment=0.0,
        mortgage_next_due_tick=None,
        mortgage_missed_payments=0,
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


class TestFinancedPurchaseTerms:
    def test_down_payment_is_the_configured_percentage(self):
        terms = housing_svc.financed_purchase_terms(1000.0)
        assert terms.down_payment == pytest.approx(1000.0 * constants.MORTGAGE_DOWN_PAYMENT_PCT)

    def test_principal_includes_the_origination_surcharge(self):
        terms = housing_svc.financed_purchase_terms(1000.0)
        financed_amount = 1000.0 * (1.0 - constants.MORTGAGE_DOWN_PAYMENT_PCT)
        assert terms.principal == pytest.approx(
            financed_amount * (1.0 + constants.MORTGAGE_INTEREST_RATE)
        )

    def test_payment_is_principal_spread_over_the_installments(self):
        terms = housing_svc.financed_purchase_terms(1000.0)
        installments = (
            constants.MORTGAGE_TERM_TICKS_DEFAULT // constants.MORTGAGE_PAYMENT_INTERVAL_TICKS
        )
        assert terms.payment == pytest.approx(terms.principal / installments)


class TestCheckOwnsProperty:
    def test_refuses_a_property_owned_by_someone_else(self):
        character = make_character()
        property_ = make_property(owner_kind=OwnerKind.CHARACTER.value, owner_id=999)
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_owns_property(character=character, property_=property_)
        assert exc_info.value.reason_key == "housing_not_your_property"

    def test_refuses_an_npc_owned_property(self):
        character = make_character()
        property_ = make_property(owner_kind=OwnerKind.NPC.value)
        with pytest.raises(NotAllowed):
            housing_svc.check_owns_property(character=character, property_=property_)

    def test_allows_the_actual_owner(self):
        character = make_character()
        property_ = make_property(owner_kind=OwnerKind.CHARACTER.value, owner_id=character.id)
        housing_svc.check_owns_property(character=character, property_=property_)


class TestPropertyValueAndRefinance:
    def test_property_value_uses_asking_price_override(self):
        property_ = make_property(suggested_price=500.0, asking_price=800.0)
        assert housing_svc.property_value(property_) == pytest.approx(800.0)

    def test_property_value_falls_back_to_suggested_price(self):
        property_ = make_property(suggested_price=500.0)
        assert housing_svc.property_value(property_) == pytest.approx(500.0)

    def test_max_refinance_amount_is_ltv_cap_minus_existing_principal(self):
        property_ = make_property(suggested_price=1000.0, mortgage_principal=100.0)
        expected = 1000.0 * constants.MORTGAGE_MAX_LTV - 100.0
        assert housing_svc.max_refinance_amount(property_) == pytest.approx(expected)

    def test_max_refinance_amount_never_goes_negative(self):
        property_ = make_property(suggested_price=100.0, mortgage_principal=1_000_000.0)
        assert housing_svc.max_refinance_amount(property_) == 0.0

    def test_check_can_refinance_refuses_over_the_cap(self):
        character = make_character()
        property_ = make_property(
            suggested_price=1000.0, owner_kind=OwnerKind.CHARACTER.value, owner_id=character.id
        )
        too_much = housing_svc.max_refinance_amount(property_) + 1.0
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_refinance(
                character=character, property_=property_, amount=too_much
            )
        assert exc_info.value.reason_key == "housing_refinance_too_much"

    def test_check_can_refinance_allows_up_to_the_cap(self):
        character = make_character()
        property_ = make_property(
            suggested_price=1000.0, owner_kind=OwnerKind.CHARACTER.value, owner_id=character.id
        )
        cap = housing_svc.max_refinance_amount(property_)
        housing_svc.check_can_refinance(character=character, property_=property_, amount=cap)

    def test_check_can_refinance_refuses_a_non_owner(self):
        character = make_character()
        property_ = make_property(owner_kind=OwnerKind.NPC.value, suggested_price=1000.0)
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_refinance(character=character, property_=property_, amount=1.0)
        assert exc_info.value.reason_key == "housing_not_your_property"

    def test_apply_refinance_adds_to_principal_and_reamortizes(self):
        property_ = make_property(
            suggested_price=1000.0, mortgage_principal=100.0, mortgage_payment=5.0
        )
        payment = housing_svc.apply_refinance(property_, 200.0, tick=100)
        installments = (
            constants.MORTGAGE_TERM_TICKS_DEFAULT // constants.MORTGAGE_PAYMENT_INTERVAL_TICKS
        )
        assert property_.mortgage_principal == pytest.approx(300.0)
        assert payment == pytest.approx(300.0 / installments)
        assert property_.mortgage_payment == pytest.approx(payment)

    def test_apply_refinance_sets_a_due_tick_when_there_was_none(self):
        property_ = make_property(suggested_price=1000.0, mortgage_principal=0.0)
        housing_svc.apply_refinance(property_, 100.0, tick=50)
        assert property_.mortgage_next_due_tick == 50 + constants.MORTGAGE_PAYMENT_INTERVAL_TICKS

    def test_apply_refinance_resets_missed_payments(self):
        property_ = make_property(
            suggested_price=1000.0, mortgage_principal=100.0, mortgage_missed_payments=2
        )
        housing_svc.apply_refinance(property_, 50.0, tick=10)
        assert property_.mortgage_missed_payments == 0


class TestAuctions:
    def test_check_can_start_auction_refuses_a_non_owner(self):
        character = make_character()
        property_ = make_property(owner_kind=OwnerKind.NPC.value)
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_start_auction(
                character=character, property_=property_, existing_auction=None
            )
        assert exc_info.value.reason_key == "housing_not_your_property"

    def test_check_can_start_auction_refuses_an_already_open_auction(self):
        character = make_character()
        property_ = make_property(owner_kind=OwnerKind.CHARACTER.value, owner_id=character.id)
        auction = PropertyAuction(
            property_id=1,
            seller_kind=OwnerKind.CHARACTER.value,
            seller_id=character.id,
            minimum_bid=100.0,
            ends_at_tick=1000,
            status="open",
        )
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_start_auction(
                character=character, property_=property_, existing_auction=auction
            )
        assert exc_info.value.reason_key == "housing_auction_already_open"

    def test_check_can_start_auction_allows_a_closed_prior_auction(self):
        character = make_character()
        property_ = make_property(owner_kind=OwnerKind.CHARACTER.value, owner_id=character.id)
        auction = PropertyAuction(
            property_id=1,
            seller_kind=OwnerKind.CHARACTER.value,
            seller_id=character.id,
            minimum_bid=100.0,
            ends_at_tick=1000,
            status="closed",
        )
        housing_svc.check_can_start_auction(
            character=character, property_=property_, existing_auction=auction
        )

    def test_check_can_bid_refuses_a_bid_at_or_below_the_floor(self):
        character = make_character(money=10_000)
        auction = PropertyAuction(
            property_id=1,
            seller_kind="bank",
            seller_id=None,
            minimum_bid=100.0,
            ends_at_tick=1000,
            status="open",
        )
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_bid(character=character, auction=auction, amount=100.0)
        assert exc_info.value.reason_key == "housing_bid_too_low"

    def test_check_can_bid_refuses_a_bid_below_the_current_bid(self):
        character = make_character(money=10_000)
        auction = PropertyAuction(
            property_id=1,
            seller_kind="bank",
            seller_id=None,
            minimum_bid=100.0,
            current_bid=200.0,
            current_bidder_id=999,
            ends_at_tick=1000,
            status="open",
        )
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_bid(character=character, auction=auction, amount=150.0)
        assert exc_info.value.reason_key == "housing_bid_too_low"

    def test_check_can_bid_refuses_insufficient_funds(self):
        character = make_character(money=50)
        auction = PropertyAuction(
            property_id=1,
            seller_kind="bank",
            seller_id=None,
            minimum_bid=100.0,
            ends_at_tick=1000,
            status="open",
        )
        with pytest.raises(NotAllowed) as exc_info:
            housing_svc.check_can_bid(character=character, auction=auction, amount=150.0)
        assert exc_info.value.reason_key == "housing_insufficient_funds"

    def test_check_can_bid_allows_a_valid_higher_bid(self):
        character = make_character(money=10_000)
        auction = PropertyAuction(
            property_id=1,
            seller_kind="bank",
            seller_id=None,
            minimum_bid=100.0,
            ends_at_tick=1000,
            status="open",
        )
        housing_svc.check_can_bid(character=character, auction=auction, amount=150.0)
