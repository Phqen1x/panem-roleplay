"""The web dashboard's REST surface: every player-facing slash command as a
tab in the same Activity the map page lives in (`static/tabs/*.js`), rather
than a command typed in Discord.

Identity here works differently from `/activity/work/*`/`/activity/crime/*`
above it in `app.py` -- those are one-shot, minted per action by `panem_bot`
for a single interaction. A dashboard tab is something a player sits on
across many actions, so instead each request carries the player's Discord
user id (`app.js` gets this for free from the embedded-app-sdk's
`authenticate()` result -- see that file's own comment) plus the
`character_id` they picked from `/activity/dashboard/identify`'s list, and
every route re-validates that pair (`_resolve_owned_character`) before
touching anything. This is not cryptographic auth -- nothing here verifies
the Discord access token itself, same documented gap as the rest of this
process (see `app.py`'s module docstring) -- it's just enough to stop one
player's dashboard session from acting as another player's character by
guessing an id, which the single-token model above doesn't even attempt.

Routers are built inside `create_app`'s closure (same DI style as the rest
of `app.py`: capture `content`/`session_factory`, no `Depends()` layer) and
included via `app.include_router(...)`. Each milestone's domain (jail,
crime, work, market, travel, residents, housing, characters) adds its own
`build_*_router` here; this module is expected to grow -- split into a
`dashboard_routes/` package by domain if it gets unwieldy, per the plan.
"""

from __future__ import annotations

import json
import random
import secrets

import redis.asyncio as redis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from panem_shared import blackmarket as blackmarket_svc
from panem_shared import characters as characters_svc
from panem_shared import constants, job_levels, simtime
from panem_shared import housing as housing_svc
from panem_shared import jail as jail_svc
from panem_shared import jobs as jobs_svc
from panem_shared import market as market_svc
from panem_shared import poaching as poaching_svc
from panem_shared import stealing as stealing_svc
from panem_shared import travel as travel_svc
from panem_shared.content.loader import ContentBundle
from panem_shared.db.models import (
    ApartmentLease,
    Character,
    Npc,
    Property,
    PropertyAuction,
    RelationshipRow,
    Scene,
    Shift,
    User,
    WorldClock,
)
from panem_shared.db.session import session_scope
from panem_shared.enums import (
    CharacterStatus,
    DayPhase,
    OwnerKind,
    Position,
    PropertyKind,
    SceneStatus,
)
from panem_shared.errors import NotAllowed, NotFound, ServiceError
from panem_shared.redis_keys import CRIME_ATTEMPT_TTL_S, crime_attempt_key
from panem_shared.relationships import relationship_key
from panem_shared.shifts import (
    already_worked_this_tick,
    has_job,
    open_adhoc_shift_override,
    start_shift_game,
)


class DashboardCharacterSummary(BaseModel):
    id: int
    name: str
    avatar_url: str | None = None
    district_id: int
    current_district_id: int
    money: int
    jailed_until_tick: int | None = None


class IdentifyRequest(BaseModel):
    discord_id: int


class IdentifyResponse(BaseModel):
    characters: list[DashboardCharacterSummary]


