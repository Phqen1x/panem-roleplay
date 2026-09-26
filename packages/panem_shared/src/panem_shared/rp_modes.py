"""RP-mode selection/switching (`Character.rp_mode`) and the Life-mode
crime opt-out toggle (`Character.crime_enabled`).

Pure logic, no DB session -- mirrors `panem_shared.travel`/`housing`: takes
ORM objects as arguments, raises `NotAllowed`/`ValidationFailed` on
refusal, leaves the actual commit to the caller. The one difference from
every other cooldown in this codebase: both gates are real wall-clock
days, not sim ticks (`Character.rp_mode_changed_at`/`crime_toggle_
changed_at` are `DateTime(timezone=True)` columns, not tick integers) --
a mode switch has to stay available even if the sim process is down.
`now` is always taken as a parameter rather than read live
(`dt.datetime.now(dt.UTC)`) so callers/tests can pin the clock.
"""

from __future__ import annotations

import datetime as dt

from panem_shared import constants
from panem_shared.db.models import Character
from panem_shared.enums import RpMode
from panem_shared.errors import NotAllowed, ValidationFailed


def next_eligible_switch_at(character: Character) -> dt.datetime | None:
    """`None` means eligible right now (never switched, or the cooldown has
    already elapsed -- callers that just want a yes/no check should use
    `check_can_switch_mode` instead of comparing this to `now` themselves)."""
    if character.rp_mode_changed_at is None:
        return None
    eligible_at = character.rp_mode_changed_at + dt.timedelta(
        days=constants.MODE_SWITCH_COOLDOWN_DAYS
    )
    return eligible_at


def check_can_switch_mode(character: Character, new_mode: RpMode, now: dt.datetime) -> None:
    if character.pending_rp_mode is not None:
        raise NotAllowed("mode_switch_already_pending")
    if new_mode.value == character.rp_mode:
        raise ValidationFailed("mode_already_active", mode=new_mode.value)
    eligible_at = next_eligible_switch_at(character)
    if eligible_at is not None and now < eligible_at:
        remaining_hours = max(1, int((eligible_at - now).total_seconds() // 3600) + 1)
        raise NotAllowed("mode_switch_on_cooldown", hours=remaining_hours)


def switch_mode(character: Character, new_mode: RpMode, now: dt.datetime) -> None:
    check_can_switch_mode(character, new_mode, now)
    character.rp_mode = new_mode.value
    character.rp_mode_changed_at = now


def mode_switch_needs_job_info(character: Character, new_mode: RpMode) -> bool:
    """Story mode never needs job info, and a character that already has
    `job_title` on file -- i.e. one that was Life/Simulation at some point
    in the past, however long ago -- keeps it and switches instantly
    (`switch_mode` above never touches `job_title`/`shift_phase`, so it's
    still sitting there). It's only switching into Life/Simulation for the
    very first time that needs fresh job info collected and, since that's
    materially the same as a new application, staff sign-off on it too --
    see `stage_mode_switch`/`apply_staged_mode_switch`."""
    return new_mode != RpMode.STORY and character.job_title is None


def stage_mode_switch(
    character: Character,
    new_mode: RpMode,
    *,
    job_title: str,
    shift_phase: str,
    job_is_illicit: bool,
) -> None:
    """Records a mode switch awaiting staff approval without touching any
    of the character's real, currently-live fields -- see `Character.
    pending_rp_mode`'s docstring for why. Callers must have already called
    `check_can_switch_mode` (this raises nothing of its own)."""
    character.pending_rp_mode = new_mode.value
    character.pending_job_title = job_title
    character.pending_shift_phase = shift_phase
    character.pending_job_is_illicit = job_is_illicit
    character.pending_mode_switch_notified_at = None


def apply_staged_mode_switch(character: Character, now: dt.datetime) -> None:
    """Staff approved a staged switch (`stage_mode_switch`) -- copy the
    staged fields onto the real ones and clear the staging columns. Callers
    are expected to have already confirmed `pending_rp_mode is not None`."""
    assert character.pending_rp_mode is not None
    character.rp_mode = character.pending_rp_mode
    character.job_title = character.pending_job_title
    character.shift_phase = character.pending_shift_phase
    character.job_is_illicit = character.pending_job_is_illicit
    character.rp_mode_changed_at = now
    discard_staged_mode_switch(character)


def discard_staged_mode_switch(character: Character) -> None:
    """Staff declined a staged switch, or it's otherwise being abandoned --
    clears the staging columns without touching the character's real mode/
    job fields at all, so nothing about the character it already was is
    lost (unlike rejecting a fresh application, which deletes the row)."""
    character.pending_rp_mode = None
    character.pending_job_title = None
    character.pending_shift_phase = None
    character.pending_job_is_illicit = False
    character.pending_mode_switch_notified_at = None


def next_eligible_crime_toggle_at(character: Character) -> dt.datetime | None:
    if character.crime_toggle_changed_at is None:
        return None
    return character.crime_toggle_changed_at + dt.timedelta(
        days=constants.CRIME_TOGGLE_COOLDOWN_DAYS
    )


def check_can_toggle_crime(character: Character, enabled: bool, now: dt.datetime) -> None:
    """Life mode only -- Simulation can never disable crime ("cannot
    disable crime without first switching to life or story mode"), and
    Story mode is already unreachable by crime regardless of this flag, so
    toggling it there would be meaningless rather than actively refused."""
    if character.rp_mode != RpMode.LIFE.value:
        raise NotAllowed("crime_toggle_wrong_mode")
    if enabled == character.crime_enabled:
        raise ValidationFailed(
            "crime_toggle_already_set", enabled="enabled" if enabled else "disabled"
        )
    eligible_at = next_eligible_crime_toggle_at(character)
    if eligible_at is not None and now < eligible_at:
        remaining_hours = max(1, int((eligible_at - now).total_seconds() // 3600) + 1)
        raise NotAllowed("crime_toggle_on_cooldown", hours=remaining_hours)


def toggle_crime(character: Character, enabled: bool, now: dt.datetime) -> None:
    check_can_toggle_crime(character, enabled, now)
    character.crime_enabled = enabled
    character.crime_toggle_changed_at = now
