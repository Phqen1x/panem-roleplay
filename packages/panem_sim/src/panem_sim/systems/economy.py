"""District economy: supply/demand pricing, quotas, exports, shopkeeper
top-up, and a daily bulletin (Spec FR-ECO-1/2/5/6/8/9).

Runs once per day (the same `ctx.tick % TICKS_PER_DAY == 0` gate as
`needs.py`), after `jobs.py` has opened/resolved the day's shifts:

1. **Supply** (FR-ECO-1) comes from two places: real completed player
   shifts (`state.completed_shifts`, a trailing-24-tick window loaded by
   `tick.py` -- see its docstring) and *expected* NPC production
   (`job.produces * NPC_JOB_COMPLETION_PROB` for every NPC holding that
   job), not each NPC's actual stochastic roll from `jobs.py`. The two
   systems intentionally don't share bookkeeping: `jobs.py` still pays
   each NPC individually and stochastically for flavor, while this only
   needs an aggregate, deterministic supply figure for pricing.
2. **National redistribution** (`_redistribute`): this raw, per-district
   production isn't what a district actually gets to sell -- every
   district's production of a good is pooled nationally, the Capitol
   takes `CAPITOL_CUT_FRACTION` off the top, and what's left is handed
   back out to every district that trades that good (produces or
   imports it) as a `MARKET_BASELINE_ALLOCATION_FRACTION` equal-share
   floor plus a bonus split proportional to each trading district's
   share of *total* national production value that day
   (`_district_production_value`, summed across every good it produces,
   not just this one). A district that produces a lot of everything
   ends up with more of everything, including things it doesn't make
   itself, and one that barely produces anything gets little even of
   its own necessities -- this is what actually makes the market feel
   "per-district" rather than uniform. Everything downstream (exports,
   pricing) runs against this redistributed figure, not raw local
   production.
3. **Demand** (FR-ECO-1, job-system rework) is per active player in the
   district (one who's `/work`ed or proxied within
   `ACTIVE_PLAYER_WINDOW_SIM_DAYS`, `Character.last_active_tick`),
   weighted per good by whether the district produces it (baseline) or
   imports it (`DISTRICT_IMPORT_DEMAND_WEIGHT`, higher -- a district
   wants more of what it doesn't make itself). Falls back to the older
   flat `District.population_base * MARKET_DEMAND_PER_CAPITA` rate for a
   district with no active-player signal yet (a fresh world, or an
   all-NPC district), so its market doesn't collapse to zero. Still not a
   real per-good consumption model (nothing tracks a character/NPC
   actually eating bread or burning coal to heat a home) -- see
   `README.md`'s "Notes on the job system rework" for what a fuller
   version would need (per-district/per-good need weights beyond the
   produces/imports heuristic, daily consumption tied to inventory).
4. **Exports** (FR-ECO-6) move goods along `routes.yaml` from their
   `from_` district, capped by each route's `capacity` and by the
   district's remaining (redistributed) supply of that route's good
   (several routes can share a `(from, good)` pair -- e.g. District 12's
   coal ships to five different destinations -- so they're processed
   together against one shared remaining-supply pool, in file order).
   Exporting reduces the *local* supply used for that district's own
   price update (an exported unit isn't for sale at home) and pays the
   exporting district's treasury at that good's last-known local price.
   The Plan mentions exports being "scaled by D6/D5 ratios"; that
   formula wasn't available in this session's context, so this uses
   plain capacity/supply capping instead -- revisit against the real
   spec.
5. **Quota progress** (FR-ECO-5) is *not* total production -- it's
   specifically what a district exports to the Capitol (district 0) of
   its own quota good, matching every district's `routes.yaml` entry
   (each ships exactly its quota good to district 0). Evaluated at the
   first tick of a new month: `capitol_favor` moves up or down depending
   on whether the month's cumulative progress met `quota_target`, then
   `quota_progress` resets for the new month.
6. **Shopkeeper top-up** (FR-ECO-9): an NPC whose job's `workplace` is a
   `LocationKind.MARKET` location is treated as that market's shopkeeper.
   If their cash (`Npc.money`) is below their `float_target`, the
   district treasury tops them up (capped by what the treasury actually
   has) -- this is the cash a player's `/market sell` gets paid from.
7. **Daily bulletin** (FR-ECO-8): one `Bulletin` per district summarizing
   today's prices for its own produced/imported goods.

`MarketPrice.supply` doubles as *today's remaining purchasable stock*,
not just a pricing input -- `panem_bot.services.market.buy`/`sell` read
and mutate it directly through the day. That's safe because this module
fully overwrites it at the top of every day (`_update_prices`), so
whatever a day's trading left it at never leaks into tomorrow's figure.

8. **Illicit goods** (`District.illicit_produces`, contraband system)
   deliberately skip steps 2-3 above entirely: the Capitol doesn't know
   these exist, so there's no cut to take and no national pool to draw
   from -- a district's black market only ever stocks exactly what its
   own illicit workers (`Character.job_is_illicit`) produced *that day*,
   carried forward 1:1 by `_carry_forward_illicit`. `_update_illicit_
   prices` writes that straight into the same `MarketPrice` table
   (`panem_bot.services.blackmarket` reads/mutates it the same way the
   legal market does) at a flat `Good.base_price` -- no demand model,
   since nothing tracks who wants contraband the way `_demand_for_good`
   tracks legal necessities. Unlike legal supply, a quiet day really does
   mean zero stock (no `MARKET_SUPPLY_FLOOR`), matching the spec: "the
   only way more goods appear... is if people with illicit jobs work."
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared.content.schemas import District, Job
from panem_shared.db.models import MarketPrice
from panem_shared.enums import LocationKind
from panem_shared.events import AnyWorldEvent, Bulletin
from panem_sim.state import TickContext, WorldState

Supply = dict[int, dict[str, float]]

QUOTA_MISSED_UNREST_DELTA = 0.1
"""Placeholder unrest bump for `crisis.py` on a missed quota -- Spec §7's
real weighting wasn't available in this session's context."""


def _add_supply(supply: Supply, district_id: int, good_id: str, qty: float) -> None:
    bucket = supply.setdefault(district_id, {})
    bucket[good_id] = bucket.get(good_id, 0.0) + qty


def _player_supply(state: WorldState, ctx: TickContext) -> Supply:
    """FR-ECO-1: real production from shifts a player actually completed,
    attributed to the character's home district. Player jobs are
    free-typed now (`Character.job_title`, not a `jobs.yaml` catalog
    entry `shift.job_id` could look up), so this reads the district off
    the character instead of a job -- `panem_shared.shifts.
    resolve_shift_game` already computed `shift.output` as one unit of
    that district's own quota good per completed shift."""
    supply: Supply = {}
    for shift in state.completed_shifts:
        character = state.characters.get(shift.character_id)
        if character is None:
            continue
        for good_id, qty in (shift.output or {}).items():
            _add_supply(supply, character.district_id, good_id, qty)
    return supply


