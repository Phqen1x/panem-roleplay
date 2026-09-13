from __future__ import annotations

from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    Job,
    JobOption,
    Location,
)
from panem_shared.db.models import Character, Npc, Shift
from panem_shared.enums import CharacterStatus, DayPhase, ShiftResult
from panem_shared.shifts import apply_shift_outcome, resolve_shift_game
from panem_sim.rng import tick_rng
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import jobs


def make_job(**overrides: object) -> Job:
    """NPCs only -- player jobs are free-typed now (`Character.job_title`),
    not a `jobs.yaml` catalog entry."""
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


def make_district(*, quota_good: str | None = None) -> District:
    from panem_shared.content.schemas import DistrictQuota

    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Station", kind="station"),
    ]
    coords = {loc.id: (0, 0) for loc in locations}
    return District(
        id=1,
        name="District One",
        industry="misc",
        locations=locations,
        culture=DistrictCulture(),
        population_base=100,
        quota=DistrictQuota(good=quota_good, amount=100) if quota_good else None,
        map=DistrictMap(image="x.png", width=10, height=10, location_coords=coords),
    )


def make_character(id_: int, **overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name=f"char{id_}",
        age=20,
        status=CharacterStatus.APPROVED.value,
        consecutive_missed=0,
        consecutive_wins=0,
        consecutive_losses=0,
        money=0,
        reputation=0.0,
        health=100.0,
        hunger=0.0,
        shifts_completed=0,
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
        content = make_content()
        character = make_character(1, job_title="Miner", shift_phase="morning")
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))

        assert len(state.open_shifts) == 1
        assert len(state.new_shifts) == 1
        shift = state.open_shifts[0]
        assert shift.character_id == 1
        assert shift.job_id == "Miner"
        assert shift.tick_opened == PHASE_TICKS
        assert shift.tick_due == PHASE_TICKS + constants.SHIFT_DURATION_TICKS

    def test_does_not_open_a_second_shift_within_the_same_phase_window(self):
        content = make_content()
        character = make_character(1, job_title="Miner", shift_phase="morning")
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))
        jobs.run(state, make_ctx(content, tick=PHASE_TICKS + 1, phase=DayPhase.MORNING))

        assert len(state.open_shifts) == 1

    def test_no_shift_opens_off_the_phase_boundary(self):
        content = make_content()
        character = make_character(1, job_title="Miner", shift_phase="morning")
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        jobs.run(state, make_ctx(content, tick=PHASE_TICKS + 1, phase=DayPhase.MORNING))

        assert state.open_shifts == []

    def test_no_shift_opens_for_an_unemployed_character(self):
        content = make_content()
        character = make_character(1, job_title=None, shift_phase=None)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))

        assert state.open_shifts == []

    def test_no_shift_opens_when_phase_does_not_match_the_characters_choice(self):
        content = make_content()
        character = make_character(1, job_title="Miner", shift_phase="evening")
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))

        assert state.open_shifts == []


