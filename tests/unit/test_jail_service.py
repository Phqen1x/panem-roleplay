from __future__ import annotations

import pytest

from panem_bot.errors import NotAllowed
from panem_bot.services import jail as jail_svc
from panem_shared import constants
from panem_shared.db.models import Character
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
        jail_sentence_ticks=None,
        jail_lockpick_tries_used=0,
        positions=[],
    )
    defaults.update(overrides)
    return Character(**defaults)  # type: ignore[arg-type]


class TestCheckIsJailed:
    def test_raises_when_not_jailed(self):
        character = make_character(jailed_until_tick=None)
        with pytest.raises(NotAllowed) as exc_info:
            jail_svc.check_is_jailed(character, 10)
        assert exc_info.value.reason_key == "jail_not_jailed"

    def test_raises_once_the_sentence_has_already_passed(self):
        character = make_character(jailed_until_tick=5)
        with pytest.raises(NotAllowed):
            jail_svc.check_is_jailed(character, 10)

    def test_no_raise_while_still_jailed(self):
        character = make_character(jailed_until_tick=20)
        jail_svc.check_is_jailed(character, 10)  # no raise


class TestBailCost:
    def test_scales_with_remaining_ticks(self):
        character = make_character(jailed_until_tick=110)
        cost = jail_svc.bail_cost(character, 100)
        assert cost == round(constants.BAIL_BASE_COST + constants.BAIL_COST_PER_REMAINING_TICK * 10)

    def test_raises_when_not_jailed(self):
        character = make_character(jailed_until_tick=None)
        with pytest.raises(NotAllowed):
            jail_svc.bail_cost(character, 10)


class TestPayBail:
    def test_happy_path_clears_the_sentence(self):
        character = make_character(
            jailed_until_tick=110, jail_sentence_ticks=10, jail_lockpick_tries_used=2, money=1000
        )
        cost = jail_svc.pay_bail(character, 100)
        assert character.money == 1000 - cost
        assert character.jailed_until_tick is None
        assert character.jail_sentence_ticks is None
        assert character.jail_lockpick_tries_used == 0

    def test_insufficient_funds_refuses_and_changes_nothing(self):
        character = make_character(jailed_until_tick=110, money=0)
        with pytest.raises(NotAllowed) as exc_info:
            jail_svc.pay_bail(character, 100)
        assert exc_info.value.reason_key == "bail_insufficient_funds"
        assert character.jailed_until_tick == 110
        assert character.money == 0


class TestCheckCanAttemptLockpick:
    def test_raises_when_not_jailed(self):
        character = make_character(jailed_until_tick=None)
        with pytest.raises(NotAllowed) as exc_info:
            jail_svc.check_can_attempt_lockpick(character, 10)
        assert exc_info.value.reason_key == "jail_not_jailed"

    def test_raises_when_out_of_tries(self):
        character = make_character(
            jailed_until_tick=110, jail_lockpick_tries_used=constants.LOCKPICK_MAX_TRIES
        )
        with pytest.raises(NotAllowed) as exc_info:
            jail_svc.check_can_attempt_lockpick(character, 10)
        assert exc_info.value.reason_key == "lockpick_no_tries_left"

    def test_no_raise_and_no_side_effect_when_allowed(self):
        character = make_character(jailed_until_tick=110, jail_lockpick_tries_used=0)
        jail_svc.check_can_attempt_lockpick(character, 10)  # no raise
        assert character.jail_lockpick_tries_used == 0


class TestAttemptLockpick:
    def test_raises_when_not_jailed(self):
        character = make_character(jailed_until_tick=None)
        with pytest.raises(NotAllowed) as exc_info:
            jail_svc.attempt_lockpick(character, 10, rng=FixedRng(0.0))
        assert exc_info.value.reason_key == "jail_not_jailed"

    def test_success_clears_the_sentence(self):
        character = make_character(
            jailed_until_tick=110, jail_sentence_ticks=1, jail_lockpick_tries_used=0
        )
        success = jail_svc.attempt_lockpick(character, 100, rng=FixedRng(0.0))
        assert success is True
        assert character.jailed_until_tick is None
        assert character.jail_lockpick_tries_used == 0

    def test_failure_consumes_a_try_but_leaves_the_sentence(self):
        character = make_character(
            jailed_until_tick=110, jail_sentence_ticks=1, jail_lockpick_tries_used=0
        )
        success = jail_svc.attempt_lockpick(character, 100, rng=FixedRng(0.99))
        assert success is False
        assert character.jailed_until_tick == 110
        assert character.jail_lockpick_tries_used == 1

    def test_out_of_tries_refuses(self):
        character = make_character(
            jailed_until_tick=110,
            jail_sentence_ticks=1,
            jail_lockpick_tries_used=constants.LOCKPICK_MAX_TRIES,
        )
        with pytest.raises(NotAllowed) as exc_info:
            jail_svc.attempt_lockpick(character, 100, rng=FixedRng(0.0))
        assert exc_info.value.reason_key == "lockpick_no_tries_left"

    def test_a_longer_sentence_is_harder_to_pick(self):
        short = make_character(jailed_until_tick=110, jail_sentence_ticks=1)
        long = make_character(jailed_until_tick=110, jail_sentence_ticks=1000)
        # A roll that clears a short sentence's odds but not a long one's.
        roll = (constants.LOCKPICK_BASE_SUCCESS_PROB + constants.LOCKPICK_MIN_SUCCESS_PROB) / 2
        assert jail_svc.attempt_lockpick(short, 100, rng=FixedRng(roll)) is True
        assert jail_svc.attempt_lockpick(long, 100, rng=FixedRng(roll)) is False