def _npc_supply(state: WorldState, ctx: TickContext) -> Supply:
    """FR-ECO-1: expected production from every NPC holding a job, using
    `NPC_JOB_COMPLETION_PROB` as an expected value rather than replaying
    `jobs.py`'s per-NPC roll (see module docstring)."""
    supply: Supply = {}
    for npc in state.npcs.values():
        if not npc.job_id:
            continue
        job = ctx.content.jobs.get(npc.job_id)
        if job is None:
            continue
        for good_id, qty in job.produces.items():
            _add_supply(supply, job.district, good_id, qty * constants.NPC_JOB_COMPLETION_PROB)
    return supply


def _combine_supply(*supplies: Supply) -> Supply:
    combined: Supply = {}
    for supply in supplies:
        for district_id, goods in supply.items():
            for good_id, qty in goods.items():
                _add_supply(combined, district_id, good_id, qty)
    return combined


def _traded_goods(district: District) -> set[str]:
    """Goods this district's market deals in -- what it makes plus what
    it brings in from elsewhere (Spec §1: `produces`/`imports`)."""
    return set(district.produces) | set(district.imports)


def _district_production_value(supply: Supply, ctx: TickContext) -> dict[int, float]:
    """Each district's total daily production, valued at every good's
    `base_price` and summed across everything that district made (not
    just one good) -- the "10,000 worth of goods produced... District One
    made 3,000 of it" ranking the redistribution below weights its bonus
    share by. Deliberately uses raw production, before any Capitol
    cut/redistribution, since this is what a district *made*, not what it
    ends up able to sell."""
    values: dict[int, float] = {}
    for district_id, goods in supply.items():
        total = 0.0
        for good_id, qty in goods.items():
            good = ctx.content.goods.get(good_id)
            if good is not None:
                total += qty * good.base_price
        values[district_id] = total
    return values


