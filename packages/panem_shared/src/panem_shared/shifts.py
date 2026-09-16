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

import random
from dataclasses import dataclass

from panem_shared import constants, job_levels
from panem_shared.content.schemas import District
from panem_shared.db.models import Character, Shift


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


def district_wealth_multiplier(district_id: int) -> float:
    """A straight line from `DISTRICT_WEALTH_WAGE_MULT_MAX` at the Capitol
    (`district_id == 0`) down to `DISTRICT_WEALTH_WAGE_MULT_MIN` at
    District Twelve (`district_id == 12`) -- canon's own wealth ordering,
    the higher the district number the poorer it is, with no per-district
    value to author or keep in sync as districts change. `District.id` is
    schema-validated to `0..12` (`panem_shared.content.schemas.District`),
    so this never needs its own clamp."""
    span = constants.DISTRICT_WEALTH_WAGE_MULT_MAX - constants.DISTRICT_WEALTH_WAGE_MULT_MIN
    return constants.DISTRICT_WEALTH_WAGE_MULT_MAX - span * (district_id / 12)


def resolve_shift_game(
    character: Character,
    district: District,
    *,
    won: bool,
    market_multiplier: float = 1.0,
    neutral: bool = False,
    rng: random.Random | None = None,
) -> ShiftOutcome:
    """FR-JOB-3/4 (reworked): `PLAYER_JOB_BASE_WAGE` scaled by the
    character's job-level multiplier, `district_wealth_multiplier(district.
    id)` (richer districts simply pay more for the same shift), the
    minigame's win/lose multiplier, and `market_multiplier` (the home
    district's current quota-good price over its base price, from
    `panem_sim.systems.economy`'s pricing -- `1.0` for a caller that
    doesn't have a live price, e.g. in tests), then divided by
    `SHIFT_DURATION_TICKS` since this is called once per `/work`
    resolution and a shift can be resolved once per tick across its whole
    window now, not just once total -- see `PLAYER_JOB_BASE_WAGE`'s own
    docstring.

    Output tracks the outcome, not just "did work happen" -- a district
    with no `quota` (the Capitol) always produces nothing, regardless of
    outcome:
    - `won`: one unit of the district's quota good, plus a second unit if
      `rng` rolls under `JOB_LEVEL_BONUS_GOOD_CHANCE[level]` -- a better
      shift at the job earns a real chance at extra output on top of the
      wage boost, scaled by mastery the same way the wage ladder is.
      `rng` defaults to a fresh `random.Random()` per call (only a test
      needs to inject a seeded one for a deterministic roll).
    - `neutral`: exactly one unit, same as before -- doing the work
      without playing the minigame still counts as a completed shift.
    - a real loss (`won=False`, `neutral=False`): no output at all -- a
      failed shift produces nothing, on top of the lose wage penalty.

    `neutral=True` (always paired with `won=False`) skips the lose
    penalty and pays the unmodified level/market wage instead -- for a
    shift a player couldn't have won no matter how they played, unlike
    every other outcome here, which does reflect something in the
    player's control (a real misplay, unlucky odds they still faced).
    Solitaire's "Give Up" is the one place this applies: some Klondike
    deals are unwinnable from the very first deal, so losing there isn't
    a skill failure the way hitting a Minesweeper mine or a Connect 4
    loss is.

    `rep_delta` reads (but doesn't mutate -- `apply_shift_outcome` does
    that) `character.consecutive_wins`/`consecutive_losses` to decide a
    streak bonus/penalty on top of the flat win/+1: a win always pays at
    least `+1`, plus `constants.REP_STREAK_BONUS` once the streak this win
    would extend to reaches a multiple of `constants.REP_STREAK_LEN`. A
    loss stays reputation-neutral (an occasional bad game doesn't hurt)
    until a real losing streak forms, at which point every
    `REP_STREAK_LEN`th loss in the streak costs `REP_STREAK_PENALTY`. A
    `neutral` outcome never moves reputation and doesn't consult either
    streak -- "stay neutral by choosing not to do the game" is meant
    literally."""
    level = job_levels.job_level_for_shifts(character.shifts_completed)
    level_mult = job_levels.wage_multiplier_for_level(level)
    if neutral:
        outcome_mult = 1.0
        rep_delta = 0
    else:
        outcome_mult = (
            constants.WORK_GAME_WIN_WAGE_MULT if won else constants.WORK_GAME_LOSE_WAGE_MULT
        )
        if won:
            new_streak = character.consecutive_wins + 1
            rep_delta = 1
            if new_streak % constants.REP_STREAK_LEN == 0:
                rep_delta += constants.REP_STREAK_BONUS
        else:
            new_streak = character.consecutive_losses + 1
            rep_delta = 0
            if new_streak % constants.REP_STREAK_LEN == 0:
                rep_delta -= constants.REP_STREAK_PENALTY
    wage = (
        constants.PLAYER_JOB_BASE_WAGE
        * level_mult
        * district_wealth_multiplier(district.id)
        * outcome_mult
        * market_multiplier
        / constants.SHIFT_DURATION_TICKS
    )
    output: dict[str, float] = {}
    if district.quota and (won or neutral):
        qty = constants.PLAYER_SHIFT_OUTPUT_QTY
        if won:
            bonus_chance = constants.JOB_LEVEL_BONUS_GOOD_CHANCE[level.value]
            roller = rng if rng is not None else random.Random()
            if roller.random() < bonus_chance:
                qty += constants.PLAYER_SHIFT_OUTPUT_QTY
        output = {district.quota.good: qty}
    return ShiftOutcome(wage=wage, output=output, rep_delta=rep_delta)


