from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    Job,
    JobOption,
    Location,
)
from panem_shared.db.models import Npc, NpcSchedule, WorldClock
from panem_shared.db.models import WorldEvent as WorldEventRow
from panem_shared.db.session import session_scope
from panem_shared.enums import DayPhase
from panem_sim import tick as tick_module
from panem_sim.systems import (
    FIXED_ORDER,
    crisis,
    economy,
    games,
    memory,
    schedule,
    social,
    time,
)


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


def make_district() -> District:
    locations = [
        Location(id="home", name="Home", kind="residential"),
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
    ]
    coords = {"home": (0, 0), "square": (100, 100), "station": (200, 200)}
    return District(
        id=1,
        name="District 1",
        industry="luxury",
        produces=[],
        imports=[],
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=500, height=500, location_coords=coords),
    )


def make_job() -> Job:
    return Job(
        id="job",
        district=1,
        title="Job",
        workplace="square",
        wage=10.0,
        shift_phase="morning",
        slots=5,
        options=[JobOption(label="a"), JobOption(label="b"), JobOption(label="c")],
    )


def make_content() -> ContentBundle:
    district = make_district()
    job = make_job()
    return ContentBundle(districts={district.id: district}, goods={}, jobs={job.id: job}, routes=[])


class TestFixedOrder:
    def test_matches_spec_order(self):
        assert [fn.__module__.rsplit(".", 1)[-1] for fn in FIXED_ORDER] == [
            "time",
            "schedule",
            "needs",
            "jobs",
            "economy",
            "social",
            "memory",
            "crisis",
            "games",
        ]

    def test_every_remaining_stub_returns_no_events(self):
        for module in (economy, social, memory, crisis, games):
            assert module.run(None, None) == []  # type: ignore[arg-type]


class TestTimeAdvance:
    @pytest.mark.parametrize(
        ("previous_tick", "expected_tick", "expected_phase", "expected_day", "expected_month"),
        [
            (0, 1, DayPhase.NIGHT, 1, 1),
            (4, 5, DayPhase.NIGHT, 1, 1),
            (5, 6, DayPhase.MORNING, 1, 1),
            (11, 12, DayPhase.AFTERNOON, 1, 1),
            (17, 18, DayPhase.EVENING, 1, 1),
            (23, 24, DayPhase.NIGHT, 2, 1),
            (23 + 24 * 29, 24 + 24 * 29, DayPhase.NIGHT, 1, 2),
        ],
    )
    def test_advance(
        self, previous_tick, expected_tick, expected_phase, expected_day, expected_month
    ):
        tick, phase, day, month = time.advance(previous_tick)
        assert (tick, phase, day, month) == (
            expected_tick,
            expected_phase,
            expected_day,
            expected_month,
        )


@pytest.fixture
async def seeded_world(db_session_factory: async_sessionmaker[AsyncSession]) -> ContentBundle:
    content = make_content()
    district = content.district(1)
    async with session_scope(db_session_factory) as session:
        session.add(WorldClock(id=1, tick=5))
        for i in range(3):
            npc_id = f"npc{i}"
            session.add(
                Npc(
                    id=npc_id,
                    district_id=district.id,
                    name=npc_id,
                    age=30,
                    job_id="job",
                    location_id="home",
                    home_location_id="home",
                )
            )
            session.add(
                NpcSchedule(
                    npc_id=npc_id, phase=DayPhase.MORNING.value, location_id="square", weight=1.0
                )
            )
    return content


class TestRunTick:
    async def test_persists_events_advances_clock_and_publishes(
        self, db_session_factory: async_sessionmaker[AsyncSession], seeded_world: ContentBundle
    ):
        redis_client = FakeRedis()

        events = await tick_module.run_tick(
            db_session_factory, redis_client, seeded_world, "test-seed"
        )

        assert len(events) == 1
        assert events[0].kind == "NarrationLine"

        async with session_scope(db_session_factory) as session:
            clock = await session.get(WorldClock, 1)
            assert clock.tick == 6

            rows = (await session.execute(select(WorldEventRow))).scalars().all()
            assert len(rows) == 1
            assert rows[0].announced is True
            assert rows[0].tick == 6

        assert len(redis_client.published) == 1
        channel, _payload = redis_client.published[0]
        assert channel == "world:events"

    async def test_failing_system_retries_once_then_raises_and_alerts(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        seeded_world: ContentBundle,
        monkeypatch: pytest.MonkeyPatch,
    ):
        redis_client = FakeRedis()
        call_count = 0

        def boom(state, ctx):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("system exploded")

        monkeypatch.setattr(schedule, "run", boom)
        monkeypatch.setattr(tick_module, "FIXED_ORDER", [time.run, schedule.run])

        with pytest.raises(RuntimeError, match="system exploded"):
            await tick_module.run_tick(db_session_factory, redis_client, seeded_world, "test-seed")

        assert call_count == 2

        async with session_scope(db_session_factory) as session:
            clock = await session.get(WorldClock, 1)
            assert clock.tick == 5

        assert any(
            channel == tick_module.SIM_ALERTS_CHANNEL for channel, _ in redis_client.published
        )

    async def test_recover_pending_events_republishes_and_marks_announced(
        self, db_session_factory: async_sessionmaker[AsyncSession]
    ):
        redis_client = FakeRedis()
        event_id = str(uuid.uuid4())

        async with session_scope(db_session_factory) as session:
            session.add(
                WorldEventRow(
                    id=uuid.UUID(event_id),
                    tick=1,
                    kind="Bulletin",
                    district_id=1,
                    payload={
                        "id": event_id,
                        "tick": 1,
                        "schema_version": 1,
                        "kind": "Bulletin",
                        "district_id": 1,
                        "text": "recovered",
                    },
                    announced=False,
                )
            )

        count = await tick_module.recover_pending_events(db_session_factory, redis_client)

        assert count == 1
        assert len(redis_client.published) == 1
        channel, payload = redis_client.published[0]
        assert channel == "world:events"
        assert "recovered" in payload

        async with session_scope(db_session_factory) as session:
            row = await session.get(WorldEventRow, uuid.UUID(event_id))
            assert row.announced is True

    async def test_recover_pending_events_is_noop_when_nothing_pending(
        self, db_session_factory: async_sessionmaker[AsyncSession]
    ):
        redis_client = FakeRedis()
        count = await tick_module.recover_pending_events(db_session_factory, redis_client)
        assert count == 0
        assert redis_client.published == []
