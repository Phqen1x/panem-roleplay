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
from panem_shared.errors import NotAllowed


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
    character.jail_lockpick_tries_used = 0
    return sentence


ARREST_PRESSURE_DELTA = 0.05
"""Mirrors `panem_bot.services.market.ILLICIT_PRESSURE_DELTA`/
`poaching.PEACEKEEPER_PRESSURE_DELTA` exactly -- same placeholder-
weighting caveat (Spec §7's real number wasn't available in this
session's context)."""


def is_crackdown_active(district_row: DistrictState | None, current_tick: int) -> bool:
    return (
        district_row is not None
        and district_row.crackdown_until_tick is not None
        and current_tick < district_row.crackdown_until_tick
    )


def crackdown_bad_odds(
    base_prob: float, district_row: DistrictState | None, current_tick: int
) -> float:
    """Scales a "bad" (get-caught) probability up under an active
    moderator crackdown (`/staff district crackdown`), clamped to stay a
    valid probability."""
    if not is_crackdown_active(district_row, current_tick):
        return base_prob
    return min(1.0, base_prob * constants.CRACKDOWN_DETECTION_MULTIPLIER)


def crackdown_good_odds(
    base_prob: float, district_row: DistrictState | None, current_tick: int
) -> float:
    """The inverse of `crackdown_bad_odds` for a "good" (succeed/escape)
    probability -- divides instead of multiplying, same clamp."""
    if not is_crackdown_active(district_row, current_tick):
        return base_prob
    return max(0.0, base_prob / constants.CRACKDOWN_DETECTION_MULTIPLIER)


def release_from_jail(character: Character) -> None:
    """Clears a served/picked/bailed sentence -- shared by `panem_bot.
    services.jail.pay_bail` and `apply_lockpick_attempt` below so both
    "ways out" reset the same three fields identically."""
    character.jailed_until_tick = None
    character.jail_sentence_ticks = None
    character.jail_lockpick_tries_used = 0


def lockpick_difficulty(character: Character) -> float:
    """0..1 difficulty for the `/lockpick` minigame (contraband system),
    derived from the same `LOCKPICK_*` constants the RNG-fallback path
    (`panem_bot.services.jail.attempt_lockpick`, used when no Activity is
    configured or the player hits Skip) rolls against, so a long sentence
    reads as a genuinely harder lock in both paths rather than two
    independently-tuned difficulty curves."""
    sentence = character.jail_sentence_ticks or 0
    prob = max(
        constants.LOCKPICK_MIN_SUCCESS_PROB,
        constants.LOCKPICK_BASE_SUCCESS_PROB - sentence * constants.LOCKPICK_DIFFICULTY_PER_TICK,
    )
    return 1.0 - prob


def apply_lockpick_attempt(character: Character, *, won: bool) -> None:
    """Applies one `/lockpick` attempt's outcome -- shared so `panem_api`'s
    lockpick-Activity result endpoint can apply the same tries/release
    bookkeeping the RNG-fallback path uses. Consumes one of
    `LOCKPICK_MAX_TRIES` on a loss; a win clears the sentence outright."""
    if won:
        release_from_jail(character)
    else:
        character.jail_lockpick_tries_used = (character.jail_lockpick_tries_used or 0) + 1


def resolve_illicit_heat(
    character: Character,
    district_row: DistrictState | None,
    *,
    lost: bool,
    current_tick: int,
    rng: random.Random | None = None,
) -> bool:
    """Bumps `Character.illicit_heat` after an illicit shift (a real loss
    adds `ILLICIT_HEAT_PER_LOSS`, anything else `ILLICIT_HEAT_PER_SHIFT`
    -- "failing a work game attracts much more attention"), and once heat
    clears `ILLICIT_HEAT_ARREST_THRESHOLD`, immediately rolls an arrest-
    evasion check (harder under an active crackdown -- `crackdown_good_
    odds`). A win halves heat back down and leaves the character free; a
    loss jails them (`commit_to_jail`, fine, reputation hit, a bump to
    `district_row.peacekeeper_pressure` if a row was found) and resets
    heat to 0. Returns whether an arrest happened, for the caller's
    reply text. Takes the `DistrictState` row directly (rather than a
    session) so this stays a plain function callable from both
    `panem_bot` and `panem_api`."""
    roller = rng if rng is not None else random.Random()
    character.illicit_heat += (
        constants.ILLICIT_HEAT_PER_LOSS if lost else constants.ILLICIT_HEAT_PER_SHIFT
    )
    if character.illicit_heat < constants.ILLICIT_HEAT_ARREST_THRESHOLD:
        return False

    evasion_prob = crackdown_good_odds(
        constants.ARREST_EVASION_BASE_PROB, district_row, current_tick
    )
    if roller.random() < evasion_prob:
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


def check_is_jailed(character: Character, current_tick: int) -> None:
    if character.jailed_until_tick is None or character.jailed_until_tick <= current_tick:
        raise NotAllowed("jail_not_jailed", name=character.name)


def bail_cost(character: Character, current_tick: int) -> int:
    """`BAIL_BASE_COST` plus `BAIL_COST_PER_REMAINING_TICK` per tick still
    left on the sentence -- bailing out right after being caught costs
    more than waiting nearly the whole way. Raises `NotAllowed` if
    `character` isn't currently jailed."""
    check_is_jailed(character, current_tick)
    assert character.jailed_until_tick is not None  # narrowed by check_is_jailed
    remaining = character.jailed_until_tick - current_tick
    return round(constants.BAIL_BASE_COST + constants.BAIL_COST_PER_REMAINING_TICK * remaining)


def pay_bail(character: Character, current_tick: int) -> int:
    """Pays `bail_cost` to clear `character`'s sentence immediately.
    Raises `NotAllowed` if not jailed or if funds are short. Returns the
    cost actually paid."""
    cost = bail_cost(character, current_tick)
    if character.money < cost:
        raise NotAllowed("bail_insufficient_funds", name=character.name, cost=cost)
    character.money -= cost
    release_from_jail(character)
    return cost


def check_can_attempt_lockpick(character: Character, current_tick: int) -> None:
    """Raises `NotAllowed` unless `character` has a lockpick attempt left
    to spend -- validation only, no roll, so `/lockpick` can refuse up
    front before launching the Activity (an attempt is spent only once a
    result -- minigame or the RNG-fallback roll below -- actually comes
    back)."""
    check_is_jailed(character, current_tick)
    tries_used = character.jail_lockpick_tries_used or 0
    if tries_used >= constants.LOCKPICK_MAX_TRIES:
        raise NotAllowed("lockpick_no_tries_left", name=character.name)


def attempt_lockpick(
    character: Character, current_tick: int, *, rng: random.Random | None = None
) -> bool:
    """The RNG-fallback path (no `ACTIVITY_PUBLIC_URL` configured, or the
    player hits Skip): one probability roll at `lockpick_difficulty`'s
    odds, then `apply_lockpick_attempt` for the tries/release bookkeeping
    -- the same function `panem_api`'s lockpick-Activity result endpoint
    calls with the minigame's own win/lose instead of a roll."""
    check_can_attempt_lockpick(character, current_tick)
    prob = 1.0 - lockpick_difficulty(character)
    roller = rng if rng is not None else random.Random()
    won = roller.random() < prob
    apply_lockpick_attempt(character, won=won)
    return won