class TestMissedShifts:
    def test_shift_missed_after_tick_due_increments_consecutive_missed(self):
        content = make_content()
        character = make_character(
            1, job_title="Miner", shift_phase="morning", consecutive_missed=2
        )
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
        content = make_content()
        character = make_character(1, job_title="Miner", shift_phase="morning")
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))
        shift = state.open_shifts[0]

        jobs.run(state, make_ctx(content, tick=shift.tick_due - 1, phase=DayPhase.MORNING))

        assert shift.result is None
        assert len(state.open_shifts) == 1

    def test_fires_character_once_misses_to_fire_is_reached(self):
        content = make_content()
        character = make_character(
            1,
            job_title="Miner",
            shift_phase="morning",
            consecutive_missed=constants.MISSES_TO_FIRE - 1,
        )
        shift = Shift(
            character_id=1,
            job_id="Miner",
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

        assert character.job_title is None
        assert character.shift_phase is None
        assert character.consecutive_missed == 0
        assert len(state.new_job_history) == 1
        assert state.new_job_history[0].reason == "fired"
        assert state.new_job_history[0].character_id == 1
        assert len(state.notable_events) == 1
        assert state.notable_events[0].owner_id == "1"
        assert state.notable_events[0].kind == "fired"


class TestWorkedShiftsCloseAsCompleted:
    """A shift stays open across every `/work` tick now (`panem_shared.
    shifts.apply_shift_outcome` no longer closes it on the first work) --
    once `tick_due` passes, it should close as COMPLETED, not MISSED, if
    it was worked at least once."""

    def test_worked_shift_closes_as_completed_at_tick_due(self):
        content = make_content()
        character = make_character(
            1, job_title="Miner", shift_phase="morning", consecutive_missed=2
        )
        shift = Shift(character_id=1, job_id="Miner", tick_opened=0, tick_due=6, last_worked_tick=3)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[shift]
        )

        jobs.run(state, make_ctx(content, tick=6, phase=DayPhase.AFTERNOON))

        assert shift.result == ShiftResult.COMPLETED.value
        assert shift.completed_at == 6
        assert state.open_shifts == []

    def test_worked_shift_leaves_consecutive_missed_untouched(self):
        content = make_content()
        character = make_character(
            1, job_title="Miner", shift_phase="morning", consecutive_missed=2
        )
        shift = Shift(character_id=1, job_id="Miner", tick_opened=0, tick_due=6, last_worked_tick=3)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[shift]
        )

        jobs.run(state, make_ctx(content, tick=6, phase=DayPhase.AFTERNOON))

        # apply_shift_outcome already reset this at the moment of work;
        # closing the shift later shouldn't touch it either way.
        assert character.consecutive_missed == 2

    def test_unworked_shift_still_misses_normally(self):
        content = make_content()
        character = make_character(
            1, job_title="Miner", shift_phase="morning", consecutive_missed=0
        )
        shift = Shift(
            character_id=1, job_id="Miner", tick_opened=0, tick_due=6, last_worked_tick=None
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[shift]
        )

        jobs.run(state, make_ctx(content, tick=6, phase=DayPhase.AFTERNOON))

        assert shift.result == ShiftResult.MISSED.value
        assert character.consecutive_missed == 1


class TestWorkGameGraceExcusesMissedShifts:
    """`/work`'s minigame: a shift whose game was started before it was due
    stays open, not missed, until WORK_GAME_GRACE_TICKS past tick_due."""

    def _make_state(self, *, started_at_tick: int | None, tick_due: int):
        character = make_character(
            1, job_title="Miner", shift_phase="morning", consecutive_missed=0
        )
        shift = Shift(
            character_id=1,
            job_id="Miner",
            tick_opened=0,
            tick_due=tick_due,
            started_at_tick=started_at_tick,
        )
        state = WorldState(
            districts={},
            npcs={},
            npc_schedules={},
            characters={1: character},
            open_shifts=[shift],
        )
        return state, shift

    def test_started_shift_stays_open_right_at_tick_due(self):
        content = make_content()
        state, shift = self._make_state(started_at_tick=0, tick_due=6)
        jobs.run(state, make_ctx(content, tick=6, phase=DayPhase.AFTERNOON))
        assert shift.result is None
        assert state.open_shifts == [shift]

    def test_started_shift_stays_open_within_the_grace_window(self):
        content = make_content()
        state, shift = self._make_state(started_at_tick=0, tick_due=6)
        tick = 6 + constants.WORK_GAME_GRACE_TICKS
        jobs.run(state, make_ctx(content, tick=tick, phase=DayPhase.AFTERNOON))
        assert shift.result is None
        assert state.open_shifts == [shift]

    def test_started_shift_is_missed_once_grace_expires(self):
        content = make_content()
        state, shift = self._make_state(started_at_tick=0, tick_due=6)
        tick = 6 + constants.WORK_GAME_GRACE_TICKS + 1
        jobs.run(state, make_ctx(content, tick=tick, phase=DayPhase.AFTERNOON))
        assert shift.result == ShiftResult.MISSED.value
        assert state.open_shifts == []

    def test_unstarted_shift_gets_no_grace(self):
        content = make_content()
        state, shift = self._make_state(started_at_tick=None, tick_due=6)
        character = state.characters[1]
        jobs.run(state, make_ctx(content, tick=6, phase=DayPhase.AFTERNOON))
        assert shift.result == ShiftResult.MISSED.value
        assert character.consecutive_missed == 1
        assert state.open_shifts == []


