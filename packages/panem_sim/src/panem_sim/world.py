"""World bootstrap: load content, seed DB rows a fresh world doesn't have
yet (Plan §4.1).

Seeding is idempotent and per-district: a district already holding
`district_state`/`npcs` rows is left untouched, so re-running this against
a world that's already been ticking is always safe.

`data/npcs/*.yaml` (real, authored NPC content -- name, age, job,
traits, backstory; see `panem_shared.content.schemas.NpcContent` and
`scripts/npc_generate.py`) is optional per district: `seed_npcs` prefers
it when present (`_seed_authored_npcs`), and falls back to a fully
synthetic population (`_seed_synthetic_npcs`) for any district with none
-- so a fresh checkout with an empty `data/npcs/` directory still boots a
complete, playable world, and authoring content for one district at a
time never blocks the rest.
"""

from __future__ import annotations

import random
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.content.loader import ContentBundle, load_content
from panem_shared.content.names import sample_names
from panem_shared.content.schemas import District, Job, Location, NpcContent
from panem_shared.content.traits import sample_traits, speech_tone
from panem_shared.db.models import DistrictState, Npc, NpcSchedule, Property
from panem_shared.enums import DayPhase, JobLevel, LocationKind, OwnerKind, PropertyKind
from panem_sim.rng import seed_rng
from panem_sim.systems.economy import is_shopkeeper_job


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


_UNTIERED_PLACEHOLDER = JobLevel.APPRENTICE.value
"""Apartments and inns aren't tier-gated (`panem_bot.services.housing`),
but `Property.tier` is `nullable=False` -- this fills the column with a
value that's simply never read for those two kinds, rather than making
the column nullable for two kinds out of three."""


async def seed_properties(session: AsyncSession, content: ContentBundle) -> None:
    """Procedurally seeds houses/apartments/inns per district (idempotent:
    a district already holding any `Property` row is left untouched) --
    no hand-authored YAML content for individual properties, the same way
    `_seed_synthetic_npcs` stands in for real NPC content until it's
    authored, except here there's no authored alternative at all (housing
    has no per-property narrative content worth hand-writing).

    Apartment units are never `for_sale` individually -- `suggested_price`
    is their rent instead (`APARTMENT_UNIT_BASE_RENT`). Buying an entire
    complex outright (`panem_bot.services.housing`) is priced from
    `APARTMENT_UNIT_BASE_PRICE * len(units)` at transaction time rather
    than stored per-unit, since "the complex" isn't a row of its own here
    -- `complex_id` is just a shared grouping key."""
    existing_districts = set(
        (await session.execute(select(Property.district_id).distinct())).scalars().all()
    )
    for district in content.districts.values():
        if district.id in existing_districts:
            continue

        for tier, base_price in constants.HOUSE_BASE_PRICE_BY_TIER.items():
            for _ in range(constants.HOUSES_PER_TIER_PER_DISTRICT):
                session.add(
                    Property(
                        district_id=district.id,
                        kind=PropertyKind.HOUSE.value,
                        tier=tier,
                        owner_kind=OwnerKind.NPC.value,
                        for_sale=True,
                        suggested_price=base_price,
                    )
                )

        for complex_n in range(1, constants.APARTMENT_COMPLEXES_PER_DISTRICT + 1):
            complex_id = f"d{district.id}_complex_{complex_n}"
            for _ in range(constants.APARTMENT_UNITS_PER_COMPLEX):
                session.add(
                    Property(
                        district_id=district.id,
                        kind=PropertyKind.APARTMENT.value,
                        tier=_UNTIERED_PLACEHOLDER,
                        complex_id=complex_id,
                        owner_kind=OwnerKind.NPC.value,
                        for_sale=False,
                        suggested_price=constants.APARTMENT_UNIT_BASE_RENT,
                    )
                )

        for _ in range(constants.INNS_PER_DISTRICT):
            session.add(
                Property(
                    district_id=district.id,
                    kind=PropertyKind.INN.value,
                    tier=_UNTIERED_PLACEHOLDER,
                    owner_kind=OwnerKind.NPC.value,
                    for_sale=True,
                    suggested_price=constants.INN_BASE_NIGHTLY_PRICE,
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


def _add_npc_with_schedule(
    session: AsyncSession,
    district: District,
    *,
    npc_id: str,
    name: str,
    age: int,
    job_id: str | None,
    job: Job | None,
    home_location_id: str,
    traits: list[str],
) -> None:
    npc = Npc(
        id=npc_id,
        district_id=district.id,
        name=name,
        age=age,
        job_id=job_id,
        home_location_id=home_location_id,
        location_id=home_location_id,
        float_target=(
            constants.SHOPKEEPER_FLOAT_TARGET
            if job is not None and is_shopkeeper_job(job, district)
            else 0.0
        ),
        traits=traits,
        speech_style={"tone": speech_tone(traits)},
    )
    session.add(npc)

    schedule = _schedule_for_npc(district, home_location_id, job)
    for phase, weights in schedule.items():
        for loc_id, weight in weights.items():
            session.add(
                NpcSchedule(npc_id=npc_id, phase=phase.value, location_id=loc_id, weight=weight)
            )


def _seed_authored_npcs(
    session: AsyncSession, content: ContentBundle, district: District, authored: list[NpcContent]
) -> None:
    """Real, content-authored residents (`data/npcs/*.yaml`, either
    hand-edited or written by `scripts/npc_generate.py`) -- preferred
    over `_seed_synthetic_npcs` whenever a district has any."""
    for entry in authored:
        job = content.jobs.get(entry.job_id) if entry.job_id else None
        _add_npc_with_schedule(
            session,
            district,
            npc_id=entry.id,
            name=entry.name,
            age=entry.age,
            job_id=entry.job_id,
            job=job,
            home_location_id=entry.home_location_id,
            traits=entry.traits,
        )


def _seed_synthetic_npcs(
    session: AsyncSession, content: ContentBundle, district: District, world_seed: str
) -> None:
    """The fully-synthetic fallback (see this module's docstring) for any
    district with no authored `data/npcs/*.yaml` content yet."""
    rng = seed_rng(world_seed, f"npcs:{district.id}")
    district_jobs = [job for job in content.jobs.values() if job.district == district.id]
    names = sample_names(rng, constants.SYNTHETIC_NPCS_PER_DISTRICT)
    for n in range(1, constants.SYNTHETIC_NPCS_PER_DISTRICT + 1):
        home = _home_location(district, rng)
        age = rng.randint(18, 65)
        job_id = _assign_job(rng, district_jobs)
        job = next((j for j in district_jobs if j.id == job_id), None)
        traits = sample_traits(rng)
        _add_npc_with_schedule(
            session,
            district,
            npc_id=f"d{district.id}_npc_{n:03d}",
            name=names[n - 1],
            age=age,
            job_id=job_id,
            job=job,
            home_location_id=home.id,
            traits=traits,
        )


async def seed_npcs(session: AsyncSession, content: ContentBundle, world_seed: str) -> None:
    existing_districts = set(
        (await session.execute(select(Npc.district_id).distinct())).scalars().all()
    )
    for district in content.districts.values():
        if district.id in existing_districts:
            continue
        authored = content.npcs_for_district(district.id)
        if authored:
            _seed_authored_npcs(session, content, district, authored)
        else:
            _seed_synthetic_npcs(session, content, district, world_seed)


async def seed_world(session: AsyncSession, content: ContentBundle, world_seed: str) -> None:
    await seed_district_state(session, content)
    await seed_npcs(session, content, world_seed)
    await seed_properties(session, content)


async def total_npc_count(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count()).select_from(Npc))).scalar_one())
