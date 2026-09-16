"""Smoke test that panem_shared.shifts works standalone (no panem_bot
dependency) -- panem_api's /work minigame endpoint imports it directly.

Also the main coverage for `resolve_shift_game`/`apply_shift_outcome`
(the job-system rework: free-typed player jobs, no more per-job catalog
wage/options -- `panem_bot.services.shifts` just re-exports these
unchanged, so its own tests only cover the bot-side wrappers around
them).
"""

from __future__ import annotations

from panem_shared import constants, job_levels
from panem_shared import shifts as shared_shifts
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    DistrictQuota,
    Location,
)
from panem_shared.db.models import Character, Shift
from panem_shared.enums import CharacterStatus, JobLevel


class FixedRng:
    """A stand-in for `random.Random` that always returns a fixed draw, so
    bonus-good-chance tests don't depend on the real thresholds."""

    def __init__(self, value: float) -> None:
        self._value = value

    def random(self) -> float:
        return self._value


def make_district(*, quota_good: str | None = "coal", district_id: int = 12) -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Station", kind="station"),
    ]
    coords = {loc.id: (0, 0) for loc in locations}
    return District(
        id=district_id,
        name=f"District {district_id}",
        industry="coal",
        locations=locations,
        culture=DistrictCulture(),
        population_base=100,
        quota=DistrictQuota(good=quota_good, amount=100) if quota_good else None,
        map=DistrictMap(image="x.png", width=10, height=10, location_coords=coords),
    )


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=12,
        current_district_id=12,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
        money=0,
        reputation=0.0,
        health=100.0,
        fatigue=100.0,
        consecutive_missed=0,
        consecutive_wins=0,
        consecutive_losses=0,
        shifts_completed=0,
        positions=[],
    )
    defaults.update(overrides)
    return Character(**defaults)  # type: ignore[arg-type]


