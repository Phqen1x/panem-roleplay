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
from panem_shared.enums import CharacterStatus, JobLevel, ShiftResult


def make_district(*, quota_good: str | None = "coal") -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Station", kind="station"),
    ]
    coords = {loc.id: (0, 0) for loc in locations}
    return District(
        id=12,
        name="District Twelve",
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
        consecutive_missed=0,
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
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
        assert outcome.wage == constants.PLAYER_JOB_BASE_WAGE * constants.WORK_GAME_WIN_WAGE_MULT

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

    def test_output_is_one_unit_of_the_districts_quota_good(self):
        character = make_character()
        district = make_district(quota_good="coal")
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
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
    def test_completes_shift_and_updates_character(self):
        character = make_character(money=0, reputation=0.0, consecutive_missed=4)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)

        shared_shifts.apply_shift_outcome(shift, character, outcome, tick=7)

        assert shift.result == ShiftResult.COMPLETED.value
        assert shift.completed_at == 7
        assert shift.output == outcome.output
        assert character.money == round(outcome.wage)
        assert character.reputation == outcome.rep_delta
        assert character.consecutive_missed == 0
        assert character.last_active_tick == 7

    def test_increments_shifts_completed(self):
        character = make_character(shifts_completed=5)
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)

        shared_shifts.apply_shift_outcome(shift, character, outcome, tick=7)

        assert character.shifts_completed == 6

    def test_enough_completed_shifts_reaches_novice(self):
        character = make_character(
            shifts_completed=constants.JOB_LEVEL_SHIFT_THRESHOLDS["novice"] - 1
        )
        district = make_district()
        outcome = shared_shifts.resolve_shift_game(character, district, won=True)
        shift = Shift(character_id=1, job_id="Miner", tick_opened=1, tick_due=7)

        shared_shifts.apply_shift_outcome(shift, character, outcome, tick=7)

        assert job_levels.job_level_for_shifts(character.shifts_completed) == JobLevel.NOVICE
