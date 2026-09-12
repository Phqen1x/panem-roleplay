from __future__ import annotations

import pytest

from panem_shared import constants
from panem_shared.db.models import Character, Npc
from panem_shared.enums import CharacterStatus, DayPhase
from panem_sim.rng import tick_rng
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import needs


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
        money=0,
        hunger=0.0,
        health=100.0,
    )
    defaults.update(overrides)
    return Character(**defaults)  # type: ignore[arg-type]


def make_npc(**overrides: object) -> Npc:
    defaults: dict[str, object] = dict(
        id="npc1", district_id=1, name="npc1", age=30, money=0.0, hunger=0.0, health=100.0
    )
    defaults.update(overrides)
    return Npc(**defaults)  # type: ignore[arg-type]


def make_ctx(tick: int) -> TickContext:
    from panem_shared.content.loader import ContentBundle

    content = ContentBundle(districts={}, goods={}, jobs={}, routes=[])
    return TickContext(
        tick=tick,
        phase=DayPhase.NIGHT,
        day=1,
        month=1,
        rng=tick_rng("test-seed", tick),
        content=content,
    )


class TestRunGating:
    def test_only_fires_on_day_boundary_ticks(self):
        character = make_character(money=0)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=1))
        assert character.hunger == 0.0

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))
        assert character.hunger != 0.0


class TestCharacterNeeds:
    def test_paying_living_cost_lowers_hunger_and_recovers_health(self):
        character = make_character(
            money=constants.NIGHTLY_LIVING_COST * 2, hunger=50.0, health=90.0
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert character.money == constants.NIGHTLY_LIVING_COST
        assert character.hunger == 50.0 - constants.HUNGER_DECREASE_MET
        assert character.health == 90.0 + constants.HEALTH_RECOVERY_PER_NIGHT

    def test_unable_to_pay_raises_hunger_and_leaves_money_unchanged(self):
        character = make_character(money=0, hunger=10.0)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert character.money == 0
        assert character.hunger == 10.0 + constants.HUNGER_INCREASE_UNMET

    def test_high_hunger_erodes_health_instead_of_recovering(self):
        # Unable to pay, so hunger rises further above the decay threshold
        # (rather than paying, which would lower hunger this same tick and
        # could drop it back under the threshold before the health check).
        character = make_character(
            money=0,
            hunger=constants.HEALTH_DECAY_HUNGER_THRESHOLD,
            health=50.0,
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert character.health == 50.0 - constants.HEALTH_DECAY_PER_NIGHT

    def test_hunger_and_health_are_clamped_to_bounds(self):
        character = make_character(
            money=0, hunger=constants.HUNGER_MAX, health=constants.HEALTH_MIN
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert character.hunger == constants.HUNGER_MAX
        assert character.health == constants.HEALTH_MIN


class TestNpcNeeds:
    def test_npc_uses_float_living_cost_and_same_hunger_health_rules(self):
        npc = make_npc(money=constants.NIGHTLY_LIVING_COST_NPC, hunger=80.0, health=95.0)
        state = WorldState(
            districts={}, npcs={"npc1": npc}, npc_schedules={}, characters={}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert npc.money == pytest.approx(0.0)
        assert npc.hunger == 80.0 - constants.HUNGER_DECREASE_MET
        assert npc.health == 95.0 - constants.HEALTH_DECAY_PER_NIGHT