def _require_session_factory(
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> async_sessionmaker[AsyncSession]:
    if session_factory is None:
        raise HTTPException(status_code=503, detail="The dashboard isn't configured")
    return session_factory


async def _resolve_owned_character(
    session: AsyncSession, *, discord_id: int, character_id: int
) -> Character:
    """Every dashboard action/read route calls this first -- the shared
    ownership check described in this module's docstring. Raises 404
    rather than 403 for a mismatched owner, same as an unknown id: this
    process has no session/login of its own to distinguish "wrong owner"
    from "doesn't exist" in a way that's worth telling a client apart."""
    character = await session.get(Character, character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="No such character")
    user = await session.get(User, character.user_id)
    if user is None or user.discord_id != discord_id:
        raise HTTPException(status_code=404, detail="No such character")
    return character


async def _current_tick(session: AsyncSession) -> int:
    clock = await session.get(WorldClock, 1)
    return clock.tick if clock is not None else 0


def _http_from_service_error(exc: ServiceError) -> HTTPException:
    """Every dashboard write route below wraps its service call in `except
    ServiceError as exc: raise _http_from_service_error(exc) from exc` --
    the service layer's `NotAllowed`/`ValidationFailed`/`LimitReached`/
    `NotFound` refusals (`panem_shared.errors`) already carry a
    `reason_key` that means the same thing `strings.py` looks it up for in
    Discord, so this reuses it as the HTTP detail rather than inventing a
    second set of messages."""
    status = 404 if isinstance(exc, NotFound) else 400
    return HTTPException(status_code=status, detail=exc.reason_key)


def build_identify_router(*, session_factory: async_sessionmaker[AsyncSession] | None) -> APIRouter:
    router = APIRouter(prefix="/activity/dashboard", tags=["dashboard"])

    @router.post("/identify", response_model=IdentifyResponse)
    async def identify(body: IdentifyRequest) -> IdentifyResponse:
        """The dashboard's entry point: resolves the Discord user id `app.js`
        got from the embedded-app-sdk handshake (or the manual preview-mode
        fallback) into that player's approved characters, so the shell can
        offer a character picker the same way each slash command's
        `character:` autocomplete does today. An unknown discord_id (no
        `User` row yet -- this player has never run a command that created
        one) isn't an error: it just means no characters yet."""
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            user_row = await session.execute(select(User).where(User.discord_id == body.discord_id))
            user = user_row.scalar_one_or_none()
            if user is None:
                return IdentifyResponse(characters=[])
            rows = await session.execute(
                select(Character)
                .where(
                    Character.user_id == user.id, Character.status == CharacterStatus.APPROVED.value
                )
                .order_by(Character.id)
            )
            characters = rows.scalars().all()
            return IdentifyResponse(
                characters=[
                    DashboardCharacterSummary(
                        id=c.id,
                        name=c.name,
                        avatar_url=c.avatar_url,
                        district_id=c.district_id,
                        current_district_id=c.current_district_id,
                        money=c.money,
                        jailed_until_tick=c.jailed_until_tick,
                    )
                    for c in characters
                ]
            )

    return router


class CharacterDetail(BaseModel):
    id: int
    name: str
    status: str
    age: int
    appearance: str
    backstory: str
    avatar_url: str | None = None
    proxy_tag: str | None = None
    district_id: int
    district_name: str
    current_district_id: int
    current_district_name: str
    job_title: str | None = None
    shift_phase: str | None = None
    job_is_illicit: bool
    money: int
    jailed_until_tick: int | None = None


def _character_detail(character: Character, *, content: ContentBundle) -> CharacterDetail:
    return CharacterDetail(
        id=character.id,
        name=character.name,
        status=character.status,
        age=character.age,
        appearance=character.appearance,
        backstory=character.backstory,
        avatar_url=character.avatar_url,
        proxy_tag=character.proxy_tag,
        district_id=character.district_id,
        district_name=content.districts[character.district_id].name,
        current_district_id=character.current_district_id,
        current_district_name=content.districts[character.current_district_id].name,
        job_title=character.job_title,
        shift_phase=character.shift_phase,
        job_is_illicit=character.job_is_illicit,
        money=character.money,
        jailed_until_tick=character.jailed_until_tick,
    )


class MyCharactersResponse(BaseModel):
    characters: list[CharacterDetail]


class CreateCharacterRequest(BaseModel):
    discord_id: int
    district_id: int
    name: str
    age: int
    appearance: str = ""
    backstory: str = ""
    avatar_url: str | None = None
    job_title: str
    shift_phase: str
    job_is_illicit: bool = False


class RetireCharacterRequest(BaseModel):
    discord_id: int


class UpdateCharacterRequest(BaseModel):
    discord_id: int
    avatar_url: str | None = None
    proxy_tag: str | None = None


def build_characters_router(
    *,
    content: ContentBundle,
    session_factory: async_sessionmaker[AsyncSession] | None,
    max_characters_per_user: int,
) -> APIRouter:
    """The Character tab's REST surface: list/create/edit/retire, mirroring
    `/character list|create|avatar|tag|retire`. Unlike Discord's `/character
    create` (a multi-step modal wizard ending in a bot-posted staff-approval
    embed), this endpoint can only write the DB row -- `panem_api` has no
    bot token to post that embed itself, so `CharacterCog._announce_pending_
    characters` (a background poll task in `panem_bot`) picks up what this
    creates and announces it the same way, shortly after. District is a
    plain field here rather than inferred from a Discord guild role (how
    `/character create` picks it) -- the dashboard has no equivalent
    without a wider OAuth scope than this feature asks for; see the
    README's note on this simplification."""
    router = APIRouter(prefix="/activity/dashboard/characters", tags=["dashboard"])

    @router.get("", response_model=MyCharactersResponse)
    async def list_my_characters(discord_id: int) -> MyCharactersResponse:
        """A rejected character is a dead end -- there's nothing left for the
        player to do with it here (no appeal flow, no resubmission), so
        unlike the Discord-side moderation queue it's never worth surfacing
        in this list. Pending/approved/retired characters are all still
        something the player manages."""
        factory = _require_session_factory(session_factory)
        visible_statuses = {
            CharacterStatus.PENDING.value,
            CharacterStatus.APPROVED.value,
            CharacterStatus.RETIRED.value,
        }
        async with session_scope(factory) as session:
            user_row = await session.execute(select(User).where(User.discord_id == discord_id))
            user = user_row.scalar_one_or_none()
            if user is None:
                return MyCharactersResponse(characters=[])
            status_rank = case(
                (Character.status == CharacterStatus.APPROVED.value, 0),
                (Character.status == CharacterStatus.RETIRED.value, 1),
                (Character.status == CharacterStatus.PENDING.value, 2),
                else_=3,
            )
            rows = await session.execute(
                select(Character)
                .where(Character.user_id == user.id, Character.status.in_(visible_statuses))
                .order_by(status_rank, Character.id)
            )
            return MyCharactersResponse(
                characters=[_character_detail(c, content=content) for c in rows.scalars().all()]
            )

    @router.post("", response_model=CharacterDetail)
    async def create_my_character(body: CreateCharacterRequest) -> CharacterDetail:
        factory = _require_session_factory(session_factory)
        if body.district_id not in content.districts:
            raise HTTPException(status_code=400, detail="No such district")
        if body.shift_phase not in {phase.value for phase in DayPhase}:
            raise HTTPException(status_code=400, detail="Invalid shift phase")
        async with session_scope(factory) as session:
            user = await characters_svc.get_or_create_user(session, body.discord_id)
            # Same fallback `effective_max_characters` applies, inlined: that
            # helper takes a full `Settings` object just for this one field,
            # which isn't worth constructing here for the sake of reuse.
            max_characters = (
                user.max_characters_override
                if user.max_characters_override is not None
                else max_characters_per_user
            )
            try:
                character = await characters_svc.create_character(
                    session,
                    user=user,
                    district_id=body.district_id,
                    name=body.name,
                    age=body.age,
                    appearance=body.appearance,
                    backstory=body.backstory,
                    avatar_url=body.avatar_url,
                    job_title=body.job_title,
                    shift_phase=body.shift_phase,
                    job_is_illicit=body.job_is_illicit,
                    max_characters=max_characters,
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            return _character_detail(character, content=content)

    @router.patch("/{character_id}", response_model=CharacterDetail)
    async def update_my_character(
        character_id: int, body: UpdateCharacterRequest
    ) -> CharacterDetail:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            try:
                if body.avatar_url is not None:
                    characters_svc.validate_avatar_url(body.avatar_url)
                    character.avatar_url = body.avatar_url or None
                if body.proxy_tag is not None:
                    characters_svc.validate_proxy_tag(body.proxy_tag)
                    character.proxy_tag = body.proxy_tag
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            return _character_detail(character, content=content)

    @router.post("/{character_id}/retire", response_model=CharacterDetail)
    async def retire_my_character(
        character_id: int, body: RetireCharacterRequest
    ) -> CharacterDetail:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            try:
                character = await characters_svc.retire_character(session, character)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            return _character_detail(character, content=content)

    return router


class JailStatusResponse(BaseModel):
    character_name: str
    avatar_url: str | None = None
    jailed: bool
    jailed_until_tick: int | None = None
    jail_sentence_ticks: int | None = None
    jail_count: int
    tries_used: int
    tries_left: int
    bail_cost: int | None = None


class BailRequest(BaseModel):
    discord_id: int


class BailResponse(BaseModel):
    character_name: str
    cost: int


class LockpickStartRequest(BaseModel):
    discord_id: int


class LockpickStartResponse(BaseModel):
    attempt_id: str
    difficulty: float


def build_jail_router(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None,
    redis_client: redis.Redis,
) -> APIRouter:
    """The Jail tab's REST surface: mirrors `/bail` and `/lockpick`. The
    lockpick-start endpoint mints a crime attempt exactly like `cogs/jail.
    py`'s `/lockpick` does today (same Redis key/payload shape
    `/activity/crime/{attempt_id}` already reads) -- the dashboard tab
    embeds `crime.html?attempt_id=...&kind=lockpick` in an iframe to play
    it, reusing that page and its minigame unmodified."""
    router = APIRouter(prefix="/activity/dashboard/jail", tags=["dashboard"])

    @router.get("/{character_id}", response_model=JailStatusResponse)
    async def jail_status(character_id: int, discord_id: int) -> JailStatusResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            current_tick = await _current_tick(session)
            jailed = (
                character.jailed_until_tick is not None
                and character.jailed_until_tick > current_tick
            )
            tries_used = character.jail_lockpick_tries_used or 0
            cost = jail_svc.bail_cost(character, current_tick) if jailed else None
            return JailStatusResponse(
                character_name=character.name,
                avatar_url=character.avatar_url,
                jailed=jailed,
                jailed_until_tick=character.jailed_until_tick,
                jail_sentence_ticks=character.jail_sentence_ticks,
                jail_count=character.jail_count,
                tries_used=tries_used,
                tries_left=max(0, constants.LOCKPICK_MAX_TRIES - tries_used),
                bail_cost=cost,
            )

    @router.post("/{character_id}/bail", response_model=BailResponse)
    async def pay_bail(character_id: int, body: BailRequest) -> BailResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            current_tick = await _current_tick(session)
            try:
                cost = jail_svc.pay_bail(character, current_tick)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            return BailResponse(character_name=character.name, cost=cost)

    @router.post("/{character_id}/lockpick/start", response_model=LockpickStartResponse)
    async def start_lockpick(
        character_id: int, body: LockpickStartRequest
    ) -> LockpickStartResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            current_tick = await _current_tick(session)
            try:
                jail_svc.check_can_attempt_lockpick(character, current_tick)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            difficulty = jail_svc.lockpick_difficulty(character)

        attempt_id = secrets.token_urlsafe(16)
        await redis_client.set(
            crime_attempt_key(attempt_id),
            json.dumps({"kind": "lockpick", "character_id": character_id}),
            ex=CRIME_ATTEMPT_TTL_S,
        )
        return LockpickStartResponse(attempt_id=attempt_id, difficulty=difficulty)

    return router


class CrimeTargetOption(BaseModel):
    name: str
    kind: str  # "player" | "npc"


class StealTargetsResponse(BaseModel):
    targets: list[CrimeTargetOption]


class BurgleTargetsResponse(BaseModel):
    owners: list[str]


class StealStartRequest(BaseModel):
    discord_id: int
    target: str


class BurgleStartRequest(BaseModel):
    discord_id: int
    owner: str


class CrimeStartResponse(BaseModel):
    attempt_id: str
    difficulty: float
    target_name: str


class PoachRequest(BaseModel):
    discord_id: int


class PoachResponse(BaseModel):
    caught: bool
    good_name: str | None = None
    qty: int | None = None
    fine: int | None = None


def build_crime_router(
    *,
    content: ContentBundle,
    session_factory: async_sessionmaker[AsyncSession] | None,
    redis_client: redis.Redis,
) -> APIRouter:
    """The Crime tab's REST surface: mirrors `/steal`, `/burgle`, `/poach`.
    Steal/burgle-start mint a crime attempt exactly like their Discord
    commands do (same Redis shape `/activity/crime/{id}` reads) for the
    dashboard to embed in an iframe; `/poach` never launches an Activity on
    the bot side either, so this resolves it instantly, same as there."""
    router = APIRouter(prefix="/activity/dashboard/crime", tags=["dashboard"])

    @router.get("/{character_id}/steal-targets", response_model=StealTargetsResponse)
    async def steal_targets(character_id: int, discord_id: int) -> StealTargetsResponse:
        """Players and NPCs sharing both district and exact location with
        `character_id` -- mirrors `/steal`'s own target autocomplete."""
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            char_names = (
                (
                    await session.execute(
                        select(Character.name).where(
                            Character.status == CharacterStatus.APPROVED.value,
                            Character.current_district_id == character.current_district_id,
                            Character.location_id == character.location_id,
                            Character.id != character.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            npc_names = (
                (
                    await session.execute(
                        select(Npc.name).where(
                            Npc.district_id == character.current_district_id,
                            Npc.location_id == character.location_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        targets = [CrimeTargetOption(name=n, kind="player") for n in char_names] + [
            CrimeTargetOption(name=n, kind="npc") for n in npc_names
        ]
        return StealTargetsResponse(targets=sorted(targets, key=lambda t: t.name))

    @router.get("/{character_id}/burgle-targets", response_model=BurgleTargetsResponse)
    async def burgle_targets(character_id: int, discord_id: int) -> BurgleTargetsResponse:
        """Owners of a house in `character_id`'s current district (not
        their own) -- an improvement over `/burgle`'s bare-string `owner`
        param, which has no autocomplete on the bot side at all."""
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            owners = (
                (
                    await session.execute(
                        select(Character.name)
                        .join(Property, Property.owner_id == Character.id)
                        .where(
                            Property.kind == PropertyKind.HOUSE.value,
                            Property.owner_kind == OwnerKind.CHARACTER.value,
                            Property.district_id == character.current_district_id,
                            Character.id != character.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        return BurgleTargetsResponse(owners=sorted(set(owners)))

    async def _resolve_steal_victim(
        session: AsyncSession, character: Character, target_name: str
    ) -> Character | Npc | None:
        target_char = (
            await session.execute(
                select(Character).where(
                    Character.name == target_name,
                    Character.status == CharacterStatus.APPROVED.value,
                    Character.current_district_id == character.current_district_id,
                    Character.location_id == character.location_id,
                    Character.id != character.id,
                )
            )
        ).scalar_one_or_none()
        if target_char is not None:
            return target_char
        return (
            await session.execute(
                select(Npc).where(
                    Npc.name == target_name,
                    Npc.district_id == character.current_district_id,
                    Npc.location_id == character.location_id,
                )
            )
        ).scalar_one_or_none()

    @router.post("/{character_id}/steal/start", response_model=CrimeStartResponse)
    async def start_steal(character_id: int, body: StealStartRequest) -> CrimeStartResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            victim = await _resolve_steal_victim(session, character, body.target)
            if victim is None:
                raise HTTPException(status_code=404, detail="steal_target_not_found")
            current_tick = await _current_tick(session)
            try:
                stealing_svc.check_can_steal(character, victim, current_tick)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            character.last_steal_tick = current_tick
            district_id = character.current_district_id
            victim_kind = "npc" if isinstance(victim, Npc) else "character"
            victim_id, victim_name = str(victim.id), victim.name
            difficulty = stealing_svc.steal_difficulty(is_npc=victim_kind == "npc")

        attempt_id = secrets.token_urlsafe(16)
        await redis_client.set(
            crime_attempt_key(attempt_id),
            json.dumps(
                {
                    "kind": "steal",
                    "character_id": character_id,
                    "district_id": district_id,
                    "current_tick": current_tick,
                    "victim_kind": victim_kind,
                    "victim_id": victim_id,
                }
            ),
            ex=CRIME_ATTEMPT_TTL_S,
        )
        return CrimeStartResponse(
            attempt_id=attempt_id, difficulty=difficulty, target_name=victim_name
        )

    @router.post("/{character_id}/burgle/start", response_model=CrimeStartResponse)
    async def start_burgle(character_id: int, body: BurgleStartRequest) -> CrimeStartResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            owner_char = (
                await session.execute(
                    select(Character).where(
                        Character.name == body.owner,
                        Character.current_district_id == character.current_district_id,
                    )
                )
            ).scalar_one_or_none()
            house = None
            if owner_char is not None:
                house = (
                    await session.execute(
                        select(Property).where(
                            Property.kind == PropertyKind.HOUSE.value,
                            Property.owner_kind == OwnerKind.CHARACTER.value,
                            Property.owner_id == owner_char.id,
                            Property.district_id == character.current_district_id,
                        )
                    )
                ).scalar_one_or_none()
            if house is None:
                raise HTTPException(status_code=404, detail="burgle_owner_not_found")

            current_tick = await _current_tick(session)
            try:
                stealing_svc.check_can_burgle(character, house, current_tick, owner=owner_char)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            character.last_steal_tick = current_tick
            house_id, district_id = house.id, house.district_id
            difficulty = stealing_svc.burgle_difficulty()

        attempt_id = secrets.token_urlsafe(16)
        await redis_client.set(
            crime_attempt_key(attempt_id),
            json.dumps(
                {
                    "kind": "burgle",
                    "character_id": character_id,
                    "property_id": house_id,
                    "district_id": district_id,
                    "current_tick": current_tick,
                }
            ),
            ex=CRIME_ATTEMPT_TTL_S,
        )
        return CrimeStartResponse(
            attempt_id=attempt_id, difficulty=difficulty, target_name=body.owner
        )

    @router.post("/{character_id}/poach", response_model=PoachResponse)
    async def poach(character_id: int, body: PoachRequest) -> PoachResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            district = content.district(character.current_district_id)
            current_tick = await _current_tick(session)
            try:
                result = await poaching_svc.resolve_poach(
                    session,
                    character=character,
                    district=district,
                    goods=content.goods,
                    current_tick=current_tick,
                    rng=random.Random(),
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
        if result.caught:
            return PoachResponse(caught=True, fine=constants.POACH_FINE)
        assert result.good is not None
        return PoachResponse(
            caught=False, good_name=result.good.name, qty=constants.POACH_YIELD_QTY
        )

    return router


class WorkStatusResponse(BaseModel):
    has_job: bool
    job_title: str | None = None
    shift_phase: str | None = None
    level: str


class WorkStartRequest(BaseModel):
    discord_id: int


class WorkStartResponse(BaseModel):
    shift_id: int
    job_title: str
    level: str
    already_worked_this_tick: bool


def build_work_router(*, session_factory: async_sessionmaker[AsyncSession] | None) -> APIRouter:
    """The Work tab's REST surface: mirrors `/work`'s shift-finding logic
    only (`GET`/`POST .../start`) -- actually playing or skipping the
    shift reuses the existing `/activity/work/{shift_id}` (status) and
    `/activity/work/{shift_id}/result` (POST, `{won: false, neutral:
    true}` for Skip) endpoints unchanged, the same way the dashboard's
    Jail/Crime tabs reuse `/activity/crime/*` rather than duplicating it.
    `open_adhoc_shift_override`'s staff privilege doesn't apply here --
    the dashboard has no notion of a Discord staff role, only the
    `Position.GAMEMAKER` half of that check (a real in-fiction position on
    the character itself, not tied to Discord)."""
    router = APIRouter(prefix="/activity/dashboard/work", tags=["dashboard"])

    @router.get("/{character_id}", response_model=WorkStatusResponse)
    async def work_status(character_id: int, discord_id: int) -> WorkStatusResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            level = job_levels.job_level_for_shifts(character.shifts_completed)
            return WorkStatusResponse(
                has_job=has_job(character),
                job_title=character.job_title,
                shift_phase=character.shift_phase,
                level=level.value,
            )

    @router.post("/{character_id}/start", response_model=WorkStartResponse)
    async def start_work(character_id: int, body: WorkStartRequest) -> WorkStartResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            if not has_job(character):
                raise HTTPException(status_code=400, detail="job_none_set")

            open_shift = (
                await session.execute(
                    select(Shift).where(Shift.character_id == character.id, Shift.result.is_(None))
                )
            ).scalar_one_or_none()
            if open_shift is None:
                is_gamemaker = Position.GAMEMAKER.value in character.positions
                current_tick = await _current_tick(session)
                open_shift = open_adhoc_shift_override(
                    character, current_tick, is_staff=is_gamemaker
                )
                if open_shift is None:
                    raise HTTPException(status_code=400, detail="job_no_open_shift")
                session.add(open_shift)
                await session.flush()

            current_tick = await _current_tick(session)
            already_worked = already_worked_this_tick(open_shift, current_tick)
            if not already_worked:
                start_shift_game(open_shift, current_tick)
            level = job_levels.job_level_for_shifts(character.shifts_completed)
            assert character.job_title is not None
            return WorkStartResponse(
                shift_id=open_shift.id,
                job_title=character.job_title,
                level=level.value,
                already_worked_this_tick=already_worked,
            )

    return router


class GoodPrice(BaseModel):
    good_id: str
    name: str
    price: float


class InventoryItem(BaseModel):
    good_id: str
    name: str
    qty: int


async def _inventory_items(
    session: AsyncSession, content: ContentBundle, character_id: int
) -> list[InventoryItem]:
    rows = await market_svc.list_inventory(session, character_id)
    return [
        InventoryItem(good_id=row.good_id, name=content.goods[row.good_id].name, qty=row.qty)
        for row in rows
        if row.good_id in content.goods
    ]


class MarketStatusResponse(BaseModel):
    prices: list[GoodPrice]
    inventory: list[InventoryItem]


class TradeRequest(BaseModel):
    discord_id: int
    good_id: str
    qty: int


class TradeResponse(BaseModel):
    good_id: str
    good_name: str
    qty: int
    total: float
    caught: bool


def build_market_router(
    *, content: ContentBundle, session_factory: async_sessionmaker[AsyncSession] | None
) -> APIRouter:
    """The legal half of the Market tab's REST surface: mirrors `/market
    prices|buy|sell` and `/inventory`."""
    router = APIRouter(prefix="/activity/dashboard/market", tags=["dashboard"])

    @router.get("/{character_id}", response_model=MarketStatusResponse)
    async def market_status(character_id: int, discord_id: int) -> MarketStatusResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            district = content.district(character.current_district_id)
            good_ids = sorted(set(district.produces) | set(district.imports))
            prices = []
            for good_id in good_ids:
                good = content.goods.get(good_id)
                if good is None:
                    continue
                price = await market_svc.get_price(session, district.id, good)
                prices.append(GoodPrice(good_id=good_id, name=good.name, price=price))
            inventory = await _inventory_items(session, content, character_id)
        return MarketStatusResponse(prices=prices, inventory=inventory)

    @router.post("/{character_id}/buy", response_model=TradeResponse)
    async def market_buy(character_id: int, body: TradeRequest) -> TradeResponse:
        return await _trade(character_id, body, side="buy")

    @router.post("/{character_id}/sell", response_model=TradeResponse)
    async def market_sell(character_id: int, body: TradeRequest) -> TradeResponse:
        return await _trade(character_id, body, side="sell")

    async def _trade(character_id: int, body: TradeRequest, *, side: str) -> TradeResponse:
        factory = _require_session_factory(session_factory)
        if body.qty <= 0:
            raise HTTPException(status_code=400, detail="market_invalid_qty")
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            district = content.district(character.current_district_id)
            trade = market_svc.buy if side == "buy" else market_svc.sell
            tick = await _current_tick(session)
            try:
                result = await trade(
                    session,
                    character=character,
                    district=district,
                    goods=content.goods,
                    good_id=body.good_id,
                    qty=body.qty,
                    tick=tick,
                    rng=random.Random(),
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            good_name = content.goods[body.good_id].name
        return TradeResponse(
            good_id=body.good_id,
            good_name=good_name,
            qty=result.qty,
            total=result.total,
            caught=result.caught,
        )

    return router


class BlackMarketStatusResponse(BaseModel):
    trusted: bool
    prices: list[GoodPrice]
    inventory: list[InventoryItem]


def build_blackmarket_router(
    *, content: ContentBundle, session_factory: async_sessionmaker[AsyncSession] | None
) -> APIRouter:
    """The illicit half of the Market tab's REST surface: mirrors
    `/blackmarket prices|buy|sell`. `prices` never checks trust (neither
    does the bot's own command) -- only `buy`/`sell` do, via
    `blackmarket_svc.check_can_trade`."""
    router = APIRouter(prefix="/activity/dashboard/blackmarket", tags=["dashboard"])

    @router.get("/{character_id}", response_model=BlackMarketStatusResponse)
    async def blackmarket_status(character_id: int, discord_id: int) -> BlackMarketStatusResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            district = content.district(character.current_district_id)
            prices = []
            for good_id in district.illicit_produces:
                good = content.goods.get(good_id)
                if good is None:
                    continue
                price = await blackmarket_svc.get_price(session, district.id, good)
                prices.append(GoodPrice(good_id=good_id, name=good.name, price=price))
            trusted = False
            try:
                fence = blackmarket_svc.resolve_fence(district.id, content.npcs)
                await blackmarket_svc.check_can_trade(session, character, fence)
                trusted = True
            except ServiceError:
                trusted = False
            inventory = await _inventory_items(session, content, character_id)
        return BlackMarketStatusResponse(trusted=trusted, prices=prices, inventory=inventory)

    @router.post("/{character_id}/buy", response_model=TradeResponse)
    async def blackmarket_buy(character_id: int, body: TradeRequest) -> TradeResponse:
        return await _trade(character_id, body, side="buy")

    @router.post("/{character_id}/sell", response_model=TradeResponse)
    async def blackmarket_sell(character_id: int, body: TradeRequest) -> TradeResponse:
        return await _trade(character_id, body, side="sell")

    async def _trade(character_id: int, body: TradeRequest, *, side: str) -> TradeResponse:
        factory = _require_session_factory(session_factory)
        if body.qty <= 0:
            raise HTTPException(status_code=400, detail="market_invalid_qty")
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            district = content.district(character.current_district_id)
            trade = blackmarket_svc.buy if side == "buy" else blackmarket_svc.sell
            tick = await _current_tick(session)
            try:
                result = await trade(
                    session,
                    character=character,
                    district=district,
                    goods=content.goods,
                    npcs=content.npcs,
                    good_id=body.good_id,
                    qty=body.qty,
                    tick=tick,
                    rng=random.Random(),
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            good_name = content.goods[body.good_id].name
        return TradeResponse(
            good_id=body.good_id,
            good_name=good_name,
            qty=result.qty,
            total=result.total,
            caught=result.caught,
        )

    return router


class TravelLocationOption(BaseModel):
    id: str
    name: str


class TravelDistrictOption(BaseModel):
    id: int
    name: str


class TravelStatusResponse(BaseModel):
    character_name: str
    current_district_id: int
    current_district_name: str
    location_id: str | None = None
    location_name: str | None = None
    locations: list[TravelLocationOption]
    districts: list[TravelDistrictOption]
    in_transit: bool
    transit_destination_id: int | None = None


class TravelToLocationRequest(BaseModel):
    discord_id: int
    location_id: str


class TravelToLocationResponse(BaseModel):
    character_name: str
    location_name: str


class TravelToDistrictRequest(BaseModel):
    discord_id: int
    destination_id: int


class TravelToDistrictResponse(BaseModel):
    character_name: str
    destination_district_name: str
    transit_ticks: int


def build_travel_router(
    *, content: ContentBundle, session_factory: async_sessionmaker[AsyncSession] | None
) -> APIRouter:
    """The Travel tab's REST surface: mirrors `/travel` (both its
    location and cross-district sub-flows, split into two endpoints the
    same way the command itself is split into `_travel_location`/
    `_travel_district`) and `/where` (folded into the status read below
    rather than a separate endpoint -- the dashboard already has
    `location_id`/`location_name` on every status response)."""
    router = APIRouter(prefix="/activity/dashboard/travel", tags=["dashboard"])

    @router.get("/{character_id}", response_model=TravelStatusResponse)
    async def travel_status(character_id: int, discord_id: int) -> TravelStatusResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            district = content.district(character.current_district_id)
            location = None
            if character.location_id is not None:
                location = travel_svc.resolve_location(district, character.location_id)
            current_tick = await _current_tick(session)
            in_transit = (
                character.in_transit_until_tick is not None
                and character.in_transit_until_tick > current_tick
            )
            return TravelStatusResponse(
                character_name=character.name,
                current_district_id=district.id,
                current_district_name=district.name,
                location_id=location.id if location is not None else None,
                location_name=location.name if location is not None else None,
                locations=[
                    TravelLocationOption(id=loc.id, name=loc.name) for loc in district.locations
                ],
                districts=[
                    TravelDistrictOption(id=d.id, name=d.name)
                    for d in sorted(content.districts.values(), key=lambda d: d.id)
                    if d.id != district.id
                ],
                in_transit=in_transit,
                transit_destination_id=character.transit_destination_id,
            )

    @router.post("/{character_id}/location", response_model=TravelToLocationResponse)
    async def travel_to_location(
        character_id: int, body: TravelToLocationRequest
    ) -> TravelToLocationResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            district = content.district(character.current_district_id)
            try:
                location = travel_svc.resolve_location(district, body.location_id)
                travel_svc.check_can_travel(character=character, location=location)
            except (NotFound, NotAllowed) as exc:
                raise _http_from_service_error(exc) from exc
            character.location_id = location.id
            placed = travel_svc.place(district, location)
            if placed is not None:
                character.x, character.y = placed
            return TravelToLocationResponse(
                character_name=character.name, location_name=location.name
            )

    @router.post("/{character_id}/district", response_model=TravelToDistrictResponse)
    async def travel_to_district(
        character_id: int, body: TravelToDistrictRequest
    ) -> TravelToDistrictResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            current_tick = await _current_tick(session)
            origin_district = content.district(character.current_district_id)
            try:
                travel_svc.check_can_travel_district(
                    character=character,
                    district=origin_district,
                    destination_id=body.destination_id,
                    current_tick=current_tick,
                )
                if not travel_svc.is_free_route(character, origin_district.id, body.destination_id):
                    await travel_svc.spend_transport(session, character)
            except (NotFound, NotAllowed) as exc:
                raise _http_from_service_error(exc) from exc
            destination_district = content.district(body.destination_id)
            character.in_transit_until_tick = current_tick + constants.TRANSIT_TICKS
            character.transit_destination_id = body.destination_id
            if character.current_district_id == character.district_id:
                character.away_since_tick = current_tick
            return TravelToDistrictResponse(
                character_name=character.name,
                destination_district_name=destination_district.name,
                transit_ticks=constants.TRANSIT_TICKS,
            )

    return router


class ResidentSummary(BaseModel):
    name: str
    job_title: str
    location_id: str | None = None
    location_name: str | None = None


class ResidentsResponse(BaseModel):
    district_name: str
    residents: list[ResidentSummary]


class ResidentProfileResponse(BaseModel):
    name: str
    job_title: str
    location_name: str | None = None
    traits: list[str]
    tone: str
    stance: str
    appearance: str
    backstory: str


def build_residents_router(
    *, content: ContentBundle, session_factory: async_sessionmaker[AsyncSession] | None
) -> APIRouter:
    """The Residents tab's REST surface: mirrors `/resident list|where|
    profile`. Unlike the split Discord commands, `list` here already
    includes each resident's current location (an improvement in the same
    spirit as the Crime tab's burgle-targets -- avoiding a second
    request per NPC the dashboard would otherwise need to make)."""
    router = APIRouter(prefix="/activity/dashboard/residents", tags=["dashboard"])

    async def _resolve_npc(session: AsyncSession, character: Character, name: str) -> Npc:
        npc = (
            await session.execute(
                select(Npc).where(
                    Npc.district_id == character.current_district_id, Npc.name == name
                )
            )
        ).scalar_one_or_none()
        if npc is None:
            raise HTTPException(status_code=404, detail="resident_not_found")
        return npc

    @router.get("/{character_id}", response_model=ResidentsResponse)
    async def resident_list(character_id: int, discord_id: int) -> ResidentsResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            district = content.district(character.current_district_id)
            npcs = (
                (
                    await session.execute(
                        select(Npc)
                        .where(Npc.district_id == character.current_district_id)
                        .order_by(Npc.name)
                    )
                )
                .scalars()
                .all()
            )
            all_jobs = await jobs_svc.get_all_jobs(session, content)
            locations_by_id = {loc.id: loc.name for loc in district.locations}
            residents = []
            for npc in npcs:
                job = all_jobs.get(npc.job_id) if npc.job_id else None
                residents.append(
                    ResidentSummary(
                        name=npc.name,
                        job_title=job.title if job else "Unemployed",
                        location_id=npc.location_id,
                        location_name=(
                            locations_by_id.get(npc.location_id)
                            if npc.location_id is not None
                            else None
                        ),
                    )
                )
        return ResidentsResponse(district_name=district.name, residents=residents)

    @router.get("/{character_id}/{resident_name}", response_model=ResidentProfileResponse)
    async def resident_profile(
        character_id: int, resident_name: str, discord_id: int
    ) -> ResidentProfileResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            npc = await _resolve_npc(session, character, resident_name)
            job = await jobs_svc.get_job(session, content, npc.job_id) if npc.job_id else None
            job_name = job.title if job else "Unemployed"

            key = relationship_key(
                (OwnerKind.CHARACTER.value, str(character.id)), (OwnerKind.NPC.value, npc.id)
            )
            relationship = await session.get(RelationshipRow, key)
            stance = relationship.stance if relationship is not None else "stranger"

            district = content.district(character.current_district_id)
            location = next((loc for loc in district.locations if loc.id == npc.location_id), None)
            tone = npc.speech_style.get("tone", "unknown") if npc.speech_style else "unknown"
            authored = content.npcs.get(npc.id)
            appearance = npc.appearance_override or (authored.appearance if authored else "")
            backstory = npc.backstory_override or (authored.backstory if authored else "")
        return ResidentProfileResponse(
            name=npc.name,
            job_title=job_name,
            location_name=location.name if location is not None else None,
            traits=list(npc.traits),
            tone=tone,
            stance=stance,
            appearance=appearance,
            backstory=backstory,
        )

    return router


class SocialStatusResponse(BaseModel):
    in_scene: bool
    scene_kind: str | None = None
    scene_title: str | None = None
    location_name: str | None = None
    participant_character_names: list[str] = []
    participant_npc_names: list[str] = []
    discord_thread_url: str | None = None


def build_social_router(
    *,
    content: ContentBundle,
    session_factory: async_sessionmaker[AsyncSession] | None,
    discord_guild_id: int,
) -> APIRouter:
    """The Social tab's REST surface -- read-only + deep link, per the
    user's own choice on `/talk`/`/engage`/`/scene`: building full
    interactivity for these would need a dashboard -> Redis ->
    `panem_bot` relay (only the bot process holds a token and can
    post/create Discord threads), which is out of scope here. This just
    shows a character's current open scene (an `/engage`/`/talk`
    engagement or a `/scene`) and a "Continue in Discord" link back to
    the real thread -- no write actions."""
    router = APIRouter(prefix="/activity/dashboard/social", tags=["dashboard"])

    @router.get("/{character_id}", response_model=SocialStatusResponse)
    async def social_status(character_id: int, discord_id: int) -> SocialStatusResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            open_scenes = (
                (await session.execute(select(Scene).where(Scene.status == SceneStatus.OPEN.value)))
                .scalars()
                .all()
            )
            scene = next(
                (s for s in open_scenes if character.id in s.participants.get("characters", [])),
                None,
            )
            if scene is None:
                return SocialStatusResponse(in_scene=False)

            char_rows = (
                await session.execute(
                    select(Character.name).where(
                        Character.id.in_(scene.participants.get("characters", []))
                    )
                )
            ).scalars()
            npc_rows = (
                await session.execute(
                    select(Npc.name).where(Npc.id.in_(scene.participants.get("npcs", [])))
                )
            ).scalars()
            district = content.district(scene.district_id)
            location = next(
                (loc for loc in district.locations if loc.id == scene.location_id), None
            )
            thread_url = (
                f"https://discord.com/channels/{discord_guild_id}/{scene.thread_id}"
                if discord_guild_id
                else None
            )
            return SocialStatusResponse(
                in_scene=True,
                scene_kind=scene.kind,
                scene_title=scene.title,
                location_name=location.name if location is not None else None,
                participant_character_names=sorted(char_rows),
                participant_npc_names=sorted(npc_rows),
                discord_thread_url=thread_url,
            )

    return router


class HousingListing(BaseModel):
    id: int
    kind: str
    tier: str
    complex_id: str | None = None
    price: float
    price_label: str  # "sale" | "night" | "day_rent"


class HousingOwnedProperty(BaseModel):
    id: int
    kind: str
    tier: str
    district_id: int
    district_name: str
    for_sale: bool
    asking_price: float | None = None
    mortgage_principal: float
    mortgage_payment: float
    has_open_auction: bool


class HousingStatusResponse(BaseModel):
    character_name: str
    fatigue: float
    home_property_id: int | None = None
    home_kind: str | None = None
    home_district_id: int | None = None
    home_district_name: str | None = None
    owned: list[HousingOwnedProperty]
    listings: list[HousingListing]


class HousingBuyRequest(BaseModel):
    discord_id: int
    financed: bool = False


class HousingBuyResponse(BaseModel):
    property_id: int
    kind: str
    price: int
    financed: bool
    down_payment: int | None = None
    payment: int | None = None


class HousingBuyComplexRequest(BaseModel):
    discord_id: int
    complex_id: str


class HousingBuyComplexResponse(BaseModel):
    complex_id: str
    units: int
    price: int


class HousingRefinanceRequest(BaseModel):
    discord_id: int
    amount: float


class HousingRefinanceResponse(BaseModel):
    property_id: int
    amount: int
    payment: int


class HousingSellRequest(BaseModel):
    discord_id: int
    price: float | None = None


class HousingSellResponse(BaseModel):
    property_id: int
    for_sale: bool
    price: int | None = None


class HousingRentOutRequest(BaseModel):
    discord_id: int
    price: float


class HousingRentOutResponse(BaseModel):
    property_id: int
    price: int


class HousingAuctionStartRequest(BaseModel):
    discord_id: int
    minimum_bid: float


class HousingAuctionStartResponse(BaseModel):
    property_id: int
    minimum_bid: int


class HousingAuctionBidRequest(BaseModel):
    discord_id: int
    amount: float


class HousingAuctionBidResponse(BaseModel):
    property_id: int
    amount: int


class HousingRentRequest(BaseModel):
    discord_id: int


class HousingRentResponse(BaseModel):
    property_id: int
    price: int


class HousingMoveOutRequest(BaseModel):
    discord_id: int


class HousingMoveOutResponse(BaseModel):
    character_name: str


class HousingInnStayRequest(BaseModel):
    discord_id: int


class HousingInnStayResponse(BaseModel):
    property_id: int
    price: int
    fatigue: float


class HousingSleepRequest(BaseModel):
    discord_id: int
    ticks: int | None = None


class HousingSleepResponse(BaseModel):
    ticks: int
    restored: float
    fatigue: float


def build_housing_router(
    *, content: ContentBundle, session_factory: async_sessionmaker[AsyncSession] | None
) -> APIRouter:
    """The Housing tab's REST surface: mirrors every `/housing` subcommand
    plus `/sleep`. Unlike the split Discord commands (each its own
    `character`/`property_id` pair typed by hand), every write here
    targets `{property_id}` in the URL path the same way the Jail/Crime/
    Market routers already do -- `property_id` is exactly the id
    `/activity/dashboard/housing/{character_id}`'s own listing already
    returns, so the dashboard never needs a separate autocomplete step."""
    router = APIRouter(prefix="/activity/dashboard/housing", tags=["dashboard"])

    def _listing(property_: Property, buyer: Character) -> HousingListing:
        price = round(housing_svc.quoted_price(property_, buyer), 2)
        label = "sale"
        if property_.kind == PropertyKind.INN.value:
            label = "night"
        elif property_.kind == PropertyKind.APARTMENT.value:
            label = "day_rent"
        return HousingListing(
            id=property_.id,
            kind=property_.kind,
            tier=property_.tier,
            complex_id=property_.complex_id,
            price=price,
            price_label=label,
        )

    async def _owned_property(
        session: AsyncSession, property_id: int, *, discord_id: int, character_id: int
    ) -> tuple[Character, Property]:
        character = await _resolve_owned_character(
            session, discord_id=discord_id, character_id=character_id
        )
        property_ = await session.get(Property, property_id)
        if property_ is None:
            raise HTTPException(status_code=404, detail="housing_not_found")
        return character, property_

    @router.get("/{character_id}", response_model=HousingStatusResponse)
    async def housing_status(character_id: int, discord_id: int) -> HousingStatusResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            home_property = None
            if character.housing_property_id is not None:
                home_property = await session.get(Property, character.housing_property_id)

            owned_rows = (
                (
                    await session.execute(
                        select(Property).where(
                            Property.owner_kind == OwnerKind.CHARACTER.value,
                            Property.owner_id == character.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            open_auction_property_ids = set(
                (
                    await session.execute(
                        select(PropertyAuction.property_id).where(PropertyAuction.status == "open")
                    )
                )
                .scalars()
                .all()
            )
            owned = [
                HousingOwnedProperty(
                    id=p.id,
                    kind=p.kind,
                    tier=p.tier,
                    district_id=p.district_id,
                    district_name=content.districts[p.district_id].name,
                    for_sale=p.for_sale,
                    asking_price=p.asking_price,
                    mortgage_principal=round(p.mortgage_principal, 2),
                    mortgage_payment=round(p.mortgage_payment, 2),
                    has_open_auction=p.id in open_auction_property_ids,
                )
                for p in sorted(owned_rows, key=lambda p: p.id)
            ]

            leased_unit_ids = select(ApartmentLease.property_id)
            rows = (
                (
                    await session.execute(
                        select(Property).where(
                            Property.district_id == character.current_district_id,
                            (
                                (Property.kind != PropertyKind.APARTMENT.value)
                                & Property.for_sale.is_(True)
                            )
                            | (
                                (Property.kind == PropertyKind.APARTMENT.value)
                                & Property.id.not_in(leased_unit_ids)
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            listings = [
                _listing(p, character) for p in sorted(rows, key=lambda p: (p.kind, p.tier, p.id))
            ]
            return HousingStatusResponse(
                character_name=character.name,
                fatigue=round(character.fatigue, 1),
                home_property_id=home_property.id if home_property is not None else None,
                home_kind=home_property.kind if home_property is not None else None,
                home_district_id=(home_property.district_id if home_property is not None else None),
                home_district_name=(
                    content.districts[home_property.district_id].name
                    if home_property is not None
                    else None
                ),
                owned=owned,
                listings=listings,
            )

    @router.post("/{character_id}/{property_id}/buy", response_model=HousingBuyResponse)
    async def buy(
        character_id: int, property_id: int, body: HousingBuyRequest
    ) -> HousingBuyResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character, property_ = await _owned_property(
                session, property_id, discord_id=body.discord_id, character_id=character_id
            )
            try:
                housing_svc.check_can_buy_property(character=character, property_=property_)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc

            price = round(housing_svc.quoted_price(property_, character))
            current_tick = await _current_tick(session)
            is_financed = body.financed and property_.kind == PropertyKind.HOUSE.value

            down_payment = None
            payment = None
            if is_financed:
                terms = housing_svc.financed_purchase_terms(price)
                down_payment = round(terms.down_payment)
                if character.money < down_payment:
                    raise HTTPException(status_code=400, detail="housing_down_payment_too_much")
                character.money -= down_payment
                property_.mortgage_principal = terms.principal
                property_.mortgage_payment = terms.payment
                property_.mortgage_next_due_tick = (
                    current_tick + constants.MORTGAGE_PAYMENT_INTERVAL_TICKS
                )
                property_.mortgage_missed_payments = 0
                payment = round(property_.mortgage_payment)
            else:
                if character.money < price:
                    raise HTTPException(status_code=400, detail="housing_insufficient_funds")
                character.money -= price

            property_.owner_kind = OwnerKind.CHARACTER.value
            property_.owner_id = character.id
            property_.for_sale = False
            property_.asking_price = None
            if property_.kind == PropertyKind.HOUSE.value:
                character.housing_property_id = property_.id
            elif property_.kind == PropertyKind.INN.value:
                property_.mortgage_payment = constants.INN_DAILY_MAINTENANCE_COST
                property_.mortgage_next_due_tick = (
                    current_tick + constants.MORTGAGE_PAYMENT_INTERVAL_TICKS
                )
                property_.mortgage_missed_payments = 0

            return HousingBuyResponse(
                property_id=property_.id,
                kind=property_.kind,
                price=price,
                financed=is_financed,
                down_payment=down_payment,
                payment=payment,
            )

    @router.post(
        "/{character_id}/complex/{complex_id}/buy", response_model=HousingBuyComplexResponse
    )
    async def buy_complex(
        character_id: int, complex_id: str, body: HousingBuyComplexRequest
    ) -> HousingBuyComplexResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            units = (
                (await session.execute(select(Property).where(Property.complex_id == complex_id)))
                .scalars()
                .all()
            )
            try:
                housing_svc.check_can_buy_complex(character=character, units=list(units))
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc

            price = round(housing_svc.complex_purchase_price(list(units)))
            if character.money < price:
                raise HTTPException(status_code=400, detail="housing_insufficient_funds")
            character.money -= price
            for unit in units:
                unit.owner_kind = OwnerKind.CHARACTER.value
                unit.owner_id = character.id
            return HousingBuyComplexResponse(complex_id=complex_id, units=len(units), price=price)

    @router.post("/{character_id}/{property_id}/refinance", response_model=HousingRefinanceResponse)
    async def refinance(
        character_id: int, property_id: int, body: HousingRefinanceRequest
    ) -> HousingRefinanceResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character, property_ = await _owned_property(
                session, property_id, discord_id=body.discord_id, character_id=character_id
            )
            try:
                housing_svc.check_can_refinance(
                    character=character, property_=property_, amount=body.amount
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc

            current_tick = await _current_tick(session)
            payment = round(housing_svc.apply_refinance(property_, body.amount, tick=current_tick))
            character.money += round(body.amount)
            return HousingRefinanceResponse(
                property_id=property_.id, amount=round(body.amount), payment=payment
            )

    @router.post("/{character_id}/{property_id}/sell", response_model=HousingSellResponse)
    async def sell(
        character_id: int, property_id: int, body: HousingSellRequest
    ) -> HousingSellResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character, property_ = await _owned_property(
                session, property_id, discord_id=body.discord_id, character_id=character_id
            )
            try:
                housing_svc.check_owns_property(character=character, property_=property_)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc

            if body.price is None:
                property_.for_sale = False
                property_.asking_price = None
            else:
                property_.for_sale = True
                property_.asking_price = body.price
            return HousingSellResponse(
                property_id=property_.id,
                for_sale=property_.for_sale,
                price=round(body.price) if body.price is not None else None,
            )

    @router.post("/{character_id}/{property_id}/rent-out", response_model=HousingRentOutResponse)
    async def rent_out(
        character_id: int, property_id: int, body: HousingRentOutRequest
    ) -> HousingRentOutResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character, property_ = await _owned_property(
                session, property_id, discord_id=body.discord_id, character_id=character_id
            )
            if property_.kind != PropertyKind.APARTMENT.value:
                raise HTTPException(status_code=400, detail="housing_not_an_apartment")
            try:
                housing_svc.check_owns_property(character=character, property_=property_)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            existing_lease = (
                await session.execute(
                    select(ApartmentLease).where(ApartmentLease.property_id == property_id)
                )
            ).scalar_one_or_none()
            if existing_lease is not None:
                raise HTTPException(status_code=400, detail="housing_unit_already_leased")

            property_.asking_price = body.price
            return HousingRentOutResponse(property_id=property_.id, price=round(body.price))

    @router.post(
        "/{character_id}/{property_id}/auction-start",
        response_model=HousingAuctionStartResponse,
    )
    async def auction_start(
        character_id: int, property_id: int, body: HousingAuctionStartRequest
    ) -> HousingAuctionStartResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character, property_ = await _owned_property(
                session, property_id, discord_id=body.discord_id, character_id=character_id
            )
            existing_auction = (
                await session.execute(
                    select(PropertyAuction).where(
                        PropertyAuction.property_id == property_id,
                        PropertyAuction.status == "open",
                    )
                )
            ).scalar_one_or_none()
            try:
                housing_svc.check_can_start_auction(
                    character=character, property_=property_, existing_auction=existing_auction
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc

            current_tick = await _current_tick(session)
            session.add(
                PropertyAuction(
                    property_id=property_.id,
                    seller_kind=OwnerKind.CHARACTER.value,
                    seller_id=character.id,
                    minimum_bid=body.minimum_bid,
                    ends_at_tick=current_tick + constants.AUCTION_DURATION_TICKS_DEFAULT,
                    status="open",
                )
            )
            property_.for_sale = False
            return HousingAuctionStartResponse(
                property_id=property_.id, minimum_bid=round(body.minimum_bid)
            )

    @router.post(
        "/{character_id}/{property_id}/auction-bid", response_model=HousingAuctionBidResponse
    )
    async def auction_bid(
        character_id: int, property_id: int, body: HousingAuctionBidRequest
    ) -> HousingAuctionBidResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            auction = (
                await session.execute(
                    select(PropertyAuction).where(
                        PropertyAuction.property_id == property_id,
                        PropertyAuction.status == "open",
                    )
                )
            ).scalar_one_or_none()
            if auction is None:
                raise HTTPException(status_code=404, detail="housing_auction_not_found")
            try:
                housing_svc.check_can_bid(character=character, auction=auction, amount=body.amount)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc

            auction.current_bid = body.amount
            auction.current_bidder_id = character.id
            return HousingAuctionBidResponse(property_id=property_id, amount=round(body.amount))

    @router.post("/{character_id}/{property_id}/rent", response_model=HousingRentResponse)
    async def rent(
        character_id: int, property_id: int, body: HousingRentRequest
    ) -> HousingRentResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character, property_ = await _owned_property(
                session, property_id, discord_id=body.discord_id, character_id=character_id
            )
            existing_lease = (
                await session.execute(
                    select(ApartmentLease).where(ApartmentLease.property_id == property_id)
                )
            ).scalar_one_or_none()
            try:
                housing_svc.check_can_rent(
                    character=character, property_=property_, existing_lease=existing_lease
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc

            landlord = (
                await session.get(Character, property_.owner_id)
                if property_.owner_kind == OwnerKind.CHARACTER.value
                else None
            )
            rent_price = round(housing_svc.quoted_price(property_, character, seller=landlord))
            current_tick = await _current_tick(session)
            session.add(
                ApartmentLease(
                    property_id=property_.id,
                    tenant_character_id=character.id,
                    rent_price=rent_price,
                    started_tick=current_tick,
                    next_rent_due_tick=current_tick + constants.TICKS_PER_DAY,
                )
            )
            character.housing_property_id = property_.id
            return HousingRentResponse(property_id=property_.id, price=rent_price)

    @router.post("/{character_id}/move-out", response_model=HousingMoveOutResponse)
    async def move_out(character_id: int, body: HousingMoveOutRequest) -> HousingMoveOutResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            if character.housing_property_id is None:
                raise HTTPException(status_code=400, detail="housing_no_home")
            lease = (
                await session.execute(
                    select(ApartmentLease).where(ApartmentLease.tenant_character_id == character.id)
                )
            ).scalar_one_or_none()
            if lease is None:
                raise HTTPException(status_code=400, detail="housing_not_a_tenant")
            await session.delete(lease)
            character.housing_property_id = None
            return HousingMoveOutResponse(character_name=character.name)

    @router.post("/{character_id}/{property_id}/inn-stay", response_model=HousingInnStayResponse)
    async def inn_stay(
        character_id: int, property_id: int, body: HousingInnStayRequest
    ) -> HousingInnStayResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character, inn = await _owned_property(
                session, property_id, discord_id=body.discord_id, character_id=character_id
            )
            if inn.kind != PropertyKind.INN.value:
                raise HTTPException(status_code=400, detail="housing_not_an_inn")

            owner = (
                await session.get(Character, inn.owner_id)
                if inn.owner_kind == OwnerKind.CHARACTER.value
                else None
            )
            price = round(housing_svc.quoted_price(inn, character, seller=owner))
            if character.money < price:
                raise HTTPException(status_code=400, detail="housing_insufficient_funds")

            character.money -= price
            if owner is not None:
                owner.money += price
            housing_svc.apply_fatigue_restoration(character, simtime.TICKS_PER_PHASE, has_bed=True)
            character.hunger = max(
                constants.HUNGER_MIN, character.hunger - constants.HUNGER_DECREASE_MET
            )
            return HousingInnStayResponse(
                property_id=inn.id, price=price, fatigue=round(character.fatigue, 1)
            )

    @router.post("/{character_id}/sleep", response_model=HousingSleepResponse)
    async def sleep(character_id: int, body: HousingSleepRequest) -> HousingSleepResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            current_tick = await _current_tick(session)
            _tick, phase, _day, _month = simtime.current(current_tick)
            try:
                housing_svc.check_can_sleep(phase)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc

            max_ticks = simtime.ticks_remaining_in_phase(current_tick)
            sleep_ticks = (
                max(1, min(body.ticks, max_ticks)) if body.ticks is not None else max_ticks
            )
            restored = housing_svc.apply_fatigue_restoration(
                character, sleep_ticks, has_bed=housing_svc.has_a_bed(character)
            )
            return HousingSleepResponse(
                ticks=sleep_ticks, restored=round(restored, 1), fatigue=round(character.fatigue, 1)
            )

    return router
