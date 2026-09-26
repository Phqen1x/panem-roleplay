#!/usr/bin/env python3
"""Headless economy calibration run (Plan §5.6, T-2.1).

Seeds a fresh world (`panem_sim.world.seed_world`, zero player
characters -- T-2.1's "zero players") and runs it for 12 simulated
months (`TICKS_PER_DAY * 30 * 12` ticks, Spec §1.1's 30-day months),
then reports each district's final quota/price/treasury state and flags
anything outside a sane band: a district whose local price for a good
sat pinned at `PRICE_CLAMP_MIN`/`PRICE_CLAMP_MAX` (supply/demand never
found a resting point) or whose treasury went negative (exports/
restocking outspent income). T-2.1's exact target bands weren't
available in this session's context (no source spec/plan file to read
them from), so this reports the numbers and flags the structurally
suspicious ones rather than asserting specific ranges -- treat it as a
sanity check to read, not a pass/fail gate.

This is a manual operator tool, not part of `pytest`: it's slow
(thousands of ticks) and mutates whatever database it's pointed at.
It refuses to run against the bot/sim's own configured `DATABASE_URL`
(`Settings.database_url`) so a typo can't fast-forward a live game's
world clock by a year -- point it at a scratch/staging database with
`--database-url` or `CALIBRATE_DATABASE_URL`, already migrated to head
(`uv run alembic upgrade head` against that URL first).

Redis publishing is skipped entirely (`_NullRedis`): calibration only
cares about the final DB state, not the events a live run would
narrate, and standing up a real Redis connection for a headless batch
run would be pure overhead.

Usage: `uv run python scripts/calibrate.py --database-url postgresql+asyncpg://...`
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from panem_shared import constants
from panem_shared.content.loader import load_content
from panem_shared.db.models import DistrictState, MarketPrice
from panem_shared.db.session import make_engine, make_session_factory, session_scope
from panem_shared.logging import configure_logging, get_logger
from panem_shared.settings import get_settings
from panem_sim import world
from panem_sim.tick import run_tick

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
MONTHS = 12
TICKS_PER_MONTH = constants.TICKS_PER_DAY * 30
WORLD_SEED = "calibration"

logger = get_logger(component="calibrate")


class _NullRedis:
    """Duck-types just the one method `run_tick` calls on its redis
    client (`publish`) -- calibration doesn't narrate anywhere, so this
    just discards whatever `panem_shared.events.publish` would have sent."""

    async def publish(self, channel: str, message: str) -> None:
        return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("CALIBRATE_DATABASE_URL"),
        help="Scratch database to run against (or set CALIBRATE_DATABASE_URL). "
        "Must differ from the configured DATABASE_URL, already migrated to head.",
    )
    return parser.parse_args()


async def _seed_and_run(session_factory: async_sessionmaker[AsyncSession]) -> None:
    content = load_content(DATA_DIR)
    async with session_scope(session_factory) as session:
        await world.seed_world(session, content, WORLD_SEED)

    redis_client = _NullRedis()
    total_ticks = MONTHS * TICKS_PER_MONTH
    for i in range(total_ticks):
        await run_tick(session_factory, redis_client, content, WORLD_SEED)  # type: ignore[arg-type]
        if (i + 1) % TICKS_PER_MONTH == 0:
            logger.info("calibration_month_done", month=(i + 1) // TICKS_PER_MONTH)

    await _report(session_factory, content.goods)


async def _report(
    session_factory: async_sessionmaker[AsyncSession], goods: dict[str, object]
) -> None:
    async with session_factory() as session:
        districts = (await session.execute(select(DistrictState))).scalars().all()
        prices = (await session.execute(select(MarketPrice))).scalars().all()

    print(f"\n=== Calibration report ({MONTHS} months) ===")
    flags: list[str] = []
    for row in sorted(districts, key=lambda d: d.district_id):
        target = (
            "-" if row.quota_target <= 0 else f"{row.quota_progress:.0f}/{row.quota_target:.0f}"
        )
        print(
            f"district {row.district_id:>2}: quota {target:<14} favor {row.capitol_favor:+6.1f}  "
            f"treasury {row.treasury:8.0f}  unrest {row.unrest:5.1f}"
        )
        if row.treasury < 0:
            flags.append(f"district {row.district_id} treasury went negative ({row.treasury:.0f})")

    for row in sorted(prices, key=lambda p: (p.district_id, p.good_id)):
        good = goods.get(row.good_id)
        base_price = getattr(good, "base_price", None)
        if base_price is None:
            continue
        low, high = base_price * constants.PRICE_CLAMP_MIN, base_price * constants.PRICE_CLAMP_MAX
        if row.price <= low or row.price >= high:
            flags.append(
                f"district {row.district_id} {row.good_id} price pinned at a clamp bound "
                f"({row.price:.2f}, band [{low:.2f}, {high:.2f}])"
            )

    if flags:
        print("\nFlags:")
        for flag in flags:
            print(f" - {flag}")
    else:
        print("\nNo flags -- prices and treasuries stayed within a sane band.")


def main() -> None:
    configure_logging(component="calibrate")
    args = _parse_args()
    settings = get_settings()
    if not args.database_url:
        raise SystemExit(
            "Refusing to run without --database-url/CALIBRATE_DATABASE_URL -- this "
            "script fast-forwards a world's clock by a year and must not run "
            "against your bot/sim's configured DATABASE_URL by accident."
        )
    if args.database_url == settings.database_url:
        raise SystemExit(
            "--database-url must not be the same as the configured DATABASE_URL "
            "(Settings.database_url) -- point calibrate.py at a scratch database."
        )
    engine = make_engine(settings.model_copy(update={"database_url": args.database_url}))
    session_factory = make_session_factory(engine)
    asyncio.run(_seed_and_run(session_factory))


if __name__ == "__main__":
    main()
