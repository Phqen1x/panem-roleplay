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
    commit_to_jail as commit_to_jail,
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


def _release(character: Character) -> None:
    character.jailed_until_tick = None
    character.jail_sentence_ticks = None
    character.jail_lockpick_tries_used = 0


def pay_bail(character: Character, current_tick: int) -> int:
    """Pays `bail_cost` to clear `character`'s sentence immediately.
    Raises `NotAllowed` if not jailed or if funds are short. Returns the
    cost actually paid."""
    cost = bail_cost(character, current_tick)
    if character.money < cost:
        raise NotAllowed("bail_insufficient_funds", name=character.name, cost=cost)
    character.money -= cost
    _release(character)
    return cost


def attempt_lockpick(
    character: Character, current_tick: int, *, rng: random.Random | None = None
) -> bool:
    """One `/lockpick` attempt: a probability roll whose odds are fixed
    from `jail_sentence_ticks` (the sentence's *original* length, not the
    counting-down `jailed_until_tick`, so difficulty doesn't ease up near
    release) -- `LOCKPICK_BASE_SUCCESS_PROB` minus `LOCKPICK_DIFFICULTY_
    PER_TICK` per sentence tick, floored at `LOCKPICK_MIN_SUCCESS_PROB`.
    Consumes one of `LOCKPICK_MAX_TRIES` tries whether it succeeds or
    fails; raises `NotAllowed` if not jailed or already out of tries. A
    success clears the sentence outright (same as `pay_bail`, minus the
    cost)."""
    check_is_jailed(character, current_tick)
    tries_used = character.jail_lockpick_tries_used or 0
    if tries_used >= constants.LOCKPICK_MAX_TRIES:
        raise NotAllowed("lockpick_no_tries_left", name=character.name)

    sentence = character.jail_sentence_ticks or 0
    prob = max(
        constants.LOCKPICK_MIN_SUCCESS_PROB,
        constants.LOCKPICK_BASE_SUCCESS_PROB - sentence * constants.LOCKPICK_DIFFICULTY_PER_TICK,
    )
    character.jail_lockpick_tries_used = tries_used + 1
    roller = rng if rng is not None else random.Random()
    if roller.random() < prob:
        _release(character)
        return True
    return False
