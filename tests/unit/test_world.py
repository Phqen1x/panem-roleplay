from __future__ import annotations

from collections import defaultdict

import pytest
from sqlalchemy import select

from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    DistrictQuota,
    Job,
    JobOption,
    Location,
)
from panem_shared.db.models import DistrictState, Npc, NpcSchedule
from panem_shared.enums import DayPhase
from panem_sim import world


def make_district(
    id_: int,
    *,
    with_market: bool = True,
    with_residential: bool = True,
    quota: DistrictQuota | None = None,
) -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
    ]
    if with_market:
        locations.append(Location(id="market", name="The Market", kind="market"))
    if with_residential:
        locations.append(Location(id="home", name="Home", kind="residential"))
    coords = {loc.id: (0, 0) for loc in locations}
    return District(
        id=id_,
        name=f"District {id_}",
        industry="luxury",
        produces=[],
        imports=[],
        quota=quota,
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=100, height=100, location_coords=coords),
    )


def make_content(*districts: District, jobs: tuple[Job, ...] = ()) -> ContentBundle:
    return ContentBundle(
        districts={d.id: d for d in districts},
        goods={},
        jobs={j.id: j for j in jobs},
        routes=[],
    )


def make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = dict(
        id="job",
        district=1,
        title="Job",
        workplace="market",
        wage=10.0,
        shift_phase="morning",
        slots=5,
        options=[JobOption(label="a"), JobOption(label="b"), JobOption(label="c")],
    )
    defaults.update(overrides)
    return Job(**defaults)  # type: ignore[arg-type]


class TestSeedDistrictState:
    async def test_creates_one_row_per_district_with_quota_target(self, db_session):
        content = make_content(
            make_district(1, quota=DistrictQuota(good="coal", amount=250)),
            make_district(2, quota=None),
        )

        await world.seed_district_state(db_session, content)
        await db_session.flush()

        rows = {
            row.district_id: row.quota_target
            for row in (await db_session.execute(select(DistrictState))).scalars()
        }
        assert rows == {1: 250.0, 2: 0.0}

    async def test_is_idempotent_per_district(self, db_session):
        content = make_content(make_district(1))

        await world.seed_district_state(db_session, content)
        await db_session.flush()
        await world.seed_district_state(db_session, content)
        await db_session.flush()

        rows = (await db_session.execute(select(DistrictState))).scalars().all()
        assert len(rows) == 1


