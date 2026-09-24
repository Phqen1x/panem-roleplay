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

import datetime as dt
import json
import random
import secrets
from pathlib import Path

import redis.asyncio as redis
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from panem_api import discord_staff
from panem_shared import affliction_types as affliction_types_svc
from panem_shared import avatars as avatars_svc
from panem_shared import blackmarket as blackmarket_svc
from panem_shared import characters as characters_svc
from panem_shared import constants, job_levels, simtime
from panem_shared import crime_log as crime_log_svc
from panem_shared import district_lore as district_lore_svc
from panem_shared import housing as housing_svc
from panem_shared import jail as jail_svc
from panem_shared import jobs as jobs_svc
from panem_shared import layers as layers_svc
from panem_shared import market as market_svc
from panem_shared import pay as pay_svc
from panem_shared import poaching as poaching_svc
from panem_shared import rp_modes as rp_modes_svc
from panem_shared import stealing as stealing_svc
from panem_shared import sustenance as sustenance_svc
from panem_shared import theme as theme_svc
from panem_shared import trades as trades_svc
from panem_shared import travel as travel_svc
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import Location
from panem_shared.db.models import (
    AfflictionType,
    ApartmentLease,
    Character,
    CharacterAffliction,
    DistrictLore,
    DistrictLorePerson,
    LayerCategory,
    Npc,
    Property,
    PropertyAuction,
    RelationshipRow,
    Scene,
    Shift,
    StaffAction,
    ThemeProfile,
    Trade,
    User,
    WorldClock,
)
from panem_shared.db.session import session_scope
from panem_shared.enums import (
    CharacterStatus,
    DayPhase,
    Gender,
    LocationKind,
    OwnerKind,
    Position,
    PropertyKind,
    RpMode,
    SceneStatus,
    TradeStatus,
)
from panem_shared.errors import NotAllowed, NotFound, ServiceError
from panem_shared.redis_keys import (
    CHARACTER_PENDING_CHANNEL,
    CRIME_ATTEMPT_TTL_S,
    crime_attempt_key,
)
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
    appearance_layers: dict[str, int]
    district_id: int
    current_district_id: int
    district_name: str = ""
    current_district_name: str = ""
    money: int
    jailed_until_tick: int | None = None
    jailed: bool = False
    # Only ever non-null for a currently donor account (see `_theme_for_
    # user`'s old docstring, now `_resolve_theme`) -- lets the theme
    # picker's profile list show which characters a profile is assigned to
    # without a separate round trip per character.
    theme_profile_id: int | None = None


class IdentifyRequest(BaseModel):
    discord_id: int


class DashboardTheme(BaseModel):
    """The four CSS custom properties `static/style.css`'s `:root` defines
    (`--bg`, `--accent`, `--panel`, `--text`), resolved for a particular
    account (and, optionally, one of its characters) -- see `panem_shared.
    theme`'s module docstring for the donor gate and the named-profile
    system this sits behind. `profile_id` names which `ThemeProfile`
    produced these values (`None` for the plain default, e.g. a non-donor,
    a lapsed donor, or an account/character that's never had one
    assigned/activated) -- the frontend uses it to highlight the active
    profile in its list without a second lookup."""

    background_hex: str = theme_svc.DEFAULT_BACKGROUND_HEX
    accent_hex: str = theme_svc.DEFAULT_ACCENT_HEX
    panel_hex: str = theme_svc.DEFAULT_PANEL_HEX
    text_hex: str = theme_svc.DEFAULT_TEXT_HEX
    profile_id: int | None = None


class ThemeProfileSummary(BaseModel):
    id: int
    name: str
    background_hex: str
    accent_hex: str
    panel_hex: str
    text_hex: str


class IdentifyResponse(BaseModel):
    characters: list[DashboardCharacterSummary]
    is_staff: bool = False
    is_donor: bool = False
    # Always present, even for a non-donor -- `app.js` applies this
    # unconditionally on load so the dashboard renders a saved theme (a
    # currently-donor account's general profile) or the plain default
    # (everyone else's) the same way, with no special-casing at the call
    # site. This is the *account-level* theme (no character context) --
    # once a character is selected, the frontend re-resolves via `GET
    # .../theme/resolve?character_id=` for that character's own override.
    theme: DashboardTheme = Field(default_factory=DashboardTheme)
    # Empty for a non-donor/lapsed donor, same surfacing rule as `theme`.
    theme_profiles: list[ThemeProfileSummary] = Field(default_factory=list)


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


def _theme_profile_summary(profile: ThemeProfile) -> ThemeProfileSummary:
    return ThemeProfileSummary(
        id=profile.id,
        name=profile.name,
        background_hex=profile.background_hex,
        accent_hex=profile.accent_hex,
        panel_hex=profile.panel_hex,
        text_hex=profile.text_hex,
    )


def _theme_for_profile(profile: ThemeProfile | None) -> DashboardTheme:
    if profile is None:
        return DashboardTheme()
    return DashboardTheme(
        background_hex=profile.background_hex,
        accent_hex=profile.accent_hex,
        panel_hex=profile.panel_hex,
        text_hex=profile.text_hex,
        profile_id=profile.id,
    )


async def _resolve_theme(
    session: AsyncSession, *, user: User, character: Character | None
) -> DashboardTheme:
    """The effective theme for `user`, optionally narrowed to one of their
    characters -- assumes the donor gate has *already* been checked by the
    caller (every call site below either just verified `is_donor` or
    returns the plain default itself first when it hasn't). Resolution
    order: `character`'s own assigned profile (if a character was given
    and it has one), else the account's general active profile, else the
    plain default -- the same "character overrides general" shape
    `Character.theme_profile_id`'s docstring describes."""
    profile_id = None
    if character is not None and character.theme_profile_id is not None:
        profile_id = character.theme_profile_id
    elif user.active_theme_profile_id is not None:
        profile_id = user.active_theme_profile_id
    if profile_id is None:
        return DashboardTheme()
    profile = await session.get(ThemeProfile, profile_id)
    return _theme_for_profile(profile)


def build_identify_router(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None,
    content: ContentBundle | None = None,
    discord_token: str = "",
    discord_guild_id: int = 0,
    staff_role_id: int = 0,
    donor_role_ids: frozenset[int] = frozenset(),
) -> APIRouter:
    router = APIRouter(prefix="/activity/dashboard", tags=["dashboard"])

    @router.post("/identify", response_model=IdentifyResponse)
    async def identify(body: IdentifyRequest) -> IdentifyResponse:
        """The dashboard's entry point: resolves the Discord user id `app.js`
        got from the embedded-app-sdk handshake (or the manual preview-mode
        fallback) into that player's approved characters, so the shell can
        offer a character picker the same way each slash command's
        `character:` autocomplete does today. An unknown discord_id (no
        `User` row yet -- this player has never run a command that created
        one) isn't an error: it just means no characters yet (and no saved
        theme -- there's no row to have one).

        `is_staff`/`is_donor` are only used to decide whether `app.js` shows
        the Staff tab's nav button / the color-wheel theme button --
        cosmetic, not a security boundary. The actual enforcement is
        `build_staff_router`/`build_theme_router` re-checking `discord_
        staff.fetch_is_staff`/`fetch_has_any_role` themselves on every
        write, the same "never trust the client with a privilege decision"
        posture `_resolve_owned_character` already applies to character
        ownership."""
        is_staff = await discord_staff.fetch_is_staff(
            body.discord_id,
            bot_token=discord_token,
            guild_id=discord_guild_id,
            staff_role_id=staff_role_id,
        )
        is_donor = await discord_staff.fetch_has_any_role(
            body.discord_id,
            bot_token=discord_token,
            guild_id=discord_guild_id,
            role_ids=donor_role_ids,
        )
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            user_row = await session.execute(select(User).where(User.discord_id == body.discord_id))
            user = user_row.scalar_one_or_none()
            if user is None:
                return IdentifyResponse(characters=[], is_staff=is_staff, is_donor=is_donor)
            rows = await session.execute(
                select(Character)
                .where(
                    Character.user_id == user.id, Character.status == CharacterStatus.APPROVED.value
                )
                .order_by(Character.id)
            )
            characters = rows.scalars().all()
            current_tick = await _current_tick(session)
            theme_profiles: list[ThemeProfileSummary] = []
            if is_donor:
                profile_rows = await session.execute(
                    select(ThemeProfile)
                    .where(ThemeProfile.user_id == user.id)
                    .order_by(ThemeProfile.id)
                )
                theme_profiles = [
                    _theme_profile_summary(p) for p in profile_rows.scalars().all()
                ]
            return IdentifyResponse(
                characters=[
                    DashboardCharacterSummary(
                        id=c.id,
                        name=c.name,
                        avatar_url=c.avatar_url,
                        appearance_layers=layers_svc.sanitize_stored_selection(
                            c.appearance_layers
                        ),
                        district_id=c.district_id,
                        current_district_id=c.current_district_id,
                        district_name=(
                            content.districts[c.district_id].name
                            if content is not None and c.district_id in content.districts
                            else f"District {c.district_id}"
                        ),
                        current_district_name=(
                            content.districts[c.current_district_id].name
                            if content is not None and c.current_district_id in content.districts
                            else f"District {c.current_district_id}"
                        ),
                        money=c.money,
                        jailed_until_tick=c.jailed_until_tick,
                        jailed=c.jailed_until_tick is not None
                        and c.jailed_until_tick > current_tick,
                        theme_profile_id=c.theme_profile_id if is_donor else None,
                    )
                    for c in characters
                ],
                is_staff=is_staff,
                is_donor=is_donor,
                theme=(
                    await _resolve_theme(session, user=user, character=None)
                    if is_donor
                    else DashboardTheme()
                ),
                theme_profiles=theme_profiles,
            )

    return router


