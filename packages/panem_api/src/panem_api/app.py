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

The `/activity/work/*` and `/activity/crime/*` endpoints below are the
one place this process *does* write game state -- `session_factory`
(`None` unless `ACTIVITY_PUBLIC_URL` is set, see `panem_bot`'s `/work`)
is this process's only DB access, kept separate from the read-only map
endpoints above. `/activity/crime/*` resolves the `/lockpick`, `/steal`,
and `/burgle` skill-check minigames (contraband system) the same way
`/activity/work/*` resolves the shift minigame -- a short-lived attempt
stashed in Redis by `panem_bot` (`panem_shared.redis_keys.crime_attempt_
key`, no DB row of its own the way a `Shift` has) rather than a database
id, since a crime attempt never needs to outlive the one interaction
that launched it.

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
import dataclasses
import json
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from panem_api.dashboard_routes import (
    build_characters_router,
    build_crime_router,
    build_identify_router,
    build_jail_router,
)
from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import District
from panem_shared.db.models import (
    Character,
    DistrictState,
    MarketPrice,
    Npc,
    Property,
    Shift,
    WorldClock,
)
from panem_shared.db.session import session_scope
from panem_shared.jail import apply_lockpick_attempt, lockpick_difficulty, resolve_illicit_heat
from panem_shared.job_levels import job_level_for_shifts
from panem_shared.logging import get_logger
from panem_shared.redis_keys import (
    crime_attempt_key,
    crime_interaction_key,
    positions_key,
    work_interaction_key,
    work_pending_key,
)
from panem_shared.shifts import (
    already_worked_this_tick,
    apply_shift_outcome,
    illicit_shift_output,
    market_wage_multiplier,
    resolve_shift_game,
)
from panem_shared.stealing import (
    apply_burgle_outcome,
    apply_steal_outcome,
    burgle_difficulty,
    steal_difficulty,
)

logger = get_logger(component="api")

STATIC_DIR = Path(__file__).parent / "static"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API_BASE = "https://discord.com/api/v10"

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
    # A shift stays open across many ticks now (see apply_shift_outcome),
    # but only one resolution per tick counts -- this tells work.js to
    # refuse before mounting a game the result endpoint would reject anyway.
    already_worked_this_tick: bool
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
    arrested: bool = False


class WorkPendingShift(BaseModel):
    shift_id: int


class CrimeAttemptStatus(BaseModel):
    kind: str
    character_name: str
    # Set for `/steal` (who's being picked); `None` for lockpick/burgle,
    # which have no single person to name (a cell door, a house).
    target_name: str | None = None
    # 0..1, purely cosmetic -- sizes the minigame's target zone/speed
    # client-side. The server never re-checks the client's own `won`
    # against it (same no-auth trust posture as `/activity/work`).
    difficulty: float


class CrimeResultRequest(BaseModel):
    won: bool


class CrimeResultResponse(BaseModel):
    kind: str
    character_name: str
    target_name: str | None = None
    success: bool
    alerted: bool = False
    caught: bool = False
    amount: int = 0
    tries_left: int | None = None


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


async def _clear_launch_message(redis_client: redis.Redis, shift_id: int) -> None:
    """Removes the now-stale Play/Skip buttons from `panem_bot`'s original
    `/work` launch message once the Activity reports a result -- this
    process has no Discord gateway connection of its own, but an
    interaction's own `application_id`/`token` (stashed by `/work`, see
    `redis_keys.work_interaction_key`) is enough to edit that message
    directly via Discord's webhook-edit REST endpoint, no bot token
    needed. Best-effort: the shift is already resolved either way by the
    time this runs, so a missing/expired token (Discord's 15-minute cap)
    or a failed request here just leaves stale buttons behind rather than
    failing the result the player is waiting on."""
    key = work_interaction_key(shift_id)
    raw = await redis_client.get(key)
    if raw is None:
        return
    await redis_client.delete(key)
    info = json.loads(raw)
    url = f"{DISCORD_API_BASE}/webhooks/{info['application_id']}/{info['token']}/messages/@original"
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.patch(
                url, json={"content": "This shift has already been worked!", "components": []}
            )
        except httpx.HTTPError as exc:
            logger.warning("work_launch_message_edit_failed", shift_id=shift_id, error=str(exc))
            return
        if response.status_code != 200:
            logger.warning(
                "work_launch_message_edit_failed",
                shift_id=shift_id,
                status=response.status_code,
            )


async def _clear_crime_launch_message(
    redis_client: redis.Redis, attempt_id: str, banner: str
) -> None:
    """The crime-attempt (`/lockpick`/`/steal`/`/burgle`) counterpart to
    `_clear_launch_message` -- same reasoning, keyed by `attempt_id`
    instead of a shift id."""
    key = crime_interaction_key(attempt_id)
    raw = await redis_client.get(key)
    if raw is None:
        return
    await redis_client.delete(key)
    info = json.loads(raw)
    url = f"{DISCORD_API_BASE}/webhooks/{info['application_id']}/{info['token']}/messages/@original"
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.patch(url, json={"content": banner, "components": []})
        except httpx.HTTPError as exc:
            logger.warning(
                "crime_launch_message_edit_failed", attempt_id=attempt_id, error=str(exc)
            )
            return
        if response.status_code != 200:
            logger.warning(
                "crime_launch_message_edit_failed",
                attempt_id=attempt_id,
                status=response.status_code,
            )


def create_app(
    *,
    content: ContentBundle,
    redis_client: redis.Redis,
    discord_client_id: str = "",
    discord_client_secret: str = "",
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    max_characters_per_user: int = 1,
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
        through `content.jobs`.

        `already_worked_this_tick` lets `work.html` refuse up front
        (`already_resolved` alone can't -- a shift now stays open across
        its whole `tick_opened`..`tick_due` window instead of closing on
        its first `/work`) rather than making the player play a whole
        minigame only to have the result endpoint reject it."""
        if session_factory is None:
            raise HTTPException(status_code=503, detail="The work minigame isn't configured")
        async with session_scope(session_factory) as session:
            shift = await session.get(Shift, shift_id)
            character = await session.get(Character, shift.character_id) if shift else None
            if shift is None or character is None or character.job_title is None:
                raise HTTPException(status_code=404, detail="No such shift")
            clock = await session.get(WorldClock, 1)
            tick = clock.tick if clock is not None else 0
            return WorkShiftStatus(
                job_title=character.job_title,
                character_name=character.name,
                already_resolved=shift.result is not None,
                already_worked_this_tick=already_worked_this_tick(shift, tick),
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
        (Solitaire's "Give Up", for an unwinnable deal). A shift can only
        be resolved once per in-game tick (`already_worked_this_tick`) --
        it otherwise stays open past this call for the rest of its
        `tick_opened`..`tick_due` window, so the player can come back and
        work it again next tick. Trusts the client's `won`/`neutral`
        outright -- see this module's docstring."""
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
            if already_worked_this_tick(shift, tick):
                raise HTTPException(status_code=409, detail="Already worked this shift this tick")
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
            if character.job_is_illicit:
                outcome = dataclasses.replace(
                    outcome,
                    output=illicit_shift_output(
                        district, character, won=body.won, neutral=body.neutral
                    ),
                )
            apply_shift_outcome(
                shift, character, outcome, won=body.won, neutral=body.neutral, tick=tick
            )
            after_level = job_level_for_shifts(character.shifts_completed)

            arrested = False
            if character.job_is_illicit and not body.neutral:
                district_row = await session.get(DistrictState, character.district_id)
                arrested = resolve_illicit_heat(
                    character, district_row, lost=not body.won, current_tick=tick
                )
            wage, character_name = round(outcome.wage), character.name
        await _clear_launch_message(redis_client, shift_id)
        logger.info("work_game_resolved", shift_id=shift_id, won=body.won, wage=wage)
        return WorkResultResponse(
            wage=wage,
            won=body.won,
            character_name=character_name,
            leveled_up=after_level != before_level,
            level=after_level.value,
            arrested=arrested,
        )

    @app.get("/activity/crime/{attempt_id}", response_model=CrimeAttemptStatus)
    async def crime_attempt_status(attempt_id: str) -> CrimeAttemptStatus:
        """Lets `crime.html` show who/what a `/lockpick`/`/steal`/`/burgle`
        attempt is for, and how hard to size the minigame's target zone
        (`difficulty`), before mounting a game at all."""
        if session_factory is None:
            raise HTTPException(status_code=503, detail="The crime minigame isn't configured")
        raw = await redis_client.get(crime_attempt_key(attempt_id))
        if raw is None:
            raise HTTPException(status_code=404, detail="No such attempt")
        attempt = json.loads(raw)
        async with session_scope(session_factory) as session:
            character = await session.get(Character, attempt["character_id"])
            if character is None:
                raise HTTPException(status_code=404, detail="No such attempt")
            kind = attempt["kind"]
            if kind == "lockpick":
                return CrimeAttemptStatus(
                    kind=kind,
                    character_name=character.name,
                    difficulty=lockpick_difficulty(character),
                )
            if kind == "steal":
                victim_kind = attempt["victim_kind"]
                victim = (
                    await session.get(Npc, attempt["victim_id"])
                    if victim_kind == "npc"
                    else await session.get(Character, int(attempt["victim_id"]))
                )
                if victim is None:
                    raise HTTPException(status_code=404, detail="No such attempt")
                return CrimeAttemptStatus(
                    kind=kind,
                    character_name=character.name,
                    target_name=victim.name,
                    difficulty=steal_difficulty(is_npc=victim_kind == "npc"),
                )
            if kind == "burgle":
                house = await session.get(Property, attempt["property_id"])
                if house is None:
                    raise HTTPException(status_code=404, detail="No such attempt")
                return CrimeAttemptStatus(
                    kind=kind, character_name=character.name, difficulty=burgle_difficulty()
                )
            raise HTTPException(status_code=400, detail="Unknown crime kind")

    @app.post("/activity/crime/{attempt_id}/result", response_model=CrimeResultResponse)
    async def crime_attempt_result(
        attempt_id: str, body: CrimeResultRequest
    ) -> CrimeResultResponse:
        """Resolves a `/lockpick`/`/steal`/`/burgle` attempt once the
        minigame reports whether the player won it -- the initial skill
        check the RNG-fallback path would otherwise roll for itself
        (`panem_bot.services.stealing.roll_and_apply_steal`/`_burgle`,
        `panem_bot.services.jail.attempt_lockpick`). One-shot: the attempt
        is deleted from Redis before it's applied, so a retried/duplicate
        POST 404s instead of double-resolving. Trusts the client's `won`
        outright -- see this module's docstring."""
        if session_factory is None:
            raise HTTPException(status_code=503, detail="The crime minigame isn't configured")
        raw = await redis_client.get(crime_attempt_key(attempt_id))
        if raw is None:
            raise HTTPException(status_code=404, detail="This attempt was already resolved")
        await redis_client.delete(crime_attempt_key(attempt_id))
        attempt = json.loads(raw)
        kind = attempt["kind"]
        rng = random.Random()

        async with session_scope(session_factory) as session:
            character = await session.get(Character, attempt["character_id"])
            if character is None:
                raise HTTPException(status_code=404, detail="No such attempt")

            if kind == "lockpick":
                apply_lockpick_attempt(character, won=body.won)
                tries_left = constants.LOCKPICK_MAX_TRIES - character.jail_lockpick_tries_used
                banner = "This lock has already been tried!"
                response_obj = CrimeResultResponse(
                    kind=kind,
                    character_name=character.name,
                    success=body.won,
                    tries_left=tries_left,
                )
            elif kind == "steal":
                district_row = await session.get(DistrictState, attempt["district_id"])
                victim_kind = attempt["victim_kind"]
                victim = (
                    await session.get(Npc, attempt["victim_id"])
                    if victim_kind == "npc"
                    else await session.get(Character, int(attempt["victim_id"]))
                )
                if victim is None:
                    raise HTTPException(status_code=404, detail="No such attempt")
                result = await apply_steal_outcome(
                    session,
                    character=character,
                    victim=victim,
                    district_row=district_row,
                    current_tick=attempt["current_tick"],
                    success=body.won,
                    rng=rng,
                )
                banner = "This lift has already been tried!"
                response_obj = CrimeResultResponse(
                    kind=kind,
                    character_name=character.name,
                    target_name=victim.name,
                    success=result.success,
                    alerted=result.alerted,
                    caught=result.caught,
                    amount=result.amount,
                )
            elif kind == "burgle":
                district_row = await session.get(DistrictState, attempt["district_id"])
                house = await session.get(Property, attempt["property_id"])
                if house is None:
                    raise HTTPException(status_code=404, detail="No such attempt")
                result = await apply_burgle_outcome(
                    session,
                    character=character,
                    house_value=house.suggested_price,
                    district_row=district_row,
                    current_tick=attempt["current_tick"],
                    success=body.won,
                    rng=rng,
                )
                banner = "This break-in has already been tried!"
                response_obj = CrimeResultResponse(
                    kind=kind,
                    character_name=character.name,
                    success=result.success,
                    alerted=result.alerted,
                    caught=result.caught,
                    amount=result.amount,
                )
            else:
                raise HTTPException(status_code=400, detail="Unknown crime kind")

        await _clear_crime_launch_message(redis_client, attempt_id, banner)
        logger.info("crime_attempt_resolved", attempt_id=attempt_id, kind=kind, won=body.won)
        return response_obj

    app.include_router(build_identify_router(session_factory=session_factory))
    app.include_router(
        build_characters_router(
            content=content,
            session_factory=session_factory,
            max_characters_per_user=max_characters_per_user,
        )
    )
    app.include_router(
        build_jail_router(session_factory=session_factory, redis_client=redis_client)
    )
    app.include_router(
        build_crime_router(
            content=content, session_factory=session_factory, redis_client=redis_client
        )
    )

    if STATIC_DIR.exists():
        # Mounted last so it only ever catches paths none of the routes
        # above matched (Starlette tries routes in registration order) --
        # the Activity frontend at "/", "/app.js", etc. sits alongside the
        # data API above without either shadowing the other.
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="activity")

    return app
