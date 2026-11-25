"""Passing out from exhaustion.

Once nightly fatigue bottoms out (`Character.fatigue <= constants.
FATIGUE_EXHAUSTION_THRESHOLD` at the midnight needs check,
`panem_sim.systems.needs`), a character is rendered unable to work,
commit crime, trade at any market, travel, or be roleplayed for a
staff-configurable duration (`ExhaustionSettings`, mirroring
`EngagementSettings`'s singleton-row shape) -- replacing the flat health
penalty this used to be.

Mirrors `panem_shared.jail`'s `jailed_until_tick`/`check_not_jailed`
shape exactly: `Character.passed_out_until_tick` is the same kind of
absolute-tick deadline, checked the same way, for the same reason (gate
several unrelated action types behind one shared predicate with a
caller-supplied reason key). Unlike jail, there's no carve-out anywhere
(jail still lets a jailed character roleplay in their own cell) and no
sentence-scaling by priors -- every pass-out is the same configured
length.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared.db.models import Character, ExhaustionSettings
from panem_shared.errors import NotAllowed


def is_passed_out(character: Character, current_tick: int) -> bool:
    return (
        character.passed_out_until_tick is not None
        and character.passed_out_until_tick > current_tick
    )


def check_not_passed_out(character: Character, current_tick: int, reason_key: str) -> None:
    """Mirrors `panem_shared.jail.check_not_jailed` exactly -- `reason_key`
    lets each caller (work, crime, market, travel, proxy) surface its own
    message for the same underlying refusal."""
    if is_passed_out(character, current_tick):
        raise NotAllowed(reason_key, name=character.name)


def passed_out_duration_ticks(settings: ExhaustionSettings | None) -> int:
    """`settings` is `None` only if the `ExhaustionSettings` singleton row
    is somehow missing (the migration that created the table seeds it) --
    `constants.PASSED_OUT_DEFAULT_TICKS` is the fallback for that case,
    same belt-and-suspenders shape `EngagementSettings`'s own reader
    uses."""
    return settings.passed_out_ticks if settings is not None else constants.PASSED_OUT_DEFAULT_TICKS


def apply_passed_out(character: Character, duration_ticks: int, current_tick: int) -> None:
    """Sets `character.passed_out_until_tick`, replacing whatever was
    there. No extend-not-shorten guard like `jail.commit_to_jail`'s --
    this only ever runs once a sim-day from the nightly needs check, so
    there's no concurrent-trigger stacking concern to guard against."""
    character.passed_out_until_tick = current_tick + duration_ticks