def _redistribute(
    raw_supply: Supply, district_values: dict[int, float], ctx: TickContext
) -> Supply:
    """Turns raw per-district production into what each district actually
    gets to sell: every good's national total is pooled, the Capitol
    takes `CAPITOL_CUT_FRACTION`, and what's left is split among the
    districts that trade that good (produce or import it) as an equal
    `MARKET_BASELINE_ALLOCATION_FRACTION` floor plus a bonus weighted by
    each trading district's share of *national* production value
    (`_district_production_value`) among just the districts trading this
    good -- a district that makes a lot of everything outbids a poor one
    even for goods neither of them produces. Falls back to an equal bonus
    split if nobody trading this good produced anything of value at all
    (a fresh world, or a good nobody's making yet), so the split stays
    well-defined rather than dividing by zero."""
    redistributed: Supply = {}
    all_goods = {
        good_id
        for district in ctx.content.districts.values()
        for good_id in _traded_goods(district)
    }
    for good_id in all_goods:
        traders = [d for d in ctx.content.districts.values() if good_id in _traded_goods(d)]
        if not traders:
            continue
        national_total = sum(raw_supply.get(d.id, {}).get(good_id, 0.0) for d in traders)
        pool = national_total * (1 - constants.CAPITOL_CUT_FRACTION)
        baseline_total = pool * constants.MARKET_BASELINE_ALLOCATION_FRACTION
        bonus_total = pool - baseline_total
        baseline_each = baseline_total / len(traders)

        trader_value_total = sum(district_values.get(d.id, 0.0) for d in traders)
        for district in traders:
            if trader_value_total > 0:
                bonus_share = (
                    bonus_total * district_values.get(district.id, 0.0) / trader_value_total
                )
            else:
                bonus_share = bonus_total / len(traders)
            _add_supply(redistributed, district.id, good_id, baseline_each + bonus_share)
    return redistributed


def _carry_forward_illicit(raw_supply: Supply, ctx: TickContext) -> Supply:
    """The illicit counterpart to `_redistribute` -- see module docstring
    point 8. Each district's `illicit_produces` goods carry forward
    exactly what was made locally that day, no Capitol cut, no
    cross-district sharing."""
    illicit: Supply = {}
    for district in ctx.content.districts.values():
        for good_id in district.illicit_produces:
            qty = raw_supply.get(district.id, {}).get(good_id, 0.0)
            _add_supply(illicit, district.id, good_id, qty)
    return illicit


def _update_illicit_prices(state: WorldState, ctx: TickContext, illicit_supply: Supply) -> None:
    """Writes today's contraband stock straight into `MarketPrice` at a
    flat `Good.base_price` -- no EMA, no demand model (module docstring
    point 8). A district that produced none of a good today gets `0.0`
    stock, not `MARKET_SUPPLY_FLOOR`: an empty black market is the point."""
    for district in ctx.content.districts.values():
        district_supply = illicit_supply.get(district.id, {})
        for good_id in district.illicit_produces:
            good = ctx.content.goods.get(good_id)
            if good is None:
                continue
            qty_supplied = district_supply.get(good_id, 0.0)

            row = state.market_prices.get((district.id, good_id))
            if row is None:
                row = MarketPrice(
                    district_id=district.id,
                    good_id=good_id,
                    price=good.base_price,
                    tick=ctx.tick,
                )
                state.market_prices[(district.id, good_id)] = row
                state.new_market_prices.append(row)

            row.price = good.base_price
            row.supply = qty_supplied
            row.demand = 0.0
            row.tick = ctx.tick


