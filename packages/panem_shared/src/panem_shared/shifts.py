"""Shift-outcome resolution shared by `panem_bot` (`/work`'s classic
option-select flow, RP-credit) and `panem_api` (the `/work` minigame's
result endpoint) -- both need to turn a resolved shift into money/output/
reputation on `Character`/`Shift` without `panem_api` depending on
`panem_bot`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from panem_shared import constants
from panem_shared.content.schemas import Job
from panem_shared.db.models import Character, Shift
from panem_shared.enums import ShiftResult


@dataclass(frozen=True, slots=True)
class ShiftOutcome:
    wage: float
    output: dict[str, float]
    rep_delta: int
    risk_triggered: bool
    risk_effect: dict[str, Any] | None


def resolve_shift(
    job: Job, option_index: int, *, is_player: bool, rng: random.Random
) -> ShiftOutcome:
    """FR-JOB-3/4: apply the chosen option's multipliers to the job's base
    wage/output, and roll its risk independently. `PLAYER_OUTPUT_WEIGHT`
    scales a player's output down relative to an NPC's (Spec §10) --
    players get the full wage regardless, output is what feeds the
    district's production for quotas/exports (Milestone D)."""
    option = job.options[option_index]
    wage = job.wage * option.wage_mult
    weight = constants.PLAYER_OUTPUT_WEIGHT if is_player else 1.0
    output = {good: qty * option.output_mult * weight for good, qty in job.produces.items()}
    risk_triggered = option.risk > 0 and rng.random() < option.risk
    return ShiftOutcome(
        wage=wage,
        output=output,
        rep_delta=option.rep_delta,
        risk_triggered=risk_triggered,
        risk_effect=option.risk_effect if risk_triggered else None,
    )


def resolve_shift_game(job: Job, won: bool) -> ShiftOutcome:
    """`/work`'s minigame (Minesweeper today, `panem_api`'s Activity
    frontend) replaces the option-multiplier axis a player's free choice
    used to control: winning pays `WORK_GAME_WIN_WAGE_MULT * job.wage`,
    losing pays `WORK_GAME_LOSE_WAGE_MULT * job.wage`. No risk roll here --
    the game itself is the "did something go wrong" axis now, so a
    `JobOption.risk_effect` (e.g. a health hit) would be double-dipping;
    output uses the job's plain `PLAYER_OUTPUT_WEIGHT`, undiminished by an
    option's `output_mult` since there's no option chosen."""
    mult = constants.WORK_GAME_WIN_WAGE_MULT if won else constants.WORK_GAME_LOSE_WAGE_MULT
    wage = job.wage * mult
    output = {
        good: qty * constants.PLAYER_OUTPUT_WEIGHT * mult for good, qty in job.produces.items()
    }
    return ShiftOutcome(
        wage=wage,
        output=output,
        rep_delta=1 if won else 0,
        risk_triggered=False,
        risk_effect=None,
    )


def apply_shift_outcome(
    shift: Shift, character: Character, outcome: ShiftOutcome, *, tick: int
) -> None:
    """Marks `shift` completed and applies its outcome to `character`.
    Resets `consecutive_missed` -- any completion, however the shift was
    resolved, breaks the miss streak `jobs.py` tracks."""
    shift.result = ShiftResult.COMPLETED.value
    shift.completed_at = tick
    shift.output = outcome.output

    character.money += round(outcome.wage)
    character.reputation += outcome.rep_delta
    character.consecutive_missed = 0

    if outcome.risk_triggered and outcome.risk_effect:
        _apply_risk_effect(character, outcome.risk_effect)


def _apply_risk_effect(character: Character, risk_effect: dict[str, Any]) -> None:
    """Applies `JobOption.risk_effect` once its risk has triggered. `health`
    (a delta, e.g. `-20`) is the only key `data/jobs.yaml` actually uses
    today; `reputation`/`jailed_ticks` are supported as the same free-form
    dict could carry them later, but aren't exercised by any current job."""
    if "health" in risk_effect:
        character.health = max(0.0, min(100.0, character.health + risk_effect["health"]))
    if "reputation" in risk_effect:
        character.reputation += risk_effect["reputation"]
    if "jailed_ticks" in risk_effect:
        base_tick = character.jailed_until_tick or 0
        character.jailed_until_tick = base_tick + risk_effect["jailed_ticks"]
