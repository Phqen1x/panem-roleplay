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
from pathlib import Path

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from panem_shared.content.loader import ContentBundle
from panem_shared.logging import get_logger
from panem_shared.redis_keys import positions_key

logger = get_logger(component="api")

STATIC_DIR = Path(__file__).parent / "static"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"

POSITIONS_POLL_INTERVAL_SECONDS = 3.0
"""How often the WebSocket re-sends a district's position snapshot.
There's no pubsub notification on `positions_key` changing (`panem_sim`
just overwrites the key each tick), so this is a plain poll rather than
push-on-change -- simple, and cheap enough at this data size/interval."""


class LocationSummary(BaseModel):
    id: str
    name: str
    x: int
    y: int


class DistrictSummary(BaseModel):
    id: int
    name: str
    map_width: int
    map_height: int
    locations: list[LocationSummary]


class Positions(BaseModel):
    npcs: list[dict[str, object]]
    characters: list[dict[str, object]]


EMPTY_POSITIONS = Positions(npcs=[], characters=[])


class ActivityConfig(BaseModel):
    client_id: str


class TokenExchangeRequest(BaseModel):
    code: str


class TokenExchangeResponse(BaseModel):
    access_token: str


async def _read_positions(redis_client: redis.Redis, district_id: int) -> Positions:
    raw = await redis_client.get(positions_key(district_id))
    if raw is None:
        return EMPTY_POSITIONS
    return Positions.model_validate(json.loads(raw))


def create_app(
    *,
    content: ContentBundle,
    redis_client: redis.Redis,
    discord_client_id: str = "",
    discord_client_secret: str = "",
) -> FastAPI:
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
            DistrictSummary(
                id=district.id,
                name=district.name,
                map_width=district.map.width,
                map_height=district.map.height,
                locations=[
                    LocationSummary(
                        id=loc.id,
                        name=loc.name,
                        x=district.map.location_coords[loc.id][0],
                        y=district.map.location_coords[loc.id][1],
                    )
                    for loc in district.locations
                ],
            )
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

    @app.get("/activity/config", response_model=ActivityConfig)
    async def activity_config() -> ActivityConfig:
        """The Activity frontend's own client id (not a secret -- Discord
        Activities embed it in the iframe URL already); lets the static
        frontend avoid hardcoding it at build time."""
        return ActivityConfig(client_id=discord_client_id)

    @app.post("/activity/token", response_model=TokenExchangeResponse)
    async def activity_token(body: TokenExchangeRequest) -> TokenExchangeResponse:
        """Exchanges the authorization code from the embedded-app-sdk's
        `commands.authorize()` for an access token, per Discord's documented
        Activity OAuth flow -- the one step that needs `discord_client_secret`
        and so can't happen in the frontend itself."""
        if not discord_client_id or not discord_client_secret:
            raise HTTPException(
                status_code=503, detail="Activity OAuth isn't configured on this server"
            )
        async with httpx.AsyncClient() as http_client:
            try:
                response = await http_client.post(
                    DISCORD_TOKEN_URL,
                    data={
                        "client_id": discord_client_id,
                        "client_secret": discord_client_secret,
                        "grant_type": "authorization_code",
                        "code": body.code,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except httpx.HTTPError as exc:
                logger.warning("activity_token_exchange_unreachable", error=str(exc))
                raise HTTPException(
                    status_code=502, detail="Could not reach Discord's token endpoint"
                ) from exc
        if response.status_code != 200:
            logger.warning(
                "activity_token_exchange_failed",
                status=response.status_code,
                body=response.text,
            )
            raise HTTPException(status_code=502, detail="Discord token exchange failed")
        return TokenExchangeResponse(access_token=response.json()["access_token"])

    if STATIC_DIR.exists():
        # Mounted last so it only ever catches paths none of the routes
        # above matched (Starlette tries routes in registration order) --
        # the Activity frontend at "/", "/app.js", etc. sits alongside the
        # data API above without either shadowing the other.
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="activity")

    return app