class TestResolveShiftGame:
    def test_win_beats_loss(self):
        character = make_character()
        district = make_district()
        win = shared_shifts.resolve_shift_game(character, district, won=True)
        lose = shared_shifts.resolve_shift_game(character, district, won=False)
        assert win.wage > lose.wage

    def test_wage_at_apprentice_matches_base_win_multiplier(self):
        character = make_character(shifts_completed=0)
        district = make_district()  # District Twelve -> DISTRICT_WEALTH_WAGE_MULT_MIN
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
        assert outcome.wage == (
            constants.PLAYER_JOB_BASE_WAGE
            * constants.WORK_GAME_WIN_WAGE_MULT
            * constants.DISTRICT_WEALTH_WAGE_MULT_MIN
            / constants.SHIFT_DURATION_TICKS
        )

    def test_higher_job_level_pays_more_for_the_same_outcome(self):
        district = make_district()
        apprentice = make_character(shifts_completed=0)
        expert = make_character(shifts_completed=constants.JOB_LEVEL_SHIFT_THRESHOLDS["expert"])
        apprentice_wage = shared_shifts.resolve_shift_game(apprentice, district, won=True).wage
        expert_wage = shared_shifts.resolve_shift_game(expert, district, won=True).wage
        assert expert_wage == apprentice_wage * 3.0

    def test_market_multiplier_scales_wage(self):
        character = make_character()
        district = make_district()
        base = shared_shifts.resolve_shift_game(character, district, won=True).wage
        boosted = shared_shifts.resolve_shift_game(
            character, district, won=True, market_multiplier=2.0
        ).wage
        assert boosted == base * 2.0

    def test_output_is_one_unit_of_the_districts_quota_good_when_no_bonus_rolled(self):
        character = make_character()
        district = make_district(quota_good="coal")
        outcome = shared_shifts.resolve_shift_game(
            character, district, won=True, rng=FixedRng(0.99)
        )
        assert outcome.output == {"coal": constants.PLAYER_SHIFT_OUTPUT_QTY}

    def test_win_produces_a_bonus_unit_when_the_chance_rolls_hit(self):
        character = make_character()
        district = make_district(quota_good="coal")
        outcome = shared_shifts.resolve_shift_game(character, district, won=True, rng=FixedRng(0.0))
        assert outcome.output == {"coal": constants.PLAYER_SHIFT_OUTPUT_QTY * 2}

    def test_bonus_chance_scales_with_job_level(self):
        district = make_district(quota_good="coal")
        apprentice = make_character(shifts_completed=0)
        expert = make_character(shifts_completed=constants.JOB_LEVEL_SHIFT_THRESHOLDS["expert"])
        # A roll that clears an apprentice's chance but not an expert's --
        # only the higher-level character should get the bonus unit.
        roll = (
            constants.JOB_LEVEL_BONUS_GOOD_CHANCE["apprentice"]
            + constants.JOB_LEVEL_BONUS_GOOD_CHANCE["expert"]
        ) / 2
        apprentice_outcome = shared_shifts.resolve_shift_game(
            apprentice, district, won=True, rng=FixedRng(roll)
        )
        expert_outcome = shared_shifts.resolve_shift_game(
            expert, district, won=True, rng=FixedRng(roll)
        )
        assert apprentice_outcome.output == {"coal": constants.PLAYER_SHIFT_OUTPUT_QTY}
        assert expert_outcome.output == {"coal": constants.PLAYER_SHIFT_OUTPUT_QTY * 2}

    def test_a_real_loss_produces_no_output(self):
        character = make_character()
        district = make_district(quota_good="coal")
        outcome = shared_shifts.resolve_shift_game(
            character, district, won=False, rng=FixedRng(0.0)
        )
        assert outcome.output == {}

    def test_neutral_still_produces_exactly_one_unit(self):
        character = make_character()
        district = make_district(quota_good="coal")
        outcome = shared_shifts.resolve_shift_game(
            character, district, won=False, neutral=True, rng=FixedRng(0.0)
        )
        assert outcome.output == {"coal": constants.PLAYER_SHIFT_OUTPUT_QTY}

    def test_no_output_for_a_district_with_no_quota_good(self):
        character = make_character()
        district = make_district(quota_good=None)
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
        assert outcome.output == {}

    def test_win_gives_a_reputation_bump_loss_gives_none(self):
        character = make_character()
        district = make_district()
        assert shared_shifts.resolve_shift_game(character, district, won=True).rep_delta == 1
        assert shared_shifts.resolve_shift_game(character, district, won=False).rep_delta == 0

    def test_win_streak_pays_a_bonus_on_the_streak_length_multiple(self):
        character = make_character(consecutive_wins=constants.REP_STREAK_LEN - 1)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
        assert outcome.rep_delta == 1 + constants.REP_STREAK_BONUS

    def test_win_streak_bonus_only_lands_on_the_streak_length_multiple(self):
        character = make_character(consecutive_wins=1)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
        assert outcome.rep_delta == 1

    def test_occasional_loss_stays_reputation_neutral(self):
        character = make_character(consecutive_losses=0)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=False)
        assert outcome.rep_delta == 0

    def test_loss_streak_costs_reputation_on_the_streak_length_multiple(self):
        character = make_character(consecutive_losses=constants.REP_STREAK_LEN - 1)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=False)
        assert outcome.rep_delta == -constants.REP_STREAK_PENALTY

    def test_neutral_ignores_the_loss_streak_entirely(self):
        character = make_character(consecutive_losses=constants.REP_STREAK_LEN - 1)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=False, neutral=True)
        assert outcome.rep_delta == 0

    def test_neutral_loss_pays_the_unmodified_wage_not_the_lose_penalty(self):
        character = make_character()
        district = make_district()
        neutral = shared_shifts.resolve_shift_game(character, district, won=False, neutral=True)
        assert neutral.wage == (
            constants.PLAYER_JOB_BASE_WAGE
            * constants.DISTRICT_WEALTH_WAGE_MULT_MIN
            / constants.SHIFT_DURATION_TICKS
        )

    def test_neutral_loss_pays_more_than_a_regular_loss(self):
        character = make_character()
        district = make_district()
        regular_loss = shared_shifts.resolve_shift_game(character, district, won=False)
        neutral_loss = shared_shifts.resolve_shift_game(
            character, district, won=False, neutral=True
        )
        assert neutral_loss.wage > regular_loss.wage

    def test_neutral_loss_still_gives_no_reputation(self):
        character = make_character()
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=False, neutral=True)
        assert outcome.rep_delta == 0

    def test_neutral_loss_still_scales_with_job_level_and_market(self):
        district = make_district()
        expert = make_character(shifts_completed=constants.JOB_LEVEL_SHIFT_THRESHOLDS["expert"])
        outcome = shared_shifts.resolve_shift_game(
            expert, district, won=False, neutral=True, market_multiplier=2.0
        )
        assert outcome.wage == (
            constants.PLAYER_JOB_BASE_WAGE
            * 3.0
            * constants.DISTRICT_WEALTH_WAGE_MULT_MIN
            * 2.0
            / constants.SHIFT_DURATION_TICKS
        )

    def test_a_poorer_district_pays_less_for_the_same_outcome(self):
        character = make_character()
        rich = make_district(district_id=1)
        poor = make_district(district_id=12)
        rich_wage = shared_shifts.resolve_shift_game(character, rich, won=True).wage
        poor_wage = shared_shifts.resolve_shift_game(character, poor, won=True).wage
        assert rich_wage > poor_wage

    def test_the_capitol_pays_the_most(self):
        character = make_character()
        capitol = make_district(district_id=0, quota_good=None)
        district_one = make_district(district_id=1)
        capitol_wage = shared_shifts.resolve_shift_game(character, capitol, won=True).wage
        district_one_wage = shared_shifts.resolve_shift_game(character, district_one, won=True).wage
        assert capitol_wage > district_one_wage


