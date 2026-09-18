"""Jailing (contraband system): re-exports `panem_shared.jail`'s
priors-scaled sentencing so every existing `jail_svc.commit_to_jail(...)`
call site in `panem_bot` keeps working unchanged -- the logic itself
lives in `panem_shared` because `panem_api`'s `/work` minigame result
endpoint needs the matching `resolve_illicit_heat` too and can't depend
on `panem_bot` to get it (same reasoning as `panem_shared.shifts`).

`/bail` and `/lockpick` live only here, not `panem_shared` -- they're
pure Discord commands with `NotAllowed` refusals, no Activity-side
equivalent the way `commit_to_jail`/`resolve_illicit_heat` need one."""

from __future__ import annotations

import random

from panem_bot.errors import NotAllowed
from panem_shared import constants
from panem_shared.db.models import Character
from panem_shared.jail import (
    apply_lockpick_attempt as apply_lockpick_attempt,
)
from panem_shared.jail import (
    commit_to_jail as commit_to_jail,
)
from panem_shared.jail import (
    crackdown_bad_odds as crackdown_bad_odds,
)
from panem_shared.jail import (
    crackdown_good_odds as crackdown_good_odds,
)
from panem_shared.jail import (
    is_crackdown_active as is_crackdown_active,
)
from panem_shared.jail import (
    lockpick_difficulty as lockpick_difficulty,
)
from panem_shared.jail import (
    release_from_jail as release_from_jail,
)


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
