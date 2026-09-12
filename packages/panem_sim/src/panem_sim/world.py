"""World bootstrap: load content, seed DB rows a fresh world doesn't have
yet (Plan §4.1).

Seeding is idempotent and per-district: a district already holding
`district_state`/`npcs` rows is left untouched, so re-running this against
a world that's already been ticking is always safe.

No real NPC content exists yet (`data/npcs/*.yaml` -- traits, speech,
relationships, backstory -- is Phase 3 content, per the Plan's own §11
authoring table and `scripts/npc_generate.py`'s docstring). Phase 1/2's
movement, needs, jobs, and shopkeeper mechanics need NPC bodies with a
name and (mostly) a job -- both cheap to synthesize from a name pool and
each district's own job list -- so residents read naturally in narration
and `/resident`, and the job-shift systems have something to act on. This
still stops well short of Phase 3: no traits/speech/dialogue/personality,
just identity. The `Npc` table already has every column Phase 3 needs;
nothing here blocks it.
"""

from __future__ import annotations

import random
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.content.loader import ContentBundle, load_content
from panem_shared.content.names import sample_names
from panem_shared.content.schemas import District, Job, Location
from panem_shared.db.models import DistrictState, Npc, NpcSchedule
from panem_shared.enums import DayPhase, LocationKind
from panem_sim.rng import seed_rng


def load_world(data_dir: Path) -> ContentBundle:
    """Thin, sim-specific entry point over the shared content loader --
    kept separate from `panem_shared.content.loader.load_content` so sim-only
    content (e.g. Phase 3's `data/npcs/*.yaml`) has an obvious place to be
    added later without touching what the bot also loads."""
    return load_content(data_dir)


async def seed_district_state(session: AsyncSession, content: ContentBundle) -> None:
    existing_ids = set((await session.execute(select(DistrictState.district_id))).scalars().all())
    for district in content.districts.values():
        if district.id in existing_ids:
            continue
        session.add(
            DistrictState(
                district_id=district.id,
                quota_target=district.quota.amount if district.quota else 0.0,
            )
        )


def _generic_schedule(district: District, home_id: str) -> dict[DayPhase, dict[str, float]]:
    """A placeholder schedule shape for synthetic NPCs with no authored
    personality: home at night, spread across public/market locations
    (weighted toward public) the rest of the day. District schema
    validation already guarantees at least one `public` location; `market`
    may not exist, in which case its share folds into `public`. Weights
    are accumulated into a dict keyed by location id rather than assigned
    positionally, so this stays correct even if `home_id` happens to
    coincide with one of the public/market locations (e.g. a district
    with no dedicated residential location)."""
    public_locs = [loc.id for loc in district.locations if loc.kind == LocationKind.PUBLIC]
    market_locs = [loc.id for loc in district.locations if loc.kind == LocationKind.MARKET]

    def _daytime_weights(home_share: float) -> dict[str, float]:
        market_share = 0.3 if market_locs else 0.0
        public_share = 1.0 - home_share - market_share
        weights: dict[str, float] = {home_id: home_share}
        for loc_id in public_locs:
            weights[loc_id] = weights.get(loc_id, 0.0) + public_share / len(public_locs)
        for loc_id in market_locs:
            weights[loc_id] = weights.get(loc_id, 0.0) + market_share / len(market_locs)
        return weights

    return {
        DayPhase.NIGHT: {home_id: 1.0},
        DayPhase.MORNING: _daytime_weights(home_share=0.2),
        DayPhase.AFTERNOON: _daytime_weights(home_share=0.2),
        DayPhase.EVENING: _daytime_weights(home_share=0.4),
    }


def _schedule_for_npc(
    district: District, home_id: str, job: Job | None
) -> dict[DayPhase, dict[str, float]]:
    """`_generic_schedule`, with an employed NPC's job phase overridden to
    pull heavily toward its workplace instead of the generic public/market
    spread -- otherwise an NPC with a job would only ever wander there by
    the same chance as anyone else. The small remaining share stays home,
    standing in for a day off/no-show rather than 100% attendance."""
    schedule = _generic_schedule(district, home_id)
    if job is not None:
        schedule[job.shift_phase] = {job.workplace: 0.85, home_id: 0.15}
    return schedule


def _assign_job(rng: random.Random, district_jobs: list[Job]) -> str | None:
    """Weighted by `slots` so higher-capacity jobs employ proportionally
    more residents; `None` if the district has no jobs defined at all
    (e.g. content still being authored)."""
    if not district_jobs:
        return None
    return rng.choices(
        [job.id for job in district_jobs], weights=[job.slots for job in district_jobs], k=1
    )[0]


def _home_location(district: District, rng: random.Random) -> Location:
    residential = [loc for loc in district.locations if loc.kind == LocationKind.RESIDENTIAL]
    pool = residential or [loc for loc in district.locations if loc.kind == LocationKind.PUBLIC]
    return rng.choice(pool)


async def seed_npcs(session: AsyncSession, content: ContentBundle, world_seed: str) -> None:
    existing_districts = set(
        (await session.execute(select(Npc.district_id).distinct())).scalars().all()
    )
    for district in content.districts.values():
        if district.id in existing_districts:
            continue
        rng = seed_rng(world_seed, f"npcs:{district.id}")
        district_jobs = [job for job in content.jobs.values() if job.district == district.id]
        names = sample_names(rng, constants.SYNTHETIC_NPCS_PER_DISTRICT)
        for n in range(1, constants.SYNTHETIC_NPCS_PER_DISTRICT + 1):
            npc_id = f"d{district.id}_npc_{n:03d}"
            home = _home_location(district, rng)
            age = rng.randint(18, 65)
            job_id = _assign_job(rng, district_jobs)
            npc = Npc(
                id=npc_id,
                district_id=district.id,
                name=names[n - 1],
                age=age,
                job_id=job_id,
                home_location_id=home.id,
                location_id=home.id,
            )
            session.add(npc)

            job = next((j for j in district_jobs if j.id == job_id), None)
            schedule = _schedule_for_npc(district, home.id, job)
            for phase, weights in schedule.items():
                for loc_id, weight in weights.items():
                    session.add(
                        NpcSchedule(
                            npc_id=npc_id, phase=phase.value, location_id=loc_id, weight=weight
                        )
                    )


async def seed_world(session: AsyncSession, content: ContentBundle, world_seed: str) -> None:
    await seed_district_state(session, content)
    await seed_npcs(session, content, world_seed)


async def total_npc_count(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count()).select_from(Npc))).scalar_one())
