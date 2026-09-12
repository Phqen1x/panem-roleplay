from __future__ import annotations

import random

from panem_bot.services import shifts as shifts_svc
from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import Job, JobOption
from panem_shared.db.models import Character, Npc
from panem_shared.enums import CharacterStatus, DayPhase, ShiftResult
from panem_sim.rng import tick_rng
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import jobs


def make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = dict(
        id="miner",
        district=1,
        title="Miner",
        workplace="mine",
        wage=10.0,
        produces={"coal": 5.0},
        shift_phase="morning",
        slots=5,
        options=[JobOption(label="safe"), JobOption(label="normal"), JobOption(label="risky")],
    )
    defaults.update(overrides)
    return Job(**defaults)  # type: ignore[arg-type]


def make_content(*jobs_: Job) -> ContentBundle:
    return ContentBundle(districts={}, goods={}, jobs={j.id: j for j in jobs_}, routes=[])


def make_character(id_: int, **overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name=f"char{id_}",
        age=20,
        status=CharacterStatus.APPROVED.value,
        consecutive_missed=0,
        money=0,
        reputation=0.0,
        health=100.0,
        hunger=0.0,
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = id_
    return character


def make_ctx(content: ContentBundle, *, tick: int, phase: DayPhase) -> TickContext:
    return TickContext(
        tick=tick, phase=phase, day=1, month=1, rng=tick_rng("test-seed", tick), content=content
    )


PHASE_TICKS = constants.TICKS_PER_DAY // 4  # 6, matches time._TICKS_PER_PHASE


class TestOpenShifts:
    def test_opens_a_shift_at_the_matching_phase_boundary(self):
        job = make_job(shift_phase="morning")
        content = make_content(job)
        character = make_character(1, job_id="miner")
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))

        assert len(state.open_shifts) == 1
        assert len(state.new_shifts) == 1
        shift = state.open_shifts[0]
        assert shift.character_id == 1
        assert shift.tick_opened == PHASE_TICKS
        assert shift.tick_due == PHASE_TICKS + constants.SHIFT_DURATION_TICKS

    def test_does_not_open_a_second_shift_within_the_same_phase_window(self):
        job = make_job(shift_phase="morning")
        content = make_content(job)
        character = make_character(1, job_id="miner")
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))
        jobs.run(state, make_ctx(content, tick=PHASE_TICKS + 1, phase=DayPhase.MORNING))

        assert len(state.open_shifts) == 1

    def test_no_shift_opens_off_the_phase_boundary(self):
        job = make_job(shift_phase="morning")
        content = make_content(job)
        character = make_character(1, job_id="miner")
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        jobs.run(state, make_ctx(content, tick=PHASE_TICKS + 1, phase=DayPhase.MORNING))

        assert state.open_shifts == []

    def test_no_shift_opens_for_an_unemployed_character(self):
        job = make_job(shift_phase="morning")
        content = make_content(job)
        character = make_character(1, job_id=None)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))

        assert state.open_shifts == []

    def test_no_shift_opens_when_phase_does_not_match_the_job(self):
        job = make_job(shift_phase="evening")
        content = make_content(job)
        character = make_character(1, job_id="miner")
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))

        assert state.open_shifts == []


class TestMissedShifts:
    def test_shift_missed_after_tick_due_increments_consecutive_missed(self):
        content = make_content(make_job())
        character = make_character(1, job_id="miner", consecutive_missed=2)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))
        shift = state.open_shifts[0]

        due_tick = shift.tick_due
        jobs.run(state, make_ctx(content, tick=due_tick, phase=DayPhase.AFTERNOON))

        assert shift.result == ShiftResult.MISSED.value
        assert character.consecutive_missed == 3
        assert state.open_shifts == []

    def test_shift_still_open_before_tick_due(self):
        content = make_content(make_job())
        character = make_character(1, job_id="miner")
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))
        shift = state.open_shifts[0]

        jobs.run(state, make_ctx(content, tick=shift.tick_due - 1, phase=DayPhase.MORNING))

        assert shift.result is None
        assert len(state.open_shifts) == 1

    def test_fires_character_once_misses_to_fire_is_reached(self):
        content = make_content(make_job())
        character = make_character(
            1, job_id="miner", consecutive_missed=constants.MISSES_TO_FIRE - 1
        )
        from panem_shared.db.models import Shift

        shift = Shift(
            character_id=1,
            job_id="miner",
            tick_opened=1,
            tick_due=constants.SHIFT_DURATION_TICKS,
            result=None,
        )
        state = WorldState(
            districts={},
            npcs={},
            npc_schedules={},
            characters={1: character},
            open_shifts=[shift],
        )

        jobs.run(
            state, make_ctx(content, tick=constants.SHIFT_DURATION_TICKS, phase=DayPhase.AFTERNOON)
        )

        assert character.job_id is None
        assert character.consecutive_missed == 0
        assert len(state.new_job_history) == 1
        assert state.new_job_history[0].reason == "fired"
        assert state.new_job_history[0].character_id == 1


