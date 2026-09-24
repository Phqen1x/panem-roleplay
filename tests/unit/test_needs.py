from __future__ import annotations

import pytest

from panem_shared import constants, simtime
from panem_shared.db.models import AfflictionType, Character, CharacterAffliction, Npc
from panem_shared.enums import AfflictionSource, CharacterStatus, DayPhase, RpMode
from panem_sim.rng import tick_rng
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import needs


class FixedRng:
    """A stand-in for `random.Random` that always returns a fixed draw for
    `uniform`, so phase-decay tests don't depend on the real random range
    -- same spirit as `test_market_service.py`'s own `FixedRng`."""

    def __init__(self, value: float) -> None:
        self._value = value

    def uniform(self, a: float, b: float) -> float:
        return self._value


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
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = 1
    return character


def make_npc(**overrides: object) -> Npc:
    defaults: dict[str, object] = dict(
        id="npc1", district_id=1, name="npc1", age=30, money=0.0, hunger=0.0, health=100.0
    )
    defaults.update(overrides)
    return Npc(**defaults)  # type: ignore[arg-type]


def make_ctx(tick: int, rng: object | None = None) -> TickContext:
    from panem_shared.content.loader import ContentBundle

    content = ContentBundle(districts={}, goods={}, jobs={}, routes=[])
    return TickContext(
        tick=tick,
        phase=DayPhase.NIGHT,
        day=1,
        month=1,
        rng=rng if rng is not None else tick_rng("test-seed", tick),  # type: ignore[arg-type]
        content=content,
    )


class TestRunGating:
    def test_phase_needs_fire_on_every_phase_boundary_not_just_daily(self):
        character = make_character(hunger=0.0)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=1, rng=FixedRng(15.0)))
        assert character.hunger == 0.0  # not a phase boundary

        needs.run(state, make_ctx(tick=simtime.TICKS_PER_PHASE, rng=FixedRng(15.0)))
        assert character.hunger == 15.0  # first phase boundary, well short of a full day

    def test_nightly_needs_only_fire_on_day_boundaries(self):
        character = make_character(money=constants.NIGHTLY_LIVING_COST * 5)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=simtime.TICKS_PER_PHASE, rng=FixedRng(0.0)))
        assert character.money == constants.NIGHTLY_LIVING_COST * 5  # untouched

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY, rng=FixedRng(0.0)))
        assert character.money == constants.NIGHTLY_LIVING_COST * 4  # nightly deduction landed


class TestCharacterPhaseNeeds:
    def test_hunger_and_thirst_climb_by_the_rngs_draw(self):
        character = make_character(hunger=20.0, thirst=30.0)
        needs._apply_character_phase_needs(character, FixedRng(12.0))
        assert character.hunger == 32.0
        assert character.thirst == 42.0

    def test_hunger_is_clamped_at_the_maximum(self):
        character = make_character(hunger=constants.HUNGER_MAX - 1)
        needs._apply_character_phase_needs(character, FixedRng(20.0))
        assert character.hunger == constants.HUNGER_MAX

    def test_high_hunger_erodes_health_instead_of_recovering(self):
        character = make_character(hunger=constants.HEALTH_DECAY_HUNGER_THRESHOLD - 1, health=50.0)
        needs._apply_character_phase_needs(character, FixedRng(5.0))
        assert character.hunger >= constants.HEALTH_DECAY_HUNGER_THRESHOLD
        assert character.health == 50.0 - constants.HEALTH_DECAY_PER_NIGHT

    def test_low_hunger_recovers_health(self):
        character = make_character(hunger=0.0, health=50.0)
        needs._apply_character_phase_needs(character, FixedRng(1.0))
        assert character.hunger < constants.HEALTH_DECAY_HUNGER_THRESHOLD
        assert character.health == 50.0 + constants.HEALTH_RECOVERY_PER_NIGHT

    def test_high_thirst_erodes_health(self):
        character = make_character(
            hunger=0.0, thirst=constants.HEALTH_DECAY_THIRST_THRESHOLD - 1, health=50.0
        )
        needs._apply_character_phase_needs(character, FixedRng(5.0))
        assert character.thirst >= constants.HEALTH_DECAY_THIRST_THRESHOLD
        # Both hunger's recovery branch and thirst's decay branch apply.
        assert character.health == pytest.approx(
            50.0 + constants.HEALTH_RECOVERY_PER_NIGHT - constants.HEALTH_DECAY_PER_NIGHT_THIRST
        )

    def test_non_simulation_characters_are_left_untouched(self):
        character = make_character(rp_mode=RpMode.LIFE.value, hunger=0.0, thirst=0.0)
        needs._apply_character_phase_needs(character, FixedRng(50.0))
        assert character.hunger == 0.0
        assert character.thirst == 0.0

    def test_uses_the_ticks_own_rng_for_determinism(self):
        character_a = make_character()
        character_b = make_character()
        rng_a = tick_rng("world-seed", 42)
        rng_b = tick_rng("world-seed", 42)
        needs._apply_character_phase_needs(character_a, rng_a)
        needs._apply_character_phase_needs(character_b, rng_b)
        assert character_a.hunger == character_b.hunger
        assert character_a.thirst == character_b.thirst


