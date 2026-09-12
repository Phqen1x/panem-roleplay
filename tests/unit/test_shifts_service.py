from __future__ import annotations

import random

import pytest

from panem_bot.errors import NotAllowed
from panem_bot.services import shifts as shifts_svc
from panem_shared.content.schemas import Job, JobOption
from panem_shared.db.models import Character, Shift
from panem_shared.enums import CharacterStatus, ShiftResult


def make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = dict(
        id="miner",
        district=1,
        title="Miner",
        workplace="mine",
        wage=10.0,
        produces={"coal": 5.0},
        shift_phase="morning",
        slots=5,
        options=[
            JobOption(label="safe", wage_mult=0.8, output_mult=0.8, risk=0.0),
            JobOption(label="normal"),
            JobOption(label="risky", wage_mult=1.5, output_mult=1.5, risk=1.0, rep_delta=2),
        ],
    )
    defaults.update(overrides)
    return Job(**defaults)  # type: ignore[arg-type]


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
        money=0,
        reputation=0.0,
        health=100.0,
        consecutive_missed=0,
    )
    defaults.update(overrides)
    return Character(**defaults)  # type: ignore[arg-type]


class TestResolveShift:
    def test_applies_option_multipliers_to_wage_and_output(self):
        job = make_job()
        outcome = shifts_svc.resolve_shift(job, 0, is_player=True, rng=random.Random(1))
        assert outcome.wage == pytest.approx(10.0 * 0.8)
        assert outcome.output["coal"] == pytest.approx(
            5.0 * 0.8 * shifts_svc.constants.PLAYER_OUTPUT_WEIGHT
        )

    def test_player_output_scaled_down_relative_to_npc(self):
        job = make_job()
        player_outcome = shifts_svc.resolve_shift(job, 1, is_player=True, rng=random.Random(1))
        npc_outcome = shifts_svc.resolve_shift(job, 1, is_player=False, rng=random.Random(1))
        assert player_outcome.output["coal"] < npc_outcome.output["coal"]

    def test_zero_risk_option_never_triggers(self):
        job = make_job()
        for seed in range(20):
            outcome = shifts_svc.resolve_shift(job, 0, is_player=True, rng=random.Random(seed))
            assert outcome.risk_triggered is False
            assert outcome.risk_effect is None

    def test_certain_risk_option_always_triggers(self):
        job = make_job()
        outcome = shifts_svc.resolve_shift(job, 2, is_player=True, rng=random.Random(1))
        assert outcome.risk_triggered is True
        assert outcome.rep_delta == 2

    def test_risk_effect_only_carried_when_triggered(self):
        job = make_job(
            options=[
                JobOption(label="safe", risk=0.0, risk_effect={"health": -50}),
                JobOption(label="b"),
                JobOption(label="c"),
            ]
        )
        outcome = shifts_svc.resolve_shift(job, 0, is_player=True, rng=random.Random(1))
        assert outcome.risk_triggered is False
        assert outcome.risk_effect is None


class TestApplyShiftOutcome:
    def test_completes_shift_and_updates_character(self):
        job = make_job()
        outcome = shifts_svc.resolve_shift(job, 1, is_player=True, rng=random.Random(1))
        shift = Shift(character_id=1, job_id="miner", tick_opened=1, tick_due=7)
        character = make_character(money=0, reputation=0.0, consecutive_missed=4)

        shifts_svc.apply_shift_outcome(shift, character, outcome, tick=7)

        assert shift.result == ShiftResult.COMPLETED.value
        assert shift.completed_at == 7
        assert shift.output == outcome.output
        assert character.money == round(outcome.wage)
        assert character.reputation == outcome.rep_delta
        assert character.consecutive_missed == 0

    def test_applies_health_delta_from_a_triggered_risk_effect(self):
        job = make_job(
            options=[
                JobOption(label="a"),
                JobOption(label="b"),
                JobOption(label="risky", risk=1.0, risk_effect={"health": -15}),
            ]
        )
        outcome = shifts_svc.resolve_shift(job, 2, is_player=True, rng=random.Random(1))
        shift = Shift(character_id=1, job_id="miner", tick_opened=1, tick_due=7)
        character = make_character(health=100.0)

        shifts_svc.apply_shift_outcome(shift, character, outcome, tick=7)

        assert character.health == 85.0

    def test_jailed_ticks_risk_effect_stacks_onto_existing_jail_time(self):
        job = make_job(
            options=[
                JobOption(label="a"),
                JobOption(label="b"),
                JobOption(label="risky", risk=1.0, risk_effect={"jailed_ticks": 12}),
            ]
        )
        outcome = shifts_svc.resolve_shift(job, 2, is_player=True, rng=random.Random(1))
        shift = Shift(character_id=1, job_id="miner", tick_opened=1, tick_due=7)
        character = make_character(jailed_until_tick=5)

        shifts_svc.apply_shift_outcome(shift, character, outcome, tick=7)

        assert character.jailed_until_tick == 17