class TestDistrictWealthMultiplier:
    def test_the_capitol_gets_the_max_multiplier(self):
        assert (
            shared_shifts.district_wealth_multiplier(0) == constants.DISTRICT_WEALTH_WAGE_MULT_MAX
        )

    def test_district_twelve_gets_the_min_multiplier(self):
        assert (
            shared_shifts.district_wealth_multiplier(12) == constants.DISTRICT_WEALTH_WAGE_MULT_MIN
        )

    def test_strictly_decreases_as_district_id_increases(self):
        multipliers = [shared_shifts.district_wealth_multiplier(i) for i in range(13)]
        assert multipliers == sorted(multipliers, reverse=True)
        assert len(set(multipliers)) == 13


class TestMarketWageMultiplier:
    def test_price_at_base_gives_unit_multiplier(self):
        assert shared_shifts.market_wage_multiplier(10.0, 10.0) == 1.0

    def test_price_above_base_boosts_wage(self):
        assert shared_shifts.market_wage_multiplier(20.0, 10.0) == 2.0

    def test_price_below_base_debuffs_wage(self):
        assert shared_shifts.market_wage_multiplier(5.0, 10.0) == 0.5

    def test_zero_base_price_falls_back_to_unit_multiplier(self):
        assert shared_shifts.market_wage_multiplier(5.0, 0.0) == 1.0


