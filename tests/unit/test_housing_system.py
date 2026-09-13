from __future__ import annotations

import pytest

from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.db.models import (
    ApartmentLease,
    Character,
    DistrictState,
    Property,
    PropertyAuction,
)
from panem_shared.enums import CharacterStatus, DayPhase, OwnerKind, PropertyKind
from panem_sim.rng import tick_rng
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import housing


def make_content() -> ContentBundle:
    return ContentBundle(districts={}, goods={}, jobs={}, routes=[])


def make_property(id_: int, **overrides: object) -> Property:
    defaults: dict[str, object] = dict(
        district_id=1,
        kind=PropertyKind.HOUSE.value,
        tier="apprentice",
        owner_kind=OwnerKind.NPC.value,
        for_sale=True,
        suggested_price=0.0,
        mortgage_principal=0.0,
        mortgage_payment=0.0,
        mortgage_next_due_tick=None,
        mortgage_missed_payments=0,
    )
    defaults.update(overrides)
    property_ = Property(**defaults)  # type: ignore[arg-type]
    property_.id = id_
    return property_


def make_district_state(**overrides: object) -> DistrictState:
    defaults: dict[str, object] = dict(district_id=1, unrest=0.0, capitol_favor=0.0)
    defaults.update(overrides)
    return DistrictState(**defaults)  # type: ignore[arg-type]


