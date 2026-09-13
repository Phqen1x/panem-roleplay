"""FastAPI REST + WebSocket bridge for the Activity's live map (Plan §8,
Phase 5), plus the `/work` minigame's result endpoint (Plan §9-adjacent).

The live-map side is reads only -- `panem_sim`'s tick loop is the sole
writer of `pos:{district_id}` (`panem_shared.redis_keys.positions_key`), a
plain Redis string holding the district's current NPC/character positions
as JSON (`panem_sim.tick._district_positions`); this app just serves that
same key back over HTTP and a polling WebSocket. No durability contract
here the way `world_events` has one -- a missing/stale key just means the
sim hasn't ticked yet (a fresh world) or Redis lost it, not a bug to
recover from.

The `/activity/work/*` endpoints below are the one place this process
*does* write game state -- `session_factory` (`None` unless
`ACTIVITY_PUBLIC_URL` is set, see `panem_bot`'s `/work`) is this process's
only DB access, kept separate from the read-only map endpoints above.

No auth is enforced anywhere in this file (see `Settings.api_host`'s
comment in `panem_shared.settings`): a real Discord Activity authenticates
through Discord's own OAuth handshake, which needs credentials and a live
Activity to verify against that this session had no way to test. Left as
an explicit, documented gap rather than unverifiable placeholder code --
the work-result endpoint trusts whatever `won` the client reports, the
same trust level as every other unauthenticated endpoint here.
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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import District
from panem_shared.db.models import Character, MarketPrice, Shift, WorldClock
from panem_shared.db.session import session_scope
from panem_shared.job_levels import job_level_for_shifts
from panem_shared.logging import get_logger
from panem_shared.redis_keys import positions_key, work_pending_key
from panem_shared.shifts import apply_shift_outcome, market_wage_multiplier, resolve_shift_game

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


class ClientErrorReport(BaseModel):
    step: str
    message: str
    stack: str | None = None


class WorkShiftStatus(BaseModel):
    job_title: str
    character_name: str
    already_resolved: bool
    # The character's current JobLevel value (apprentice..expert) --
    # work.js uses this to pick which minigames are available and how
    # hard the harder ones are (Snake's win score, Minesweeper's grid).
    level: str


class WorkResultRequest(BaseModel):
    won: bool
    # Set by Solitaire's "Give Up" button: some Klondike deals are
    # unwinnable from the start, so that loss shouldn't carry the usual
    # lose-wage penalty (`panem_shared.shifts.resolve_shift_game`'s
    # `neutral` param). Every other game always sends the default `False`.
    neutral: bool = False


class WorkResultResponse(BaseModel):
    wage: int
    won: bool
    character_name: str
    leveled_up: bool
    level: str


class WorkPendingShift(BaseModel):
    shift_id: int


async def _market_multiplier(
    session: AsyncSession, content: ContentBundle, district: District
) -> float:
    """Same wage feedback `panem_bot.services.shifts.
    market_multiplier_for_district` gives the bot's own `/work` -- this
    process can't import `panem_bot`, so the same small lookup is
    duplicated here rather than shared."""
    if district.quota is None:
        return 1.0
    good = content.goods.get(district.quota.good)
    if good is None:
        return 1.0
    row = await session.get(MarketPrice, (district.id, good.id))
    price = row.price if row is not None else good.base_price
    return market_wage_multiplier(price, good.base_price)


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
    session_factory: async_sessionmaker[AsyncSession] | None = None,
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

    @app.post("/activity/debug")
    async def activity_debug(report: ClientErrorReport) -> dict[str, bool]:
        """A real Discord Activity's devtools can be genuinely hard to
        reach (no right-click Inspect in most clients), so app.js posts
        its own auth failures here instead of only logging to a browser
        console nobody watching the server can see -- this just puts the
        same information into this process's own log output."""
        logger.warning(
            "activity_client_error", step=report.step, message=report.message, stack=report.stack
        )
        return {"logged": True}

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

    @app.get("/activity/work/for-channel/{channel_id}", response_model=WorkPendingShift)
    async def work_pending_shift(channel_id: int) -> WorkPendingShift:
        """`work.html` launched as a real embedded Discord Activity has no
        `?shift_id=` to read (Discord only ever loads the Activity's one
        configured root URL, appending its own `channel_id` among other
        params) -- this resolves it from what `/work` stashed in Redis for
        the voice channel the launch invite was made on
        (`panem_shared.redis_keys.work_pending_key`)."""
        raw = await redis_client.get(work_pending_key(channel_id))
        if raw is None:
            raise HTTPException(status_code=404, detail="No pending work shift for this channel")
        return WorkPendingShift(shift_id=int(raw))

    @app.get("/activity/work/{shift_id}", response_model=WorkShiftStatus)
    async def work_shift_status(shift_id: int) -> WorkShiftStatus:
        """Lets `work.html` show the player who/what they're playing for
        (and refuse a stale link) before they've played anything, and
        tells it the character's job level so it can pick/scale a
        minigame accordingly (`work.js`'s `LEVELS` array). Jobs are
        free-typed (`Character.job_title`) rather than a catalog entry
        now, so this reads the character directly instead of joining
        through `content.jobs`."""
        if session_factory is None:
            raise HTTPException(status_code=503, detail="The work minigame isn't configured")
        async with session_scope(session_factory) as session:
            shift = await session.get(Shift, shift_id)
            character = await session.get(Character, shift.character_id) if shift else None
            if shift is None or character is None or character.job_title is None:
                raise HTTPException(status_code=404, detail="No such shift")
            return WorkShiftStatus(
                job_title=character.job_title,
                character_name=character.name,
                already_resolved=shift.result is not None,
                level=job_level_for_shifts(character.shifts_completed).value,
            )

    @app.post("/activity/work/{shift_id}/result", response_model=WorkResultResponse)
    async def work_shift_result(shift_id: int, body: WorkResultRequest) -> WorkResultResponse:
        """The only DB write in this process: `work.html` reports whether
        its randomly-picked minigame was won or lost once the player
        finishes, and this resolves the shift the same way `panem_bot`'s
        `/work` does (`panem_shared.shifts.resolve_shift_game`) -- job
        level, win/lose, and the home district's current market price for
        its quota good all factor into the wage. `neutral` skips the
        lose-wage penalty for a loss the player had no way to avoid
        (Solitaire's "Give Up", for an unwinnable deal). Trusts the
        client's `won`/`neutral` outright -- see this module's
        docstring."""
        if session_factory is None:
            raise HTTPException(status_code=503, detail="The work minigame isn't configured")
        async with session_scope(session_factory) as session:
            shift = await session.get(Shift, shift_id)
            if shift is None:
                raise HTTPException(status_code=404, detail="No such shift")
            if shift.result is not None:
                raise HTTPException(status_code=409, detail="This shift was already resolved")
            character = await session.get(Character, shift.character_id)
            if character is None:
                raise HTTPException(status_code=404, detail="No such shift")
            clock = await session.get(WorldClock, 1)
            tick = clock.tick if clock is not None else 0
            district = content.district(character.district_id)
            market_multiplier = await _market_multiplier(session, content, district)
            before_level = job_level_for_shifts(character.shifts_completed)
            outcome = resolve_shift_game(
                character,
                district,
                won=body.won,
                market_multiplier=market_multiplier,
                neutral=body.neutral,
            )
            apply_shift_outcome(shift, character, outcome, tick=tick)
            after_level = job_level_for_shifts(character.shifts_completed)
            wage, character_name = round(outcome.wage), character.name
        logger.info("work_game_resolved", shift_id=shift_id, won=body.won, wage=wage)
        return WorkResultResponse(
            wage=wage,
            won=body.won,
            character_name=character_name,
            leveled_up=after_level != before_level,
            level=after_level.value,
        )

    if STATIC_DIR.exists():
        # Mounted last so it only ever catches paths none of the routes
        # above matched (Starlette tries routes in registration order) --
        # the Activity frontend at "/", "/app.js", etc. sits alongside the
        # data API above without either shadowing the other.
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="activity")

    return app