class TestSeedNpcs:
    async def test_creates_configured_npc_count_per_district(self, db_session):
        content = make_content(make_district(1), make_district(2))

        await world.seed_npcs(db_session, content, "test-seed")
        await db_session.flush()

        for district_id in (1, 2):
            count = len(
                (await db_session.execute(select(Npc).where(Npc.district_id == district_id)))
                .scalars()
                .all()
            )

            assert count == constants.SYNTHETIC_NPCS_PER_DISTRICT

    async def test_is_idempotent_per_district(self, db_session):
        content = make_content(make_district(1))

        await world.seed_npcs(db_session, content, "test-seed")
        await db_session.flush()
        await world.seed_npcs(db_session, content, "test-seed")
        await db_session.flush()

        rows = (await db_session.execute(select(Npc))).scalars().all()

        assert len(rows) == constants.SYNTHETIC_NPCS_PER_DISTRICT

    async def test_every_npc_phase_schedule_sums_to_one(self, db_session):
        content = make_content(make_district(1))

        await world.seed_npcs(db_session, content, "test-seed")
        await db_session.flush()

        rows = (await db_session.execute(select(NpcSchedule))).scalars().all()
        assert rows, "expected schedule rows to have been created"

        sums: dict[tuple[str, str], float] = defaultdict(float)
        for row in rows:
            sums[(row.npc_id, row.phase)] += row.weight

        for (npc_id, phase), total in sums.items():
            assert total == pytest.approx(1.0, abs=1e-6), f"{npc_id}/{phase} summed to {total}"

    async def test_night_phase_is_entirely_at_home(self, db_session):
        content = make_content(make_district(1))

        await world.seed_npcs(db_session, content, "test-seed")
        await db_session.flush()

        npcs = {row.id: row for row in (await db_session.execute(select(Npc))).scalars()}
        night_rows = (
            (
                await db_session.execute(
                    select(NpcSchedule).where(NpcSchedule.phase == DayPhase.NIGHT.value)
                )
            )
            .scalars()
            .all()
        )

        for row in night_rows:
            assert row.weight == pytest.approx(1.0)
            assert row.location_id == npcs[row.npc_id].home_location_id

    async def test_market_share_folds_into_public_when_no_market(self, db_session):
        content = make_content(make_district(1, with_market=False))

        await world.seed_npcs(db_session, content, "test-seed")
        await db_session.flush()

        rows = (
            (
                await db_session.execute(
                    select(NpcSchedule).where(NpcSchedule.phase == DayPhase.MORNING.value)
                )
            )
            .scalars()
            .all()
        )
        assert all(row.location_id != "market" for row in rows)

    async def test_home_location_falls_back_to_public_without_residential(self, db_session):
        content = make_content(make_district(1, with_residential=False))

        await world.seed_npcs(db_session, content, "test-seed")
        await db_session.flush()

        npcs = (await db_session.execute(select(Npc))).scalars().all()
        assert npcs
        for npc in npcs:
            assert npc.home_location_id == "square"

    async def test_npcs_get_unique_real_names(self, db_session):
        content = make_content(make_district(1))

        await world.seed_npcs(db_session, content, "test-seed")
        await db_session.flush()

        npcs = (await db_session.execute(select(Npc))).scalars().all()
        names = [npc.name for npc in npcs]
        assert all(not name.startswith("Resident") for name in names)
        assert len(set(names)) == len(names)

    async def test_npcs_are_assigned_a_district_job_when_one_exists(self, db_session):
        job = make_job(id="only_job", district=1, workplace="market", shift_phase="morning")
        content = make_content(make_district(1), jobs=(job,))

        await world.seed_npcs(db_session, content, "test-seed")
        await db_session.flush()

        npcs = (await db_session.execute(select(Npc))).scalars().all()
        assert all(npc.job_id == "only_job" for npc in npcs)

    async def test_npcs_have_no_job_when_district_has_none(self, db_session):
        content = make_content(make_district(1))

        await world.seed_npcs(db_session, content, "test-seed")
        await db_session.flush()

        npcs = (await db_session.execute(select(Npc))).scalars().all()
        assert all(npc.job_id is None for npc in npcs)

    async def test_employed_npc_schedule_favors_workplace_during_shift_phase(self, db_session):
        job = make_job(id="only_job", district=1, workplace="market", shift_phase="morning")
        content = make_content(make_district(1), jobs=(job,))

        await world.seed_npcs(db_session, content, "test-seed")
        await db_session.flush()

        morning_rows = (
            (
                await db_session.execute(
                    select(NpcSchedule).where(NpcSchedule.phase == DayPhase.MORNING.value)
                )
            )
            .scalars()
            .all()
        )
        by_npc: dict[str, dict[str, float]] = defaultdict(dict)
        for row in morning_rows:
            by_npc[row.npc_id][row.location_id] = row.weight

        for weights in by_npc.values():
            assert weights.get("market") == pytest.approx(0.85)


class TestSeedWorld:
    async def test_seeds_both_district_state_and_npcs(self, db_session):
        content = make_content(make_district(1), make_district(2))

        await world.seed_world(db_session, content, "test-seed")
        await db_session.flush()

        district_state_count = len(
            (await db_session.execute(select(DistrictState))).scalars().all()
        )
        npc_count = await world.total_npc_count(db_session)

        assert district_state_count == 2
        assert npc_count == 2 * constants.SYNTHETIC_NPCS_PER_DISTRICT