class TestMeetsRpCredit:
    def test_below_threshold_fails(self):
        assert (
            shifts_svc.meets_rp_credit("x" * (shifts_svc.constants.RP_CREDIT_MIN_CHARS - 1))
            is False
        )

    def test_at_threshold_passes(self):
        assert shifts_svc.meets_rp_credit("x" * shifts_svc.constants.RP_CREDIT_MIN_CHARS) is True


class TestCheckCanApply:
    def test_allows_unemployed_approved_character(self):
        job = make_job(min_reputation=None)
        character = make_character(job_id=None)
        shifts_svc.check_can_apply(character, job)  # no raise

    def test_refuses_already_employed(self):
        job = make_job()
        character = make_character(job_id="baker")
        with pytest.raises(NotAllowed) as exc_info:
            shifts_svc.check_can_apply(character, job)
        assert exc_info.value.reason_key == "already_employed"

    def test_refuses_reputation_below_minimum(self):
        job = make_job(min_reputation=50)
        character = make_character(job_id=None, reputation=10)
        with pytest.raises(NotAllowed) as exc_info:
            shifts_svc.check_can_apply(character, job)
        assert exc_info.value.reason_key == "reputation_too_low"

    def test_refuses_non_approved_character(self):
        job = make_job()
        character = make_character(job_id=None, status=CharacterStatus.PENDING.value)
        with pytest.raises(NotAllowed) as exc_info:
            shifts_svc.check_can_apply(character, job)
        assert exc_info.value.reason_key == "character_not_approved"


class TestCheckPromotionEligible:
    def test_false_when_no_ladder_next(self):
        job = make_job(ladder_next=None)
        character = make_character(reputation=100)
        assert shifts_svc.check_promotion_eligible(character, job) is False

    def test_true_when_reputation_requirement_met(self):
        job = make_job(ladder_next="foreman", ladder_requirement={"min_reputation": 20})
        character = make_character(reputation=25)
        assert shifts_svc.check_promotion_eligible(character, job) is True

    def test_false_when_reputation_requirement_not_met(self):
        job = make_job(ladder_next="foreman", ladder_requirement={"min_reputation": 20})
        character = make_character(reputation=5)
        assert shifts_svc.check_promotion_eligible(character, job) is False


class TestApplyAndQuitJob:
    def test_apply_for_job_sets_job_fields_and_resets_miss_streak(self):
        job = make_job()
        character = make_character(consecutive_missed=3)
        shifts_svc.apply_for_job(character, job, tick=100)
        assert character.job_id == "miner"
        assert character.job_started_tick == 100
        assert character.consecutive_missed == 0

    def test_quit_job_clears_job_and_returns_history_fields(self):
        character = make_character(job_id="miner", job_started_tick=50, consecutive_missed=2)
        job_id, started_tick = shifts_svc.quit_job(character)
        assert (job_id, started_tick) == ("miner", 50)
        assert character.job_id is None
        assert character.job_started_tick is None
        assert character.consecutive_missed == 0

    def test_quit_job_without_a_job_raises(self):
        character = make_character(job_id=None)
        with pytest.raises(NotAllowed) as exc_info:
            shifts_svc.quit_job(character)
        assert exc_info.value.reason_key == "not_employed"
