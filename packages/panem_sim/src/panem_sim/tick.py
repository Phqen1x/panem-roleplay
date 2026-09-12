"""The tick loop (Plan §4.1, FR-TCK-2/3/5).

One tick: load `WorldState` from the persisted `WorldClock`, run
`FIXED_ORDER` against it, persist every returned event as an
(unannounced) `WorldEvent` row, and commit -- all inside one DB
transaction (FR-TCK-3). Only once that commit succeeds are events
published to Redis and their rows marked `announced=True`; a crash
between commit and that follow-up write leaves rows `announced=False`
for `recover_pending_events` to re-publish on the next startup (NFR-7),
rather than losing them or replaying the tick itself.

A tick that raises is retried once (a fresh session, same persisted
tick); a second failure pauses the loop and raises an alert on
`SIM_ALERTS_CHANNEL` instead of silently wedging or skipping the tick.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from panem_shared.content.loader import ContentBundle
from panem_shared.db.models import Character, DistrictState, Npc, NpcSchedule, Shift, WorldClock
from panem_shared.db.models import WorldEvent as WorldEventRow
from panem_shared.db.session import session_scope
from panem_shared.events import AnyWorldEvent, parse_event, publish
from panem_shared.logging import get_logger
from panem_sim.rng import tick_rng
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import FIXED_ORDER
from panem_sim.systems.time import advance

SIM_ALERTS_CHANNEL = "sim:alerts"

logger = get_logger()


async def _load_state(session: AsyncSession) -> tuple[WorldState, WorldClock]:
    clock = await session.get(WorldClock, 1)
    if clock is None:
        clock = WorldClock(id=1, tick=0)
        session.add(clock)
        await session.flush()

    districts = {
        row.district_id: row for row in (await session.execute(select(DistrictState))).scalars()
    }
    npcs = {row.id: row for row in (await session.execute(select(Npc))).scalars()}
    schedules: dict[str, list[NpcSchedule]] = {}
    for row in (await session.execute(select(NpcSchedule))).scalars():
        schedules.setdefault(row.npc_id, []).append(row)
    characters = {row.id: row for row in (await session.execute(select(Character))).scalars()}
    open_shifts = list(
        (await session.execute(select(Shift).where(Shift.result.is_(None)))).scalars()
    )

    state = WorldState(
        districts=districts,
        npcs=npcs,
        npc_schedules=schedules,
        characters=characters,
        open_shifts=open_shifts,
    )
    return state, clock


def _event_row(event: AnyWorldEvent) -> WorldEventRow:
    district_id = getattr(event, "district_id", None)
    return WorldEventRow(
        id=uuid.UUID(event.id),
        tick=event.tick,
        kind=event.kind,
        district_id=district_id,
        payload=event.model_dump(mode="json"),
        announced=False,
    )


async def _run_tick_once(
    session_factory: async_sessionmaker[AsyncSession],
    content: ContentBundle,
    world_seed: str,
) -> list[AnyWorldEvent]:
    async with session_scope(session_factory) as session:
        state, clock = await _load_state(session)
        tick, phase, day, month = advance(clock.tick)
        ctx = TickContext(
            tick=tick,
            phase=phase,
            day=day,
            month=month,
            rng=tick_rng(world_seed, tick),
            content=content,
        )

        events: list[AnyWorldEvent] = []
        for system in FIXED_ORDER:
            events.extend(system(state, ctx))

        clock.tick = tick
        clock.updated_at = dt.datetime.now(dt.UTC)
        for event in events:
            session.add(_event_row(event))
        for shift in state.new_shifts:
            session.add(shift)
        for history_row in state.new_job_history:
            session.add(history_row)

    return events


async def _publish_and_mark_announced(
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: redis.Redis,
    events: list[AnyWorldEvent],
) -> None:
    if not events:
        return
    for event in events:
        await publish(redis_client, event)
    ids = [uuid.UUID(event.id) for event in events]
    async with session_scope(session_factory) as session:
        rows = (
            await session.execute(select(WorldEventRow).where(WorldEventRow.id.in_(ids)))
        ).scalars()
        for row in rows:
            row.announced = True


async def run_tick(
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: redis.Redis,
    content: ContentBundle,
    world_seed: str,
) -> list[AnyWorldEvent]:
    """Run exactly one tick, retrying once on failure (FR-TCK-3). Raises
    (after alerting) if both attempts fail -- callers must treat that as
    fatal and stop the loop rather than skip ahead to the next tick."""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            events = await _run_tick_once(session_factory, content, world_seed)
        except Exception as exc:
            last_error = exc
            logger.error("tick_failed", attempt=attempt, error=str(exc))
            continue
        await _publish_and_mark_announced(session_factory, redis_client, events)
        return events

    assert last_error is not None
    await redis_client.publish(
        SIM_ALERTS_CHANNEL, f"Tick failed twice in a row, pausing: {last_error}"
    )
    raise last_error


async def recover_pending_events(
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: redis.Redis,
) -> int:
    """Re-publish any `world_events` rows a previous run committed but
    never confirmed sent (`announced=False`), then mark them announced.
    Call once at startup, before the loop begins ticking (NFR-7)."""
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(WorldEventRow)
                    .where(~WorldEventRow.announced)
                    .order_by(WorldEventRow.tick)
                )
            )
            .scalars()
            .all()
        )
        pending = [parse_event(row.kind, row.payload) for row in rows]

    await _publish_and_mark_announced(session_factory, redis_client, pending)
    if pending:
        logger.warning("recovered_pending_events", count=len(pending))
    return len(pending)


async def run_forever(
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: redis.Redis,
    content: ContentBundle,
    world_seed: str,
    interval_seconds: int,
) -> None:
    await recover_pending_events(session_factory, redis_client)
    while True:
        start = asyncio.get_event_loop().time()
        events = await run_tick(session_factory, redis_client, content, world_seed)
        logger.info("tick_done", event_count=len(events))
        elapsed = asyncio.get_event_loop().time() - start
        await asyncio.sleep(max(0.0, interval_seconds - elapsed))
