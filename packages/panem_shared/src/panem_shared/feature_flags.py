"""Admin-toggleable kill switches for beta-stage tabs/commands/systems --
backed by the `feature_flags` table (`panem_shared.db.models.FeatureFlag`,
one row per key, absent row == ENABLED). Written from two places: `/staff
feature set` (`panem_bot.cogs.staff`) and the Activity's Staff tab flags
panel (`dashboard_routes.py`'s `/activity/dashboard/staff/flags/set`).
Read from: `dashboard_routes.identify` (which tabs to hide from `app.js`),
`cogs/dialogue.py`'s `/talk`, `cogs/engagements.py`'s `/engage start` (NPC
branch), and `cogs/proxy.py`'s NPC-reply trigger -- the concrete "disable
NPC roleplay interaction" ask this module was built for, generalized into
a reusable key/state store rather than one bespoke bool for just that one
case, so the same mechanism also covers "disable the Map tab for the
public while it's WIP" and any future tab/command a release isn't ready
to show everyone yet.

Three states, not a plain bool:
  ENABLED    -- visible/usable by everyone.
  STAFF_ONLY -- staff bypass it, everyone else is blocked/doesn't see it.
  DISABLED   -- blocked/hidden for literally everyone, staff included --
               a true kill switch for something not ready to show at all.
"""

from __future__ import annotations

import enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared.db.models import FeatureFlag
from panem_shared.errors import NotAllowed


class FlagState(enum.StrEnum):
    ENABLED = "enabled"
    STAFF_ONLY = "staff_only"
    DISABLED = "disabled"


# The one flag gating NPC roleplay interaction specifically: `/talk`,
# `/engage` with an NPC named, and the reply either of those (or a plain
# proxied message in a scene an NPC already joined) triggers. NPCs keep
# showing up on the Social tab and keep working their jobs for the economy
# either way -- this only ever governs a *player* starting or continuing
# a conversation with one.
NPC_INTERACTION = "npc_interaction"

_TAB_PREFIX = "tab:"
_COMMAND_PREFIX = "command:"


def tab_key(tab_name: str) -> str:
    return f"{_TAB_PREFIX}{tab_name}"


def command_key(command_name: str) -> str:
    return f"{_COMMAND_PREFIX}{command_name}"


# Mirrors `static/app.js`'s `TABS`/`STAFF_ONLY_TABS` -- there's no single
# source of truth shared between this process and the static frontend
# bundle, so this is kept in sync by hand. Only used to pre-populate
# `/staff feature list`'s/the Staff tab panel's display and the `key`
# autocomplete; `set_state` accepts any string key, so a tab that's missing
# here can still be flagged by typing its literal `tab:<name>` -- it just
# won't show up as a suggestion until this tuple is updated too.
ACTIVITY_TABS: tuple[str, ...] = (
    "home",
    "map",
    "character",
    "vitals",
    "work",
    "market",
    "travel",
    "social",
    "jail",
    "crime",
    "housing",
    "pets",
    "hobbies",
    "museum",
    "weather",
    "bulletin",
    "rumors",
    "events",
    "reactions",
    "staff",
    "history",
    "games",
)

KNOWN_FLAGS: dict[str, str] = {
    NPC_INTERACTION: "NPC roleplay interaction (/talk, /engage with NPCs)",
    **{tab_key(name): f"Activity tab: {name}" for name in ACTIVITY_TABS},
}


def _coerce(raw: str) -> FlagState:
    try:
        return FlagState(raw)
    except ValueError:
        return FlagState.ENABLED


async def get_state(session: AsyncSession, key: str) -> FlagState:
    row = await session.get(FeatureFlag, key)
    return FlagState.ENABLED if row is None else _coerce(row.state)


async def list_states(session: AsyncSession) -> dict[str, FlagState]:
    """Every flag that's ever been set away from the default, keyed by its
    `key`. A key with no row (still ENABLED) is simply absent -- callers
    that want to show the full catalog combine this with `KNOWN_FLAGS`."""
    rows = (await session.execute(select(FeatureFlag))).scalars().all()
    return {row.key: _coerce(row.state) for row in rows}


async def set_state(
    session: AsyncSession,
    key: str,
    state: FlagState,
    *,
    updated_by_discord_id: int | None = None,
) -> FeatureFlag:
    row = await session.get(FeatureFlag, key)
    if row is None:
        row = FeatureFlag(key=key, state=state.value, updated_by_discord_id=updated_by_discord_id)
        session.add(row)
    else:
        row.state = state.value
        row.updated_by_discord_id = updated_by_discord_id
    await session.flush()
    return row


def is_allowed(state: FlagState, *, is_staff: bool) -> bool:
    if state is FlagState.DISABLED:
        return False
    if state is FlagState.STAFF_ONLY:
        return is_staff
    return True


async def require_allowed(
    session: AsyncSession,
    key: str,
    *,
    is_staff: bool,
    reason_disabled: str,
    reason_staff_only: str,
    **fmt: object,
) -> None:
    """Raises `NotAllowed(reason_disabled | reason_staff_only)` unless
    `key`'s current state permits `is_staff` -- the one check every
    try/except-style call site (e.g. `cogs/dialogue.py`'s `/talk`) shares.
    A call site that already branches on state directly (e.g.
    `cogs/engagements.py`'s `/engage start`, which isn't inside a
    try/except there) can instead call `get_state`/`is_allowed` itself."""
    state = await get_state(session, key)
    if is_allowed(state, is_staff=is_staff):
        return
    reason = reason_staff_only if state is FlagState.STAFF_ONLY else reason_disabled
    raise NotAllowed(reason, **fmt)