class TestNpcJobCompletion:
    def test_npc_gains_wage_probabilistically_at_phase_boundary(self):
        job = make_job(shift_phase="morning", wage=10.0)
        content = make_content(job)
        npc = Npc(id="npc1", district_id=1, name="npc1", age=30, job_id="miner", money=0.0)
        state = WorldState(
            districts={}, npcs={"npc1": npc}, npc_schedules={}, characters={}, open_shifts=[]
        )

        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))

        assert npc.money in (0.0, 10.0)

    def test_no_shift_row_ever_created_for_npcs(self):
        job = make_job(shift_phase="morning")
        content = make_content(job)
        npc = Npc(id="npc1", district_id=1, name="npc1", age=30, job_id="miner", money=0.0)
        state = WorldState(
            districts={}, npcs={"npc1": npc}, npc_schedules={}, characters={}, open_shifts=[]
        )

        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))

        assert state.open_shifts == []
        assert state.new_shifts == []

    def test_deterministic_for_a_fixed_tick(self):
        job = make_job(shift_phase="morning", wage=10.0)
        content = make_content(job)

        def run_once() -> float:
            npc = Npc(id="npc1", district_id=1, name="npc1", age=30, job_id="miner", money=0.0)
            state = WorldState(
                districts={}, npcs={"npc1": npc}, npc_schedules={}, characters={}, open_shifts=[]
            )
            jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))
            return npc.money

        assert run_once() == run_once()


class TestT22ScriptedLifecycle:
    """T-2.2: complete a shift, miss five in a row, lose the job, re-apply."""

    def test_complete_then_miss_streak_fires_then_reapply_works(self):
        job = make_job(shift_phase="morning", wage=10.0)
        content = make_content(job)
        character = make_character(1, job_id="miner", job_started_tick=0)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        # Day 1: shift opens, gets completed (e.g. via /work or RP credit)
        # before its due tick -- breaking any prior miss streak.
        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))
        shift = state.open_shifts[0]
        outcome = shifts_svc.resolve_shift(job, 1, is_player=True, rng=random.Random(1))
        shifts_svc.apply_shift_outcome(shift, character, outcome, tick=PHASE_TICKS)
        state.open_shifts = [s for s in state.open_shifts if s.result is None]
        assert character.consecutive_missed == 0

        # Days 2-6: the shift opens and is never resolved -- five straight
        # misses fires the character.
        day_start = PHASE_TICKS + constants.TICKS_PER_DAY
        for day in range(5):
            open_tick = day_start + day * constants.TICKS_PER_DAY
            jobs.run(state, make_ctx(content, tick=open_tick, phase=DayPhase.MORNING))
            due_tick = open_tick + constants.SHIFT_DURATION_TICKS
            jobs.run(state, make_ctx(content, tick=due_tick, phase=DayPhase.AFTERNOON))

        assert character.job_id is None
        assert character.consecutive_missed == 0
        assert len(state.new_job_history) == 1
        assert state.new_job_history[0].reason == "fired"

        # Re-apply: a fresh cycle starts clean.
        shifts_svc.apply_for_job(character, job, tick=due_tick)
        assert character.job_id == "miner"
        assert character.consecutive_missed == 0

        reapply_open_tick = due_tick + PHASE_TICKS
        jobs.run(state, make_ctx(content, tick=reapply_open_tick, phase=DayPhase.MORNING))
        assert len(state.open_shifts) == 1
        assert state.open_shifts[0].character_id == 1