def already_worked_this_tick(shift: Shift, tick: int) -> bool:
    """A shift can be worked at most once per in-game tick -- a player
    still gets multiple goes at it across its `tick_opened`..`tick_due`
    window (unlike the old one-and-done shift), just not more than one per
    tick. `panem_bot`'s `/work` and `panem_api`'s minigame-result endpoint
    both check this before resolving an outcome."""
    return shift.last_worked_tick == tick


def apply_shift_outcome(
    shift: Shift,
    character: Character,
    outcome: ShiftOutcome,
    *,
    won: bool,
    neutral: bool = False,
    tick: int,
) -> None:
    """Applies `outcome` to `character` for one `/work` resolution during
    `shift`. Doesn't close `shift` (`result` stays `None`) -- a shift now
    stays open for its whole `tick_opened`..`tick_due` window so it can be
    worked again on a later tick (capped at one resolution per tick by
    `already_worked_this_tick`); `panem_sim.systems.jobs.
    _resolve_missed_shifts` is what finally marks it COMPLETED once
    `tick_due` passes, the same way it already marks an unworked shift
    MISSED. `output` accumulates across every resolution this shift gets
    (each one is real work done), not just the last one, so `panem_sim.
    systems.economy`'s supply side still sees every unit produced.

    `won`/`neutral` are the same values already passed to
    `resolve_shift_game` to build `outcome` -- repeated here (rather than
    inferred from `outcome.rep_delta`, which is `0` for both a neutral skip
    and a non-streak loss, indistinguishable after the fact) purely to
    update the win/loss streak counters
    (`character.consecutive_wins`/`consecutive_losses`) that
    `resolve_shift_game` reads on the *next* call: a win extends the win
    streak and resets the loss streak, a real loss the reverse, and a
    `neutral` skip leaves both alone -- "stay neutral by choosing not to
    do the game" means the streaks don't move either way.

    Docks `FATIGUE_COST_PER_WORK` from `character.fatigue` on every
    resolution (including `neutral` -- skipping the minigame still means
    a shift was worked) -- fatigue "goes down... based on how many times
    they work," and a shift's multiple resolutions across ticks (the
    once-per-tick multi-work feature) each cost fatigue independently.

    Resets `consecutive_missed` -- any resolution, however the shift was
    resolved, breaks the miss streak `jobs.py` tracks. Increments
    `shifts_completed` only the *first* time this shift is worked --
    that's what actually drives job-level progression (`panem_shared.
    job_levels`), and it counts shifts worked, not `/work` calls, so
    reworking the same still-open shift on a later tick doesn't count
    again."""
    is_first_work_this_shift = shift.last_worked_tick is None
    shift.last_worked_tick = tick

    # `shift.output`'s column default only applies once SQLAlchemy actually
    # inserts the row -- a freshly constructed, not-yet-flushed `Shift`
    # (every unit test, and `open_adhoc_shift_override`'s shift before
    # `session.add`) still has it as `None`.
    new_output = dict(shift.output) if shift.output else {}
    for good, qty in outcome.output.items():
        new_output[good] = new_output.get(good, 0) + qty
    shift.output = new_output

    character.money += round(outcome.wage)
    character.reputation += outcome.rep_delta
    character.fatigue = max(
        constants.FATIGUE_MIN, character.fatigue - constants.FATIGUE_COST_PER_WORK
    )
    character.consecutive_missed = 0
    if not neutral:
        if won:
            character.consecutive_wins += 1
            character.consecutive_losses = 0
        else:
            character.consecutive_losses += 1
            character.consecutive_wins = 0
    if is_first_work_this_shift:
        character.shifts_completed += 1
    character.last_active_tick = tick
