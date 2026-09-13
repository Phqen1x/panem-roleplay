from __future__ import annotations

from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    DistrictQuota,
    Good,
    Job,
    JobOption,
    Location,
    Route,
)
from panem_shared.db.models import Character, DistrictState, MarketPrice, Npc, Shift
from panem_shared.enums import DayPhase
from panem_sim.rng import tick_rng
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import economy

DAY_TICK = constants.TICKS_PER_DAY  # 24, the first day-boundary tick > 0


def make_district(
    id_: int,
    *,
    produces: list[str] | None = None,
    imports: list[str] | None = None,
    quota: DistrictQuota | None = None,
    population_base: int = 1000,
    market_kind_at: str | None = "market",
) -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
    ]
    if market_kind_at:
        locations.append(Location(id=market_kind_at, name="Market", kind="market"))
    coords = {loc.id: (0, 0) for loc in locations}
    return District(
        id=id_,
        name=f"District {id_}",
        industry="x",
        produces=produces or [],
        imports=imports or [],
        quota=quota,
        population_base=population_base,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=100, height=100, location_coords=coords),
    )


def make_good(id_: str, *, base_price: float = 10.0) -> Good:
    return Good(id=id_, name=id_, base_price=base_price, category="misc")


def make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = dict(
        id="job",
        district=1,
        title="Job",
        workplace="square",
        wage=10.0,
        produces={},
        shift_phase="morning",
        slots=5,
        options=[JobOption(label="a"), JobOption(label="b"), JobOption(label="c")],
    )
    defaults.update(overrides)
    return Job(**defaults)  # type: ignore[arg-type]


def make_content(
    districts: list[District], goods: list[Good], jobs: list[Job] = (), routes: list[Route] = ()
) -> ContentBundle:
    return ContentBundle(
        districts={d.id: d for d in districts},
        goods={g.id: g for g in goods},
        jobs={j.id: j for j in jobs},
        routes=list(routes),
    )


def make_district_row(district_id: int, **overrides: object) -> DistrictState:
    defaults: dict[str, object] = dict(
        district_id=district_id,
        treasury=0.0,
        quota_progress=0.0,
        quota_target=0.0,
        capitol_favor=0.0,
        unrest=0.0,
        peacekeeper_pressure=0.3,
    )
    defaults.update(overrides)
    return DistrictState(**defaults)  # type: ignore[arg-type]


def make_npc(id_: str, **overrides: object) -> Npc:
    defaults: dict[str, object] = dict(
        id=id_, district_id=1, name=id_, age=30, money=0.0, float_target=0.0
    )
    defaults.update(overrides)
    return Npc(**defaults)  # type: ignore[arg-type]


def make_ctx(content: ContentBundle, *, tick: int = DAY_TICK, day: int = 2) -> TickContext:
    return TickContext(
        tick=tick,
        phase=DayPhase.NIGHT,
        day=day,
        month=1,
        rng=tick_rng("test-seed", tick),
        content=content,
    )


def make_state(
    *, districts=None, npcs=None, characters=None, completed_shifts=None, market_prices=None
) -> WorldState:
    return WorldState(
        districts=districts or {},
        npcs=npcs or {},
        npc_schedules={},
        characters=characters or {},
        open_shifts=[],
        completed_shifts=completed_shifts or [],
        market_prices=market_prices or {},
    )


class TestGating:
    def test_no_op_off_the_day_boundary(self):
        district = make_district(1, produces=["coal"])
        good = make_good("coal")
        content = make_content([district], [good])
        district_row = make_district_row(1, treasury=100.0)
        state = make_state(districts={1: district_row})

        events = economy.run(state, make_ctx(content, tick=DAY_TICK - 1))

        assert events == []
        assert district_row.treasury == 100.0
        assert state.new_market_prices == []


