from __future__ import annotations

import pytest

from panem_bot.errors import NotAllowed
from panem_bot.services import sustenance as sustenance_svc
from panem_shared import constants
from panem_shared.db.models import Character
from panem_shared.enums import CharacterStatus, RpMode

TICK = constants.TICKS_PER_DAY * 3 + 5
"""Mid-day, not a day-boundary tick -- `TICK - 1` must land on the same
sim-day as `TICK` for the "already done today" tests below to mean what
they say."""


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
        rp_mode=RpMode.SIMULATION.value,
        money=1_000,
        hunger=50.0,
        thirst=50.0,
        sanity=50.0,
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = 1
    return character


class TestCheckCanEat:
    def test_refuses_a_dead_character(self):
        character = make_character(status=CharacterStatus.DEAD.value)
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_eat(character, TICK)
        assert exc_info.value.reason_key == "character_dead"

    def test_refuses_a_non_simulation_character(self):
        character = make_character(rp_mode=RpMode.LIFE.value)
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_eat(character, TICK)
        assert exc_info.value.reason_key == "sustenance_mode_forbidden"

    def test_refuses_a_second_time_the_same_sim_day(self):
        character = make_character(last_ate_tick=TICK - 1)
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_eat(character, TICK)
        assert exc_info.value.reason_key == "already_ate_today"

    def test_allows_once_a_new_sim_day_starts(self):
        character = make_character(last_ate_tick=TICK - constants.TICKS_PER_DAY)
        sustenance_svc.check_can_eat(character, TICK)

    def test_refuses_insufficient_funds(self):
        character = make_character(money=constants.EAT_COST - 1)
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_eat(character, TICK)
        assert exc_info.value.reason_key == "sustenance_insufficient_funds"


class TestEat:
    def test_relieves_hunger_spends_money_and_stamps_the_tick(self):
        character = make_character(hunger=80.0, money=100)
        hunger = sustenance_svc.eat(character, TICK)
        assert hunger == 80.0 - constants.EAT_RELIEF
        assert character.hunger == hunger
        assert character.money == 100 - constants.EAT_COST
        assert character.last_ate_tick == TICK

    def test_clamps_hunger_at_the_minimum(self):
        character = make_character(hunger=10.0)
        hunger = sustenance_svc.eat(character, TICK)
        assert hunger == constants.HUNGER_MIN

    def test_raises_and_leaves_the_character_untouched_when_already_ate_today(self):
        character = make_character(last_ate_tick=TICK, hunger=80.0, money=100)
        with pytest.raises(NotAllowed):
            sustenance_svc.eat(character, TICK)
        assert character.hunger == 80.0
        assert character.money == 100


class TestCheckCanDrink:
    def test_refuses_a_non_simulation_character(self):
        character = make_character(rp_mode=RpMode.STORY.value)
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_drink(character, TICK)
        assert exc_info.value.reason_key == "sustenance_mode_forbidden"

    def test_refuses_a_second_time_the_same_sim_day(self):
        character = make_character(last_drank_tick=TICK - 1)
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_drink(character, TICK)
        assert exc_info.value.reason_key == "already_drank_today"


class TestDrink:
    def test_relieves_thirst_spends_money_and_stamps_the_tick(self):
        character = make_character(thirst=80.0, money=100)
        thirst = sustenance_svc.drink(character, TICK)
        assert thirst == 80.0 - constants.THIRST_RELIEF_PER_DRINK
        assert character.thirst == thirst
        assert character.money == 100 - constants.DRINK_COST
        assert character.last_drank_tick == TICK

    def test_clamps_thirst_at_the_minimum(self):
        character = make_character(thirst=10.0)
        thirst = sustenance_svc.drink(character, TICK)
        assert thirst == constants.THIRST_MIN


class TestCheckCanEntertain:
    def test_refuses_a_second_time_the_same_sim_day(self):
        character = make_character(last_entertained_tick=TICK - 1)
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_entertain(character, TICK)
        assert exc_info.value.reason_key == "already_entertained_today"


class TestEntertain:
    def test_relieves_sanity_spends_money_and_stamps_the_tick(self):
        character = make_character(sanity=20.0, money=100)
        sanity = sustenance_svc.entertain(character, TICK)
        assert sanity == 20.0 + constants.SANITY_RELIEF_PER_ENTERTAIN
        assert character.sanity == sanity
        assert character.money == 100 - constants.ENTERTAIN_COST
        assert character.last_entertained_tick == TICK

    def test_clamps_sanity_at_the_maximum(self):
        character = make_character(sanity=90.0)
        sanity = sustenance_svc.entertain(character, TICK)
        assert sanity == constants.SANITY_MAX
