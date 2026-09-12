"""Shift resolution and job eligibility (Spec FR-JOB-3/4/9, FR-PRX-7).

`panem_sim.systems.jobs` opens and misses/fires shifts inside the tick
loop; everything here resolves a shift a player actually did something
about -- picked an option with `/work`, or earned RP credit by proxying
in the right scene -- which happens outside the tick loop, on the bot's
own DB session.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from panem_bot.errors import NotAllowed
from panem_shared import constants
from panem_shared.content.schemas import Job
from panem_shared.db.models import Character, Shift
from panem_shared.enums import CharacterStatus, ShiftResult


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


def meets_rp_credit(content: str) -> bool:
    """FR-PRX-7: whether a proxied message is long enough to count as
    working a shift. Whether the *scene* is the right one (tagged for the
    job's workplace) is the caller's job -- this only checks length."""
    return len(content) >= constants.RP_CREDIT_MIN_CHARS


def check_can_apply(character: Character, job: Job) -> None:
    """FR-JOB-1 (implied): raises `NotAllowed` if `character` can't take
    `job` right now."""
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")
    if character.job_id is not None:
        raise NotAllowed("already_employed")
    if job.min_reputation is not None and character.reputation < job.min_reputation:
        raise NotAllowed("reputation_too_low", min_reputation=job.min_reputation)


def check_promotion_eligible(character: Character, job: Job) -> bool:
    """FR-JOB-9: whether `character` qualifies for `job.ladder_next`.
    `ladder_requirement`'s exact key schema isn't available in this
    session's context either -- this only recognizes a `min_reputation`
    key, the one requirement that reads unambiguously from its name."""
    if job.ladder_next is None:
        return False
    requirement = job.ladder_requirement or {}
    min_reputation = requirement.get("min_reputation")
    return min_reputation is None or character.reputation >= min_reputation


def apply_for_job(character: Character, job: Job, *, tick: int) -> None:
    character.job_id = job.id
    character.job_started_tick = tick
    character.consecutive_missed = 0


def quit_job(character: Character) -> tuple[str, int | None]:
    """Returns `(job_id, started_tick)` for the caller to record a
    `JobHistory` row with; raises `NotAllowed` if there's no job to quit."""
    if character.job_id is None:
        raise NotAllowed("not_employed")
    job_id, started_tick = character.job_id, character.job_started_tick
    character.job_id = None
    character.job_started_tick = None
    character.consecutive_missed = 0
    return job_id, started_tick
