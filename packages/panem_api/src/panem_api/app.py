"""FastAPI REST + WebSocket bridge for the Activity's live map (Plan §8,
Phase 5).

Reads only -- nothing here writes game state. `panem_sim`'s tick loop is
the sole writer of `pos:{district_id}` (`panem_shared.redis_keys
.positions_key`), a plain Redis string holding the district's current
NPC/character positions as JSON (`panem_sim.tick._district_positions`);
this app just serves that same key back over HTTP and a polling
WebSocket. No durability contract here the way `world_events` has one --
a missing/stale key just means the sim hasn't ticked yet (a fresh world)
or Redis lost it, not a bug to recover from.

No auth is enforced (see `Settings.api_host`'s comment in
`panem_shared.settings`): a real Discord Activity authenticates through
Discord's own OAuth handshake, which needs credentials and a live Activity
to verify against that this session had no way to test. Left as an
explicit, documented gap rather than unverifiable placeholder code.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from panem_shared.content.loader import ContentBundle
from panem_shared.logging import get_logger
from panem_shared.redis_keys import positions_key

logger = get_logger(component="api")

POSITIONS_POLL_INTERVAL_SECONDS = 3.0
"""How often the WebSocket re-sends a district's position snapshot.
There's no pubsub notification on `positions_key` changing (`panem_sim`
just overwrites the key each tick), so this is a plain poll rather than
push-on-change -- simple, and cheap enough at this data size/interval."""


class DistrictSummary(BaseModel):
    id: int
    name: str


class Positions(BaseModel):
    npcs: list[dict[str, object]]
    characters: list[dict[str, object]]


EMPTY_POSITIONS = Positions(npcs=[], characters=[])


async def _read_positions(redis_client: redis.Redis, district_id: int) -> Positions:
    raw = await redis_client.get(positions_key(district_id))
    if raw is None:
        return EMPTY_POSITIONS
    return Positions.model_validate(json.loads(raw))


def create_app(*, content: ContentBundle, redis_client: redis.Redis) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger.info("api_started", district_count=len(content.districts))
        yield
        await redis_client.aclose()

    app = FastAPI(title="Panem: The Long Year -- Activity API", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/districts", response_model=list[DistrictSummary])
    async def list_districts() -> list[DistrictSummary]:
        return [
            DistrictSummary(id=district.id, name=district.name)
            for district in sorted(content.districts.values(), key=lambda d: d.id)
        ]

    @app.get("/districts/{district_id}/positions", response_model=Positions)
    async def district_positions(district_id: int) -> Positions:
        if district_id not in content.districts:
            raise HTTPException(status_code=404, detail="No such district")
        return await _read_positions(redis_client, district_id)

    @app.websocket("/ws/districts/{district_id}/positions")
    async def district_positions_ws(websocket: WebSocket, district_id: int) -> None:
        if district_id not in content.districts:
            await websocket.close(code=4004, reason="No such district")
            return
        await websocket.accept()
        try:
            while True:
                positions = await _read_positions(redis_client, district_id)
                await websocket.send_json(positions.model_dump())
                await asyncio.sleep(POSITIONS_POLL_INTERVAL_SECONDS)
        except WebSocketDisconnect:
            pass

    return app
