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
        raise ValidationFailed("crime_toggle_already_set", enabled=enabled)
    eligible_at = next_eligible_crime_toggle_at(character)
    if eligible_at is not None and now < eligible_at:
        remaining_hours = max(1, int((eligible_at - now).total_seconds() // 3600) + 1)
        raise NotAllowed("crime_toggle_on_cooldown", hours=remaining_hours)


def toggle_crime(character: Character, enabled: bool, now: dt.datetime) -> None:
    check_can_toggle_crime(character, enabled, now)
    character.crime_enabled = enabled
    character.crime_toggle_changed_at = now
