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
