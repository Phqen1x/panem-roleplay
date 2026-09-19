"""Smoke test that panem_shared.jail works standalone (no panem_bot
dependency) -- panem_api's /work minigame result endpoint imports it
directly, same reason panem_shared.shifts does.
"""

from __future__ import annotations

import pytest

from panem_shared import constants
from panem_shared import jail as shared_jail
from panem_shared.db.models import Character, DistrictState
from panem_shared.enums import CharacterStatus
from panem_shared.errors import NotAllowed


def make_district_row(**overrides: object) -> DistrictState:
    defaults: dict[str, object] = dict(district_id=1, peacekeeper_pressure=0.3)
    defaults.update(overrides)
    return DistrictState(**defaults)  # type: ignore[arg-type]


class FixedRng:
    def __init__(self, value: float) -> None:
        self._value = value

    def random(self) -> float:
        return self._value


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
        money=100,
        reputation=0.0,
        jail_count=0,
        jailed_until_tick=None,
        illicit_heat=0.0,
        positions=[],
    )
    defaults.update(overrides)
    return Character(**defaults)  # type: ignore[arg-type]


class TestCommitToJail:
    def test_first_offense_uses_the_base_sentence(self):
        character = make_character(jail_count=0, jailed_until_tick=None)
        sentence = shared_jail.commit_to_jail(character, 10, 0)
        assert sentence == 10
        assert character.jailed_until_tick == 10
        assert character.jail_sentence_ticks == 10
        assert character.jail_count == 1

    def test_priors_lengthen_the_sentence(self):
        character = make_character(jail_count=3, jailed_until_tick=None)
        sentence = shared_jail.commit_to_jail(character, 10, 0)
        assert sentence == 10 + 3 * constants.JAIL_PRIOR_TICKS_PER_COUNT
        assert character.jail_count == 4

    def test_extends_a_sentence_already_running_rather_than_shortening_it(self):
        character = make_character(jail_count=0, jailed_until_tick=50)
        shared_jail.commit_to_jail(character, 10, 0)
        assert character.jailed_until_tick == 60

    def test_resets_lockpick_tries_for_the_fresh_sentence(self):
        character = make_character(jail_count=0, jailed_until_tick=None)
        character.jail_lockpick_tries_used = 2
        shared_jail.commit_to_jail(character, 10, 0)
        assert character.jail_lockpick_tries_used == 0

    def test_first_offense_at_a_non_zero_world_tick_jails_from_now_not_from_zero(self):
        # Regression test: `base_tick` used to be `jailed_until_tick or 0`
        # with no `current_tick` involved at all, so a first-time
        # offender's sentence was always anchored at absolute tick
        # `sentence` regardless of what tick the world clock was actually
        # on. Any real game (world tick > 0 almost immediately) had every
        # first jailing land in the past the instant it was set --
        # `jailed_until_tick (10) <= current_tick (1000)` reads as already
        # free. The sentence must run from *now*.
        character = make_character(jail_count=0, jailed_until_tick=None)
        sentence = shared_jail.commit_to_jail(character, 10, 1000)
        assert sentence == 10
        assert character.jailed_until_tick == 1010
        assert character.jailed_until_tick > 1000  # actually jailed, not already free

    def test_a_lapsed_previous_sentence_also_anchors_from_now_not_from_the_stale_value(self):
        # A character jailed long ago (jailed_until_tick already in the
        # past relative to current_tick) getting caught again shouldn't
        # extend from that stale, already-expired value either -- same
        # underlying bug, just via the "extends a sentence" branch instead
        # of the "first offense" branch.
        character = make_character(jail_count=0, jailed_until_tick=50)
        shared_jail.commit_to_jail(character, 10, 1000)
        assert character.jailed_until_tick == 1010


class TestCheckNotJailed:
    def test_no_raise_when_never_jailed(self):
        character = make_character(jailed_until_tick=None)
        shared_jail.check_not_jailed(character, 100, "travel_jailed")  # no raise

    def test_raises_while_currently_jailed(self):
        character = make_character(jailed_until_tick=200)
        with pytest.raises(NotAllowed) as exc_info:
            shared_jail.check_not_jailed(character, 100, "travel_jailed")
        assert exc_info.value.reason_key == "travel_jailed"

    def test_no_raise_once_the_sentence_has_lapsed(self):
        character = make_character(jailed_until_tick=50)
        shared_jail.check_not_jailed(character, 100, "travel_jailed")  # no raise

    def test_reason_key_is_caller_specific(self):
        character = make_character(jailed_until_tick=200)
        with pytest.raises(NotAllowed) as exc_info:
            shared_jail.check_not_jailed(character, 100, "work_jailed")
        assert exc_info.value.reason_key == "work_jailed"