class TestTravelGraceExcusesMissedShifts:
    """FR-LOC-9: a shift missed while traveling is excused, not missed."""

    def test_shift_missed_while_in_transit_is_excused_not_missed(self):
        content = make_content()
        character = make_character(
            1,
            job_title="Miner",
            shift_phase="morning",
            consecutive_missed=2,
            in_transit_until_tick=10_000,
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))
        shift = state.open_shifts[0]

        jobs.run(state, make_ctx(content, tick=shift.tick_due, phase=DayPhase.AFTERNOON))

        assert shift.result == ShiftResult.EXCUSED.value
        assert character.consecutive_missed == 0

    def test_shift_missed_while_visiting_within_grace_window_is_excused(self):
        content = make_content()
        character = make_character(
            1, job_title="Miner", shift_phase="morning", consecutive_missed=2, away_since_tick=0
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))
        shift = state.open_shifts[0]
        assert shift.tick_due <= constants.AWAY_GRACE_DAYS * constants.TICKS_PER_DAY

        jobs.run(state, make_ctx(content, tick=shift.tick_due, phase=DayPhase.AFTERNOON))

        assert shift.result == ShiftResult.EXCUSED.value
        assert character.consecutive_missed == 0

    def test_shift_missed_once_grace_window_has_elapsed_still_counts(self):
        content = make_content()
        long_ago = -(constants.AWAY_GRACE_DAYS * constants.TICKS_PER_DAY) - 1
        character = make_character(
            1,
            job_title="Miner",
            shift_phase="morning",
            consecutive_missed=2,
            away_since_tick=long_ago,
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))
        shift = state.open_shifts[0]

        jobs.run(state, make_ctx(content, tick=shift.tick_due, phase=DayPhase.AFTERNOON))

        assert shift.result == ShiftResult.MISSED.value
        assert character.consecutive_missed == 3

    def test_not_away_and_not_in_transit_is_missed_as_normal(self):
        content = make_content()
        character = make_character(
            1, job_title="Miner", shift_phase="morning", consecutive_missed=2
        )
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )
        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))
        shift = state.open_shifts[0]

        jobs.run(state, make_ctx(content, tick=shift.tick_due, phase=DayPhase.AFTERNOON))

        assert shift.result == ShiftResult.MISSED.value
        assert character.consecutive_missed == 3


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
    """T-2.2: complete a shift, miss five in a row, lose the job, get
    reassigned by staff (no more player self-service `/job apply` since
    the job rework -- only `/staff give job` sets `job_title`/
    `shift_phase` now)."""

    def test_complete_then_miss_streak_fires_then_staff_reassigns(self):
        content = make_content()
        district = make_district()
        character = make_character(1, job_title="Miner", shift_phase="morning", job_started_tick=0)
        state = WorldState(
            districts={}, npcs={}, npc_schedules={}, characters={1: character}, open_shifts=[]
        )

        # Day 1: shift opens, gets worked (e.g. via /work or RP credit)
        # before its due tick -- breaking any prior miss streak -- and the
        # tick loop closes it as completed (not missed) once tick_due
        # passes, since it stays open rather than closing on that one work.
        jobs.run(state, make_ctx(content, tick=PHASE_TICKS, phase=DayPhase.MORNING))
        shift = state.open_shifts[0]
        outcome = resolve_shift_game(character, district, won=True)
        apply_shift_outcome(shift, character, outcome, won=True, tick=PHASE_TICKS)
        assert character.consecutive_missed == 0

        jobs.run(state, make_ctx(content, tick=shift.tick_due, phase=DayPhase.AFTERNOON))
        assert shift.result == ShiftResult.COMPLETED.value
        assert state.open_shifts == []

        # Days 2-6: the shift opens and is never resolved -- five straight
        # misses fires the character.
        day_start = PHASE_TICKS + constants.TICKS_PER_DAY
        for day in range(5):
            open_tick = day_start + day * constants.TICKS_PER_DAY
            jobs.run(state, make_ctx(content, tick=open_tick, phase=DayPhase.MORNING))
            due_tick = open_tick + constants.SHIFT_DURATION_TICKS
            jobs.run(state, make_ctx(content, tick=due_tick, phase=DayPhase.AFTERNOON))

        assert character.job_title is None
        assert character.shift_phase is None
        assert character.consecutive_missed == 0
        assert len(state.new_job_history) == 1
        assert state.new_job_history[0].reason == "fired"

        # Staff reassigns: a fresh cycle starts clean.
        character.job_title = "Miner"
        character.shift_phase = "morning"
        assert character.consecutive_missed == 0

        reapply_open_tick = due_tick + PHASE_TICKS
        jobs.run(state, make_ctx(content, tick=reapply_open_tick, phase=DayPhase.MORNING))
        assert len(state.open_shifts) == 1
        assert state.open_shifts[0].character_id == 1