def _active_player_count(state: WorldState, ctx: TickContext, district_id: int) -> int:
    """Characters whose home is `district_id` and who've done something
    active (`/work`, a proxied message -- `Character.last_active_tick`)
    within `ACTIVE_PLAYER_WINDOW_SIM_DAYS`, the sim-time equivalent of a
    real week at the default tick rate."""
    cutoff = ctx.tick - constants.ACTIVE_PLAYER_WINDOW_SIM_DAYS * constants.TICKS_PER_DAY
    return sum(
        1
        for character in state.characters.values()
        if character.district_id == district_id
        and character.last_active_tick is not None
        and character.last_active_tick >= cutoff
    )


def _demand_weight(district: District, good_id: str) -> float:
    """A district demands a good it imports more than one it makes
    itself (Capitol wants luxury goods more than coal, D12 wants grain
    more than luxury goods) -- see `DISTRICT_IMPORT_DEMAND_WEIGHT`."""
    return constants.DISTRICT_IMPORT_DEMAND_WEIGHT if good_id in district.imports else 1.0


def _demand_for_good(district: District, good_id: str, *, active_players: int) -> float:
    """FR-ECO-1's demand side: per-active-player once a district has any
    active-player signal at all, falling back to the older flat
    `population_base` rate for an all-NPC/nobody-active-yet district so
    its market doesn't collapse to zero the moment the sim starts."""
    weight = _demand_weight(district, good_id)
    if active_players > 0:
        return active_players * constants.ACTIVE_PLAYER_DEMAND_PER_CAPITA * weight
    return district.population_base * constants.MARKET_DEMAND_PER_CAPITA * weight


def _current_price(state: WorldState, district_id: int, good_id: str, ctx: TickContext) -> float:
    row = state.market_prices.get((district_id, good_id))
    if row is not None:
        return row.price
    good = ctx.content.goods.get(good_id)
    return good.base_price if good is not None else 1.0


def _run_exports(state: WorldState, ctx: TickContext, supply: Supply) -> dict[int, float]:
    """Mutates `supply` in place (exported units leave the local market)
    and pays exporting districts' treasuries. Returns each district's
    total export to the Capitol of its *own* quota good, for FR-ECO-5."""
    quota_exports: dict[int, float] = {}
    for route in ctx.content.routes:
        available = supply.get(route.from_, {}).get(route.good, 0.0)
        if available <= 0:
            continue
        exported = min(route.capacity, available)
        supply[route.from_][route.good] = available - exported

        price = _current_price(state, route.from_, route.good, ctx)
        district_row = state.districts.get(route.from_)
        if district_row is not None:
            district_row.treasury += exported * price

        district_content = ctx.content.districts.get(route.from_)
        quota = district_content.quota if district_content is not None else None
        if (
            route.to == constants.CAPITOL_DISTRICT_ID
            and quota is not None
            and quota.good == route.good
        ):
            quota_exports[route.from_] = quota_exports.get(route.from_, 0.0) + exported
    return quota_exports


def _update_prices(state: WorldState, ctx: TickContext, supply: Supply) -> None:
    """FR-ECO-2: EMA toward a target price derived from the demand/supply
    ratio, clamped to `[PRICE_CLAMP_MIN, PRICE_CLAMP_MAX] * base_price`.
    A district producing less of a good than active players there demand
    (or less than other districts are buying via `_run_exports`) sees
    that ratio climb -- scarcity raises the price everywhere the good is
    traded; overproduction relative to demand pushes it back down."""
    for district in ctx.content.districts.values():
        district_supply = supply.get(district.id, {})
        active_players = _active_player_count(state, ctx, district.id)
        for good_id in _traded_goods(district):
            good = ctx.content.goods.get(good_id)
            if good is None:
                continue
            qty_supplied = max(district_supply.get(good_id, 0.0), constants.MARKET_SUPPLY_FLOOR)
            demand = _demand_for_good(district, good_id, active_players=active_players)
            ratio = demand / qty_supplied
            clamped = min(
                max(ratio**constants.PRICE_EXPONENT, constants.PRICE_CLAMP_MIN),
                constants.PRICE_CLAMP_MAX,
            )
            target_price = good.base_price * clamped

            row = state.market_prices.get((district.id, good_id))
            if row is None:
                row = MarketPrice(
                    district_id=district.id,
                    good_id=good_id,
                    price=good.base_price,
                    tick=ctx.tick,
                )
                state.market_prices[(district.id, good_id)] = row
                state.new_market_prices.append(row)

            row.price += (target_price - row.price) * constants.PRICE_EMA_ALPHA
            row.supply = qty_supplied
            row.demand = demand
            row.tick = ctx.tick


