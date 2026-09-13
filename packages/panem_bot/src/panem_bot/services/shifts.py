"""Shift resolution and job eligibility (Spec FR-JOB-3/4/9, FR-PRX-7).

`panem_sim.systems.jobs` opens and misses/fires shifts inside the tick
loop; everything here resolves a shift a player actually did something
about -- picked an option with `/work`, played its minigame, or earned RP
credit by proxying in the right scene -- which happens outside the tick
loop, on the bot's own DB session.

`ShiftOutcome`/`resolve_shift`/`apply_shift_outcome` live in
`panem_shared.shifts`, not here, since `panem_api`'s `/work` minigame
result endpoint needs them too and can't depend on `panem_bot`; re-exported
below so every existing call site (`shifts_svc.resolve_shift(...)`) keeps
working unchanged.
"""

from __future__ import annotations

from panem_bot.errors import NotAllowed
from panem_shared import constants
from panem_shared.content.schemas import Job
from panem_shared.db.models import Character, Shift
from panem_shared.enums import CharacterStatus, Position
from panem_shared.shifts import (
    ShiftOutcome as ShiftOutcome,
)
from panem_shared.shifts import (
    apply_shift_outcome as apply_shift_outcome,
)
from panem_shared.shifts import (
    resolve_shift as resolve_shift,
)
from panem_shared.shifts import (
    resolve_shift_game as resolve_shift_game,
)


def start_shift_game(shift: Shift, tick: int) -> None:
    """Marks `/work`'s minigame as launched for `shift` -- idempotent, so
    re-running `/work` on an already-started shift doesn't push back its
    `WORK_GAME_GRACE_TICKS` window."""
    if shift.started_at_tick is None:
        shift.started_at_tick = tick


def open_adhoc_shift_override(
    character: Character, tick: int, *, is_staff: bool = False
) -> Shift | None:
    """A Gamemaker can `/work` at any time, in any place -- not just when
    `panem_sim` has already opened a shift for their job's `shift_phase`,
    and not only when physically at its `workplace` (see
    `can_earn_rp_credit_anywhere`). `is_staff` extends the same "no open
    shift needed" privilege to real (Discord-role) staff working their own
    characters, regardless of the character's in-fiction `Position` --
    staff shouldn't have to wait on the shift schedule to test or
    demonstrate a job. Synthesizes a fresh `Shift` on the spot instead of
    refusing with "no open shift"; `None` if `character` has no job to work
    at all, or neither privilege applies."""
    if character.job_id is None:
        return None
    if not is_staff and Position.GAMEMAKER.value not in character.positions:
        return None
    return Shift(
        character_id=character.id,
        job_id=character.job_id,
        tick_opened=tick,
        tick_due=tick + constants.SHIFT_DURATION_TICKS,
    )


def can_earn_rp_credit_anywhere(character: Character) -> bool:
    """A Gamemaker's RP-credit shift completion (FR-PRX-7) isn't tied to
    being physically in the job's `workplace` scene -- the same "any
    place" privilege `open_adhoc_shift_override` gives `/work`
    itself."""
    return Position.GAMEMAKER.value in character.positions


def meets_rp_credit(content: str) -> bool:
    """FR-PRX-7: whether a proxied message is long enough to count as
    working a shift. Whether the *scene* is the right one (tagged for the
    job's workplace) is the caller's job -- this only checks length."""
    return len(content) >= constants.RP_CREDIT_MIN_CHARS


def check_can_apply(character: Character, job: Job) -> None:
    """FR-JOB-1 (implied): raises `NotAllowed` if `character` can't take
    `job` right now."""
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")
    if character.job_id is not None:
        raise NotAllowed("already_employed")
    if job.staff_only:
        raise NotAllowed("job_staff_only")
    if job.min_reputation is not None and character.reputation < job.min_reputation:
        raise NotAllowed("reputation_too_low", min_reputation=job.min_reputation)


def check_promotion_eligible(character: Character, job: Job) -> bool:
    """FR-JOB-9: whether `character` qualifies for `job.ladder_next`.
    `ladder_requirement`'s exact key schema isn't available in this
    session's context either -- this only recognizes a `min_reputation`
    key, the one requirement that reads unambiguously from its name."""
    if job.ladder_next is None:
        return False
    requirement = job.ladder_requirement or {}
    min_reputation = requirement.get("min_reputation")
    return min_reputation is None or character.reputation >= min_reputation


def apply_for_job(character: Character, job: Job, *, tick: int) -> None:
    character.job_id = job.id
    character.job_started_tick = tick
    character.consecutive_missed = 0


def quit_job(character: Character) -> tuple[str, int | None]:
    """Returns `(job_id, started_tick)` for the caller to record a
    `JobHistory` row with; raises `NotAllowed` if there's no job to quit."""
    if character.job_id is None:
        raise NotAllowed("not_employed")
    job_id, started_tick = character.job_id, character.job_started_tick
    character.job_id = None
    character.job_started_tick = None
    character.consecutive_missed = 0
    return job_id, started_tick
