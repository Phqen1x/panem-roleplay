"""Shift lifecycle: opening shifts, NPC job completion, and
consecutive-miss tracking -> firing (Spec FR-JOB-2/6/7/10).

Two populations, handled differently:

- Player characters get a real `Shift` row opened once per day, at the
  exact tick their job's `shift_phase` begins (`time.is_phase_boundary`).
  `/work` or an RP-credited proxy message (both outside the tick loop,
  in `panem_bot.services.shifts`) resolve it; a shift whose `tick_due`
  passes still unresolved is marked MISSED here, which increments
  `Character.consecutive_missed` and fires the character past
  `MISSES_TO_FIRE` (job cleared, a `JobHistory` row recorded).
  `consecutive_missed` resets to 0 on any resolution outside this system
  (a completed or excused shift), so this system only ever increments it.
- NPCs have no `Shift` rows at all (`shifts.character_id` is a FK to
  `characters`, not `npcs`) and no player to notify, so an NPC with a
  matching job just probabilistically "completes" it in place each
  phase boundary -- feeding `Npc.money` -- with no miss-tracking at all.

Job-status changes (a shift missed, a firing) are pure DB-state changes
in this milestone; there's no player-facing push notification yet (that
would need a new DM-capable `WorldEvent` kind, which is more than this
milestone's scope) -- a fired character finds out via `/job list` or by
`/work` failing next time, not a proactive DM.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared.content.schemas import Job
from panem_shared.db.models import Character, JobHistory, Shift
from panem_shared.enums import ShiftResult
from panem_shared.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState
from panem_sim.systems.time import is_phase_boundary


def _job_for(ctx: TickContext, job_id: str | None) -> Job | None:
    if job_id is None:
        return None
    return ctx.content.jobs.get(job_id)


def _resolve_missed_shifts(state: WorldState, ctx: TickContext) -> None:
    still_open: list[Shift] = []
    for shift in state.open_shifts:
        if shift.tick_due > ctx.tick:
            still_open.append(shift)
            continue

        shift.result = ShiftResult.MISSED.value
        character = state.characters.get(shift.character_id)
        if character is None:
            continue
        character.consecutive_missed += 1
        if character.consecutive_missed >= constants.MISSES_TO_FIRE:
            _fire(state, character, shift.job_id, ctx)

    state.open_shifts = still_open


def _fire(state: WorldState, character: Character, job_id: str, ctx: TickContext) -> None:
    state.new_job_history.append(
        JobHistory(
            character_id=character.id,
            job_id=job_id,
            started_tick=character.job_started_tick or ctx.tick,
            ended_tick=ctx.tick,
            reason="fired",
        )
    )
    character.job_id = None
    character.job_started_tick = None
    character.consecutive_missed = 0


def _open_shifts_for_due_characters(state: WorldState, ctx: TickContext) -> None:
    if not is_phase_boundary(ctx.tick):
        return

    already_open = {shift.character_id for shift in state.open_shifts}
    for character in state.characters.values():
        if character.id in already_open:
            continue
        job = _job_for(ctx, character.job_id)
        if job is None or job.shift_phase != ctx.phase:
            continue

        shift = Shift(
            character_id=character.id,
            job_id=job.id,
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
