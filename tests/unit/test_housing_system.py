from __future__ import annotations

from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.db.models import DistrictState, Property
from panem_shared.enums import DayPhase, OwnerKind, PropertyKind
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
    )
    defaults.update(overrides)
    property_ = Property(**defaults)  # type: ignore[arg-type]
    property_.id = id_
    return property_


def make_district_state(**overrides: object) -> DistrictState:
    defaults: dict[str, object] = dict(district_id=1, unrest=0.0, capitol_favor=0.0)
    defaults.update(overrides)
    return DistrictState(**defaults)  # type: ignore[arg-type]


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
    *properties: Property, districts: dict[int, DistrictState] | None = None
) -> WorldState:
    return WorldState(
        districts=districts or {},
        npcs={},
        npc_schedules={},
        characters={},
        open_shifts=[],
        properties={p.id: p for p in properties},
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
