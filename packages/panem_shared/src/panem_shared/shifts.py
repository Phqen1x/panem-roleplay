"""Shift-outcome resolution shared by `panem_bot` (`/work`, RP-credit) and
`panem_api` (the `/work` minigame's result endpoint) -- both need to turn
a resolved shift into money/output/reputation on `Character`/`Shift`
without `panem_api` depending on `panem_bot`.

Player jobs are free-typed (`Character.job_title`) rather than picked from
a `jobs.yaml` catalog entry, so there's no more per-job `wage`/`produces`/
`options` to read for a player shift: `resolve_shift_game` is the only
resolution path now (the old per-option `resolve_shift`, and the classic
"work hard / play safe / cover a crewmate" menu it drove, are retired --
win/lose, from the minigame or a coin-flip when no Activity is configured,
is the sole outcome axis). Wage is `PLAYER_JOB_BASE_WAGE` scaled by three
independent multipliers: the character's job level (`panem_shared.
job_levels`), the win/lose outcome, and the district's current market
price for its own quota good (rewarding/penalizing a district whose goods
are worth more/less than base price). NPCs are untouched by any of this --
`panem_sim.systems.jobs`'s NPC path still reads `Job.wage`/`Job.produces`
from the catalog directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from panem_shared import constants, job_levels
from panem_shared.content.schemas import District
from panem_shared.db.models import Character, Shift
from panem_shared.enums import ShiftResult


@dataclass(frozen=True, slots=True)
class ShiftOutcome:
    wage: float
    output: dict[str, float]
    rep_delta: int


def market_wage_multiplier(price: float, base_price: float) -> float:
    """A district whose quota good is currently worth more than its
    `base_price` (scarce, high demand) pays a wage boost; one whose good
    is worth less (oversupplied) pays a debuff. `panem_sim.systems.
    economy`'s price update already clamps `price` to
    `[PRICE_CLAMP_MIN, PRICE_CLAMP_MAX] * base_price`, so the ratio here
    needs no separate clamp of its own."""
    if base_price <= 0:
        return 1.0
    return price / base_price


def resolve_shift_game(
    character: Character,
    district: District,
    *,
    won: bool,
    market_multiplier: float = 1.0,
    neutral: bool = False,
) -> ShiftOutcome:
    """FR-JOB-3/4 (reworked): `PLAYER_JOB_BASE_WAGE` scaled by the
    character's job-level multiplier, the minigame's win/lose multiplier,
    and `market_multiplier` (the home district's current quota-good price
    over its base price, from `panem_sim.systems.economy`'s pricing --
    `1.0` for a caller that doesn't have a live price, e.g. in tests).
    Output is one unit of the district's own quota good per completed
    shift (win or lose -- they still did the work), feeding
    `panem_sim.systems.economy`'s supply the way `Job.produces` used to;
    a district with no `quota` (the Capitol) produces nothing.

    `neutral=True` (always paired with `won=False`) skips the lose
    penalty and pays the unmodified level/market wage instead -- for a
    shift a player couldn't have won no matter how they played, unlike
    every other outcome here, which does reflect something in the
    player's control (a real misplay, unlucky odds they still faced).
    Solitaire's "Give Up" is the one place this applies: some Klondike
    deals are unwinnable from the very first deal, so losing there isn't
    a skill failure the way hitting a Minesweeper mine or a Connect 4
    loss is. Reputation still doesn't move (same as any other loss)."""
    level = job_levels.job_level_for_shifts(character.shifts_completed)
    level_mult = job_levels.wage_multiplier_for_level(level)
    if neutral:
        outcome_mult = 1.0
    else:
        outcome_mult = (
            constants.WORK_GAME_WIN_WAGE_MULT if won else constants.WORK_GAME_LOSE_WAGE_MULT
        )
    wage = constants.PLAYER_JOB_BASE_WAGE * level_mult * outcome_mult * market_multiplier
    output = {district.quota.good: constants.PLAYER_SHIFT_OUTPUT_QTY} if district.quota else {}
    return ShiftOutcome(wage=wage, output=output, rep_delta=1 if won else 0)


def apply_shift_outcome(
    shift: Shift, character: Character, outcome: ShiftOutcome, *, tick: int
) -> None:
    """Marks `shift` completed and applies its outcome to `character`.
    Resets `consecutive_missed` -- any completion, however the shift was
    resolved, breaks the miss streak `jobs.py` tracks. Increments
    `shifts_completed`, which is what actually drives job-level
    progression (`panem_shared.job_levels`) -- the *next* shift's wage
    uses the level this produces, not this one's."""
    shift.result = ShiftResult.COMPLETED.value
    shift.completed_at = tick
    shift.output = outcome.output

    character.money += round(outcome.wage)
    character.reputation += outcome.rep_delta
    character.consecutive_missed = 0
    character.shifts_completed += 1
    character.last_active_tick = tick
