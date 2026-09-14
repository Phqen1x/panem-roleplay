"""Shift lifecycle: opening shifts, NPC job completion, and
consecutive-miss tracking -> a mastery penalty (Spec FR-JOB-2/6/7/10).

Two populations, handled differently:

- Player characters get a real `Shift` row opened once per day, at the
  exact tick their job's `shift_phase` begins (`time.is_phase_boundary`).
  `/work` or an RP-credited proxy message (both outside the tick loop,
  in `panem_bot.services.shifts`) resolve it; a shift whose `tick_due`
  passes still unresolved is marked MISSED here, which increments
  `Character.consecutive_missed`. Missing shifts no longer costs a
  character their job -- once the streak reaches
  `MISSES_TO_MASTERY_PENALTY` (3) or more, every further miss instead
  costs `SHIFT_MASTERY_MISS_PENALTY` off `Character.shifts_completed`
  (floored at 0), eroding job-level progress the same way working a
  shift builds it up. `consecutive_missed` resets to 0 on any resolution
  outside this system (a completed or excused shift), so this system
  only ever increments it.
- NPCs have no `Shift` rows at all (`shifts.character_id` is a FK to
  `characters`, not `npcs`) and no player to notify, so an NPC with a
  matching job just probabilistically "completes" it in place each
  phase boundary -- feeding `Npc.money` -- with no miss-tracking at all.

Job-status changes (a shift missed, a mastery penalty) are pure DB-state
changes in this milestone; there's no player-facing push notification yet
(that would need a new DM-capable `WorldEvent` kind, which is more than
this milestone's scope) -- a character finds out from `/character status`
(shifts_completed/level dropping) rather than a proactive DM. A missed
streak never touches `Character.job_title`/`shift_phase` -- a job is only
ever cleared or changed by staff (`/staff give job`), never by the sim.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared.content.schemas import Job
from panem_shared.db.models import Character, Shift
from panem_shared.enums import OwnerKind, ShiftResult
from panem_shared.events import AnyWorldEvent
from panem_sim.state import NotableEvent, TickContext, WorldState
from panem_sim.systems.time import is_phase_boundary


def _job_for(ctx: TickContext, job_id: str | None) -> Job | None:
    if job_id is None:
        return None
    return ctx.content.jobs.get(job_id)


def _is_within_travel_grace(character: Character, ctx: TickContext) -> bool:
    """FR-LOC-9: a shift missed while physically en route is always
    excused; one missed while visiting another district still is, for
    `AWAY_GRACE_DAYS` from the tick they first left home -- long enough
    to cover a short trip, not so long that leaving home is a permanent
    way to dodge the missed-shift mastery penalty."""
    if character.in_transit_until_tick is not None and character.in_transit_until_tick >= ctx.tick:
        return True
    if character.away_since_tick is None:
        return False
    return (
        ctx.tick - character.away_since_tick <= constants.AWAY_GRACE_DAYS * constants.TICKS_PER_DAY
    )


def _is_within_work_game_grace(shift: Shift, ctx: TickContext) -> bool:
    """`/work`'s minigame (Minesweeper, `panem_api`'s Activity frontend)
    can still be in progress past `tick_due` -- a shift whose game was
    actually launched (`Shift.started_at_tick`) gets `WORK_GAME_GRACE_TICKS`
    before falling through to the travel-grace/miss logic below, so a
    player is paid for having started before the deadline even if they
    finish the board after it."""
    if shift.started_at_tick is None:
        return False
    return ctx.tick - shift.tick_due <= constants.WORK_GAME_GRACE_TICKS


def _resolve_missed_shifts(state: WorldState, ctx: TickContext) -> None:
    still_open: list[Shift] = []
    for shift in state.open_shifts:
        if shift.tick_due > ctx.tick:
            still_open.append(shift)
            continue
        if _is_within_work_game_grace(shift, ctx):
            still_open.append(shift)
            continue
        if shift.last_worked_tick is not None:
            # Worked at least once (`panem_shared.shifts.apply_shift_outcome`
            # no longer closes a shift on its first `/work` -- it stays open
            # for the whole shift so it can be worked again on a later
            # tick), so its deadline passing closes it as done rather than
            # missed. `consecutive_missed` was already reset at that work.
            shift.result = ShiftResult.COMPLETED.value
            shift.completed_at = ctx.tick
            continue

        character = state.characters.get(shift.character_id)
        if character is not None and _is_within_travel_grace(character, ctx):
            shift.result = ShiftResult.EXCUSED.value
            character.consecutive_missed = 0
            continue

        shift.result = ShiftResult.MISSED.value
        if character is None:
            continue
        character.consecutive_missed += 1
        character.reputation -= constants.REP_MISS_PENALTY
        if character.consecutive_missed >= constants.MISSES_TO_MASTERY_PENALTY:
            _apply_mastery_penalty(state, character)

    state.open_shifts = still_open


def _apply_mastery_penalty(state: WorldState, character: Character) -> None:
    """Replaces the old firing behavior: once `consecutive_missed` reaches
    `MISSES_TO_MASTERY_PENALTY` (3) or more, every further miss chips
    `SHIFT_MASTERY_MISS_PENALTY` off the character's `shifts_completed`
    instead of taking their job away -- the same counter `/work` builds up
    one at a time, so a habitual no-show's job-level progress erodes for as
    long as the streak continues rather than resetting once and stopping."""
    character.shifts_completed = max(
        0, character.shifts_completed - constants.SHIFT_MASTERY_MISS_PENALTY
    )
    state.notable_events.append(
        NotableEvent(
            owner_kind=OwnerKind.CHARACTER.value,
            owner_id=str(character.id),
            kind="mastery_slip",
            importance=2,
            text=f"{character.name}'s skills have grown rusty after missing too many shifts.",
            tags=["job", "mastery"],
        )
    )


def _open_shifts_for_due_characters(state: WorldState, ctx: TickContext) -> None:
    if not is_phase_boundary(ctx.tick):
        return

    already_open = {shift.character_id for shift in state.open_shifts}
    for character in state.characters.values():
        if character.id in already_open:
            continue
        if character.job_title is None or character.shift_phase != ctx.phase:
            continue

        shift = Shift(
            character_id=character.id,
            job_id=character.job_title,
            tick_opened=ctx.tick,
            tick_due=ctx.tick + constants.SHIFT_DURATION_TICKS,
            result=None,
        )
        state.open_shifts.append(shift)
        state.new_shifts.append(shift)


def _apply_npc_job_completion(state: WorldState, ctx: TickContext) -> None:
    if not is_phase_boundary(ctx.tick):
        return

    for npc in state.npcs.values():
        job = _job_for(ctx, npc.job_id)
        if job is None or job.shift_phase != ctx.phase:
            continue
        if ctx.rng.random() < constants.NPC_JOB_COMPLETION_PROB:
            npc.money += job.wage


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    _resolve_missed_shifts(state, ctx)
    _open_shifts_for_due_characters(state, ctx)
    _apply_npc_job_completion(state, ctx)
    return []
