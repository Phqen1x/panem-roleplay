"""Smoke test that panem_shared.jail works standalone (no panem_bot
dependency) -- panem_api's /work minigame result endpoint imports it
directly, same reason panem_shared.shifts does.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared import jail as shared_jail
from panem_shared.db.models import Character, DistrictState
from panem_shared.enums import CharacterStatus


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
        sentence = shared_jail.commit_to_jail(character, 10)
        assert sentence == 10
        assert character.jailed_until_tick == 10
        assert character.jail_sentence_ticks == 10
        assert character.jail_count == 1

    def test_priors_lengthen_the_sentence(self):
        character = make_character(jail_count=3, jailed_until_tick=None)
        sentence = shared_jail.commit_to_jail(character, 10)
        assert sentence == 10 + 3 * constants.JAIL_PRIOR_TICKS_PER_COUNT
        assert character.jail_count == 4

    def test_extends_a_sentence_already_running_rather_than_shortening_it(self):
        character = make_character(jail_count=0, jailed_until_tick=50)
        shared_jail.commit_to_jail(character, 10)
        assert character.jailed_until_tick == 60


class TestResolveIllicitHeat:
    def test_below_threshold_no_arrest(self):
        character = make_character(illicit_heat=0.0)
        arrested = shared_jail.resolve_illicit_heat(character, None, lost=False, rng=FixedRng(0.99))
        assert arrested is False
        assert character.illicit_heat == constants.ILLICIT_HEAT_PER_SHIFT
        assert character.jailed_until_tick is None

    def test_a_loss_adds_more_heat_than_a_win(self):
        won_char = make_character(illicit_heat=0.0)
        lost_char = make_character(illicit_heat=0.0)
        shared_jail.resolve_illicit_heat(won_char, None, lost=False, rng=FixedRng(0.99))
        shared_jail.resolve_illicit_heat(lost_char, None, lost=True, rng=FixedRng(0.99))
        assert lost_char.illicit_heat > won_char.illicit_heat

    def test_over_threshold_evasion_success_halves_heat_and_avoids_jail(self):
        character = make_character(illicit_heat=constants.ILLICIT_HEAT_ARREST_THRESHOLD)
        # A roll safely below ARREST_EVASION_BASE_PROB succeeds evasion.
        arrested = shared_jail.resolve_illicit_heat(character, None, lost=False, rng=FixedRng(0.0))
        assert arrested is False
        assert character.jailed_until_tick is None
        expected_heat = (
            constants.ILLICIT_HEAT_ARREST_THRESHOLD + constants.ILLICIT_HEAT_PER_SHIFT
        ) / 2
        assert character.illicit_heat == expected_heat

    def test_over_threshold_evasion_failure_jails_fines_and_resets_heat(self):
        character = make_character(illicit_heat=constants.ILLICIT_HEAT_ARREST_THRESHOLD, money=100)
        arrested = shared_jail.resolve_illicit_heat(character, None, lost=False, rng=FixedRng(0.99))
        assert arrested is True
        assert character.money == 100 - constants.ILLICIT_ARREST_FINE
        assert character.jailed_until_tick == constants.ILLICIT_ARREST_JAIL_TICKS
        assert character.reputation == -constants.ILLICIT_ARREST_REP_PENALTY
        assert character.illicit_heat == 0.0

    def test_arrest_bumps_district_pressure_when_a_row_is_given(self):
        character = make_character(illicit_heat=constants.ILLICIT_HEAT_ARREST_THRESHOLD)
        district_row = DistrictState(district_id=1, peacekeeper_pressure=0.3)
        shared_jail.resolve_illicit_heat(character, district_row, lost=False, rng=FixedRng(0.99))
        assert district_row.peacekeeper_pressure == 0.3 + shared_jail.ARREST_PRESSURE_DELTA

    def test_missing_district_row_does_not_raise(self):
        character = make_character(illicit_heat=constants.ILLICIT_HEAT_ARREST_THRESHOLD)
        arrested = shared_jail.resolve_illicit_heat(character, None, lost=False, rng=FixedRng(0.99))
        assert arrested is True
