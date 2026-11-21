"""`/weather forecast` -- and the background poll that generates each
district's daily forecast, flips predicted events live, applies exposure
health damage, and posts every forecast/warning/active/ended event to
the Bulletin board (`panem_bot.cogs.bulletin`). `/housing prepare`/
`/housing repair` (property prep and damage repair) live in
`panem_bot.cogs.housing` instead, next to the rest of the property
toolkit.

The poll walks every tick between the last tick it saw and the current
one (not just "do a pass right now") so a bot restart that misses a few
ticks still ends up generating exactly the forecasts/events/exposure
damage that should have happened, in order, rather than skipping
straight to the end state."""

from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands, tasks
from sqlalchemy import select

from panem_bot import autocomplete, feature_gate, utils
from panem_bot.strings import t
from panem_shared import bulletin as bulletin_svc
from panem_shared import constants, simtime
from panem_shared import rumors as rumors_svc
from panem_shared import weather as weather_svc
from panem_shared.db.models import Character, WeatherEvent, WeatherForecast, WorldClock
from panem_shared.enums import BulletinSourceKind, WeatherEventStatus

_RUMOR_FLAVOR_BY_KIND = {
    "thunderstorm": "Talk of a storm brewing on the horizon.",
    "hailstorm": "Word that hail's been seen further out, moving this way.",
    "blizzard": "Whispers about a cold snap settling in soon.",
    "flash_flood": "Someone's heard the waterways are about to run high.",
    "heatwave": "A warning that the heat's about to get a lot worse.",
    "windstorm": "Chatter about the wind picking up something fierce.",
}

_POLL_SECONDS = 120


def _kind_label(kind: str) -> str:
    return kind.replace("_", " ").title()


class WeatherCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_tick: int | None = None

    async def cog_load(self) -> None:
        self.poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()

    group = app_commands.Group(name="weather", description="Check the forecast")

    async def _current_tick(self, session) -> int:  # type: ignore[no-untyped-def]
        clock = await session.get(WorldClock, 1)
        return clock.tick if clock is not None else 0

    @group.command(name="forecast", description="See your character's district forecast")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def forecast(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(
                session, interaction.user.id, character
            )
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            district_id = char.current_district_id
            current_tick = await self._current_tick(session)
            today = simtime.day_index(current_tick)
            today_forecast = await session.get(WeatherForecast, (district_id, today))
            # Literal `+ 1` ("tomorrow"), not `+ constants.WEATHER_
            # FORECAST_LEAD_DAYS` -- that constant now governs how far
            # ahead the rolling forecast *generation*/display window
            # reaches (10 days, for the dashboard's 10-day view), not
            # what counts as "tomorrow" in this one-day-ahead embed.
            lead_forecast = await session.get(WeatherForecast, (district_id, today + 1))
            active_event = await weather_svc.active_event_for_district(session, district_id)
            upcoming = (
                await session.execute(
                    select(WeatherEvent)
                    .where(
                        WeatherEvent.district_id == district_id,
                        WeatherEvent.status == WeatherEventStatus.FORECAST.value,
                    )
                    .order_by(WeatherEvent.actual_start_tick)
                    .limit(1)
                )
            ).scalar_one_or_none()

            if today_forecast is None and lead_forecast is None:
                await interaction.response.send_message(t("weather_forecast_none"), ephemeral=True)
                return

            embed = discord.Embed(title=f"District {district_id} Forecast")
            if today_forecast is not None:
                embed.add_field(
                    name="Today",
                    value=f"{_kind_label(today_forecast.condition)}, "
                    f"{today_forecast.temp_low}°F-{today_forecast.temp_high}°F",
                    inline=False,
                )
            if lead_forecast is not None:
                embed.add_field(
                    name="Tomorrow",
                    value=f"{_kind_label(lead_forecast.condition)}, "
                    f"{lead_forecast.temp_low}°F-{lead_forecast.temp_high}°F",
                    inline=False,
                )
            if active_event is not None:
                embed.add_field(
                    name="Happening now",
                    value=f"**{_kind_label(active_event.kind)}** (category {active_event.severity}) "
                    f"-- expected to pass around tick {active_event.actual_end_tick}. Take cover.",
                    inline=False,
                )
            if upcoming is not None:
                embed.add_field(
                    name="Predicted",
                    value=f"**{_kind_label(upcoming.kind)}** (category {upcoming.severity}) expected "
                    f"around tick {upcoming.predicted_start_tick}.",
                    inline=False,
                )
        await interaction.response.send_message(embed=embed)

    # --------------------------------------------------------------- poll

    @tasks.loop(seconds=_POLL_SECONDS)
    async def poll(self) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            clock = await session.get(WorldClock, 1)
            tick = clock.tick if clock is not None else 0
            if self._last_tick is not None and tick <= self._last_tick:
                return
            start_tick = tick if self._last_tick is None else self._last_tick + 1
            content = self.bot.content  # type: ignore[attr-defined]
            rng = random.Random()

            # Beta-gating (`/staff feature set tab:weather ...`): a
            # character whose owner currently lacks Weather tab access
            # takes none of this poll's exposure/property damage --
            # `feature_gate.disallowed_character_ids` is a no-op query
            # returning an empty set in the overwhelmingly common
            # ENABLED case, so this costs nothing once the tab's out of
            # beta. Computed once per poll call (not per tick/event
            # below), since the flag can't change mid-call.
            all_character_ids = (
                await session.execute(select(Character.id))
            ).scalars().all()
            weather_excluded = await feature_gate.disallowed_character_ids(
                self.bot, session, "weather", all_character_ids
            )

            for step_tick in range(start_tick, tick + 1):
                for district_id, climate in content.climate.items():
                    # `ensure_forecast` is a no-op once a given target day
                    # already has a row, so calling it every tick is what
                    # keeps a rolling `WEATHER_FORECAST_LEAD_DAYS`-day-ahead
                    # window of forecasts (and any event that window's
                    # worth of lead time decides) filled at all times --
                    # the bulletin post below is a separate concern (once
                    # per *day*, announcing *today's* forecast, not every
                    # time a new day gets generated).
                    _forecast, event = await weather_svc.ensure_forecast(
                        session,
                        district_id=district_id,
                        climate=climate,
                        current_tick=step_tick,
                        rng=rng,
                    )
                    if event is not None and event.status == WeatherEventStatus.FORECAST.value:
                        await bulletin_svc.record_system_entry(
                            session,
                            source_kind=BulletinSourceKind.WEATHER_EVENT,
                            source_id=event.id,
                            tag="Weather warning",
                            title=f"District {district_id}: {_kind_label(event.kind)} expected",
                            body=(
                                f"Category {event.severity}, predicted to start around tick "
                                f"{event.predicted_start_tick} and last about "
                                f"{event.predicted_duration_ticks}h."
                            ),
                            district_id=district_id,
                            posted_tick=step_tick,
                            event_tick=event.predicted_start_tick,
                            danger_level=event.severity,
                        )
                        # A freshly-decided severe event is real, concrete
                        # advance notice (Spec: "prior warning to collect
                        # materials") -- tying a rumor to it with `related_
                        # kind`/`related_id` is what lets investigating it
                        # actually resolve to something true, instead of
                        # every rumor being free-floating flavor with
                        # nothing behind it (this module's own docstring
                        # already names this exact spawn site as the
                        # intended source). Not every event gets one --
                        # same `RUMOR_SPAWN_CHANCE` roll the ordinary
                        # free-floating spawn loop uses, so a rumored storm
                        # stays the exception, not the rule.
                        if rng.random() < constants.RUMOR_SPAWN_CHANCE:
                            await rumors_svc.spawn_rumor(
                                session,
                                district_id=district_id,
                                current_tick=step_tick,
                                rng=rng,
                                text=_RUMOR_FLAVOR_BY_KIND.get(
                                    event.kind, f"Talk of {_kind_label(event.kind).lower()} coming."
                                ),
                                origin="overheard",
                                related_kind="weather_event",
                                related_id=event.id,
                                expires_tick=event.predicted_start_tick,
                            )

                # The bulletin should only ever show *today's* forecast,
                # swapped for the new day's the instant one begins --
                # never accumulating one entry per day forever (the bug
                # the old forecast-generation-triggered post above had:
                # it fired once per day forever with nothing retracting
                # the previous day's entry). Gated on the exact first tick
                # of a sim day rather than every tick so this fires
                # exactly once per day per district, not once per poll.
                if step_tick % constants.TICKS_PER_DAY == 0:
                    today_index = simtime.day_index(step_tick)
                    for district_id in content.climate:
                        today_forecast = await session.get(
                            WeatherForecast, (district_id, today_index)
                        )
                        if today_forecast is None:
                            # Cold-start gap: a freshly-deployed world only
                            # has forecasts from day `WEATHER_FORECAST_
                            # LEAD_DAYS` onward until the rolling window
                            # above has had that many days to fill in --
                            # nothing to post yet for this district/day.
                            continue
                        await bulletin_svc.retract_system_entries_for(
                            session,
                            source_kind=BulletinSourceKind.WEATHER_FORECAST,
                            district_id=district_id,
                        )
                        await bulletin_svc.record_system_entry(
                            session,
                            source_kind=BulletinSourceKind.WEATHER_FORECAST,
                            source_id=None,
                            tag="Weather",
                            title=(
                                f"District {district_id} forecast: "
                                f"{_kind_label(today_forecast.condition)}"
                            ),
                            body=(
                                f"High {today_forecast.temp_high}°F, "
                                f"low {today_forecast.temp_low}°F."
                            ),
                            district_id=district_id,
                            posted_tick=step_tick,
                            event_tick=today_index * constants.TICKS_PER_DAY,
                        )

                newly_active, newly_ended = await weather_svc.activate_and_end_events(
                    session,
                    current_tick=step_tick,
                    rng=rng,
                    excluded_character_ids=weather_excluded,
                )
                for event in newly_active:
                    await bulletin_svc.record_system_entry(
                        session,
                        source_kind=BulletinSourceKind.WEATHER_EVENT,
                        source_id=event.id,
                        tag="Weather alert",
                        title=f"District {event.district_id}: {_kind_label(event.kind)} underway",
                        body=f"Category {event.severity}. Take cover if you're outside.",
                        district_id=event.district_id,
                        posted_tick=step_tick,
                        event_tick=step_tick,
                        danger_level=event.severity,
                    )
                for event in newly_ended:
                    await bulletin_svc.record_system_entry(
                        session,
                        source_kind=BulletinSourceKind.WEATHER_EVENT,
                        source_id=event.id,
                        tag="Weather update",
                        title=f"District {event.district_id}: {_kind_label(event.kind)} has passed",
                        district_id=event.district_id,
                        posted_tick=step_tick,
                        event_tick=step_tick,
                    )

                active_now = (
                    (
                        await session.execute(
                            select(WeatherEvent).where(
                                WeatherEvent.status == WeatherEventStatus.ACTIVE.value
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for event in active_now:
                    district = content.district(event.district_id)
                    exposed_candidates = (
                        (
                            await session.execute(
                                select(Character).where(
                                    Character.current_district_id == event.district_id
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    weather_svc.apply_exposure_damage(
                        list(exposed_candidates),
                        district=district,
                        event=event,
                        excluded_character_ids=weather_excluded,
                    )

            self._last_tick = tick

    @poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WeatherCog(bot))