class TestPricing:
    def test_scarcity_raises_price_toward_base(self):
        district = make_district(1, produces=["coal"], population_base=100_000)
        good = make_good("coal", base_price=10.0)
        content = make_content([district], [good])
        state = make_state(districts={1: make_district_row(1)})

        economy.run(state, make_ctx(content))

        row = state.market_prices[(1, "coal")]
        assert row.price > 10.0

    def test_glut_lowers_price(self):
        district = make_district(1, produces=["coal"], population_base=10)
        good = make_good("coal", base_price=10.0)
        job = make_job(id="miner", district=1, produces={"coal": 10_000.0})
        content = make_content([district], [good], [job])
        npc = make_npc("n1", district_id=1, job_id="miner")
        state = make_state(districts={1: make_district_row(1)}, npcs={"n1": npc})

        economy.run(state, make_ctx(content))

        row = state.market_prices[(1, "coal")]
        assert row.price < 10.0

    def test_price_stays_within_clamp_bounds(self):
        district = make_district(1, produces=["coal"], population_base=10_000_000)
        good = make_good("coal", base_price=10.0)
        content = make_content([district], [good])
        state = make_state(districts={1: make_district_row(1)})

        # Run several days in a row so the EMA has time to approach its ceiling.
        ctx = make_ctx(content)
        for _ in range(50):
            economy.run(state, ctx)

        row = state.market_prices[(1, "coal")]
        assert row.price <= 10.0 * constants.PRICE_CLAMP_MAX + 1e-6

    def test_creates_a_new_price_row_when_none_existed(self):
        district = make_district(1, produces=["coal"])
        good = make_good("coal")
        content = make_content([district], [good])
        state = make_state(districts={1: make_district_row(1)})

        economy.run(state, make_ctx(content))

        assert len(state.new_market_prices) == 1
        assert state.new_market_prices[0].good_id == "coal"

    def test_existing_price_row_is_reused_not_duplicated(self):
        district = make_district(1, produces=["coal"])
        good = make_good("coal")
        content = make_content([district], [good])
        existing = MarketPrice(district_id=1, good_id="coal", price=10.0, tick=0)
        state = make_state(
            districts={1: make_district_row(1)}, market_prices={(1, "coal"): existing}
        )

        economy.run(state, make_ctx(content))

        assert state.new_market_prices == []
        assert state.market_prices[(1, "coal")] is existing


class TestPlayerSupply:
    def test_completed_shift_output_feeds_supply_and_lowers_price(self):
        district = make_district(1, produces=["coal"], population_base=10)
        good = make_good("coal", base_price=10.0)
        content = make_content([district], [good])
        character = Character(
            id=1,
            user_id=1,
            district_id=1,
            current_district_id=1,
            name="Wren",
            age=20,
            status="approved",
            job_title="Miner",
            shift_phase="morning",
        )
        shift = Shift(
            character_id=1,
            job_id="Miner",
            tick_opened=1,
            tick_due=6,
            completed_at=5,
            result="completed",
            output={"coal": 5000.0},
        )
        state = make_state(
            districts={1: make_district_row(1)},
            characters={1: character},
            completed_shifts=[shift],
        )

        economy.run(state, make_ctx(content))

        row = state.market_prices[(1, "coal")]
        assert row.supply >= 5000.0


class TestActivePlayerDemand:
    def test_falls_back_to_population_based_demand_with_no_active_players(self):
        district = make_district(1, produces=["coal"], population_base=100)
        good = make_good("coal", base_price=10.0)
        content = make_content([district], [good])
        state = make_state(districts={1: make_district_row(1)})

        economy.run(state, make_ctx(content))

        row = state.market_prices[(1, "coal")]
        assert row.demand == 100 * constants.MARKET_DEMAND_PER_CAPITA

    def test_active_player_demand_overrides_population_base(self):
        district = make_district(1, produces=["coal"], population_base=1_000_000)
        good = make_good("coal", base_price=10.0)
        content = make_content([district], [good])
        character = Character(
            id=1,
            user_id=1,
            district_id=1,
            current_district_id=1,
            name="Wren",
            age=20,
            status="approved",
            last_active_tick=DAY_TICK,
        )
        state = make_state(districts={1: make_district_row(1)}, characters={1: character})

        economy.run(state, make_ctx(content, tick=DAY_TICK))

        row = state.market_prices[(1, "coal")]
        assert row.demand == constants.ACTIVE_PLAYER_DEMAND_PER_CAPITA

    def test_stale_activity_does_not_count_as_active(self):
        district = make_district(1, produces=["coal"], population_base=100)
        good = make_good("coal", base_price=10.0)
        content = make_content([district], [good])
        stale_tick = (
            DAY_TICK - (constants.ACTIVE_PLAYER_WINDOW_SIM_DAYS * constants.TICKS_PER_DAY) - 1
        )
        character = Character(
            id=1,
            user_id=1,
            district_id=1,
            current_district_id=1,
            name="Wren",
            age=20,
            status="approved",
            last_active_tick=stale_tick,
        )
        state = make_state(districts={1: make_district_row(1)}, characters={1: character})

        economy.run(state, make_ctx(content, tick=DAY_TICK))

        row = state.market_prices[(1, "coal")]
        assert row.demand == 100 * constants.MARKET_DEMAND_PER_CAPITA

    def test_imported_good_gets_a_higher_demand_weight_than_produced(self):
        district = make_district(1, produces=["coal"], imports=["grain"], population_base=1)
        coal = make_good("coal", base_price=10.0)
        grain = make_good("grain", base_price=10.0)
        content = make_content([district], [coal, grain])
        character = Character(
            id=1,
            user_id=1,
            district_id=1,
            current_district_id=1,
            name="Wren",
            age=20,
            status="approved",
            last_active_tick=DAY_TICK,
        )
        state = make_state(districts={1: make_district_row(1)}, characters={1: character})

        economy.run(state, make_ctx(content, tick=DAY_TICK))

        coal_row = state.market_prices[(1, "coal")]
        grain_row = state.market_prices[(1, "grain")]
        assert grain_row.demand > coal_row.demand