def _evaluate_quotas(state: WorldState, ctx: TickContext, quota_exports: dict[int, float]) -> None:
    """FR-ECO-5: accumulate today's quota-good exports, then (only on the
    first day of a new month) compare the month's total against
    `quota_target` and move `capitol_favor` accordingly."""
    for district in ctx.content.districts.values():
        if district.quota is None:
            continue
        district_row = state.districts.get(district.id)
        if district_row is None:
            continue
        district_row.quota_progress += quota_exports.get(district.id, 0.0)

        if ctx.day == 1:
            met = district_row.quota_progress >= district_row.quota_target
            district_row.capitol_favor += (
                constants.QUOTA_MET_FAVOR_DELTA if met else -constants.QUOTA_MISSED_FAVOR_DELTA
            )
            if not met:
                # Feeds crisis.py's unrest score the same tick -- crisis
                # runs later in FIXED_ORDER, off this same DistrictState row.
                district_row.unrest = min(1.0, district_row.unrest + QUOTA_MISSED_UNREST_DELTA)
            district_row.quota_progress = 0.0


def is_shopkeeper_job(job: Job, district: District) -> bool:
    """A job whose workplace is a `LocationKind.MARKET` location is treated
    as that market's shopkeeper role (FR-ECO-9) -- also used by
    `panem_sim.world` at seed time to give these NPCs a real
    `float_target` (otherwise 0, same as everyone else, and this system
    would never have anything to top up)."""
    location = next((loc for loc in district.locations if loc.id == job.workplace), None)
    return location is not None and location.kind == LocationKind.MARKET


def _restock_shopkeepers(state: WorldState, ctx: TickContext) -> None:
    """FR-ECO-9: top up a shopkeeper NPC's cash from their district's
    treasury, capped by what the treasury actually has."""
    for npc in state.npcs.values():
        if not npc.job_id:
            continue
        job = ctx.content.jobs.get(npc.job_id)
        if job is None:
            continue
        district = ctx.content.districts.get(job.district)
        if district is None or not is_shopkeeper_job(job, district):
            continue
        shortfall = npc.float_target - npc.money
        if shortfall <= 0:
            continue
        district_row = state.districts.get(job.district)
        if district_row is None:
            continue
        top_up = min(shortfall, district_row.treasury)
        district_row.treasury -= top_up
        npc.money += top_up


def _daily_bulletin(ctx: TickContext, district: District) -> Bulletin | None:
    goods = sorted(_traded_goods(district))
    if not goods:
        return None
    prices = ", ".join(
        f"{ctx.content.goods[g].name} {ctx.content.goods[g].base_price:.0f}"
        for g in goods
        if g in ctx.content.goods
    )
    return Bulletin(tick=ctx.tick, district_id=district.id, text=f"Today's market: {prices}.")


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    if ctx.tick % constants.TICKS_PER_DAY != 0:
        return []

    raw_supply = _combine_supply(_player_supply(state, ctx), _npc_supply(state, ctx))
    district_values = _district_production_value(raw_supply, ctx)
    supply = _redistribute(raw_supply, district_values, ctx)
    illicit_supply = _carry_forward_illicit(raw_supply, ctx)
    quota_exports = _run_exports(state, ctx, supply)
    _update_prices(state, ctx, supply)
    _update_illicit_prices(state, ctx, illicit_supply)
    _evaluate_quotas(state, ctx, quota_exports)
    _restock_shopkeepers(state, ctx)

    events: list[AnyWorldEvent] = []
    for district in ctx.content.districts.values():
        bulletin = _daily_bulletin(ctx, district)
        if bulletin is not None:
            events.append(bulletin)
    return events
