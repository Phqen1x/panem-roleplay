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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from panem_shared import characters as characters_svc
from panem_shared.content.loader import ContentBundle
from panem_shared.db.models import Character, User
from panem_shared.db.session import session_scope
from panem_shared.enums import CharacterStatus, DayPhase
from panem_shared.errors import NotFound, ServiceError


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
    current_district_id: int
    job_title: str | None = None
    shift_phase: str | None = None
    job_is_illicit: bool
    money: int
    jailed_until_tick: int | None = None


def _character_detail(character: Character) -> CharacterDetail:
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
        current_district_id=character.current_district_id,
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
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            user_row = await session.execute(select(User).where(User.discord_id == discord_id))
            user = user_row.scalar_one_or_none()
            if user is None:
                return MyCharactersResponse(characters=[])
            rows = await session.execute(
                select(Character).where(Character.user_id == user.id).order_by(Character.id)
            )
            return MyCharactersResponse(
                characters=[_character_detail(c) for c in rows.scalars().all()]
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
            return _character_detail(character)

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
            return _character_detail(character)

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
            return _character_detail(character)

    return router