class TestResolveIllicitHeat:
    def test_below_threshold_no_arrest(self):
        character = make_character(illicit_heat=0.0)
        arrested = shared_jail.resolve_illicit_heat(
            character, None, lost=False, current_tick=0, rng=FixedRng(0.99)
        )
        assert arrested is False
        assert character.illicit_heat == constants.ILLICIT_HEAT_PER_SHIFT
        assert character.jailed_until_tick is None

    def test_a_loss_adds_more_heat_than_a_win(self):
        won_char = make_character(illicit_heat=0.0)
        lost_char = make_character(illicit_heat=0.0)
        shared_jail.resolve_illicit_heat(
            won_char, None, lost=False, current_tick=0, rng=FixedRng(0.99)
        )
        shared_jail.resolve_illicit_heat(
            lost_char, None, lost=True, current_tick=0, rng=FixedRng(0.99)
        )
        assert lost_char.illicit_heat > won_char.illicit_heat

    def test_over_threshold_evasion_success_halves_heat_and_avoids_jail(self):
        character = make_character(illicit_heat=constants.ILLICIT_HEAT_ARREST_THRESHOLD)
        # A roll safely below ARREST_EVASION_BASE_PROB succeeds evasion.
        arrested = shared_jail.resolve_illicit_heat(
            character, None, lost=False, current_tick=0, rng=FixedRng(0.0)
        )
        assert arrested is False
        assert character.jailed_until_tick is None
        expected_heat = (
            constants.ILLICIT_HEAT_ARREST_THRESHOLD + constants.ILLICIT_HEAT_PER_SHIFT
        ) / 2
        assert character.illicit_heat == expected_heat

    def test_over_threshold_evasion_failure_jails_fines_and_resets_heat(self):
        character = make_character(illicit_heat=constants.ILLICIT_HEAT_ARREST_THRESHOLD, money=100)
        arrested = shared_jail.resolve_illicit_heat(
            character, None, lost=False, current_tick=0, rng=FixedRng(0.99)
        )
        assert arrested is True
        assert character.money == 100 - constants.ILLICIT_ARREST_FINE
        assert character.jailed_until_tick == constants.ILLICIT_ARREST_JAIL_TICKS
        assert character.reputation == -constants.ILLICIT_ARREST_REP_PENALTY
        assert character.illicit_heat == 0.0

    def test_arrest_bumps_district_pressure_when_a_row_is_given(self):
        character = make_character(illicit_heat=constants.ILLICIT_HEAT_ARREST_THRESHOLD)
        district_row = DistrictState(district_id=1, peacekeeper_pressure=0.3)
        shared_jail.resolve_illicit_heat(
            character, district_row, lost=False, current_tick=0, rng=FixedRng(0.99)
        )
        assert district_row.peacekeeper_pressure == 0.3 + shared_jail.ARREST_PRESSURE_DELTA

    def test_missing_district_row_does_not_raise(self):
        character = make_character(illicit_heat=constants.ILLICIT_HEAT_ARREST_THRESHOLD)
        arrested = shared_jail.resolve_illicit_heat(
            character, None, lost=False, current_tick=0, rng=FixedRng(0.99)
        )
        assert arrested is True

    def test_arrest_evasion_is_harder_under_an_active_crackdown(self):
        character = make_character(illicit_heat=constants.ILLICIT_HEAT_ARREST_THRESHOLD)
        district_row = make_district_row(crackdown_until_tick=100)
        # A roll that clears the base evasion prob but not the scaled-down one.
        roll = (
            constants.ARREST_EVASION_BASE_PROB
            + constants.ARREST_EVASION_BASE_PROB / constants.CRACKDOWN_DETECTION_MULTIPLIER
        ) / 2
        arrested = shared_jail.resolve_illicit_heat(
            character, district_row, lost=False, current_tick=10, rng=FixedRng(roll)
        )
        assert arrested is True

    def test_crackdown_has_no_effect_once_its_window_passes(self):
        character = make_character(illicit_heat=constants.ILLICIT_HEAT_ARREST_THRESHOLD)
        district_row = make_district_row(crackdown_until_tick=5)
        roll = (
            constants.ARREST_EVASION_BASE_PROB
            + constants.ARREST_EVASION_BASE_PROB / constants.CRACKDOWN_DETECTION_MULTIPLIER
        ) / 2
        arrested = shared_jail.resolve_illicit_heat(
            character, district_row, lost=False, current_tick=10, rng=FixedRng(roll)
        )
        assert arrested is False


