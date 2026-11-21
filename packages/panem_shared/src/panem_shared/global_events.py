"""Global events -- staff-scheduled (eventually: automatic, Spec's "once a
week" cadence, not yet implemented), hobby-themed competitions anyone in
range can contribute to just by playing that hobby normally while the
event is `ACTIVE`. There's no separate "sign up" step: `record_progress`
auto-creates a character's `GlobalEventEntry` the first time they score,
the Hypixel-Jacob's-Contest shape Spec explicitly calls out -- a
character already pursuing the hobby has the natural edge Spec asks for
simply because their existing hobby mastery bonus applies to every one
of those sessions, no separate competition skill check.

`panem_bot.cogs.events`'s own background loop is what actually flips
`SCHEDULED` -> `ACTIVE` -> `FINISHED` (and distributes rewards) off the
two tick columns, the same "real-time poll, not `panem_sim`'s per-tick
`WorldState`" choice `panem_shared.weather` already makes and explains in
its own module docstring; `conclude_event` here is the one function
doing the rewarding, shared by that loop and `/staff event conclude`'s
manual early-end override.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.db.models import Character, GlobalEvent, GlobalEventEntry, Inventory
from panem_shared.enums import GlobalEventStatus, OwnerKind
from panem_shared.errors import NotAllowed, NotFound, ValidationFailed


async def create_event(
    session: AsyncSession,
    *,
    hobby_id: str,
    name: str,
    description: str,
    district_id: int | None,
    starts_tick: int,
    ends_tick: int,
    content: ContentBundle,
) -> GlobalEvent:
    if hobby_id not in content.hobbies:
        raise NotFound("hobby_not_found")
    if ends_tick <= starts_tick:
        raise ValidationFailed("global_event_bad_window")
    event = GlobalEvent(
        hobby_id=hobby_id,
        name=name,
        description=description,
        district_id=district_id,
        status=GlobalEventStatus.SCHEDULED.value,
        starts_tick=starts_tick,
        ends_tick=ends_tick,
    )
    session.add(event)
    return event


async def active_events_for(
    session: AsyncSession, *, district_id: int, hobby_id: str
) -> list[GlobalEvent]:
    result = await session.execute(
        select(GlobalEvent).where(
            GlobalEvent.status == GlobalEventStatus.ACTIVE.value,
            GlobalEvent.hobby_id == hobby_id,
            (GlobalEvent.district_id.is_(None)) | (GlobalEvent.district_id == district_id),
        )
    )
    return list(result.scalars().all())


async def record_progress(
    session: AsyncSession, *, event: GlobalEvent, character_id: int, amount: float = 1.0
) -> tuple[GlobalEventEntry, bool]:
    """Returns `(entry, joined_now)` -- `joined_now` is `True` exactly
    once, the first time this character scores against this event, so
    the caller (`panem_bot.cogs.hobbies`) can show the one-time "you're
    in" notice (Spec's no-separate-sign-up-step join) instead of a plain
    score update on every later session."""
    entry = await session.get(GlobalEventEntry, (event.id, character_id))
    joined_now = entry is None
    if entry is None:
        entry = GlobalEventEntry(event_id=event.id, character_id=character_id)
        session.add(entry)
    entry.score += amount
    entry.attempts += 1
    return entry, joined_now


async def leaderboard(
    session: AsyncSession, event_id: int, limit: int = 10
) -> list[GlobalEventEntry]:
    result = await session.execute(
        select(GlobalEventEntry)
        .where(GlobalEventEntry.event_id == event_id)
        .order_by(GlobalEventEntry.score.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def _grant(session: AsyncSession, character_id: int, *, money: int, tickets: int) -> None:
    character = await session.get(Character, character_id)
    if character is not None:
        character.money += money
    if tickets:
        owner_id = str(character_id)
        inv = await session.get(Inventory, (OwnerKind.CHARACTER.value, owner_id, "event_ticket"))
        if inv is None:
            session.add(
                Inventory(
                    owner_kind=OwnerKind.CHARACTER.value,
                    owner_id=owner_id,
                    good_id="event_ticket",
                    qty=tickets,
                )
            )
        else:
            inv.qty += tickets


@dataclass(frozen=True, slots=True)
class ConcludeResult:
    winner_character_id: int | None
    participant_count: int


async def conclude_event(session: AsyncSession, *, event: GlobalEvent) -> ConcludeResult:
    """Idempotent-ish: rewarding only ever happens for an entry with
    `rewarded=False`, so calling this twice (the sim's automatic end-tick
    sweep, then a staff `/staff event conclude` on the same event) never
    double-pays anyone."""
    entries = await leaderboard(session, event.id, limit=1000)
    event.status = GlobalEventStatus.FINISHED.value
    if not entries:
        return ConcludeResult(winner_character_id=None, participant_count=0)

    winner = entries[0]
    for entry in entries:
        if entry.rewarded:
            continue
        if entry is winner:
            await _grant(
                session,
                entry.character_id,
                money=constants.GLOBAL_EVENT_MONEY_REWARD_TOP,
                tickets=constants.GLOBAL_EVENT_TICKET_REWARD_TOP,
            )
        else:
            await _grant(
                session,
                entry.character_id,
                money=constants.GLOBAL_EVENT_MONEY_REWARD_PARTICIPANT,
                tickets=constants.GLOBAL_EVENT_TICKET_REWARD_PARTICIPANT,
            )
        entry.rewarded = True
    return ConcludeResult(winner_character_id=winner.character_id, participant_count=len(entries))


async def cancel_event(session: AsyncSession, *, event: GlobalEvent) -> None:
    """Raises `NotAllowed` if already `FINISHED`/`CANCELLED`. Unlike
    `conclude_event`, pays nobody out -- a cancelled event is called off,
    not finished early."""
    if event.status in (GlobalEventStatus.FINISHED.value, GlobalEventStatus.CANCELLED.value):
        raise NotAllowed("global_event_already_over")
    event.status = GlobalEventStatus.CANCELLED.value


async def due_transitions(
    session: AsyncSession, *, current_tick: int
) -> tuple[list[GlobalEvent], list[GlobalEvent]]:
    """`panem_bot.cogs.events`'s poll calls this once per tick it advances
    through. Returns `(newly_active, newly_finished)` -- `newly_finished`
    events have already been rewarded (via `conclude_event`) by the time
    they're returned, so the caller only needs to announce them."""
    newly_active_result = await session.execute(
        select(GlobalEvent).where(
            GlobalEvent.status == GlobalEventStatus.SCHEDULED.value,
            GlobalEvent.starts_tick <= current_tick,
        )
    )
    newly_active = list(newly_active_result.scalars().all())
    for event in newly_active:
        event.status = GlobalEventStatus.ACTIVE.value

    newly_due_result = await session.execute(
        select(GlobalEvent).where(
            GlobalEvent.status == GlobalEventStatus.ACTIVE.value,
            GlobalEvent.ends_tick <= current_tick,
        )
    )
    newly_finished = list(newly_due_result.scalars().all())
    for event in newly_finished:
        await conclude_event(session, event=event)

    return newly_active, newly_finished
