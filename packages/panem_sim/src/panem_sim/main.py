"""`python -m panem_sim` entrypoint."""

from __future__ import annotations

import asyncio
from pathlib import Path

import redis.asyncio as redis

from panem_shared.db.session import make_engine, make_session_factory, session_scope
from panem_shared.logging import configure_logging, get_logger
from panem_shared.settings import get_settings
from panem_sim.tick import run_forever
from panem_sim.world import load_world, seed_world

REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = REPO_ROOT / "data"


async def _run() -> None:
    configure_logging(component="sim")
    logger = get_logger()
    settings = get_settings()

    content = load_world(DATA_DIR)
    engine = make_engine(settings)
    session_factory = make_session_factory(engine)
    redis_client: redis.Redis = redis.from_url(settings.redis_url)

    async with session_scope(session_factory) as session:
        await seed_world(session, content, settings.world_seed)

    logger.info(
        "sim_started",
        tick_interval_seconds=settings.tick_interval_seconds,
        district_count=len(content.districts),
    )
    try:
        await run_forever(
            session_factory,
            redis_client,
            content,
            settings.world_seed,
            settings.tick_interval_seconds,
        )
    finally:
        await redis_client.aclose()
        await engine.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
