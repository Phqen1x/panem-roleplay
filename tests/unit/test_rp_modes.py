from __future__ import annotations

import datetime as dt

import pytest

from panem_shared import rp_modes
from panem_shared.db.models import Character
from panem_shared.enums import CharacterStatus, RpMode
from panem_shared.errors import NotAllowed, ValidationFailed

NOW = dt.datetime(2026, 1, 10, tzinfo=dt.UTC)


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
        rp_mode=RpMode.SIMULATION.value,
        crime_enabled=True,
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = 1
    return character


class TestCheckCanSwitchMode:
    def test_allows_a_first_ever_switch(self):
        character = make_character(rp_mode_changed_at=None)
        rp_modes.check_can_switch_mode(character, RpMode.LIFE, NOW)

    def test_refuses_switching_to_the_currently_active_mode(self):
        character = make_character(rp_mode=RpMode.LIFE.value)
        with pytest.raises(ValidationFailed) as exc_info:
            rp_modes.check_can_switch_mode(character, RpMode.LIFE, NOW)
        assert exc_info.value.reason_key == "mode_already_active"

    def test_refuses_within_the_cooldown_window(self):
        character = make_character(rp_mode_changed_at=NOW - dt.timedelta(days=1))
        with pytest.raises(NotAllowed) as exc_info:
            rp_modes.check_can_switch_mode(character, RpMode.LIFE, NOW)
        assert exc_info.value.reason_key == "mode_switch_on_cooldown"

    def test_allows_once_the_cooldown_has_fully_elapsed(self):
        character = make_character(rp_mode_changed_at=NOW - dt.timedelta(days=3, minutes=1))
        rp_modes.check_can_switch_mode(character, RpMode.LIFE, NOW)

    def test_refuses_while_a_switch_is_already_staged(self):
        character = make_character(rp_mode_changed_at=None, pending_rp_mode=RpMode.LIFE.value)
        with pytest.raises(NotAllowed) as exc_info:
            rp_modes.check_can_switch_mode(character, RpMode.STORY, NOW)
        assert exc_info.value.reason_key == "mode_switch_already_pending"


class TestSwitchMode:
    def test_updates_mode_and_stamps_the_change(self):
        character = make_character(rp_mode_changed_at=None)
        rp_modes.switch_mode(character, RpMode.STORY, NOW)
        assert character.rp_mode == RpMode.STORY.value
        assert character.rp_mode_changed_at == NOW

    def test_raises_and_leaves_the_character_untouched_on_cooldown(self):
        character = make_character(rp_mode_changed_at=NOW - dt.timedelta(days=1))
        with pytest.raises(NotAllowed):
            rp_modes.switch_mode(character, RpMode.LIFE, NOW)
        assert character.rp_mode == RpMode.SIMULATION.value


class TestModeSwitchNeedsJobInfo:
    def test_false_for_switching_to_story(self):
        character = make_character(rp_mode=RpMode.LIFE.value, job_title=None)
        assert rp_modes.mode_switch_needs_job_info(character, RpMode.STORY) is False

    def test_true_for_a_character_that_has_never_had_a_job(self):
        character = make_character(rp_mode=RpMode.STORY.value, job_title=None)
        assert rp_modes.mode_switch_needs_job_info(character, RpMode.SIMULATION) is True

    def test_false_for_a_character_that_had_a_job_in_the_past(self):
        character = make_character(rp_mode=RpMode.STORY.value, job_title="Baker")
        assert rp_modes.mode_switch_needs_job_info(character, RpMode.SIMULATION) is False


class TestStageAndApplyModeSwitch:
    def test_stage_touches_only_the_pending_columns(self):
        character = make_character(
            rp_mode=RpMode.STORY.value, job_title=None, shift_phase=None, job_is_illicit=False
        )
        rp_modes.stage_mode_switch(
            character,
            RpMode.SIMULATION,
            job_title="Baker",
            shift_phase="morning",
            job_is_illicit=False,
        )
        assert character.rp_mode == RpMode.STORY.value
        assert character.job_title is None
        assert character.shift_phase is None
        assert character.pending_rp_mode == RpMode.SIMULATION.value
        assert character.pending_job_title == "Baker"
        assert character.pending_shift_phase == "morning"
        assert character.pending_mode_switch_notified_at is None

    def test_apply_copies_staged_fields_and_clears_staging(self):
        character = make_character(rp_mode=RpMode.STORY.value, job_title=None)
        rp_modes.stage_mode_switch(
            character,
            RpMode.SIMULATION,
            job_title="Baker",
            shift_phase="morning",
            job_is_illicit=True,
        )
        rp_modes.apply_staged_mode_switch(character, NOW)
        assert character.rp_mode == RpMode.SIMULATION.value
        assert character.job_title == "Baker"
        assert character.shift_phase == "morning"
        assert character.job_is_illicit is True
        assert character.rp_mode_changed_at == NOW
        assert character.pending_rp_mode is None
        assert character.pending_job_title is None
        assert character.pending_shift_phase is None
        assert character.pending_job_is_illicit is False

    def test_discard_clears_staging_without_touching_the_real_mode(self):
        character = make_character(rp_mode=RpMode.STORY.value, job_title=None)
        rp_modes.stage_mode_switch(
            character,
            RpMode.SIMULATION,
            job_title="Baker",
            shift_phase="morning",
            job_is_illicit=False,
        )
        rp_modes.discard_staged_mode_switch(character)
        assert character.rp_mode == RpMode.STORY.value
        assert character.job_title is None
        assert character.pending_rp_mode is None
        assert character.pending_job_title is None


class TestCheckCanToggleCrime:
    def test_refuses_a_non_life_mode_character(self):
        character = make_character(rp_mode=RpMode.SIMULATION.value)
        with pytest.raises(NotAllowed) as exc_info:
            rp_modes.check_can_toggle_crime(character, False, NOW)
        assert exc_info.value.reason_key == "crime_toggle_wrong_mode"

    def test_refuses_setting_the_already_active_value(self):
        character = make_character(rp_mode=RpMode.LIFE.value, crime_enabled=True)
        with pytest.raises(ValidationFailed) as exc_info:
            rp_modes.check_can_toggle_crime(character, True, NOW)
        assert exc_info.value.reason_key == "crime_toggle_already_set"

    def test_refuses_within_the_cooldown_window(self):
        character = make_character(
            rp_mode=RpMode.LIFE.value,
            crime_enabled=True,
            crime_toggle_changed_at=NOW - dt.timedelta(hours=1),
        )
        with pytest.raises(NotAllowed) as exc_info:
            rp_modes.check_can_toggle_crime(character, False, NOW)
        assert exc_info.value.reason_key == "crime_toggle_on_cooldown"

    def test_allows_a_life_mode_character_once_off_cooldown(self):
        character = make_character(
            rp_mode=RpMode.LIFE.value,
            crime_enabled=True,
            crime_toggle_changed_at=NOW - dt.timedelta(days=1, minutes=1),
        )
        rp_modes.check_can_toggle_crime(character, False, NOW)


class TestToggleCrime:
    def test_flips_the_flag_and_stamps_the_change(self):
        character = make_character(rp_mode=RpMode.LIFE.value, crime_enabled=True)
        rp_modes.toggle_crime(character, False, NOW)
        assert character.crime_enabled is False
        assert character.crime_toggle_changed_at == NOW