MAX_THEME_PROFILES_PER_USER = 20


class ThemeProfileCreateRequest(BaseModel):
    discord_id: int
    name: str
    background_hex: str
    accent_hex: str
    panel_hex: str
    text_hex: str


class ThemeProfileUpdateRequest(BaseModel):
    discord_id: int
    name: str | None = None
    background_hex: str | None = None
    accent_hex: str | None = None
    panel_hex: str | None = None
    text_hex: str | None = None


class ThemeProfileActionRequest(BaseModel):
    discord_id: int


class ThemeProfileAssignRequest(BaseModel):
    discord_id: int
    character_id: int


class ThemeUnassignRequest(BaseModel):
    discord_id: int
    character_id: int


class ThemeResolveRequest(BaseModel):
    discord_id: int
    character_id: int | None = None


class ThemeProfileListResponse(BaseModel):
    profiles: list[ThemeProfileSummary]
    active_profile_id: int | None = None


class ResetThemeRequest(BaseModel):
    discord_id: int
    # When given, resets that character's own assignment (falls back to
    # the account's general profile); omitted resets the account's general
    # profile itself (falls back to the plain default). Either way this
    # never deletes a saved profile -- just clears one FK.
    character_id: int | None = None


def build_theme_router(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None,
    discord_token: str = "",
    discord_guild_id: int = 0,
    donor_role_ids: frozenset[int] = frozenset(),
) -> APIRouter:
    """The color-wheel popup's REST surface -- donor-only for every write
    (`panem_shared.theme`'s module docstring). Every write route here
    re-checks `discord_staff.fetch_has_any_role` itself rather than
    trusting a client-supplied flag (`/identify`'s `is_donor` is cosmetic,
    only used to decide whether `app.js` shows the button at all) -- the
    same posture `build_staff_router`'s `_require_staff` already uses for
    the Staff tab. The one read route (`GET .../resolve`) never 403s --
    like the old single-color `_theme_for_user`, a non-donor/lapsed-donor
    request just gets the plain default back."""
    router = APIRouter(prefix="/activity/dashboard/theme", tags=["dashboard"])

    async def _is_donor(discord_id: int) -> bool:
        return await discord_staff.fetch_has_any_role(
            discord_id,
            bot_token=discord_token,
            guild_id=discord_guild_id,
            role_ids=donor_role_ids,
        )

    async def _require_donor(discord_id: int) -> None:
        if not await _is_donor(discord_id):
            raise HTTPException(status_code=403, detail="donor_only")

    async def _resolve_owned_profile(
        session: AsyncSession, *, discord_id: int, profile_id: int
    ) -> tuple[User, ThemeProfile]:
        """Mirrors `_resolve_owned_character`'s trust model: 404 (not 403)
        for a profile that exists but belongs to someone else, same as an
        unknown id -- there's no session/login here to make that
        distinction worth exposing to the caller."""
        user_row = await session.execute(select(User).where(User.discord_id == discord_id))
        user = user_row.scalar_one_or_none()
        profile = await session.get(ThemeProfile, profile_id) if user is not None else None
        if user is None or profile is None or profile.user_id != user.id:
            raise HTTPException(status_code=404, detail="No such theme profile")
        return user, profile

    async def _profiles_list_response(
        session: AsyncSession, user: User
    ) -> ThemeProfileListResponse:
        rows = await session.execute(
            select(ThemeProfile).where(ThemeProfile.user_id == user.id).order_by(ThemeProfile.id)
        )
        return ThemeProfileListResponse(
            profiles=[_theme_profile_summary(p) for p in rows.scalars().all()],
            active_profile_id=user.active_theme_profile_id,
        )

    # POST (not GET), and `/resolve` below too, deliberately: both need a
    # `discord_id` and both go through `_require_donor` -> `discord_staff.
    # fetch_has_any_role`, which does its own outbound `httpx.AsyncClient.
    # get(...)` to Discord's REST API -- a GET route here, calling out to
    # another GET under the hood, is awkward to unit-test with the
    # `patch.object(httpx.AsyncClient, "get", ...)` convention this test
    # suite already uses for that Discord call, since it patches the
    # method for every `AsyncClient` instance including the test's own.
    # `POST /activity/dashboard/identify` (a read) already sets this same
    # precedent for the same reason.
    @router.post("/profiles/list", response_model=ThemeProfileListResponse)
    async def list_profiles(body: ThemeProfileActionRequest) -> ThemeProfileListResponse:
        await _require_donor(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            user_row = await session.execute(
                select(User).where(User.discord_id == body.discord_id)
            )
            user = user_row.scalar_one_or_none()
            if user is None:
                return ThemeProfileListResponse(profiles=[], active_profile_id=None)
            return await _profiles_list_response(session, user)

    @router.post("/profiles", response_model=ThemeProfileSummary)
    async def create_profile(body: ThemeProfileCreateRequest) -> ThemeProfileSummary:
        await _require_donor(body.discord_id)
        try:
            name = theme_svc.validate_profile_name(body.name)
            background_hex = theme_svc.validate_hex_color(body.background_hex)
            accent_hex = theme_svc.validate_hex_color(body.accent_hex)
            panel_hex = theme_svc.validate_hex_color(body.panel_hex)
            text_hex = theme_svc.validate_hex_color(body.text_hex)
        except ServiceError as exc:
            raise _http_from_service_error(exc) from exc
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            user = await characters_svc.get_or_create_user(session, body.discord_id)
            count_row = await session.execute(
                select(func.count()).select_from(ThemeProfile).where(ThemeProfile.user_id == user.id)
            )
            if count_row.scalar_one() >= MAX_THEME_PROFILES_PER_USER:
                raise HTTPException(status_code=400, detail="theme_profile_limit_reached")
            profile = ThemeProfile(
                user_id=user.id,
                name=name,
                background_hex=background_hex,
                accent_hex=accent_hex,
                panel_hex=panel_hex,
                text_hex=text_hex,
            )
            session.add(profile)
            await session.flush()
            return _theme_profile_summary(profile)

    @router.patch("/profiles/{profile_id}", response_model=ThemeProfileSummary)
    async def update_profile(
        profile_id: int, body: ThemeProfileUpdateRequest
    ) -> ThemeProfileSummary:
        await _require_donor(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            _, profile = await _resolve_owned_profile(
                session, discord_id=body.discord_id, profile_id=profile_id
            )
            try:
                if body.name is not None:
                    profile.name = theme_svc.validate_profile_name(body.name)
                if body.background_hex is not None:
                    profile.background_hex = theme_svc.validate_hex_color(body.background_hex)
                if body.accent_hex is not None:
                    profile.accent_hex = theme_svc.validate_hex_color(body.accent_hex)
                if body.panel_hex is not None:
                    profile.panel_hex = theme_svc.validate_hex_color(body.panel_hex)
                if body.text_hex is not None:
                    profile.text_hex = theme_svc.validate_hex_color(body.text_hex)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            return _theme_profile_summary(profile)

    @router.post("/profiles/{profile_id}/delete", response_model=ThemeProfileListResponse)
    async def delete_profile(
        profile_id: int, body: ThemeProfileActionRequest
    ) -> ThemeProfileListResponse:
        await _require_donor(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            user, profile = await _resolve_owned_profile(
                session, discord_id=body.discord_id, profile_id=profile_id
            )
            # `ondelete="SET NULL"` on both `User.active_theme_profile_id`
            # and `Character.theme_profile_id` clears any reference to this
            # row at the database level -- nothing to clear by hand here.
            await session.delete(profile)
            await session.flush()
            await session.refresh(user)
            return await _profiles_list_response(session, user)

    @router.post("/profiles/{profile_id}/activate", response_model=DashboardTheme)
    async def activate_profile(profile_id: int, body: ThemeProfileActionRequest) -> DashboardTheme:
        """Sets this profile as the account's general default -- the "save
        in general to go back to" half of the feature request."""
        await _require_donor(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            user, profile = await _resolve_owned_profile(
                session, discord_id=body.discord_id, profile_id=profile_id
            )
            user.active_theme_profile_id = profile.id
            return _theme_for_profile(profile)

    @router.post("/profiles/{profile_id}/assign", response_model=DashboardTheme)
    async def assign_profile(profile_id: int, body: ThemeProfileAssignRequest) -> DashboardTheme:
        """Assigns this profile to one specific character, overriding the
        account's general default while that character is selected."""
        await _require_donor(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            _, profile = await _resolve_owned_profile(
                session, discord_id=body.discord_id, profile_id=profile_id
            )
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=body.character_id
            )
            character.theme_profile_id = profile.id
            return _theme_for_profile(profile)

    @router.post("/unassign", response_model=DashboardTheme)
    async def unassign_profile(body: ThemeUnassignRequest) -> DashboardTheme:
        """Clears a character's own profile assignment -- it goes back to
        following whatever the account's general profile resolves to."""
        await _require_donor(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=body.character_id
            )
            character.theme_profile_id = None
            user_row = await session.execute(select(User).where(User.discord_id == body.discord_id))
            user = user_row.scalar_one_or_none()
            if user is None:
                return DashboardTheme()
            return await _resolve_theme(session, user=user, character=character)

    @router.post("/reset", response_model=DashboardTheme)
    async def reset_theme(body: ResetThemeRequest) -> DashboardTheme:
        """The picker's "Reset to default" button: clears whichever context
        is currently open -- never deletes a saved profile, just the one FK
        pointing at it. With a `character_id`, that means clearing *that
        character's own* assignment, which falls back to the account's
        general profile if one is active (not necessarily the plain
        default -- same "character overrides general" order `_resolve_
        theme` always uses). Without one, it clears the account's general
        profile itself, which has no further fallback."""
        await _require_donor(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            user = await characters_svc.get_or_create_user(session, body.discord_id)
            if body.character_id is not None:
                character = await _resolve_owned_character(
                    session, discord_id=body.discord_id, character_id=body.character_id
                )
                character.theme_profile_id = None
                return await _resolve_theme(session, user=user, character=character)
            user.active_theme_profile_id = None
            return DashboardTheme()

    @router.post("/resolve", response_model=DashboardTheme)
    async def resolve_theme(body: ThemeResolveRequest) -> DashboardTheme:
        """Called by `app.js` whenever the selected character changes (and
        on initial load with no character yet) to get the effective theme
        for that context. Never 403s -- a non-donor/lapsed donor just gets
        the plain default, the same non-throwing shape `/identify`'s
        `theme` field already has."""
        if not await _is_donor(body.discord_id):
            return DashboardTheme()
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            user_row = await session.execute(select(User).where(User.discord_id == body.discord_id))
            user = user_row.scalar_one_or_none()
            if user is None:
                return DashboardTheme()
            character = None
            if body.character_id is not None:
                character = await _resolve_owned_character(
                    session, discord_id=body.discord_id, character_id=body.character_id
                )
            return await _resolve_theme(session, user=user, character=character)

    return router


class CharacterDetail(BaseModel):
    id: int
    name: str
    status: str
    age: int
    gender: str | None = None
    appearance: str
    appearance_layers: dict[str, int]
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
    jailed: bool = False
    rp_mode: str = RpMode.SIMULATION.value
    death_cause: str | None = None


def _character_detail(
    character: Character, *, content: ContentBundle, current_tick: int
) -> CharacterDetail:
    return CharacterDetail(
        id=character.id,
        name=character.name,
        status=character.status,
        age=character.age,
        gender=character.gender,
        appearance=character.appearance,
        appearance_layers=layers_svc.sanitize_stored_selection(character.appearance_layers),
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
        jailed=(
            character.jailed_until_tick is not None and character.jailed_until_tick > current_tick
        ),
        rp_mode=character.rp_mode,
        death_cause=character.death_cause,
    )


class MyCharactersResponse(BaseModel):
    characters: list[CharacterDetail]


class CreateCharacterRequest(BaseModel):
    discord_id: int
    district_id: int
    name: str
    age: int
    gender: str | None = None
    appearance: str = ""
    backstory: str = ""
    avatar_url: str | None = None
    appearance_layers: dict[str, int] | None = None
    job_title: str | None = None
    shift_phase: str | None = None
    job_is_illicit: bool = False
    rp_mode: str = RpMode.SIMULATION.value


class RetireCharacterRequest(BaseModel):
    discord_id: int


class UpdateCharacterRequest(BaseModel):
    discord_id: int
    avatar_url: str | None = None
    proxy_tag: str | None = None
    appearance_layers: dict[str, int] | None = None
    gender: str | None = None


def build_characters_router(
    *,
    content: ContentBundle,
    session_factory: async_sessionmaker[AsyncSession] | None,
    max_characters_per_user: int,
    redis_client: redis.Redis,
    static_dir: Path,
    activity_public_url: str,
) -> APIRouter:
    """The Character tab's REST surface: list/create/edit/retire, mirroring
    `/character list|create|avatar|tag|retire`. Unlike Discord's `/character
    create` (a multi-step modal wizard ending in a bot-posted staff-approval
    embed), this endpoint can only write the DB row -- `panem_api` has no
    bot token to post that embed itself, so it publishes the new character's
    id on `CHARACTER_PENDING_CHANNEL` right after creating it, which
    `CharacterCog` (in `panem_bot`) subscribes to and announces from
    immediately. `CharacterCog._announce_pending_characters` (a background
    poll) stays as a fallback for the case that publish never reaches a
    listening bot (e.g. it was down at that moment -- pub/sub has no
    replay). District is a
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
            current_tick = await _current_tick(session)
            return MyCharactersResponse(
                characters=[
                    _character_detail(c, content=content, current_tick=current_tick)
                    for c in rows.scalars().all()
                ]
            )

    @router.post("", response_model=CharacterDetail)
    async def create_my_character(body: CreateCharacterRequest) -> CharacterDetail:
        factory = _require_session_factory(session_factory)
        if body.district_id not in content.districts:
            raise HTTPException(status_code=400, detail="No such district")
        if body.rp_mode not in {mode.value for mode in RpMode}:
            raise HTTPException(status_code=400, detail="Invalid RP mode")
        if body.gender is not None and body.gender not in {g.value for g in Gender}:
            raise HTTPException(status_code=400, detail="Invalid gender")
        is_story = body.rp_mode == RpMode.STORY.value
        if not is_story and body.shift_phase not in {phase.value for phase in DayPhase}:
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
                    gender=body.gender,
                    appearance=body.appearance,
                    backstory=body.backstory,
                    avatar_url=body.avatar_url,
                    appearance_layers=body.appearance_layers,
                    job_title=body.job_title,
                    shift_phase=body.shift_phase,
                    job_is_illicit=body.job_is_illicit,
                    max_characters=max_characters,
                    rp_mode=body.rp_mode,
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            current_tick = await _current_tick(session)
            detail = _character_detail(character, content=content, current_tick=current_tick)
        await redis_client.publish(CHARACTER_PENDING_CHANNEL, str(character.id))
        return detail

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
                if body.appearance_layers is not None:
                    character.appearance_layers = await layers_svc.validate_layer_selection(
                        session, body.appearance_layers
                    )
                if body.gender is not None:
                    if body.gender not in {g.value for g in Gender}:
                        raise HTTPException(status_code=400, detail="Invalid gender")
                    character.gender = body.gender
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            current_tick = await _current_tick(session)
            return _character_detail(character, content=content, current_tick=current_tick)

    @router.post("/{character_id}/avatar-upload", response_model=CharacterDetail)
    async def upload_my_character_avatar(
        character_id: int,
        discord_id: int = Form(...),
        file: UploadFile = File(...),  # noqa: B008 -- FastAPI's own sentinel-default idiom
    ) -> CharacterDetail:
        """The file-upload alternative to `PATCH .../avatar_url`'s plain
        text field -- typing/finding a hosted image URL is the whole
        friction this exists to remove. Persists the bytes under
        `static_dir` (`avatars_svc.save_avatar_image`, same validation and
        on-disk layout `panem_shared.layers` uses for staff-uploaded
        artwork) rather than only storing a URL, so it survives independent
        of wherever the image originally came from -- unlike `/character
        avatar`'s Discord-attachment option, which just took the CDN's own
        ~24h-expiring URL."""
        if not activity_public_url:
            raise HTTPException(
                status_code=400,
                detail="Avatar uploads aren't configured on this server (no public URL set).",
            )
        data = await file.read()
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            try:
                relative_path = avatars_svc.save_avatar_image(
                    content_type=file.content_type or "", data=data, static_dir=static_dir
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            character.avatar_url = f"{activity_public_url.rstrip('/')}/{relative_path}"
            current_tick = await _current_tick(session)
            return _character_detail(character, content=content, current_tick=current_tick)

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
            current_tick = await _current_tick(session)
            return _character_detail(character, content=content, current_tick=current_tick)

    return router


class LayerOptionResponse(BaseModel):
    id: int
    name: str
    image_url: str


class LayerCategoryResponse(BaseModel):
    id: int
    name: str
    z_index: int
    options: list[LayerOptionResponse]


class LayerCatalogResponse(BaseModel):
    categories: list[LayerCategoryResponse]


def _layer_category_response(category: LayerCategory) -> LayerCategoryResponse:
    return LayerCategoryResponse(
        id=category.id,
        name=category.name,
        z_index=category.z_index,
        options=[
            LayerOptionResponse(id=o.id, name=o.name, image_url=f"/{o.image_path}")
            for o in category.options
        ],
    )


def build_layers_router(
    *, session_factory: async_sessionmaker[AsyncSession] | None
) -> APIRouter:
    """The Picrew-style customizer's read side: every category (in render
    order) and its options. No `discord_id` needed -- like the old
    `appearance-options` endpoint this replaces, it's static, non-sensitive
    catalog data, same trust level as `/districts`. Starts out returning
    `{"categories": []}` until staff upload something through `build_staff_
    router`'s layer-management endpoints below; the frontend picker is
    built to render that (nothing to customize yet) rather than treating
    it as an error."""
    router = APIRouter(prefix="/activity/dashboard/layers", tags=["dashboard"])

    @router.get("", response_model=LayerCatalogResponse)
    async def layer_catalog() -> LayerCatalogResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            categories = await layers_svc.list_categories(session)
            return LayerCatalogResponse(
                categories=[_layer_category_response(c) for c in categories]
            )

    return router


class AfflictionTypeResponse(BaseModel):
    id: int
    name: str
    description: str
    is_permanent: bool
    cure_stat: str | None = None
    cure_threshold: float | None = None
    auto_apply_stat: str | None = None
    auto_apply_threshold: float | None = None


class AfflictionTypeCatalogResponse(BaseModel):
    types: list[AfflictionTypeResponse]


def _affliction_type_response(row: AfflictionType) -> AfflictionTypeResponse:
    return AfflictionTypeResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        is_permanent=row.is_permanent,
        cure_stat=row.cure_stat,
        cure_threshold=row.cure_threshold,
        auto_apply_stat=row.auto_apply_stat,
        auto_apply_threshold=row.auto_apply_threshold,
    )


def build_affliction_types_router(
    *, session_factory: async_sessionmaker[AsyncSession] | None
) -> APIRouter:
    """The catalog players need when self-inflicting (`/character
    afflict`, a later milestone) -- same public, unauthenticated posture
    as `build_layers_router`'s read side: non-sensitive staff-authored
    catalog data, starts out empty until staff add something through
    `build_staff_router`'s affliction-type endpoints below."""
    router = APIRouter(prefix="/activity/dashboard/affliction-types", tags=["dashboard"])

    @router.get("", response_model=AfflictionTypeCatalogResponse)
    async def affliction_type_catalog() -> AfflictionTypeCatalogResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            types = await affliction_types_svc.list_types(session)
            return AfflictionTypeCatalogResponse(
                types=[_affliction_type_response(t) for t in types]
            )

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
    # `None` for `/poach`, which has no single target the way steal/burgle do.
    target_name: str | None = None


class PoachStartRequest(BaseModel):
    discord_id: int


class CrimeLogEntry(BaseModel):
    kind: str
    tick: int
    success: bool
    caught: bool
    target_name: str | None = None
    good_name: str | None = None
    amount: int
    created_at: str


class CrimeLogResponse(BaseModel):
    entries: list[CrimeLogEntry]


def build_crime_router(
    *,
    content: ContentBundle,
    session_factory: async_sessionmaker[AsyncSession] | None,
    redis_client: redis.Redis,
) -> APIRouter:
    """The Crime tab's REST surface: mirrors `/steal`, `/burgle`, `/poach`.
    All three `*-start` routes mint a crime attempt exactly like their
    Discord commands do (same Redis shape `/activity/crime/{id}` reads) for
    the dashboard to embed in an iframe."""
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
                            Character.rp_mode != RpMode.STORY.value,
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
        param, which has no autocomplete on the bot side at all. Life mode
        has no housing access at all, so the option shouldn't even show
        up to them -- an empty list, same shape as a district with no
        houses, rather than a distinct error."""
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            if character.rp_mode == RpMode.LIFE.value:
                return BurgleTargetsResponse(owners=[])
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
                            Character.rp_mode != RpMode.STORY.value,
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
                # `.scalars().first()`, not `.scalar_one_or_none()` -- nothing in the
                # housing system stops one character from owning more than one house
                # in the same district, so this can legitimately match more than one
                # row; `scalar_one_or_none()` raised `MultipleResultsFound` (an
                # unhandled 500) the moment a real player actually did. `order_by`
                # id keeps which house gets targeted stable across repeated attempts
                # rather than depending on the database's unspecified row order.
                house = (
                    await session.execute(
                        select(Property)
                        .where(
                            Property.kind == PropertyKind.HOUSE.value,
                            Property.owner_kind == OwnerKind.CHARACTER.value,
                            Property.owner_id == owner_char.id,
                            Property.district_id == character.current_district_id,
                        )
                        .order_by(Property.id)
                    )
                ).scalars().first()
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

    @router.post("/{character_id}/poach/start", response_model=CrimeStartResponse)
    async def start_poach(character_id: int, body: PoachStartRequest) -> CrimeStartResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            district = content.district(character.current_district_id)
            current_tick = await _current_tick(session)
            try:
                good = poaching_svc.check_can_poach(
                    character, district, content.goods, current_tick
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            character.last_poach_tick = current_tick
            district_id, good_id = district.id, good.id
            difficulty = poaching_svc.poach_difficulty()

        attempt_id = secrets.token_urlsafe(16)
        await redis_client.set(
            crime_attempt_key(attempt_id),
            json.dumps(
                {
                    "kind": "poach",
                    "character_id": character_id,
                    "district_id": district_id,
                    "good_id": good_id,
                    "current_tick": current_tick,
                }
            ),
            ex=CRIME_ATTEMPT_TTL_S,
        )
        return CrimeStartResponse(attempt_id=attempt_id, difficulty=difficulty)

    @router.get("/{character_id}/log", response_model=CrimeLogResponse)
    async def crime_log(character_id: int, discord_id: int) -> CrimeLogResponse:
        """The character's own recent `/steal`/`/burgle`/`/poach` history,
        most recent first -- `CrimeLog` rows `apply_steal_outcome`/`apply_
        burgle_outcome`/`apply_poach_outcome` already write on every
        resolved attempt, RNG-fallback or Activity minigame alike."""
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            rows = await crime_log_svc.list_crime_log(session, character_id)
        return CrimeLogResponse(
            entries=[
                CrimeLogEntry(
                    kind=row.kind,
                    tick=row.tick,
                    success=row.success,
                    caught=row.caught,
                    target_name=row.target_name,
                    good_name=row.good_name,
                    amount=row.amount,
                    created_at=row.created_at.isoformat(),
                )
                for row in rows
            ]
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
            current_tick = await _current_tick(session)
            try:
                jail_svc.check_not_jailed(character, current_tick, "work_jailed")
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc

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
    # Vitals tab feature -- "replenishment values listed... in the
    # market" -- populated straight from content, zero/None for goods
    # that aren't directly consumable (see `content.schemas.Good`).
    hunger_value: float = 0.0
    thirst_value: float = 0.0
    cook_method: str | None = None


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
                prices.append(
                    GoodPrice(
                        good_id=good_id,
                        name=good.name,
                        price=price,
                        hunger_value=good.hunger_value,
                        thirst_value=good.thirst_value,
                        cook_method=good.cook_method,
                    )
                )
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
            current_tick = await _current_tick(session)
            try:
                location = travel_svc.resolve_location(district, body.location_id)
                travel_svc.check_can_travel(
                    character=character, location=location, current_tick=current_tick
                )
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
                if travel_svc.should_charge_transport(
                    character, origin_district.id, body.destination_id
                ):
                    await travel_svc.spend_transport(session, character)
            except (NotFound, NotAllowed) as exc:
                raise _http_from_service_error(exc) from exc
            destination_district = content.district(body.destination_id)
            transit_ticks = travel_svc.transit_ticks_for(character)
            if transit_ticks == 0:
                travel_svc.apply_instant_arrival(character, destination_district)
            else:
                character.in_transit_until_tick = current_tick + transit_ticks
                character.transit_destination_id = body.destination_id
                if character.current_district_id == character.district_id:
                    character.away_since_tick = current_tick
            return TravelToDistrictResponse(
                character_name=character.name,
                destination_district_name=destination_district.name,
                transit_ticks=transit_ticks,
            )

    return router


class ResidentSummary(BaseModel):
    name: str
    job_title: str
    location_id: str | None = None
    location_name: str | None = None
    kind: str = "npc"
    """`"npc"` or `"user"` -- the Residents directory's own citizens versus
    other players' characters currently in this district. The frontend's
    `determineStatus()` already prefers an explicit `status` over its own
    location-name heuristic, so `status` below is populated for `"user"`
    rows (computed from real state) and left `None` for `"npc"` rows
    (unchanged: still inferred client-side from location/job)."""
    status: str | None = None
    """`"sleeping"`/`"engaged"`/`"idle"` for a `"user"` row -- see
    `resident_list`'s `_character_status` for how each is decided."""


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

    def _character_status(
        char: Character, *, engaged_character_ids: set[int], locations_by_id: dict[str, Location]
    ) -> str:
        """`"engaged"` wins over location -- a character mid-conversation in
        their own home still reads as talking, not sleeping. Otherwise,
        `"sleeping"` is inferred from standing in a `RESIDENTIAL` location
        (home, an inn) rather than a real "currently asleep" flag: `/sleep`
        is a one-shot fatigue-restoring action with no lasting state of its
        own (see `panem_shared.housing`), so this is the closest real signal
        to "probably resting" the data actually offers. Anyone else is
        `"idle"` -- present in the district, not doing anything trackable."""
        if char.id in engaged_character_ids:
            return "engaged"
        location = locations_by_id.get(char.location_id) if char.location_id else None
        if location is not None and location.kind == LocationKind.RESIDENTIAL:
            return "sleeping"
        return "idle"

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
            other_characters = (
                (
                    await session.execute(
                        select(Character)
                        .where(
                            Character.current_district_id == character.current_district_id,
                            Character.status == CharacterStatus.APPROVED.value,
                            Character.id != character.id,
                        )
                        .order_by(Character.name)
                    )
                )
                .scalars()
                .all()
            )
            open_scenes = (
                (await session.execute(select(Scene).where(Scene.status == SceneStatus.OPEN.value)))
                .scalars()
                .all()
            )
            engaged_character_ids = {
                cid for scene in open_scenes for cid in scene.participants.get("characters", [])
            }
            all_jobs = await jobs_svc.get_all_jobs(session, content)
            locations_by_id = {loc.id: loc for loc in district.locations}
            residents = []
            for npc in npcs:
                job = all_jobs.get(npc.job_id) if npc.job_id else None
                location = locations_by_id.get(npc.location_id) if npc.location_id else None
                residents.append(
                    ResidentSummary(
                        name=npc.name,
                        job_title=job.title if job else "Unemployed",
                        location_id=npc.location_id,
                        location_name=location.name if location is not None else None,
                        kind="npc",
                    )
                )
            for other in other_characters:
                location = locations_by_id.get(other.location_id) if other.location_id else None
                residents.append(
                    ResidentSummary(
                        name=other.name,
                        job_title=other.job_title or "Unemployed",
                        location_id=other.location_id,
                        location_name=location.name if location is not None else None,
                        kind="user",
                        status=_character_status(
                            other,
                            engaged_character_ids=engaged_character_ids,
                            locations_by_id=locations_by_id,
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


class PayRequest(BaseModel):
    discord_id: int
    target: str
    amount: int


class PayResponse(BaseModel):
    sender_money: int
    recipient_name: str


def build_pay_router(*, session_factory: async_sessionmaker[AsyncSession] | None) -> APIRouter:
    """`/pay`'s dashboard equivalent -- instant, no confirmation, same
    trust level as the bot command."""
    router = APIRouter(prefix="/activity/dashboard/pay", tags=["dashboard"])

    @router.post("/{character_id}", response_model=PayResponse)
    async def pay(character_id: int, body: PayRequest) -> PayResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            sender = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            recipient = (
                await session.execute(select(Character).where(Character.name == body.target))
            ).scalar_one_or_none()
            if recipient is None:
                raise HTTPException(status_code=404, detail="character_not_found")
            try:
                pay_svc.pay(sender, recipient, body.amount)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            return PayResponse(sender_money=sender.money, recipient_name=recipient.name)

    return router


class TradeSummary(BaseModel):
    id: int
    initiator_character_id: int
    initiator_name: str
    recipient_character_id: int
    recipient_name: str
    give_good_id: str | None = None
    give_qty: int | None = None
    give_money: int
    want_good_id: str | None = None
    want_qty: int | None = None
    want_money: int
    status: str
    # "incoming" for the requesting character's own pending offers to
    # respond to, "outgoing" for ones they sent themselves -- the
    # dashboard's Trade panel splits the pending list on this.
    direction: str


class TradeListResponse(BaseModel):
    trades: list[TradeSummary]


class TradeOfferRequest(BaseModel):
    discord_id: int
    target: str
    give_good_id: str | None = None
    give_qty: int | None = None
    give_money: int = 0
    want_good_id: str | None = None
    want_qty: int | None = None
    want_money: int = 0


class TradeActionRequest(BaseModel):
    discord_id: int


def _trade_summary(trade: Trade, *, initiator_name: str, recipient_name: str, viewer_id: int) -> TradeSummary:
    return TradeSummary(
        id=trade.id,
        initiator_character_id=trade.initiator_character_id,
        initiator_name=initiator_name,
        recipient_character_id=trade.recipient_character_id,
        recipient_name=recipient_name,
        give_good_id=trade.give_good_id,
        give_qty=trade.give_qty,
        give_money=trade.give_money,
        want_good_id=trade.want_good_id,
        want_qty=trade.want_qty,
        want_money=trade.want_money,
        status=trade.status,
        direction="outgoing" if trade.initiator_character_id == viewer_id else "incoming",
    )


def build_trade_router(*, session_factory: async_sessionmaker[AsyncSession] | None) -> APIRouter:
    """`/trade`'s dashboard equivalent -- mirrors the bot's offer/accept/
    decline/cancel flow 1:1. Unlike the bot (which DMs the recipient an
    Accept/Decline view), the dashboard has no push channel of its own,
    so the recipient discovers a pending offer by checking `.../list`
    themselves -- the same "poll, don't push" posture `build_social_
    router`'s read-only Engagement panel already has."""
    router = APIRouter(prefix="/activity/dashboard/trade", tags=["dashboard"])

    @router.get("/{character_id}/list", response_model=TradeListResponse)
    async def list_trades(character_id: int, discord_id: int) -> TradeListResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            rows = (
                await session.execute(
                    select(Trade).where(
                        Trade.status == TradeStatus.PENDING.value,
                        (Trade.initiator_character_id == character.id)
                        | (Trade.recipient_character_id == character.id),
                    )
                )
            ).scalars()
            summaries = []
            for trade in rows:
                initiator = await session.get(Character, trade.initiator_character_id)
                recipient = await session.get(Character, trade.recipient_character_id)
                summaries.append(
                    _trade_summary(
                        trade,
                        initiator_name=initiator.name if initiator else "?",
                        recipient_name=recipient.name if recipient else "?",
                        viewer_id=character.id,
                    )
                )
            return TradeListResponse(trades=summaries)

    @router.post("/{character_id}/offer", response_model=TradeSummary)
    async def offer_trade(character_id: int, body: TradeOfferRequest) -> TradeSummary:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            initiator = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            recipient = (
                await session.execute(select(Character).where(Character.name == body.target))
            ).scalar_one_or_none()
            if recipient is None:
                raise HTTPException(status_code=404, detail="character_not_found")
            try:
                trades_svc.check_can_trade(initiator, recipient)
                trades_svc.validate_offer(
                    initiator=initiator,
                    give_good_id=body.give_good_id,
                    give_qty=body.give_qty,
                    give_money=body.give_money,
                    want_good_id=body.want_good_id,
                    want_qty=body.want_qty,
                    want_money=body.want_money,
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            trade = Trade(
                initiator_character_id=initiator.id,
                recipient_character_id=recipient.id,
                give_good_id=body.give_good_id,
                give_qty=body.give_qty,
                give_money=body.give_money,
                want_good_id=body.want_good_id,
                want_qty=body.want_qty,
                want_money=body.want_money,
                status=TradeStatus.PENDING.value,
            )
            session.add(trade)
            await session.flush()
            return _trade_summary(
                trade,
                initiator_name=initiator.name,
                recipient_name=recipient.name,
                viewer_id=initiator.id,
            )

    async def _resolve_trade_and_character(
        session: AsyncSession, *, discord_id: int, character_id: int, trade_id: int
    ) -> tuple[Trade, Character]:
        character = await _resolve_owned_character(
            session, discord_id=discord_id, character_id=character_id
        )
        trade = await session.get(Trade, trade_id)
        if trade is None:
            raise HTTPException(status_code=404, detail="trade_not_found")
        return trade, character

    @router.post("/{character_id}/{trade_id}/accept", response_model=TradeSummary)
    async def accept_trade_route(
        character_id: int, trade_id: int, body: TradeActionRequest
    ) -> TradeSummary:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            trade, character = await _resolve_trade_and_character(
                session, discord_id=body.discord_id, character_id=character_id, trade_id=trade_id
            )
            if trade.recipient_character_id != character.id:
                raise HTTPException(status_code=404, detail="trade_not_found")
            initiator = await session.get(Character, trade.initiator_character_id)
            if initiator is None:
                raise HTTPException(status_code=404, detail="character_not_found")
            try:
                await trades_svc.accept_trade(
                    session,
                    trade=trade,
                    initiator=initiator,
                    recipient=character,
                    now=dt.datetime.now(dt.UTC),
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            return _trade_summary(
                trade,
                initiator_name=initiator.name,
                recipient_name=character.name,
                viewer_id=character.id,
            )

    @router.post("/{character_id}/{trade_id}/decline", response_model=TradeSummary)
    async def decline_trade_route(
        character_id: int, trade_id: int, body: TradeActionRequest
    ) -> TradeSummary:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            trade, character = await _resolve_trade_and_character(
                session, discord_id=body.discord_id, character_id=character_id, trade_id=trade_id
            )
            if trade.recipient_character_id != character.id:
                raise HTTPException(status_code=404, detail="trade_not_found")
            try:
                trades_svc.decline_trade(trade, dt.datetime.now(dt.UTC))
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            initiator = await session.get(Character, trade.initiator_character_id)
            return _trade_summary(
                trade,
                initiator_name=initiator.name if initiator else "?",
                recipient_name=character.name,
                viewer_id=character.id,
            )

    @router.post("/{character_id}/{trade_id}/cancel", response_model=TradeSummary)
    async def cancel_trade_route(
        character_id: int, trade_id: int, body: TradeActionRequest
    ) -> TradeSummary:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            trade, character = await _resolve_trade_and_character(
                session, discord_id=body.discord_id, character_id=character_id, trade_id=trade_id
            )
            if trade.initiator_character_id != character.id:
                raise HTTPException(status_code=404, detail="trade_not_found")
            try:
                trades_svc.cancel_trade(trade, dt.datetime.now(dt.UTC))
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            recipient = await session.get(Character, trade.recipient_character_id)
            return _trade_summary(
                trade,
                initiator_name=character.name,
                recipient_name=recipient.name if recipient else "?",
                viewer_id=character.id,
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
    mortgage_missed_payments: int
    has_open_auction: bool


class HousingMortgageTerms(BaseModel):
    """The constants behind `panem_shared.housing.financed_purchase_terms`/
    `apply_refinance`, sent down so the Housing tab can explain the real
    numbers instead of the client hardcoding a copy that could drift."""

    down_payment_pct: float
    interest_rate: float
    term_days: int
    max_ltv: float
    misses_to_foreclose: int


class HousingStatusResponse(BaseModel):
    character_name: str
    fatigue: float
    home_property_id: int | None = None
    home_kind: str | None = None
    home_district_id: int | None = None
    home_district_name: str | None = None
    owned: list[HousingOwnedProperty]
    listings: list[HousingListing]
    mortgage_terms: HousingMortgageTerms


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
                    mortgage_missed_payments=p.mortgage_missed_payments,
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
                mortgage_terms=HousingMortgageTerms(
                    down_payment_pct=constants.MORTGAGE_DOWN_PAYMENT_PCT,
                    interest_rate=constants.MORTGAGE_INTEREST_RATE,
                    term_days=constants.MORTGAGE_TERM_TICKS_DEFAULT
                    // constants.TICKS_PER_DAY,
                    max_ltv=constants.MORTGAGE_MAX_LTV,
                    misses_to_foreclose=constants.MORTGAGE_MISSES_TO_FORECLOSE,
                ),
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
                housing_svc.check_can_sleep(character, phase)
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


ENTERTAINMENT_LABELS: dict[str, str] = {
    "minesweeper": "Minesweeper",
    "snake": "Snake",
    "connect4": "Connect Four",
    "coinflip": "Coin Flip",
    "poison": "Pick Your Poison",
    "solitaire": "Solitaire",
}


class VitalsConsumable(BaseModel):
    good_id: str
    name: str
    qty: int
    hunger_value: float
    thirst_value: float
    cook_method: str | None = None


class VitalsEntertainmentOption(BaseModel):
    game_id: str
    label: str
    sanity_value: float


class VitalsStatusResponse(BaseModel):
    hunger: float
    thirst: float
    sanity: float
    fatigue: float
    health: float
    has_bed: bool
    max_sleep_ticks: int
    fatigue_restore_per_tick: float
    edible: list[VitalsConsumable]
    drinkable: list[VitalsConsumable]
    entertainment: list[VitalsEntertainmentOption]


class VitalsEatRequest(BaseModel):
    discord_id: int
    good_id: str
    bonus: bool = False


class VitalsEatResponse(BaseModel):
    good_id: str
    good_name: str
    hunger: float


class VitalsDrinkRequest(BaseModel):
    discord_id: int
    good_id: str


class VitalsDrinkResponse(BaseModel):
    good_id: str
    good_name: str
    thirst: float


class VitalsEntertainRequest(BaseModel):
    discord_id: int
    game_id: str


class VitalsEntertainResponse(BaseModel):
    game_id: str
    sanity: float


def build_vitals_router(
    *, content: ContentBundle, session_factory: async_sessionmaker[AsyncSession] | None
) -> APIRouter:
    """The Vitals tab's REST surface: eat/drink/entertain, inventory-gated
    per `panem_shared.sustenance`'s rewrite (see that module's docstring).
    Sleep has no route of its own here -- the frontend calls the existing
    `POST /activity/dashboard/housing/{id}/sleep` directly; this router's
    status endpoint just hands back what that panel needs for its live
    preview (`has_bed`/`max_sleep_ticks`/`fatigue_restore_per_tick`)."""
    router = APIRouter(prefix="/activity/dashboard/vitals", tags=["dashboard"])

    @router.get("/{character_id}/status", response_model=VitalsStatusResponse)
    async def vitals_status(character_id: int, discord_id: int) -> VitalsStatusResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            current_tick = await _current_tick(session)
            pairs = await sustenance_svc.owned_consumables(session, character, content.goods)
            edible = [
                VitalsConsumable(
                    good_id=good.id,
                    name=good.name,
                    qty=row.qty,
                    hunger_value=good.hunger_value,
                    thirst_value=good.thirst_value,
                    cook_method=good.cook_method,
                )
                for row, good in pairs
                if good.hunger_value > 0
            ]
            drinkable = [
                VitalsConsumable(
                    good_id=good.id,
                    name=good.name,
                    qty=row.qty,
                    hunger_value=good.hunger_value,
                    thirst_value=good.thirst_value,
                    cook_method=good.cook_method,
                )
                for row, good in pairs
                if good.thirst_value > 0
            ]
            entertainment = [
                VitalsEntertainmentOption(
                    game_id=game_id,
                    label=ENTERTAINMENT_LABELS.get(game_id, game_id.title()),
                    sanity_value=sanity_value,
                )
                for game_id, sanity_value in constants.ENTERTAINMENT_SANITY_VALUES.items()
            ]
            has_bed = housing_svc.has_a_bed(character)
            max_sleep_ticks = simtime.ticks_remaining_in_phase(current_tick)
            fatigue_restore_per_tick = housing_svc.fatigue_restored(1, has_bed=has_bed)
        return VitalsStatusResponse(
            hunger=round(character.hunger, 1),
            thirst=round(character.thirst, 1),
            sanity=round(character.sanity, 1),
            fatigue=round(character.fatigue, 1),
            health=round(character.health, 1),
            has_bed=has_bed,
            max_sleep_ticks=max_sleep_ticks,
            fatigue_restore_per_tick=round(fatigue_restore_per_tick, 2),
            edible=edible,
            drinkable=drinkable,
            entertainment=entertainment,
        )

    @router.post("/{character_id}/eat", response_model=VitalsEatResponse)
    async def vitals_eat(character_id: int, body: VitalsEatRequest) -> VitalsEatResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            good = content.goods.get(body.good_id)
            if good is None:
                raise HTTPException(status_code=404, detail="good_not_edible")
            current_tick = await _current_tick(session)
            try:
                hunger = await sustenance_svc.eat(
                    session, character, good, current_tick, bonus=body.bonus
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
        return VitalsEatResponse(good_id=good.id, good_name=good.name, hunger=round(hunger, 1))

    @router.post("/{character_id}/drink", response_model=VitalsDrinkResponse)
    async def vitals_drink(character_id: int, body: VitalsDrinkRequest) -> VitalsDrinkResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            good = content.goods.get(body.good_id)
            if good is None:
                raise HTTPException(status_code=404, detail="good_not_drinkable")
            current_tick = await _current_tick(session)
            try:
                thirst = await sustenance_svc.drink(session, character, good, current_tick)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
        return VitalsDrinkResponse(good_id=good.id, good_name=good.name, thirst=round(thirst, 1))

    @router.post("/{character_id}/entertain", response_model=VitalsEntertainResponse)
    async def vitals_entertain(
        character_id: int, body: VitalsEntertainRequest
    ) -> VitalsEntertainResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            current_tick = await _current_tick(session)
            try:
                sanity = sustenance_svc.entertain(character, body.game_id, current_tick)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
        return VitalsEntertainResponse(game_id=body.game_id, sanity=round(sanity, 1))

    return router


class StaffJailRequest(BaseModel):
    discord_id: int
    character_name: str
    ticks: int
    reason: str | None = None


class StaffJailResponse(BaseModel):
    character_name: str
    base_ticks: int
    prior_bonus_ticks: int
    applied_ticks: int
    jailed_until_tick: int


class CreateLayerCategoryRequest(BaseModel):
    discord_id: int
    name: str
    z_index: int = 0


class UpdateLayerCategoryRequest(BaseModel):
    discord_id: int
    name: str | None = None
    z_index: int | None = None


class DeleteLayerCategoryRequest(BaseModel):
    discord_id: int


class DeleteLayerOptionRequest(BaseModel):
    discord_id: int


class UpdateDistrictMottoRequest(BaseModel):
    discord_id: int
    district_id: int
    motto: str


class DistrictMottoResponse(BaseModel):
    district_id: int
    motto: str


class CreateAfflictionTypeRequest(BaseModel):
    discord_id: int
    name: str
    description: str = ""
    is_permanent: bool = False
    cure_stat: str | None = None
    cure_threshold: float | None = None
    auto_apply_stat: str | None = None
    auto_apply_threshold: float | None = None


class UpdateAfflictionTypeRequest(BaseModel):
    discord_id: int
    name: str | None = None
    description: str | None = None
    is_permanent: bool = False
    cure_stat: str | None = None
    cure_threshold: float | None = None
    auto_apply_stat: str | None = None
    auto_apply_threshold: float | None = None


class DeleteAfflictionTypeRequest(BaseModel):
    discord_id: int


class ActiveAfflictionSummary(BaseModel):
    id: int
    name: str
    description: str
    is_permanent: bool
    cause: str | None = None
    applied_at: str


class RpModeStatusResponse(BaseModel):
    """The Milestone 11 home page's data source -- everything about a
    character's current RP mode in one call (mode, meters, crime toggle,
    both cooldowns, active afflictions, death). Meters/afflictions are
    always included regardless of mode -- Life/Story characters just never
    have anything write to them, so they come back at their column
    defaults, and the frontend decides whether to show the bars per mode
    (see `RP_MODE_DESCRIPTIONS`'s shape)."""

    mode: str
    dead: bool
    death_cause: str | None = None
    crime_enabled: bool
    next_mode_switch_eligible_at: str | None = None
    next_crime_toggle_eligible_at: str | None = None
    health: float
    hunger: float
    thirst: float
    fatigue: float
    sanity: float
    afflictions: list[ActiveAfflictionSummary]


class RpModeSwitchRequest(BaseModel):
    discord_id: int
    new_mode: RpMode


class RpModeCrimeToggleRequest(BaseModel):
    discord_id: int
    enabled: bool


def build_rp_mode_router(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> APIRouter:
    """The home page's mode-switch panel (Milestone 11) -- `/character
    mode`/`/character crime`'s dashboard equivalent, mirroring the bot
    cog's confirm-then-commit shape (the frontend shows its own
    confirmation panel first, per the feature's "abundantly clear what the
    repercussions of switching are" requirement, then calls straight
    through to `/switch`/`/crime-toggle` once the player confirms)."""
    router = APIRouter(prefix="/activity/dashboard/mode", tags=["dashboard"])

    async def _status_response(session: AsyncSession, character: Character) -> RpModeStatusResponse:
        rows = await session.execute(
            select(CharacterAffliction, AfflictionType)
            .join(AfflictionType, CharacterAffliction.affliction_type_id == AfflictionType.id)
            .where(
                CharacterAffliction.character_id == character.id,
                CharacterAffliction.cured_at.is_(None),
            )
            .order_by(CharacterAffliction.applied_at)
        )
        afflictions = [
            ActiveAfflictionSummary(
                id=affliction.id,
                name=affliction_type.name,
                description=affliction_type.description,
                is_permanent=affliction_type.is_permanent,
                cause=affliction.cause,
                applied_at=affliction.applied_at.isoformat(),
            )
            for affliction, affliction_type in rows.all()
        ]
        next_switch = rp_modes_svc.next_eligible_switch_at(character)
        next_toggle = rp_modes_svc.next_eligible_crime_toggle_at(character)
        return RpModeStatusResponse(
            mode=character.rp_mode,
            dead=character.status == CharacterStatus.DEAD.value,
            death_cause=character.death_cause,
            crime_enabled=character.crime_enabled is not False,
            next_mode_switch_eligible_at=next_switch.isoformat() if next_switch else None,
            next_crime_toggle_eligible_at=next_toggle.isoformat() if next_toggle else None,
            health=character.health,
            hunger=character.hunger,
            thirst=character.thirst,
            fatigue=character.fatigue,
            sanity=character.sanity,
            afflictions=afflictions,
        )

    @router.get("/{character_id}/status", response_model=RpModeStatusResponse)
    async def status(character_id: int, discord_id: int) -> RpModeStatusResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            return await _status_response(session, character)

    @router.post("/{character_id}/switch", response_model=RpModeStatusResponse)
    async def switch(character_id: int, body: RpModeSwitchRequest) -> RpModeStatusResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            try:
                rp_modes_svc.switch_mode(character, body.new_mode, dt.datetime.now(dt.UTC))
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            return await _status_response(session, character)

    @router.post("/{character_id}/crime-toggle", response_model=RpModeStatusResponse)
    async def crime_toggle(
        character_id: int, body: RpModeCrimeToggleRequest
    ) -> RpModeStatusResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            try:
                rp_modes_svc.toggle_crime(character, body.enabled, dt.datetime.now(dt.UTC))
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            return await _status_response(session, character)

    return router


def build_staff_router(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None,
    static_dir: Path,
    discord_token: str = "",
    discord_guild_id: int = 0,
    staff_role_id: int = 0,
    log_channel_id: int = 0,
) -> APIRouter:
    """The Staff tab's REST surface -- the dashboard equivalent of `/staff
    jail`. Staff-only: every route here re-checks `discord_staff.fetch_is_
    staff` itself rather than trusting a client-supplied flag (`/identify`'s
    `is_staff` is cosmetic, only used to decide whether `app.js` shows the
    tab at all) -- a client could lie about being staff the same way it
    could lie about owning a character, so this is checked server-side on
    every write, not read off whatever `/identify` last returned. Acts on
    any character by name (staff jail someone else's character, not their
    own), so unlike every other router in this module there's no `_resolve_
    owned_character` ownership check to reuse here."""
    router = APIRouter(prefix="/activity/dashboard/staff", tags=["dashboard"])

    async def _require_staff(discord_id: int) -> None:
        ok = await discord_staff.fetch_is_staff(
            discord_id,
            bot_token=discord_token,
            guild_id=discord_guild_id,
            staff_role_id=staff_role_id,
        )
        if not ok:
            raise HTTPException(status_code=403, detail="staff_only")

    @router.post("/jail", response_model=StaffJailResponse)
    async def jail_character(body: StaffJailRequest) -> StaffJailResponse:
        await _require_staff(body.discord_id)
        if body.ticks < 1:
            raise HTTPException(status_code=400, detail="invalid_ticks")
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = (
                await session.execute(
                    select(Character).where(Character.name == body.character_name)
                )
            ).scalar_one_or_none()
            if character is None:
                raise HTTPException(status_code=404, detail="character_not_found")
            current_tick = await _current_tick(session)
            prior_bonus = (character.jail_count or 0) * constants.JAIL_PRIOR_TICKS_PER_COUNT
            applied = jail_svc.commit_to_jail(character, body.ticks, current_tick)
            assert character.jailed_until_tick is not None  # always set by commit_to_jail
            session.add(
                StaffAction(
                    staff_discord_id=body.discord_id,
                    action="jail",
                    target=str(character.id),
                    payload={"ticks": body.ticks, "applied_ticks": applied, "reason": body.reason},
                )
            )
            response = StaffJailResponse(
                character_name=character.name,
                base_ticks=body.ticks,
                prior_bonus_ticks=prior_bonus,
                applied_ticks=applied,
                jailed_until_tick=character.jailed_until_tick,
            )
        await discord_staff.post_staff_log(
            channel_id=log_channel_id,
            bot_token=discord_token,
            content=(
                f"**Staff action:** <@{body.discord_id}> `jail` -> `{response.character_name}` "
                f"(ticks={body.ticks}, applied={applied}, reason={body.reason!r})"
            ),
        )
        return response

    # ---- Layer management (Picrew-style customizer admin) --------------
    # The upload flow this feature was actually asked for: staff add a
    # category once (a stack position + a name), then upload images into
    # it one at a time, each with its own display name. There is no seed
    # data -- these are the only way any category/option ever comes to
    # exist, matching the "start off with no options" requirement.

    @router.post("/layers/categories", response_model=LayerCategoryResponse)
    async def create_layer_category(body: CreateLayerCategoryRequest) -> LayerCategoryResponse:
        await _require_staff(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            try:
                category = await layers_svc.create_category(
                    session, name=body.name, z_index=body.z_index
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            # A freshly flushed row's `options` relationship isn't
            # automatically "loaded empty" -- reading it unrefreshed raises
            # (async sessions can't transparently lazy-load), same as
            # update_category below.
            await session.refresh(category, attribute_names=["options"])
            return _layer_category_response(category)

    @router.patch("/layers/categories/{category_id}", response_model=LayerCategoryResponse)
    async def update_layer_category(
        category_id: int, body: UpdateLayerCategoryRequest
    ) -> LayerCategoryResponse:
        await _require_staff(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            try:
                category = await layers_svc.update_category(
                    session, category_id, name=body.name, z_index=body.z_index
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            # update_category doesn't touch/load `options` (it only mutates
            # name/z_index on an already-fetched row), and accessing an
            # unloaded relationship on an async session raises rather than
            # lazy-loading -- refresh it explicitly before building the
            # response, which reads `.options`.
            await session.refresh(category, attribute_names=["options"])
            return _layer_category_response(category)

    @router.post("/layers/categories/{category_id}/delete")
    async def delete_layer_category(
        category_id: int, body: DeleteLayerCategoryRequest
    ) -> dict[str, bool]:
        await _require_staff(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            try:
                await layers_svc.delete_category(session, category_id, static_dir=static_dir)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
        return {"deleted": True}

    @router.post(
        "/layers/categories/{category_id}/options", response_model=LayerOptionResponse
    )
    async def create_layer_option(
        category_id: int,
        discord_id: int = Form(...),
        name: str = Form(...),
        file: UploadFile = File(...),  # noqa: B008 -- FastAPI's own sentinel-default idiom
    ) -> LayerOptionResponse:
        await _require_staff(discord_id)
        data = await file.read()
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            try:
                option = await layers_svc.create_option(
                    session,
                    category_id=category_id,
                    name=name,
                    content_type=file.content_type or "",
                    data=data,
                    static_dir=static_dir,
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            return LayerOptionResponse(
                id=option.id, name=option.name, image_url=f"/{option.image_path}"
            )

    @router.post("/layers/options/{option_id}/delete")
    async def delete_layer_option(
        option_id: int, body: DeleteLayerOptionRequest
    ) -> dict[str, bool]:
        await _require_staff(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            try:
                await layers_svc.delete_option(session, option_id, static_dir=static_dir)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
        return {"deleted": True}

    @router.get("/districts/mottos")
    async def get_district_mottos() -> dict[str, str]:
        mottos_file = static_dir / "district_mottos.json"
        if mottos_file.is_file():
            try:
                return json.loads(mottos_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    @router.post("/districts/motto", response_model=DistrictMottoResponse)
    async def update_district_motto(body: UpdateDistrictMottoRequest) -> DistrictMottoResponse:
        await _require_staff(body.discord_id)
        if body.district_id < 0 or body.district_id > 12:
            raise HTTPException(status_code=400, detail="invalid_district_id")
        motto = body.motto.strip()
        if not motto or len(motto) > 120:
            raise HTTPException(status_code=400, detail="invalid_motto_length")

        mottos_file = static_dir / "district_mottos.json"
        data: dict[str, str] = {}
        if mottos_file.is_file():
            try:
                data = json.loads(mottos_file.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data[str(body.district_id)] = motto
        mottos_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        await discord_staff.post_staff_log(
            channel_id=log_channel_id,
            bot_token=discord_token,
            content=(
                f"**Staff action:** <@{body.discord_id}> updated District {body.district_id} motto "
                f"to {motto!r}"
            ),
        )
        return DistrictMottoResponse(district_id=body.district_id, motto=motto)

    # ---- Affliction-type catalog admin (RP modes feature) --------------
    # Mirrors the layer-category CRUD immediately above exactly -- a flat
    # staff-authored catalog, no upload/sub-catalog needed. No staff-log
    # posting here, same as layer categories/options: this is catalog
    # management, not a punitive action worth a moderation-log entry the
    # way `/staff jail` is.

    @router.post("/affliction-types", response_model=AfflictionTypeResponse)
    async def create_affliction_type(
        body: CreateAfflictionTypeRequest,
    ) -> AfflictionTypeResponse:
        await _require_staff(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            try:
                row = await affliction_types_svc.create_type(
                    session,
                    name=body.name,
                    description=body.description,
                    is_permanent=body.is_permanent,
                    cure_stat=body.cure_stat,
                    cure_threshold=body.cure_threshold,
                    auto_apply_stat=body.auto_apply_stat,
                    auto_apply_threshold=body.auto_apply_threshold,
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            return _affliction_type_response(row)

    @router.patch("/affliction-types/{affliction_type_id}", response_model=AfflictionTypeResponse)
    async def update_affliction_type(
        affliction_type_id: int, body: UpdateAfflictionTypeRequest
    ) -> AfflictionTypeResponse:
        await _require_staff(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            try:
                row = await affliction_types_svc.update_type(
                    session,
                    affliction_type_id,
                    name=body.name,
                    description=body.description,
                    is_permanent=body.is_permanent,
                    cure_stat=body.cure_stat,
                    cure_threshold=body.cure_threshold,
                    auto_apply_stat=body.auto_apply_stat,
                    auto_apply_threshold=body.auto_apply_threshold,
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            return _affliction_type_response(row)

    @router.post("/affliction-types/{affliction_type_id}/delete")
    async def delete_affliction_type(
        affliction_type_id: int, body: DeleteAfflictionTypeRequest
    ) -> dict[str, bool]:
        await _require_staff(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            try:
                await affliction_types_svc.delete_type(session, affliction_type_id)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
        return {"deleted": True}

    return router


class DistrictLorePersonResponse(BaseModel):
    id: int
    district_id: int
    role: str
    name: str
    character_id: int | None = None
    character_name: str | None = None
    is_active: bool
    notes: str


class DistrictLoreResponse(BaseModel):
    district_id: int
    classification: str | None = None
    adjectives: list[str] = Field(default_factory=list)
    accent_notes: str = ""
    urban_rural_notes: str = ""
    academy_name: str | None = None
    academy_notes: str = ""
    games_history: str = ""
    regime_notes: str = ""
    opinions: dict[str, str] = Field(default_factory=dict)
    misc_notes: str = ""
    updated_by: int | None = None
    people: list[DistrictLorePersonResponse] = Field(default_factory=list)


class DistrictLoreCatalogResponse(BaseModel):
    districts: list[DistrictLoreResponse]


class UpsertDistrictLoreRequest(BaseModel):
    discord_id: int
    classification: str | None = None
    adjectives: list[str] = Field(default_factory=list)
    accent_notes: str = ""
    urban_rural_notes: str = ""
    academy_name: str | None = None
    academy_notes: str = ""
    games_history: str = ""
    regime_notes: str = ""
    opinions: dict[str, str] = Field(default_factory=dict)
    misc_notes: str = ""


class CreateDistrictLorePersonRequest(BaseModel):
    discord_id: int
    role: str
    name: str
    character_id: int | None = None
    is_active: bool = True
    notes: str = ""


class UpdateDistrictLorePersonRequest(BaseModel):
    discord_id: int
    role: str | None = None
    name: str | None = None
    character_id: int | None = None
    character_id_set: bool = False
    is_active: bool | None = None
    notes: str | None = None


class DeleteDistrictLorePersonRequest(BaseModel):
    discord_id: int


def _district_lore_person_response(row: DistrictLorePerson) -> DistrictLorePersonResponse:
    return DistrictLorePersonResponse(
        id=row.id,
        district_id=row.district_id,
        role=row.role,
        name=row.name,
        character_id=row.character_id,
        character_name=row.character.name if row.character is not None else None,
        is_active=row.is_active,
        notes=row.notes,
    )


def _district_lore_response(
    district_id: int, lore: DistrictLore | None, people: list[DistrictLorePerson]
) -> DistrictLoreResponse:
    base = DistrictLoreResponse(district_id=district_id)
    if lore is not None:
        base = DistrictLoreResponse(
            district_id=district_id,
            classification=lore.classification,
            adjectives=list(lore.adjectives),
            accent_notes=lore.accent_notes,
            urban_rural_notes=lore.urban_rural_notes,
            academy_name=lore.academy_name,
            academy_notes=lore.academy_notes,
            games_history=lore.games_history,
            regime_notes=lore.regime_notes,
            opinions=dict(lore.opinions),
            misc_notes=lore.misc_notes,
            updated_by=lore.updated_by,
        )
    base.people = [_district_lore_person_response(p) for p in people]
    return base


def build_district_lore_router(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None,
    discord_token: str = "",
    discord_guild_id: int = 0,
    staff_role_id: int = 0,
) -> APIRouter:
    """The Activity's staff-only History tab: staff-authored district
    context (adjectives, accent notes, urban/rural feel, career academy
    naming, a natural-language Games-performance summary, opinions of other
    districts, inner/outlier classification, and victor/mentor rosters)
    that `panem_bot.services.dialogue` folds a short, capped excerpt of
    into an NPC's prompt (see `panem_shared.district_lore.prompt_summary`).

    Every route here re-checks `discord_staff.fetch_is_staff` itself, same
    posture as `build_staff_router` -- this is staff-only content (district
    opinions, regime notes) that a player's own dashboard session should
    never be able to read or write just by knowing the URL, not only a
    write-time check."""
    router = APIRouter(prefix="/activity/dashboard/history", tags=["dashboard"])

    async def _require_staff(discord_id: int) -> None:
        ok = await discord_staff.fetch_is_staff(
            discord_id,
            bot_token=discord_token,
            guild_id=discord_guild_id,
            staff_role_id=staff_role_id,
        )
        if not ok:
            raise HTTPException(status_code=403, detail="staff_only")

    @router.get("/districts", response_model=DistrictLoreCatalogResponse)
    async def all_district_lore(discord_id: int) -> DistrictLoreCatalogResponse:
        await _require_staff(discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            lore_by_district = {
                row.district_id: row for row in await district_lore_svc.list_lore(session)
            }
            people_by_district: dict[int, list[DistrictLorePerson]] = {}
            for person in await district_lore_svc.list_people_all(session):
                people_by_district.setdefault(person.district_id, []).append(person)
            districts = [
                _district_lore_response(
                    did, lore_by_district.get(did), people_by_district.get(did, [])
                )
                for did in range(13)
            ]
            return DistrictLoreCatalogResponse(districts=districts)

    @router.get("/districts/{district_id}", response_model=DistrictLoreResponse)
    async def one_district_lore(district_id: int, discord_id: int) -> DistrictLoreResponse:
        await _require_staff(discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            lore = await district_lore_svc.get_lore(session, district_id)
            people = await district_lore_svc.list_people(session, district_id)
            return _district_lore_response(district_id, lore, people)

    @router.put("/districts/{district_id}", response_model=DistrictLoreResponse)
    async def save_district_lore(
        district_id: int, body: UpsertDistrictLoreRequest
    ) -> DistrictLoreResponse:
        await _require_staff(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            try:
                lore = await district_lore_svc.upsert_lore(
                    session,
                    district_id,
                    classification=body.classification,
                    adjectives=body.adjectives,
                    accent_notes=body.accent_notes,
                    urban_rural_notes=body.urban_rural_notes,
                    academy_name=body.academy_name,
                    academy_notes=body.academy_notes,
                    games_history=body.games_history,
                    regime_notes=body.regime_notes,
                    opinions=body.opinions,
                    misc_notes=body.misc_notes,
                    updated_by=body.discord_id,
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            people = await district_lore_svc.list_people(session, district_id)
            return _district_lore_response(district_id, lore, people)

    @router.post("/districts/{district_id}/people", response_model=DistrictLorePersonResponse)
    async def create_district_lore_person(
        district_id: int, body: CreateDistrictLorePersonRequest
    ) -> DistrictLorePersonResponse:
        await _require_staff(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            try:
                row = await district_lore_svc.create_person(
                    session,
                    district_id=district_id,
                    role=body.role,
                    name=body.name,
                    character_id=body.character_id,
                    is_active=body.is_active,
                    notes=body.notes,
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            if row.character_id is not None:
                await session.refresh(row, attribute_names=["character"])
            return _district_lore_person_response(row)

    @router.patch("/people/{person_id}", response_model=DistrictLorePersonResponse)
    async def update_district_lore_person(
        person_id: int, body: UpdateDistrictLorePersonRequest
    ) -> DistrictLorePersonResponse:
        await _require_staff(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            try:
                row = await district_lore_svc.update_person(
                    session,
                    person_id,
                    role=body.role,
                    name=body.name,
                    character_id=body.character_id,
                    character_id_set=body.character_id_set,
                    is_active=body.is_active,
                    notes=body.notes,
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            await session.refresh(row, attribute_names=["character"])
            return _district_lore_person_response(row)

    @router.post("/people/{person_id}/delete")
    async def delete_district_lore_person(
        person_id: int, body: DeleteDistrictLorePersonRequest
    ) -> dict[str, bool]:
        await _require_staff(body.discord_id)
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            try:
                await district_lore_svc.delete_person(session, person_id)
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
        return {"deleted": True}

    return router
