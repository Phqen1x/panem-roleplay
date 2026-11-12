"""Seasons & weather (Spec "Seasons & Weather Events"). A district's
`data/climate.yaml` entry gives each season's temperature range and
which severity-rated (`WeatherKind`, category 1-6) events that season
can ever roll; rain/snow carry no severity and are generated for every
district every day regardless.

Unlike most of this codebase's day-to-day mechanics, weather isn't
driven by `panem_sim`'s per-tick `WorldState` loop (that engine's
`_load_state` is already a fixed, heavily-tested set of tables, and
nothing here needs per-tick resolution against every NPC/character the
way needs/jobs/crisis do) -- `panem_bot.cogs.weather`'s own `tasks.loop`
polls `advance_weather` on a short real-time interval, the same
established pattern `cogs/trades.py`'s `expire_stale_trades` and
`cogs/characters.py`'s pending-approval polls already use for
world-housekeeping that just needs to happen "soon enough", not on an
exact tick boundary.

A `WeatherEvent`'s actual start/end and whether it fizzles are all
decided once, at generation time (`_decide_event`) -- `advance_weather`'s
job on every later poll is just to flip `FORECAST`->`ACTIVE`->`ENDED`
once `current_tick` reaches those already-decided tick numbers, and to
roll property damage exactly once, at the moment an event goes `ACTIVE`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants, simtime
from panem_shared.content.schemas import District, DistrictClimate
from panem_shared.db.models import Character, Inventory, Property, WeatherEvent, WeatherForecast
from panem_shared.enums import (
    LocationKind,
    OwnerKind,
    RpMode,
    Season,
    WeatherEventStatus,
    WeatherKind,
)
from panem_shared.errors import NotAllowed, NotFound, ValidationFailed

_EXPOSED_LOCATION_KINDS = frozenset({LocationKind.PUBLIC, LocationKind.OUTSKIRTS})
"""Simplification: the 2D location model has no explicit "character is
inside a building" flag outside the housing/interior feature, which
doesn't track a character's *current* position. Standing at a plainly
outdoor-flavored location (or mid-travel) counts as exposed; every other
kind (market, workplace, government, residential, station, jail) is
treated as sheltered."""


def is_exposed(character: Character, location: object) -> bool:
    if character.in_transit_until_tick is not None:
        return True
    kind = getattr(location, "kind", None)
    return kind in _EXPOSED_LOCATION_KINDS


def apply_exposure_damage(
    characters: list[Character], *, district: District, event: WeatherEvent
) -> list[Character]:
    """Simulation-mode characters standing exposed (`is_exposed`) in
    `district` while `event` is `ACTIVE` lose `constants.WEATHER_HEALTH_
    DAMAGE_PER_SEVERITY * event.severity` health -- Spec: "being outside
    or travelling somewhere during a weather event can damage you."
    Life/Story characters are exempt, the same carve-out `panem_sim.
    systems.needs` uses for its own automatic stat damage. Called once per
    tick `panem_bot.cogs.weather`'s poll advances through (never per real-
    time poll interval, so the total damage over an event's duration
    doesn't depend on how often the bot happens to poll). Returns the
    characters actually hit, for the caller to decide whether to warn
    them."""
    damage = constants.WEATHER_HEALTH_DAMAGE_PER_SEVERITY * event.severity
    hit: list[Character] = []
    for character in characters:
        if character.rp_mode != RpMode.SIMULATION.value:
            continue
        location = next(
            (loc for loc in district.locations if loc.id == character.location_id), None
        )
        if not is_exposed(character, location):
            continue
        character.health = max(constants.HEALTH_MIN, character.health - damage)
        hit.append(character)
    return hit


def _condition_for_season(season: Season, rng: random.Random) -> str:
    if rng.random() >= constants.WEATHER_RAIN_SNOW_CHANCE:
        return WeatherKind.CLEAR.value
    return WeatherKind.SNOW.value if season == Season.WINTER else WeatherKind.RAIN.value


def generate_forecast_values(
    climate: DistrictClimate, *, season: Season, rng: random.Random
) -> tuple[int, int, str]:
    season_range = climate.range_for(season)
    low = rng.randint(season_range.low, season_range.high - 1)
    high = rng.randint(low + 1, season_range.high)
    return low, high, _condition_for_season(season, rng)


@dataclass(frozen=True, slots=True)
class DecidedEvent:
    kind: str
    severity: int
    predicted_start_tick: int
    predicted_duration_ticks: int
    actual_start_tick: int | None
    actual_end_tick: int | None
    status: str


def _decide_event(
    climate: DistrictClimate, *, season: Season, lead_start_tick: int, rng: random.Random
) -> DecidedEvent | None:
    """Rolls whether an inclement event happens on the day whose window
    starts at `lead_start_tick`, and if so, exactly how it plays out --
    see the module docstring for why this is all decided up front rather
    than resolved incrementally."""
    eligible = climate.inclement.get(season, [])
    if not eligible or rng.random() >= constants.WEATHER_DAILY_INCLEMENT_CHANCE:
        return None

    kind = rng.choice(eligible)
    severities = list(constants.WEATHER_SEVERITY_WEIGHTS.keys())
    weights = list(constants.WEATHER_SEVERITY_WEIGHTS.values())
    severity = rng.choices(severities, weights=weights, k=1)[0]
    duration = rng.randint(*constants.WEATHER_DURATION_TICKS_RANGE)

    if rng.random() < constants.WEATHER_FIZZLE_CHANCE:
        return DecidedEvent(
            kind=kind,
            severity=severity,
            predicted_start_tick=lead_start_tick,
            predicted_duration_ticks=duration,
            actual_start_tick=None,
            actual_end_tick=None,
            status=WeatherEventStatus.FIZZLED.value,
        )

    drift = rng.randint(-constants.WEATHER_START_DRIFT_TICKS, constants.WEATHER_START_DRIFT_TICKS)
    actual_start = max(
        lead_start_tick + drift, lead_start_tick - constants.WEATHER_START_DRIFT_TICKS
    )
    return DecidedEvent(
        kind=kind,
        severity=severity,
        predicted_start_tick=lead_start_tick,
        predicted_duration_ticks=duration,
        actual_start_tick=actual_start,
        actual_end_tick=actual_start + duration,
        status=WeatherEventStatus.FORECAST.value,
    )


async def _last_event_day(session: AsyncSession, district_id: int) -> int | None:
    result = await session.execute(
        select(WeatherEvent.day_index)
        .where(
            WeatherEvent.district_id == district_id,
            WeatherEvent.status != WeatherEventStatus.FIZZLED.value,
        )
        .order_by(WeatherEvent.day_index.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def ensure_forecast(
    session: AsyncSession,
    *,
    district_id: int,
    climate: DistrictClimate,
    current_tick: int,
    rng: random.Random,
) -> tuple[WeatherForecast | None, WeatherEvent | None]:
    """Generates the forecast (and, maybe, an inclement event) for
    `constants.WEATHER_FORECAST_LEAD_DAYS` days ahead of `current_tick`,
    if it doesn't already exist -- a no-op every other poll. Returns
    `(forecast_row_or_None, event_row_or_None)`, both `None` when today's
    lead day was already generated on an earlier poll."""
    target_day = simtime.day_index(current_tick) + constants.WEATHER_FORECAST_LEAD_DAYS
    existing = await session.get(WeatherForecast, (district_id, target_day))
    if existing is not None:
        return None, None

    lead_tick = target_day * 24
    _, _, _, month = simtime.current(lead_tick)
    season = simtime.season_for_month(month)
    low, high, condition = generate_forecast_values(climate, season=season, rng=rng)
    forecast = WeatherForecast(
        district_id=district_id,
        day_index=target_day,
        season=season.value,
        temp_high=high,
        temp_low=low,
        condition=condition,
        announced_tick=current_tick,
    )
    session.add(forecast)

    event_row = None
    last_day = await _last_event_day(session, district_id)
    gap_ok = last_day is None or target_day - last_day >= constants.WEATHER_EVENT_MIN_GAP_DAYS
    if gap_ok:
        decided = _decide_event(climate, season=season, lead_start_tick=lead_tick, rng=rng)
        if decided is not None:
            event_row = WeatherEvent(
                district_id=district_id,
                day_index=target_day,
                kind=decided.kind,
                severity=decided.severity,
                predicted_start_tick=decided.predicted_start_tick,
                predicted_duration_ticks=decided.predicted_duration_ticks,
                actual_start_tick=decided.actual_start_tick,
                actual_end_tick=decided.actual_end_tick,
                status=decided.status,
                announced_tick=current_tick,
            )
            session.add(event_row)
    return forecast, event_row


def property_damage_chance(severity: int, prep_level: int) -> float:
    base = min(1.0, constants.WEATHER_PROPERTY_DAMAGE_BASE_CHANCE * severity / 3)
    reduction = min(1.0, prep_level * constants.WEATHER_PREP_DAMAGE_REDUCTION_PER_LEVEL)
    return base * (1 - reduction)


def roll_property_damage(
    property_row: Property, *, kind: str, severity: int, rng: random.Random
) -> int:
    prep_level = property_row.weather_prep.get(kind, 0)
    if rng.random() >= property_damage_chance(severity, prep_level):
        return 0
    return rng.randint(*constants.WEATHER_PROPERTY_DAMAGE_RANGE)


async def activate_and_end_events(
    session: AsyncSession, *, current_tick: int, rng: random.Random
) -> tuple[list[WeatherEvent], list[WeatherEvent]]:
    """Flips due `WeatherEvent`s between states and, on activation, rolls
    damage once against every `Property` in that district. Returns
    `(newly_active, newly_ended)` for the caller to announce."""
    newly_active: list[WeatherEvent] = []
    due = (
        (
            await session.execute(
                select(WeatherEvent).where(
                    WeatherEvent.status == WeatherEventStatus.FORECAST.value,
                    WeatherEvent.actual_start_tick.is_not(None),
                    WeatherEvent.actual_start_tick <= current_tick,
                )
            )
        )
        .scalars()
        .all()
    )
    for event in due:
        event.status = WeatherEventStatus.ACTIVE.value
        newly_active.append(event)
        properties = (
            (
                await session.execute(
                    select(Property).where(Property.district_id == event.district_id)
                )
            )
            .scalars()
            .all()
        )
        for property_row in properties:
            damage = roll_property_damage(
                property_row, kind=event.kind, severity=event.severity, rng=rng
            )
            if damage:
                property_row.damage_level = min(100, property_row.damage_level + damage)

    newly_ended: list[WeatherEvent] = []
    ending = (
        (
            await session.execute(
                select(WeatherEvent).where(
                    WeatherEvent.status == WeatherEventStatus.ACTIVE.value,
                    WeatherEvent.actual_end_tick.is_not(None),
                    WeatherEvent.actual_end_tick <= current_tick,
                )
            )
        )
        .scalars()
        .all()
    )
    for event in ending:
        event.status = WeatherEventStatus.ENDED.value
        newly_ended.append(event)
    return newly_active, newly_ended


async def active_event_for_district(session: AsyncSession, district_id: int) -> WeatherEvent | None:
    result = await session.execute(
        select(WeatherEvent).where(
            WeatherEvent.district_id == district_id,
            WeatherEvent.status == WeatherEventStatus.ACTIVE.value,
        )
    )
    return result.scalar_one_or_none()


async def prepare_property(
    session: AsyncSession, *, character: Character, property_row: Property, kind: str
) -> int:
    """Raises `NotFound` for an unrecognized `WeatherKind`, `NotAllowed`
    if already at `WEATHER_PREP_MAX_LEVEL`, `ValidationFailed` if the
    character lacks the lumber/money to pay for the next level. Returns
    the new prep level."""
    try:
        WeatherKind(kind)
    except ValueError:
        raise NotFound("weather_prep_invalid_kind") from None
    current_level = property_row.weather_prep.get(kind, 0)
    if current_level >= constants.WEATHER_PREP_MAX_LEVEL:
        raise NotAllowed("weather_prep_maxed")

    owner_id = str(character.id)
    inv = await session.get(
        Inventory, (OwnerKind.CHARACTER.value, owner_id, constants.WEATHER_PREP_MATERIAL_GOOD_ID)
    )
    if (
        inv is None
        or inv.qty < constants.WEATHER_PREP_COST_PER_LEVEL
        or character.money < constants.WEATHER_PREP_COST_PER_LEVEL
    ):
        raise ValidationFailed(
            "weather_prep_insufficient_materials",
            qty=constants.WEATHER_PREP_COST_PER_LEVEL,
            good=constants.WEATHER_PREP_MATERIAL_GOOD_ID,
            money=constants.WEATHER_PREP_COST_PER_LEVEL,
        )

    inv.qty -= constants.WEATHER_PREP_COST_PER_LEVEL
    character.money -= constants.WEATHER_PREP_COST_PER_LEVEL
    property_row.weather_prep = {**property_row.weather_prep, kind: current_level + 1}
    return current_level + 1


async def repair_property(
    session: AsyncSession, *, character: Character, property_row: Property, points: int
) -> int:
    """Raises `ValidationFailed` with nothing to repair or insufficient
    money. Returns the money actually spent."""
    points = min(points, property_row.damage_level)
    if points <= 0:
        raise ValidationFailed("weather_repair_nothing")
    cost = points * constants.WEATHER_REPAIR_COST_PER_POINT
    if character.money < cost:
        raise ValidationFailed("weather_repair_insufficient_money", points=points, cost=cost)
    character.money -= cost
    property_row.damage_level -= points
    return cost