class TestReleaseFromJail:
    def test_clears_all_three_fields(self):
        character = make_character(
            jailed_until_tick=50, jail_sentence_ticks=10, jail_lockpick_tries_used=2
        )
        shared_jail.release_from_jail(character)
        assert character.jailed_until_tick is None
        assert character.jail_sentence_ticks is None
        assert character.jail_lockpick_tries_used == 0


class TestLockpickDifficulty:
    def test_no_sentence_reads_as_the_base_difficulty(self):
        character = make_character(jail_sentence_ticks=None)
        assert (
            shared_jail.lockpick_difficulty(character) == 1.0 - constants.LOCKPICK_BASE_SUCCESS_PROB
        )

    def test_a_longer_sentence_is_harder(self):
        short = make_character(jail_sentence_ticks=1)
        long = make_character(jail_sentence_ticks=1000)
        assert shared_jail.lockpick_difficulty(long) > shared_jail.lockpick_difficulty(short)

    def test_floors_at_the_minimum_success_prob(self):
        character = make_character(jail_sentence_ticks=10_000_000)
        assert (
            shared_jail.lockpick_difficulty(character) == 1.0 - constants.LOCKPICK_MIN_SUCCESS_PROB
        )


class TestApplyLockpickAttempt:
    def test_a_win_releases(self):
        character = make_character(
            jailed_until_tick=50, jail_sentence_ticks=10, jail_lockpick_tries_used=1
        )
        shared_jail.apply_lockpick_attempt(character, won=True)
        assert character.jailed_until_tick is None
        assert character.jail_lockpick_tries_used == 0

    def test_a_loss_consumes_a_try_but_leaves_the_sentence(self):
        character = make_character(
            jailed_until_tick=50, jail_sentence_ticks=10, jail_lockpick_tries_used=1
        )
        shared_jail.apply_lockpick_attempt(character, won=False)
        assert character.jailed_until_tick == 50
        assert character.jail_lockpick_tries_used == 2


class TestCrackdownOdds:
    def test_inactive_without_a_row(self):
        assert shared_jail.is_crackdown_active(None, 10) is False

    def test_inactive_once_the_window_has_passed(self):
        row = make_district_row(crackdown_until_tick=5)
        assert shared_jail.is_crackdown_active(row, 10) is False

    def test_active_within_the_window(self):
        row = make_district_row(crackdown_until_tick=100)
        assert shared_jail.is_crackdown_active(row, 10) is True

    def test_bad_odds_scale_up_when_active(self):
        row = make_district_row(crackdown_until_tick=100)
        assert (
            shared_jail.crackdown_bad_odds(0.1, row, 10)
            == 0.1 * constants.CRACKDOWN_DETECTION_MULTIPLIER
        )

    def test_bad_odds_unchanged_when_inactive(self):
        assert shared_jail.crackdown_bad_odds(0.1, None, 10) == 0.1

    def test_good_odds_scale_down_when_active(self):
        row = make_district_row(crackdown_until_tick=100)
        assert (
            shared_jail.crackdown_good_odds(0.5, row, 10)
            == 0.5 / constants.CRACKDOWN_DETECTION_MULTIPLIER
        )

    def test_good_odds_unchanged_when_inactive(self):
        assert shared_jail.crackdown_good_odds(0.5, None, 10) == 0.5

    def test_bad_odds_clamp_at_one(self):
        row = make_district_row(crackdown_until_tick=100)
        assert shared_jail.crackdown_bad_odds(0.9, row, 10) == 1.0
