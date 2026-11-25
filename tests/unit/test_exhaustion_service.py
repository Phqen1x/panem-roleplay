from __future__ import annotations

import pytest

from panem_shared import exhaustion
from panem_shared.db.models import Character, ExhaustionSettings
from panem_shared.enums import CharacterStatus
from panem_shared.errors import NotAllowed


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
        passed_out_until_tick=None,
    )
    defaults.update(overrides)
    return Character(**defaults)  # type: ignore[arg-type]


class TestIsPassedOut:
    def test_false_when_never_passed_out(self):
        character = make_character(passed_out_until_tick=None)
        assert exhaustion.is_passed_out(character, 10) is False

    def test_true_while_still_passed_out(self):
        character = make_character(passed_out_until_tick=20)
        assert exhaustion.is_passed_out(character, 10) is True

    def test_false_once_the_duration_has_passed(self):
        character = make_character(passed_out_until_tick=5)
        assert exhaustion.is_passed_out(character, 10) is False

    def test_false_exactly_at_the_deadline(self):
        character = make_character(passed_out_until_tick=10)
        assert exhaustion.is_passed_out(character, 10) is False


class TestCheckNotPassedOut:
    def test_raises_with_the_callers_reason_key_while_passed_out(self):
        character = make_character(passed_out_until_tick=20)
        with pytest.raises(NotAllowed) as exc_info:
            exhaustion.check_not_passed_out(character, 10, "work_passed_out")
        assert exc_info.value.reason_key == "work_passed_out"

    def test_no_raise_once_recovered(self):
        character = make_character(passed_out_until_tick=5)
        exhaustion.check_not_passed_out(character, 10, "work_passed_out")  # no raise

    def test_no_raise_when_never_passed_out(self):
        character = make_character(passed_out_until_tick=None)
        exhaustion.check_not_passed_out(character, 10, "work_passed_out")  # no raise


class TestPassedOutDurationTicks:
    def test_reads_the_settings_row_when_present(self):
        settings = ExhaustionSettings(id=1, passed_out_ticks=7)
        assert exhaustion.passed_out_duration_ticks(settings) == 7

    def test_falls_back_to_the_default_constant_when_missing(self):
        from panem_shared import constants

        assert exhaustion.passed_out_duration_ticks(None) == constants.PASSED_OUT_DEFAULT_TICKS


class TestApplyPassedOut:
    def test_sets_the_deadline_from_the_current_tick_plus_duration(self):
        character = make_character(passed_out_until_tick=None)
        exhaustion.apply_passed_out(character, 12, 100)
        assert character.passed_out_until_tick == 112

    def test_overwrites_an_existing_deadline(self):
        character = make_character(passed_out_until_tick=50)
        exhaustion.apply_passed_out(character, 12, 100)
        assert character.passed_out_until_tick == 112
