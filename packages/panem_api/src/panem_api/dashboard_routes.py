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

from panem_shared.db.models import Character, User
from panem_shared.db.session import session_scope
from panem_shared.enums import CharacterStatus


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
    """Every dashboard action/read route (from M2 onward) calls this first --
    the shared ownership check described in this module's docstring. Raises
    404 rather than 403 for a mismatched owner, same as an unknown id: this
    process has no session/login of its own to distinguish "wrong owner"
    from "doesn't exist" in a way that's worth telling a client apart."""
    character = await session.get(Character, character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="No such character")
    user = await session.get(User, character.user_id)
    if user is None or user.discord_id != discord_id:
        raise HTTPException(status_code=404, detail="No such character")
    return character


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
