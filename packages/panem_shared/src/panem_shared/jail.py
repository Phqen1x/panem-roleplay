"""Jailing and illicit-work heat (contraband system) -- shared by
`panem_bot` (every consequence path: market, poaching, illicit work,
`/steal`) and `panem_api` (the `/work` minigame result endpoint, which
needs the same illicit-work heat/arrest handling `panem_bot`'s `/work`
cog applies and can't depend on `panem_bot` to get it).
"""

from __future__ import annotations

import random

from panem_shared import constants
from panem_shared.db.models import Character, DistrictState


def commit_to_jail(character: Character, base_ticks: int) -> int:
    """Sentences `character` to jail, scaling the length by their priors
    (`Character.jail_count`) -- repeat offenders serve longer. Extends
    any sentence already running (`jailed_until_tick`) rather than
    shortening it, sets `jail_sentence_ticks` to *this* sentence's fixed
    length (what `/lockpick` reads for difficulty), and increments
    `jail_count`. Returns the sentence length actually applied."""
    # `character.jail_count`'s column `default=0` only applies once
    # SQLAlchemy actually inserts the row -- a freshly constructed,
    # not-yet-flushed `Character` (every unit test that builds one
    # without explicitly setting it) still has it as `None`.
    sentence = base_ticks + (character.jail_count or 0) * constants.JAIL_PRIOR_TICKS_PER_COUNT
    base_tick = character.jailed_until_tick or 0
    character.jailed_until_tick = base_tick + sentence
    character.jail_sentence_ticks = sentence
    character.jail_count = (character.jail_count or 0) + 1
    return sentence


ARREST_PRESSURE_DELTA = 0.05
"""Mirrors `panem_bot.services.market.ILLICIT_PRESSURE_DELTA`/
`poaching.PEACEKEEPER_PRESSURE_DELTA` exactly -- same placeholder-
weighting caveat (Spec §7's real number wasn't available in this
session's context)."""


def resolve_illicit_heat(
    character: Character,
    district_row: DistrictState | None,
    *,
    lost: bool,
    rng: random.Random | None = None,
) -> bool:
    """Bumps `Character.illicit_heat` after an illicit shift (a real loss
    adds `ILLICIT_HEAT_PER_LOSS`, anything else `ILLICIT_HEAT_PER_SHIFT`
    -- "failing a work game attracts much more attention"), and once heat
    clears `ILLICIT_HEAT_ARREST_THRESHOLD`, immediately rolls an arrest-
    evasion check. A win halves heat back down and leaves the character
    free; a loss jails them (`commit_to_jail`, fine, reputation hit, a
    bump to `district_row.peacekeeper_pressure` if a row was found) and
    resets heat to 0. Returns whether an arrest happened, for the
    caller's reply text. Takes the `DistrictState` row directly (rather
    than a session) so this stays a plain function callable from both
    `panem_bot` and `panem_api`."""
    roller = rng if rng is not None else random.Random()
    character.illicit_heat += (
        constants.ILLICIT_HEAT_PER_LOSS if lost else constants.ILLICIT_HEAT_PER_SHIFT
    )
    if character.illicit_heat < constants.ILLICIT_HEAT_ARREST_THRESHOLD:
        return False

    if roller.random() < constants.ARREST_EVASION_BASE_PROB:
        character.illicit_heat /= 2
        return False

    character.money = max(0, character.money - constants.ILLICIT_ARREST_FINE)
    commit_to_jail(character, constants.ILLICIT_ARREST_JAIL_TICKS)
    character.reputation -= constants.ILLICIT_ARREST_REP_PENALTY
    character.illicit_heat = 0.0

    if district_row is not None:
        district_row.peacekeeper_pressure = min(
            1.0, district_row.peacekeeper_pressure + ARREST_PRESSURE_DELTA
        )
    return True
