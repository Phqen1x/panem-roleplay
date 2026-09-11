"""World bootstrap: load content, seed DB rows a fresh world doesn't have
yet (Plan §4.1).

Seeding is idempotent and per-district: a district already holding
`district_state`/`npcs` rows is left untouched, so re-running this against
a world that's already been ticking is always safe.

No real NPC content exists yet (`data/npcs/*.yaml` -- names, traits,
speech, relationships -- is Phase 3 content, per the Plan's own §11
authoring table and `scripts/npc_generate.py`'s docstring). Phase 1/2's
movement, needs, jobs, and shopkeeper mechanics only need *some* NPC
bodies to exist, so this seeds a minimal synthetic population directly
into `npcs`/`npc_schedule` -- no traits/speech/dialogue -- that Phase 3
enriches in place later. The `Npc` table already has every column Phase 3
needs; nothing here blocks it.
"""

from __future__ import annotations

import random
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.content.loader import ContentBundle, load_content
from panem_shared.content.schemas import District, Location
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
        for n in range(1, constants.SYNTHETIC_NPCS_PER_DISTRICT + 1):
            npc_id = f"d{district.id}_npc_{n:03d}"
            home = _home_location(district, rng)
            age = rng.randint(18, 65)
            npc = Npc(
                id=npc_id,
                district_id=district.id,
                name=f"Resident {n}",
                age=age,
                home_location_id=home.id,
                location_id=home.id,
            )
            session.add(npc)

            schedule = _generic_schedule(district, home.id)
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
