from __future__ import annotations

import pytest

from panem_shared import constants
from panem_shared.db.models import AfflictionType, Character, CharacterAffliction, Npc
from panem_shared.enums import AfflictionSource, CharacterStatus, DayPhase, RpMode
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
        rp_mode=RpMode.SIMULATION.value,
        money=0,
        hunger=0.0,
        health=100.0,
        fatigue=100.0,
        thirst=0.0,
        sanity=100.0,
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


class TestFatigueExhaustion:
    def test_exhausted_fatigue_costs_extra_health_on_top_of_hunger(self):
        character = make_character(
            money=constants.NIGHTLY_LIVING_COST * 2,
            hunger=0.0,
            health=90.0,
            fatigue=constants.FATIGUE_EXHAUSTION_THRESHOLD,
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        # Hunger is paid off this same tick (health recovers from that),
        # but exhaustion still docks its own separate penalty.
        assert character.health == pytest.approx(
            90.0 + constants.HEALTH_RECOVERY_PER_NIGHT - constants.FATIGUE_EXHAUSTION_HEALTH_PENALTY
        )

    def test_rested_fatigue_costs_no_extra_health(self):
        character = make_character(
            money=constants.NIGHTLY_LIVING_COST * 2,
            hunger=0.0,
            health=90.0,
            fatigue=constants.FATIGUE_EXHAUSTION_THRESHOLD + 1,
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert character.health == pytest.approx(90.0 + constants.HEALTH_RECOVERY_PER_NIGHT)

    def test_exhaustion_penalty_is_clamped_to_health_min(self):
        character = make_character(
            money=0,
            hunger=constants.HEALTH_DECAY_HUNGER_THRESHOLD,
            health=constants.HEALTH_MIN,
            fatigue=0.0,
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

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


class TestModeGating:
    def test_life_mode_characters_are_left_entirely_untouched(self):
        character = make_character(
            rp_mode=RpMode.LIFE.value, money=0, hunger=0.0, health=100.0, thirst=0.0, sanity=100.0
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert character.money == 0
        assert character.hunger == 0.0
        assert character.health == 100.0
        assert character.thirst == 0.0
        assert character.sanity == 100.0

    def test_story_mode_characters_are_left_entirely_untouched(self):
        character = make_character(rp_mode=RpMode.STORY.value, money=0, thirst=0.0, sanity=100.0)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert character.thirst == 0.0
        assert character.sanity == 100.0


class TestThirstAndSanity:
    def test_thirst_rises_when_not_drunk_today(self):
        character = make_character(thirst=0.0, last_drank_tick=None)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert character.thirst == constants.THIRST_INCREASE_PER_DAY

    def test_thirst_holds_steady_if_drunk_earlier_that_same_day(self):
        character = make_character(thirst=20.0, last_drank_tick=5)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert character.thirst == 20.0

    def test_high_thirst_erodes_health(self):
        character = make_character(
            thirst=constants.HEALTH_DECAY_THIRST_THRESHOLD,
            last_drank_tick=None,
            health=50.0,
            money=constants.NIGHTLY_LIVING_COST * 2,
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert character.health == pytest.approx(
            50.0 + constants.HEALTH_RECOVERY_PER_NIGHT - constants.HEALTH_DECAY_PER_NIGHT_THIRST
        )

    def test_sanity_falls_when_not_entertained_today(self):
        character = make_character(sanity=100.0, last_entertained_tick=None)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert character.sanity == 100.0 - constants.SANITY_DECREASE_PER_DAY

    def test_sanity_holds_steady_if_entertained_earlier_that_same_day(self):
        character = make_character(sanity=90.0, last_entertained_tick=5)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert character.sanity == 90.0

    def test_low_sanity_erodes_health(self):
        character = make_character(
            sanity=constants.HEALTH_DECAY_SANITY_THRESHOLD,
            last_entertained_tick=None,
            health=50.0,
            money=constants.NIGHTLY_LIVING_COST * 2,
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert character.health == pytest.approx(
            50.0 + constants.HEALTH_RECOVERY_PER_NIGHT - constants.HEALTH_DECAY_PER_NIGHT_SANITY
        )


class TestAutoAfflictionsAndDeath:
    def test_applies_a_catalog_affliction_once_a_stat_crosses_its_threshold(self):
        character = make_character(thirst=90.0, money=constants.NIGHTLY_LIVING_COST * 2)
        character.id = 1
        affliction_type = AfflictionType(
            name="Dehydrated",
            description="",
            is_permanent=False,
            auto_apply_stat="thirst",
            auto_apply_threshold=1000.0,
        )
        state = WorldState(
            districts={},
            npcs={},
            npc_schedules={},
            characters={1: character},
            open_shifts=[],
            affliction_types=[affliction_type],
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert len(state.new_character_afflictions) == 1
        assert state.new_character_afflictions[0].character_id == 1

    def test_cures_an_active_affliction_once_its_stat_recovers(self):
        character = make_character(sanity=80.0, money=constants.NIGHTLY_LIVING_COST * 2)
        character.id = 1
        affliction_type = AfflictionType(
            name="Nightmares",
            description="",
            is_permanent=False,
            cure_stat="sanity",
            cure_threshold=60.0,
        )
        active_row = CharacterAffliction(
            character_id=1, affliction_type_id=1, source=AfflictionSource.MANUAL.value
        )
        active_row.affliction_type = affliction_type
        state = WorldState(
            districts={},
            npcs={},
            npc_schedules={},
            characters={1: character},
            open_shifts=[],
            active_afflictions={1: [active_row]},
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert active_row.cured_at is not None

    def test_dies_automatically_once_health_bottoms_out(self):
        character = make_character(money=0, hunger=constants.HEALTH_DECAY_HUNGER_THRESHOLD)
        character.health = constants.HEALTH_MIN
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert character.status == CharacterStatus.DEAD.value
        assert character.death_cause is not None
