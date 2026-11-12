"""The Bulletin -- every announced-or-rumored event in the world/district
in one place (Spec "Bulletin"). `BulletinEntry` is a snapshot, not a join:
once posted, its display fields (`title`/`body`/`district_id`/...) stay
stable even if whatever produced it (a `WeatherEvent`, a `GlobalEvent`, a
`Rumor`) is later deleted or changes, the same reasoning `CrimeLog.
target_name` already documents for this codebase.

Per-viewer state (`BulletinUserState`: interest marking, manual drag
position, a free-text category) and reminders (`BulletinReminder`) are
kept separate from the entry itself since they're never shared between
viewers -- Spec: "you can organize it freely," meaning *your* view, not
the board's own ordering.

Deliberately has no opinion on what tag/label a `source_kind` displays as
("Admin hosted"/"Player hosted"/"NPC hosted"/a plain event-type label) --
that's cosmetic, picked by `panem_bot.cogs.bulletin` (NFR-10: services
don't format user-facing strings), same as every other service module
here.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared.db.models import BulletinEntry, BulletinReminder, BulletinUserState, User
from panem_shared.enums import BulletinSourceKind
from panem_shared.errors import NotAllowed, NotFound, ValidationFailed


async def record_system_entry(
    session: AsyncSession,
    *,
    source_kind: BulletinSourceKind,
    source_id: int | None,
    tag: str,
    title: str,
    body: str = "",
    district_id: int | None,
    location_id: str | None = None,
    posted_tick: int,
    event_tick: int | None = None,
    requirements: str | None = None,
    danger_level: int | None = None,
) -> BulletinEntry:
    """Posted by whatever system produced the thing being announced
    (`panem_bot.cogs.weather`'s poll, `panem_shared.global_events.
    create_event`'s caller, a rumor spawn) -- never by a player directly,
    so there's no `posted_by_user_id` here (`post_player_entry` is that
    path)."""
    entry = BulletinEntry(
        source_kind=source_kind.value,
        source_id=source_id,
        tag=tag,
        title=title,
        body=body,
        district_id=district_id,
        location_id=location_id,
        posted_tick=posted_tick,
        event_tick=event_tick,
        requirements=requirements,
        danger_level=danger_level,
    )
    session.add(entry)
    return entry


async def post_player_entry(
    session: AsyncSession,
    *,
    user: User,
    tag: str,
    title: str,
    body: str,
    district_id: int | None,
    location_id: str | None,
    posted_tick: int,
    event_tick: int | None,
    requirements: str | None,
    npc_hosted: bool = False,
) -> BulletinEntry:
    """Raises `ValidationFailed` for an empty title. `npc_hosted` just
    picks `NPC_POST` over `PLAYER_POST` for the stored `source_kind` --
    staff posting *as* an NPC host, not a separate permission check
    (`/bulletin post` is open to every player; staff pick the NPC flavor
    themselves when narrating on an NPC's behalf)."""
    if not title.strip():
        raise ValidationFailed("bulletin_title_required")
    entry = BulletinEntry(
        source_kind=(
            BulletinSourceKind.NPC_POST if npc_hosted else BulletinSourceKind.PLAYER_POST
        ).value,
        source_id=None,
        tag=tag,
        title=title.strip(),
        body=body.strip(),
        district_id=district_id,
        location_id=location_id,
        posted_tick=posted_tick,
        event_tick=event_tick,
        requirements=requirements,
        posted_by_user_id=user.id,
    )
    session.add(entry)
    return entry


async def retract(session: AsyncSession, *, user: User, entry_id: int) -> BulletinEntry:
    """Raises `NotFound` for an unknown/already-retracted entry, `NotAllowed`
    if it wasn't this user's own post (system-generated entries can't be
    retracted by anyone through this path at all)."""
    entry = await session.get(BulletinEntry, entry_id)
    if entry is None or entry.retracted:
        raise NotFound("bulletin_entry_not_found")
    if entry.posted_by_user_id != user.id:
        raise NotAllowed("bulletin_not_yours")
    entry.retracted = True
    return entry


async def _user_states(
    session: AsyncSession, user_id: int, entry_ids: list[int]
) -> dict[int, BulletinUserState]:
    if not entry_ids:
        return {}
    result = await session.execute(
        select(BulletinUserState).where(
            BulletinUserState.user_id == user_id, BulletinUserState.entry_id.in_(entry_ids)
        )
    )
    return {row.entry_id: row for row in result.scalars().all()}


async def board_for(
    session: AsyncSession, *, district_id: int | None, viewer_user_id: int
) -> list[tuple[BulletinEntry, BulletinUserState | None]]:
    """Every live (non-retracted) entry for `district_id` plus every
    world-wide one (`district_id IS NULL`), paired with the viewer's own
    state if any. Sorted by the viewer's manual `sort_position` first
    (lower = earlier), falling back to start time/date (`event_tick`,
    then `posted_tick`) -- Spec's documented default -- for everything
    they haven't dragged."""
    result = await session.execute(
        select(BulletinEntry).where(
            BulletinEntry.retracted.is_(False),
            (BulletinEntry.district_id.is_(None)) | (BulletinEntry.district_id == district_id),
        )
    )
    entries = list(result.scalars().all())
    states = await _user_states(session, viewer_user_id, [e.id for e in entries])

    def sort_key(entry: BulletinEntry) -> tuple[int, int, int]:
        state = states.get(entry.id)
        has_manual = state is not None and state.sort_position is not None
        manual_position = (
            state.sort_position if state is not None and state.sort_position is not None else 0
        )
        return (
            0 if has_manual else 1,
            manual_position,
            entry.event_tick if entry.event_tick is not None else entry.posted_tick,
        )

    ordered = sorted(entries, key=sort_key)
    return [(entry, states.get(entry.id)) for entry in ordered]


async def _get_or_create_state(
    session: AsyncSession, *, user_id: int, entry_id: int
) -> BulletinUserState:
    state = await session.get(BulletinUserState, (user_id, entry_id))
    if state is None:
        state = BulletinUserState(user_id=user_id, entry_id=entry_id)
        session.add(state)
    return state


async def set_interest(
    session: AsyncSession, *, user: User, entry_id: int, interested: bool
) -> BulletinEntry:
    entry = await session.get(BulletinEntry, entry_id)
    if entry is None or entry.retracted:
        raise NotFound("bulletin_entry_not_found")
    state = await _get_or_create_state(session, user_id=user.id, entry_id=entry_id)
    state.interested = interested
    return entry


async def move_entry(
    session: AsyncSession, *, user: User, entry_id: int, position: int, category: str | None
) -> BulletinEntry:
    """Manual drag-organize -- per-viewer, never changes the board for
    anyone else."""
    entry = await session.get(BulletinEntry, entry_id)
    if entry is None or entry.retracted:
        raise NotFound("bulletin_entry_not_found")
    state = await _get_or_create_state(session, user_id=user.id, entry_id=entry_id)
    state.sort_position = position
    if category is not None:
        state.category = category
    return entry


async def reset_organization(session: AsyncSession, *, user_id: int) -> None:
    """The "reset to default" button -- clears every one of this user's
    manual `sort_position`s back to `None` so the plain start-time/date
    sort applies again; categories are left alone (a separate, explicit
    choice from reordering)."""
    result = await session.execute(
        select(BulletinUserState).where(BulletinUserState.user_id == user_id)
    )
    for state in result.scalars().all():
        state.sort_position = None


async def schedule_reminder(
    session: AsyncSession, *, user: User, entry: BulletinEntry, lead_ticks: int, current_tick: int
) -> BulletinReminder:
    """Raises `ValidationFailed` if the entry has no `event_tick` to count
    down from. Replaces any existing reminder this user already set for
    this entry (re-asking just moves the lead time) rather than erroring
    on the `UniqueConstraint`."""
    if entry.event_tick is None:
        raise ValidationFailed("bulletin_reminder_needs_time")
    remind_at = max(current_tick, entry.event_tick - max(lead_ticks, 0))
    existing = (
        await session.execute(
            select(BulletinReminder).where(
                BulletinReminder.user_id == user.id, BulletinReminder.entry_id == entry.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.remind_at_tick = remind_at
        return existing
    reminder = BulletinReminder(user_id=user.id, entry_id=entry.id, remind_at_tick=remind_at)
    session.add(reminder)
    return reminder


async def due_reminders(
    session: AsyncSession, current_tick: int
) -> list[tuple[BulletinReminder, BulletinEntry, User]]:
    """`panem_bot.cogs.bulletin`'s poll calls this once per tick it
    advances through, DMs each result, then deletes the `BulletinReminder`
    row itself (not this function -- the DM send can fail/be forbidden,
    and the caller is what knows whether to retry or just give up)."""
    result = await session.execute(
        select(BulletinReminder, BulletinEntry, User)
        .join(BulletinEntry, BulletinEntry.id == BulletinReminder.entry_id)
        .join(User, User.id == BulletinReminder.user_id)
        .where(BulletinReminder.remind_at_tick <= current_tick)
    )
    return [(reminder, entry, user) for reminder, entry, user in result.all()]