def make_character(id_: int, **overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=id_,
        district_id=1,
        current_district_id=1,
        name=f"Char{id_}",
        age=20,
        status=CharacterStatus.APPROVED.value,
        money=1_000,
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = id_
    return character


def make_lease(id_: int, **overrides: object) -> ApartmentLease:
    defaults: dict[str, object] = dict(
        property_id=1,
        tenant_character_id=1,
        rent_price=40.0,
        started_tick=0,
        next_rent_due_tick=0,
        missed_payments=0,
    )
    defaults.update(overrides)
    lease = ApartmentLease(**defaults)  # type: ignore[arg-type]
    lease.id = id_
    return lease


def make_auction(id_: int, **overrides: object) -> PropertyAuction:
    defaults: dict[str, object] = dict(
        property_id=1,
        seller_kind="bank",
        seller_id=None,
        minimum_bid=100.0,
        ends_at_tick=0,
        status="open",
    )
    defaults.update(overrides)
    auction = PropertyAuction(**defaults)  # type: ignore[arg-type]
    auction.id = id_
    return auction


def make_ctx(*, tick: int) -> TickContext:
    return TickContext(
        tick=tick,
        phase=DayPhase.MORNING,
        day=1,
        month=1,
        rng=tick_rng("seed", tick),
        content=make_content(),
    )


def make_state(
    *properties: Property,
    districts: dict[int, DistrictState] | None = None,
    characters: dict[int, Character] | None = None,
    apartment_leases: dict[int, ApartmentLease] | None = None,
    property_auctions: dict[int, PropertyAuction] | None = None,
) -> WorldState:
    return WorldState(
        districts=districts or {},
        npcs={},
        npc_schedules={},
        characters=characters or {},
        open_shifts=[],
        properties={p.id: p for p in properties},
        apartment_leases=apartment_leases or {},
        property_auctions=property_auctions or {},
    )


class TestRefreshPrices:
    def test_only_runs_once_per_day(self):
        house = make_property(1, tier="novice", suggested_price=0.0)
        state = make_state(house, districts={1: make_district_state()})

        housing.run(state, make_ctx(tick=1))

        assert house.suggested_price == 0.0

    def test_npc_house_price_tracks_the_tier_base(self):
        house = make_property(1, tier="novice", suggested_price=0.0)
        state = make_state(house, districts={1: make_district_state()})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert house.suggested_price == constants.HOUSE_BASE_PRICE_BY_TIER["novice"]

    def test_apartment_price_tracks_the_base_rent(self):
        unit = make_property(1, kind=PropertyKind.APARTMENT.value, suggested_price=0.0)
        state = make_state(unit, districts={1: make_district_state()})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert unit.suggested_price == constants.APARTMENT_UNIT_BASE_RENT

    def test_inn_price_tracks_the_base_nightly_price(self):
        inn = make_property(1, kind=PropertyKind.INN.value, suggested_price=0.0)
        state = make_state(inn, districts={1: make_district_state()})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert inn.suggested_price == constants.INN_BASE_NIGHTLY_PRICE

    def test_high_unrest_lowers_the_suggested_price(self):
        house = make_property(1, tier="novice", suggested_price=0.0)
        state = make_state(house, districts={1: make_district_state(unrest=1.0)})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert house.suggested_price < constants.HOUSE_BASE_PRICE_BY_TIER["novice"]

    def test_high_capitol_favor_raises_the_suggested_price(self):
        house = make_property(1, tier="novice", suggested_price=0.0)
        state = make_state(house, districts={1: make_district_state(capitol_favor=10.0)})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert house.suggested_price > constants.HOUSE_BASE_PRICE_BY_TIER["novice"]

    def test_player_owned_property_is_never_refreshed(self):
        house = make_property(
            1, tier="novice", owner_kind=OwnerKind.CHARACTER.value, suggested_price=12345.0
        )
        state = make_state(house, districts={1: make_district_state()})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert house.suggested_price == 12345.0

    def test_missing_district_state_falls_back_to_base_price(self):
        house = make_property(1, district_id=99, tier="novice", suggested_price=0.0)
        state = make_state(house, districts={})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert house.suggested_price == constants.HOUSE_BASE_PRICE_BY_TIER["novice"]


class TestCollectPayments:
    def test_deducts_an_affordable_installment_and_advances_the_due_date(self):
        owner = make_character(1, money=1_000)
        house = make_property(
            1,
            owner_kind=OwnerKind.CHARACTER.value,
            owner_id=1,
            mortgage_principal=500.0,
            mortgage_payment=50.0,
            mortgage_next_due_tick=constants.TICKS_PER_DAY,
        )
        state = make_state(house, characters={1: owner})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert owner.money == 950
        assert house.mortgage_principal == pytest.approx(450.0)
        assert house.mortgage_missed_payments == 0
        assert house.mortgage_next_due_tick == constants.TICKS_PER_DAY * 2

    def test_paying_off_the_principal_clears_the_mortgage(self):
        owner = make_character(1, money=1_000)
        house = make_property(
            1,
            owner_kind=OwnerKind.CHARACTER.value,
            owner_id=1,
            mortgage_principal=50.0,
            mortgage_payment=50.0,
            mortgage_next_due_tick=constants.TICKS_PER_DAY,
        )
        state = make_state(house, characters={1: owner})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert house.mortgage_principal == 0.0
        assert house.mortgage_payment == 0.0
        assert house.mortgage_next_due_tick is None

    def test_inn_maintenance_just_recurs_rather_than_paying_down_a_principal(self):
        owner = make_character(1, money=1_000)
        inn = make_property(
            1,
            kind=PropertyKind.INN.value,
            owner_kind=OwnerKind.CHARACTER.value,
            owner_id=1,
            mortgage_principal=0.0,
            mortgage_payment=constants.INN_DAILY_MAINTENANCE_COST,
            mortgage_next_due_tick=constants.TICKS_PER_DAY,
        )
        state = make_state(inn, characters={1: owner})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert owner.money == 1_000 - round(constants.INN_DAILY_MAINTENANCE_COST)
        assert inn.mortgage_next_due_tick == constants.TICKS_PER_DAY * 2

    def test_an_unaffordable_payment_counts_a_miss(self):
        owner = make_character(1, money=0)
        house = make_property(
            1,
            owner_kind=OwnerKind.CHARACTER.value,
            owner_id=1,
            mortgage_principal=500.0,
            mortgage_payment=50.0,
            mortgage_next_due_tick=constants.TICKS_PER_DAY,
        )
        state = make_state(house, characters={1: owner})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert owner.money == 0
        assert house.mortgage_principal == 500.0
        assert house.mortgage_missed_payments == 1

    def test_forecloses_and_lists_an_auction_past_the_miss_limit(self):
        owner = make_character(1, money=0)
        house = make_property(
            1,
            owner_kind=OwnerKind.CHARACTER.value,
            owner_id=1,
            mortgage_principal=500.0,
            mortgage_payment=50.0,
            mortgage_next_due_tick=constants.TICKS_PER_DAY,
            mortgage_missed_payments=constants.MORTGAGE_MISSES_TO_FORECLOSE - 1,
        )
        owner.housing_property_id = 1
        state = make_state(house, characters={1: owner})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert house.owner_kind == OwnerKind.NPC.value
        assert house.owner_id is None
        assert house.for_sale is True
        assert house.mortgage_principal == 0.0
        assert owner.housing_property_id is None
        assert len(state.new_property_auctions) == 1
        auction = state.new_property_auctions[0]
        assert auction.property_id == 1
        assert auction.seller_kind == "bank"
        assert auction.minimum_bid == pytest.approx(500.0)

    def test_a_property_with_no_payment_due_is_left_alone(self):
        owner = make_character(1, money=1_000)
        house = make_property(
            1,
            owner_kind=OwnerKind.CHARACTER.value,
            owner_id=1,
            mortgage_principal=500.0,
            mortgage_payment=50.0,
            mortgage_next_due_tick=None,
        )
        state = make_state(house, characters={1: owner})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert owner.money == 1_000
        assert house.mortgage_principal == 500.0


class TestCollectRent:
    def test_deducts_rent_and_credits_a_player_landlord(self):
        tenant = make_character(1, money=1_000)
        landlord = make_character(2, money=0)
        unit = make_property(
            1,
            kind=PropertyKind.APARTMENT.value,
            owner_kind=OwnerKind.CHARACTER.value,
            owner_id=2,
        )
        lease = make_lease(
            1,
            property_id=1,
            tenant_character_id=1,
            rent_price=40.0,
            next_rent_due_tick=constants.TICKS_PER_DAY,
        )
        state = make_state(
            unit,
            characters={1: tenant, 2: landlord},
            apartment_leases={1: lease},
        )

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert tenant.money == 960
        assert landlord.money == 40
        assert lease.missed_payments == 0
        assert lease.next_rent_due_tick == constants.TICKS_PER_DAY * 2

    def test_npc_landlord_takes_no_credit(self):
        tenant = make_character(1, money=1_000)
        unit = make_property(1, kind=PropertyKind.APARTMENT.value, owner_kind=OwnerKind.NPC.value)
        lease = make_lease(
            1, property_id=1, tenant_character_id=1, next_rent_due_tick=constants.TICKS_PER_DAY
        )
        state = make_state(unit, characters={1: tenant}, apartment_leases={1: lease})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert tenant.money == 960

    def test_an_unaffordable_rent_counts_a_miss(self):
        tenant = make_character(1, money=0)
        unit = make_property(1, kind=PropertyKind.APARTMENT.value, owner_kind=OwnerKind.NPC.value)
        lease = make_lease(
            1, property_id=1, tenant_character_id=1, next_rent_due_tick=constants.TICKS_PER_DAY
        )
        state = make_state(unit, characters={1: tenant}, apartment_leases={1: lease})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert lease.missed_payments == 1
        assert 1 in state.apartment_leases

    def test_evicts_past_the_miss_limit(self):
        tenant = make_character(1, money=0)
        tenant.housing_property_id = 1
        unit = make_property(1, kind=PropertyKind.APARTMENT.value, owner_kind=OwnerKind.NPC.value)
        lease = make_lease(
            1,
            property_id=1,
            tenant_character_id=1,
            next_rent_due_tick=constants.TICKS_PER_DAY,
            missed_payments=constants.RENT_MISSES_TO_EVICT - 1,
        )
        state = make_state(unit, characters={1: tenant}, apartment_leases={1: lease})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert 1 not in state.apartment_leases
        assert state.deleted_apartment_lease_ids == [1]
        assert tenant.housing_property_id is None

    def test_a_lease_with_no_rent_due_is_left_alone(self):
        tenant = make_character(1, money=1_000)
        unit = make_property(1, kind=PropertyKind.APARTMENT.value, owner_kind=OwnerKind.NPC.value)
        lease = make_lease(
            1, property_id=1, tenant_character_id=1, next_rent_due_tick=constants.TICKS_PER_DAY + 1
        )
        state = make_state(unit, characters={1: tenant}, apartment_leases={1: lease})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert tenant.money == 1_000
        assert lease.next_rent_due_tick == constants.TICKS_PER_DAY + 1


class TestResolveAuctions:
    def test_transfers_ownership_to_an_affording_bidder(self):
        bidder = make_character(1, money=1_000)
        house = make_property(1, owner_kind=OwnerKind.NPC.value, for_sale=True)
        auction = make_auction(
            1,
            property_id=1,
            current_bid=300.0,
            current_bidder_id=1,
            ends_at_tick=constants.TICKS_PER_DAY,
        )
        state = make_state(house, characters={1: bidder}, property_auctions={1: auction})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert bidder.money == 700
        assert house.owner_kind == OwnerKind.CHARACTER.value
        assert house.owner_id == 1
        assert house.for_sale is False
        assert auction.status == "closed"

    def test_credits_a_character_seller_not_a_bank_auction(self):
        bidder = make_character(1, money=1_000)
        seller = make_character(2, money=0)
        house = make_property(1, owner_kind=OwnerKind.CHARACTER.value, owner_id=2, for_sale=True)
        auction = make_auction(
            1,
            property_id=1,
            seller_kind=OwnerKind.CHARACTER.value,
            seller_id=2,
            current_bid=300.0,
            current_bidder_id=1,
            ends_at_tick=constants.TICKS_PER_DAY,
        )
        state = make_state(house, characters={1: bidder, 2: seller}, property_auctions={1: auction})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert seller.money == 300
        assert house.owner_id == 1

    def test_relists_at_the_minimum_bid_when_there_was_no_bid(self):
        house = make_property(1, owner_kind=OwnerKind.NPC.value, for_sale=False)
        auction = make_auction(
            1, property_id=1, minimum_bid=250.0, ends_at_tick=constants.TICKS_PER_DAY
        )
        state = make_state(house, property_auctions={1: auction})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert house.owner_kind == OwnerKind.NPC.value
        assert house.for_sale is True
        assert house.asking_price == pytest.approx(250.0)
        assert auction.status == "closed"

    def test_relists_when_the_winning_bidder_can_no_longer_afford_it(self):
        bidder = make_character(1, money=0)
        house = make_property(1, owner_kind=OwnerKind.NPC.value, for_sale=False)
        auction = make_auction(
            1,
            property_id=1,
            minimum_bid=250.0,
            current_bid=300.0,
            current_bidder_id=1,
            ends_at_tick=constants.TICKS_PER_DAY,
        )
        state = make_state(house, characters={1: bidder}, property_auctions={1: auction})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert bidder.money == 0
        assert house.owner_kind == OwnerKind.NPC.value
        assert house.for_sale is True
        assert auction.status == "closed"

    def test_an_auction_not_yet_ended_is_left_open(self):
        house = make_property(1, owner_kind=OwnerKind.NPC.value, for_sale=False)
        auction = make_auction(
            1, property_id=1, minimum_bid=250.0, ends_at_tick=constants.TICKS_PER_DAY + 1
        )
        state = make_state(house, property_auctions={1: auction})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert auction.status == "open"

    def test_an_already_closed_auction_is_ignored(self):
        house = make_property(1, owner_kind=OwnerKind.NPC.value, for_sale=False, asking_price=None)
        auction = make_auction(
            1,
            property_id=1,
            minimum_bid=250.0,
            ends_at_tick=constants.TICKS_PER_DAY,
            status="closed",
        )
        state = make_state(house, property_auctions={1: auction})

        housing.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert house.owner_kind == OwnerKind.NPC.value
        assert house.asking_price is None