class TestExports:
    def test_export_pays_treasury_and_reduces_local_supply(self):
        district = make_district(1, produces=["coal"], population_base=10)
        capitol = make_district(0, produces=[], population_base=10, market_kind_at=None)
        good = make_good("coal", base_price=10.0)
        job = make_job(id="miner", district=1, produces={"coal": 1000.0})
        route = Route(**{"from": 1, "to": 0, "good": "coal", "capacity": 100.0})
        content = make_content([district, capitol], [good], [job], [route])
        npc = make_npc("n1", district_id=1, job_id="miner")
        district_row = make_district_row(1, treasury=0.0)
        state = make_state(districts={1: district_row}, npcs={"n1": npc})

        economy.run(state, make_ctx(content))

        # NPC supply is an *expected* value (job.produces * NPC_JOB_COMPLETION_PROB
        # = 1000 * 0.85 = 850), which comfortably exceeds the 100-capacity route,
        # so capacity is the binding constraint here.
        assert district_row.treasury == 100.0 * 10.0  # capacity * base price (no prior row)
        row = state.market_prices[(1, "coal")]
        assert row.supply == 850.0 - 100.0

    def test_export_capped_by_available_supply_not_just_capacity(self):
        district = make_district(1, produces=["coal"], population_base=10)
        good = make_good("coal", base_price=10.0)
        job = make_job(id="miner", district=1, produces={"coal": 50.0})
        route = Route(**{"from": 1, "to": 0, "good": "coal", "capacity": 1000.0})
        content = make_content([district], [good], [job], [route])
        npc = make_npc("n1", district_id=1, job_id="miner")
        district_row = make_district_row(1, treasury=0.0)
        state = make_state(districts={1: district_row}, npcs={"n1": npc})

        economy.run(state, make_ctx(content))

        # Expected supply (50 * NPC_JOB_COMPLETION_PROB = 42.5) is well below
        # the route's 1000 capacity, so supply is the binding constraint.
        expected_supply = 50.0 * constants.NPC_JOB_COMPLETION_PROB
        assert district_row.treasury == expected_supply * 10.0
        row = state.market_prices[(1, "coal")]
        assert row.supply == constants.MARKET_SUPPLY_FLOOR

    def test_multiple_routes_share_one_remaining_supply_pool(self):
        district = make_district(1, produces=["coal"], population_base=10)
        good = make_good("coal", base_price=10.0)
        job = make_job(id="miner", district=1, produces={"coal": 100.0})
        route_a = Route(**{"from": 1, "to": 0, "good": "coal", "capacity": 80.0})
        route_b = Route(**{"from": 1, "to": 2, "good": "coal", "capacity": 80.0})
        content = make_content([district], [good], [job], [route_a, route_b])
        npc = make_npc("n1", district_id=1, job_id="miner")
        district_row = make_district_row(1, treasury=0.0)
        state = make_state(districts={1: district_row}, npcs={"n1": npc})

        economy.run(state, make_ctx(content))

        # Expected supply is 100 * NPC_JOB_COMPLETION_PROB = 85. Route A (listed
        # first) takes 80 of it, leaving only 5 for route B.
        assert district_row.treasury == (80.0 + 5.0) * 10.0