class TestApplyShiftOutcome:
    def test_records_the_work_but_leaves_the_shift_open(self):
        character = make_character(money=0, reputation=0.0, consecutive_missed=4)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)

        shared_shifts.apply_shift_outcome(shift, character, outcome, won=True, tick=3)

        assert shift.result is None
        assert shift.completed_at is None
        assert shift.last_worked_tick == 3
        assert shift.output == outcome.output
        assert character.money == round(outcome.wage)
        assert character.reputation == outcome.rep_delta
        assert character.consecutive_missed == 0
        assert character.last_active_tick == 3

    def test_increments_shifts_completed(self):
        character = make_character(shifts_completed=5)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)

        shared_shifts.apply_shift_outcome(shift, character, outcome, won=True, tick=7)

        assert character.shifts_completed == 6

    def test_enough_completed_shifts_reaches_novice(self):
        character = make_character(
            shifts_completed=constants.JOB_LEVEL_SHIFT_THRESHOLDS["novice"] - 1
        )
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)

        shared_shifts.apply_shift_outcome(shift, character, outcome, won=True, tick=7)

        assert job_levels.job_level_for_shifts(character.shifts_completed) == JobLevel.NOVICE

    def test_reworking_the_same_shift_on_a_later_tick_does_not_double_count(self):
        character = make_character(shifts_completed=5)
        district = make_district()
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=13)

        shared_shifts.apply_shift_outcome(
            shift,
            character,
            shared_shifts.resolve_shift_game(character, district, won=True),
            won=True,
            tick=2,
        )
        shared_shifts.apply_shift_outcome(
            shift,
            character,
            shared_shifts.resolve_shift_game(character, district, won=True),
            won=True,
            tick=3,
        )

        assert character.shifts_completed == 6

    def test_output_accumulates_across_multiple_works_in_the_same_shift(self):
        character = make_character()
        district = make_district(quota_good="coal")
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=13)
        outcome = shared_shifts.resolve_shift_game(
            character, district, won=True, rng=FixedRng(0.99)
        )

        shared_shifts.apply_shift_outcome(shift, character, outcome, won=True, tick=2)
        shared_shifts.apply_shift_outcome(shift, character, outcome, won=True, tick=3)

        assert shift.output == {"coal": constants.PLAYER_SHIFT_OUTPUT_QTY * 2}

    def test_last_worked_tick_tracks_the_most_recent_work(self):
        character = make_character()
        district = make_district()
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=13)
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)

        shared_shifts.apply_shift_outcome(shift, character, outcome, won=True, tick=2)
        shared_shifts.apply_shift_outcome(shift, character, outcome, won=True, tick=5)

        assert shift.last_worked_tick == 5

    def test_docks_fatigue_per_work_resolution(self):
        character = make_character(fatigue=100.0)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)

        shared_shifts.apply_shift_outcome(shift, character, outcome, won=True, tick=3)

        assert character.fatigue == 100.0 - constants.FATIGUE_COST_PER_WORK

    def test_neutral_still_docks_fatigue(self):
        character = make_character(fatigue=100.0)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=False, neutral=True)
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)

        shared_shifts.apply_shift_outcome(
            shift, character, outcome, won=False, neutral=True, tick=3
        )

        assert character.fatigue == 100.0 - constants.FATIGUE_COST_PER_WORK

    def test_fatigue_is_floored_at_min(self):
        character = make_character(fatigue=1.0)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)

        shared_shifts.apply_shift_outcome(shift, character, outcome, won=True, tick=3)

        assert character.fatigue == constants.FATIGUE_MIN

    def test_win_extends_win_streak_and_resets_loss_streak(self):
        character = make_character(consecutive_wins=1, consecutive_losses=2)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)

        shared_shifts.apply_shift_outcome(shift, character, outcome, won=True, tick=3)

        assert character.consecutive_wins == 2
        assert character.consecutive_losses == 0

    def test_loss_extends_loss_streak_and_resets_win_streak(self):
        character = make_character(consecutive_wins=2, consecutive_losses=1)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=False)
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)

        shared_shifts.apply_shift_outcome(shift, character, outcome, won=False, tick=3)

        assert character.consecutive_losses == 2
        assert character.consecutive_wins == 0

    def test_neutral_leaves_both_streaks_untouched(self):
        character = make_character(consecutive_wins=2, consecutive_losses=1)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=False, neutral=True)
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)

        shared_shifts.apply_shift_outcome(
            shift, character, outcome, won=False, neutral=True, tick=3
        )

        assert character.consecutive_wins == 2
        assert character.consecutive_losses == 1


class TestAlreadyWorkedThisTick:
    def test_false_before_any_work(self):
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)
        assert shared_shifts.already_worked_this_tick(shift, 3) is False

    def test_true_for_the_tick_it_was_just_worked(self):
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)
        shift.last_worked_tick = 3
        assert shared_shifts.already_worked_this_tick(shift, 3) is True

    def test_false_once_the_tick_has_moved_on(self):
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)
        shift.last_worked_tick = 3
        assert shared_shifts.already_worked_this_tick(shift, 4) is False