class TestCharacterNightlyNeeds:
    def test_paying_living_cost_deducts_money_with_no_direct_health_effect(self):
        character = make_character(money=constants.NIGHTLY_LIVING_COST * 2, health=90.0)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        needs._apply_character_nightly_needs(character, state, day_index=0)
        assert character.money == constants.NIGHTLY_LIVING_COST
        assert character.health == 90.0

    def test_unable_to_pay_leaves_money_and_health_unchanged(self):
        character = make_character(money=0, health=90.0)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        needs._apply_character_nightly_needs(character, state, day_index=0)
        assert character.money == 0
        assert character.health == 90.0

    def test_non_simulation_characters_are_left_untouched(self):
        character = make_character(rp_mode=RpMode.STORY.value, money=0, sanity=100.0)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        needs._apply_character_nightly_needs(character, state, day_index=0)
        assert character.sanity == 100.0


class TestFatigueExhaustion:
    def test_exhausted_fatigue_docks_health(self):
        character = make_character(health=90.0, fatigue=constants.FATIGUE_EXHAUSTION_THRESHOLD)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        needs._apply_character_nightly_needs(character, state, day_index=0)
        assert character.health == 90.0 - constants.FATIGUE_EXHAUSTION_HEALTH_PENALTY

    def test_rested_fatigue_costs_no_extra_health(self):
        character = make_character(health=90.0, fatigue=constants.FATIGUE_EXHAUSTION_THRESHOLD + 1)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        needs._apply_character_nightly_needs(character, state, day_index=0)
        assert character.health == 90.0

    def test_exhaustion_penalty_is_clamped_to_health_min(self):
        character = make_character(health=constants.HEALTH_MIN, fatigue=0.0)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        needs._apply_character_nightly_needs(character, state, day_index=0)
        assert character.health == constants.HEALTH_MIN


class TestNpcNeeds:
    def test_npc_uses_float_living_cost_and_same_hunger_health_rules(self):
        npc = make_npc(money=constants.NIGHTLY_LIVING_COST_NPC, hunger=80.0, health=95.0)
        state = WorldState(
            districts={}, npcs={"npc1": npc}, npc_schedules={}, characters={}, open_shifts=[]
        )

        needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY, rng=FixedRng(0.0)))

        assert npc.money == pytest.approx(0.0)
        assert npc.hunger == 80.0 - constants.HUNGER_DECREASE_MET
        assert npc.health == 95.0 - constants.HEALTH_DECAY_PER_NIGHT


class TestSanity:
    def test_sanity_falls_when_not_entertained_today(self):
        character = make_character(sanity=100.0, last_entertained_tick=None)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        needs._apply_character_nightly_needs(character, state, day_index=0)
        assert character.sanity == 100.0 - constants.SANITY_DECREASE_PER_DAY

    def test_sanity_holds_steady_if_entertained_earlier_that_same_day(self):
        character = make_character(sanity=90.0, last_entertained_tick=5)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        needs._apply_character_nightly_needs(character, state, day_index=0)
        assert character.sanity == 90.0

    def test_low_sanity_erodes_health(self):
        character = make_character(
            sanity=constants.HEALTH_DECAY_SANITY_THRESHOLD,
            last_entertained_tick=None,
            health=50.0,
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        needs._apply_character_nightly_needs(character, state, day_index=0)
        assert character.health == pytest.approx(50.0 - constants.HEALTH_DECAY_PER_NIGHT_SANITY)


class TestAutoAfflictionsAndDeath:
    def test_applies_a_catalog_affliction_once_a_stat_crosses_its_threshold(self):
        character = make_character(thirst=90.0)
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

        needs._apply_character_nightly_needs(character, state, day_index=0)

        assert len(state.new_character_afflictions) == 1
        assert state.new_character_afflictions[0].character_id == 1

    def test_cures_an_active_affliction_once_its_stat_recovers(self):
        character = make_character(sanity=80.0)
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

        needs._apply_character_nightly_needs(character, state, day_index=0)

        assert active_row.cured_at is not None

    def test_dies_automatically_once_health_bottoms_out(self):
        character = make_character(health=constants.HEALTH_MIN)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        needs._apply_character_nightly_needs(character, state, day_index=0)

        assert character.status == CharacterStatus.DEAD.value
        assert character.death_cause is not None

    def test_run_emits_a_character_died_event_on_the_death_tick(self):
        # hunger already at its own health-decay threshold so the phase
        # step (which also fires on this same tick) decays health instead
        # of recovering it -- health lands back on HEALTH_MIN rather than
        # ticking up past it before the nightly death check runs.
        character = make_character(health=constants.HEALTH_MIN, hunger=constants.HUNGER_MAX)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        events = needs.run(state, make_ctx(tick=constants.TICKS_PER_DAY, rng=FixedRng(0.0)))

        assert len(events) == 1
        assert events[0].kind == "CharacterDied"
        assert events[0].character_id == 1