class TestQuotas:
    def test_quota_progress_accumulates_from_capitol_bound_exports_only(self):
        quota = DistrictQuota(good="coal", amount=1000.0)
        district = make_district(1, produces=["coal"], quota=quota, population_base=10)
        good = make_good("coal", base_price=10.0)
        job = make_job(id="miner", district=1, produces={"coal": 500.0})
        route_to_capitol = Route(**{"from": 1, "to": 0, "good": "coal", "capacity": 50.0})
        route_elsewhere = Route(**{"from": 1, "to": 2, "good": "coal", "capacity": 50.0})
        content = make_content([district], [good], [job], [route_to_capitol, route_elsewhere])
        npc = make_npc("n1", district_id=1, job_id="miner")
        district_row = make_district_row(1)
        state = make_state(districts={1: district_row}, npcs={"n1": npc})

        economy.run(state, make_ctx(content, day=15))  # not a month boundary

        assert district_row.quota_progress == 50.0

    def test_quota_met_raises_favor_and_resets_progress(self):
        quota = DistrictQuota(good="coal", amount=10.0)
        district = make_district(1, produces=["coal"], quota=quota, population_base=10)
        good = make_good("coal", base_price=10.0)
        content = make_content([district], [good])
        district_row = make_district_row(
            1, quota_progress=50.0, quota_target=10.0, capitol_favor=0.0
        )
        state = make_state(districts={1: district_row})

        economy.run(state, make_ctx(content, day=1))

        assert district_row.capitol_favor == constants.QUOTA_MET_FAVOR_DELTA
        assert district_row.quota_progress == 0.0

    def test_quota_missed_lowers_favor_and_resets_progress(self):
        quota = DistrictQuota(good="coal", amount=1000.0)
        district = make_district(1, produces=["coal"], quota=quota, population_base=10)
        good = make_good("coal", base_price=10.0)
        content = make_content([district], [good])
        district_row = make_district_row(
            1, quota_progress=5.0, quota_target=1000.0, capitol_favor=0.0
        )
        state = make_state(districts={1: district_row})

        economy.run(state, make_ctx(content, day=1))

        assert district_row.capitol_favor == -constants.QUOTA_MISSED_FAVOR_DELTA
        assert district_row.quota_progress == 0.0

    def test_district_with_no_quota_is_left_alone(self):
        district = make_district(1, produces=["coal"], quota=None, population_base=10)
        good = make_good("coal", base_price=10.0)
        content = make_content([district], [good])
        district_row = make_district_row(1, capitol_favor=5.0)
        state = make_state(districts={1: district_row})

        economy.run(state, make_ctx(content, day=1))

        assert district_row.capitol_favor == 5.0


class TestShopkeeperRestock:
    def test_shopkeeper_npc_is_topped_up_from_treasury(self):
        district = make_district(1, produces=[], population_base=10)
        job = make_job(id="hob_trader", district=1, workplace="market")
        content = make_content([district], [], [job])
        npc = make_npc("shop1", district_id=1, job_id="hob_trader", money=5.0, float_target=50.0)
        district_row = make_district_row(1, treasury=1000.0)
        state = make_state(districts={1: district_row}, npcs={"shop1": npc})

        economy.run(state, make_ctx(content))

        assert npc.money == 50.0
        assert district_row.treasury == 1000.0 - 45.0

    def test_topup_capped_by_available_treasury(self):
        district = make_district(1, produces=[], population_base=10)
        job = make_job(id="hob_trader", district=1, workplace="market")
        content = make_content([district], [], [job])
        npc = make_npc("shop1", district_id=1, job_id="hob_trader", money=0.0, float_target=100.0)
        district_row = make_district_row(1, treasury=10.0)
        state = make_state(districts={1: district_row}, npcs={"shop1": npc})

        economy.run(state, make_ctx(content))

        assert npc.money == 10.0
        assert district_row.treasury == 0.0

    def test_non_shopkeeper_npc_is_not_topped_up(self):
        district = make_district(1, produces=[], population_base=10)
        job = make_job(id="miner", district=1, workplace="square")
        content = make_content([district], [], [job])
        npc = make_npc("n1", district_id=1, job_id="miner", money=0.0, float_target=100.0)
        district_row = make_district_row(1, treasury=1000.0)
        state = make_state(districts={1: district_row}, npcs={"n1": npc})

        economy.run(state, make_ctx(content))

        assert npc.money == 0.0
        assert district_row.treasury == 1000.0


class TestBulletin:
    def test_one_bulletin_per_district_with_traded_goods(self):
        district = make_district(1, produces=["coal"], population_base=10)
        good = make_good("coal")
        content = make_content([district], [good])
        state = make_state(districts={1: make_district_row(1)})

        events = economy.run(state, make_ctx(content))

        assert len(events) == 1
        assert events[0].kind == "Bulletin"
        assert events[0].district_id == 1

    def test_no_bulletin_for_a_district_with_nothing_traded(self):
        district = make_district(1, produces=[], imports=[], population_base=10)
        content = make_content([district], [])
        state = make_state(districts={1: make_district_row(1)})

        events = economy.run(state, make_ctx(content))

        assert events == []
