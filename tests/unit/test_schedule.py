from __future__ import annotations

from collections import Counter

from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    Job,
    JobOption,
    Location,
)
from panem_shared.db.models import Npc, NpcSchedule
from panem_shared.enums import DayPhase
from panem_sim.rng import tick_rng
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import schedule


def make_district() -> District:
    locations = [
        Location(id="home", name="Home", kind="residential"),
        Location(id="square", name="The Square", kind="public"),
        Location(id="market", name="The Market", kind="market"),
        Location(id="station", name="Rail Station", kind="station"),
    ]
    coords = {"home": (0, 0), "square": (100, 100), "market": (200, 200), "station": (300, 300)}
    return District(
        id=1,
        name="District 1",
        industry="luxury",
        produces=[],
        imports=[],
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=500, height=500, location_coords=coords),
    )


def make_content(district: District, *jobs: Job) -> ContentBundle:
    return ContentBundle(
        districts={district.id: district}, goods={}, jobs={j.id: j for j in jobs}, routes=[]
    )


def make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = dict(
        id="job",
        district=1,
        title="Job",
        workplace="market",
        wage=10.0,
        shift_phase="morning",
        slots=5,
        options=[JobOption(label="a"), JobOption(label="b"), JobOption(label="c")],
    )
    defaults.update(overrides)
    return Job(**defaults)  # type: ignore[arg-type]


def make_npc(
    npc_id: str,
    district_id: int,
    location_id: str,
    *,
    home_location_id: str | None = None,
    job_id: str | None = None,
) -> Npc:
    return Npc(
        id=npc_id,
        district_id=district_id,
        name=npc_id,
        age=30,
        location_id=location_id,
        home_location_id=home_location_id if home_location_id is not None else location_id,
        job_id=job_id,
    )


def make_ctx(
    content: ContentBundle, *, tick: int = 1, phase: DayPhase = DayPhase.MORNING
) -> TickContext:
    return TickContext(
        tick=tick, phase=phase, day=1, month=1, rng=tick_rng("test-seed", tick), content=content
    )


class TestChooseLocation:
    def test_deterministic_for_same_seed(self):
        weights = {"home": 0.5, "square": 0.5}
        rng1 = tick_rng("seed-a", 7)
        rng2 = tick_rng("seed-a", 7)
        choices1 = [schedule._choose_location(rng1, weights) for _ in range(20)]
        choices2 = [schedule._choose_location(rng2, weights) for _ in range(20)]
        assert choices1 == choices2

    def test_distribution_matches_weights_within_tolerance(self):
        weights = {"home": 0.7, "square": 0.3}
        rng = tick_rng("distribution-seed", 1)
        draws = [schedule._choose_location(rng, weights) for _ in range(5000)]
        counts = Counter(draws)
        home_fraction = counts["home"] / len(draws)
        assert 0.65 <= home_fraction <= 0.75


class TestScheduleRun:
    def test_npcs_arriving_for_a_shift_batch_into_one_narration_line(self):
        district = make_district()
        job = make_job(workplace="market", shift_phase="morning")
        content = make_content(district, job)
        npcs = {
            f"npc{i}": make_npc(f"npc{i}", district.id, "home", job_id=job.id) for i in range(5)
        }
        schedules = {
            npc_id: [
                NpcSchedule(
                    npc_id=npc_id, phase=DayPhase.MORNING.value, location_id="market", weight=1.0
                )
            ]
            for npc_id in npcs
        }
        state = WorldState(
            districts={}, npcs=npcs, npc_schedules=schedules, characters={}, open_shifts=[]
        )
        ctx = make_ctx(content)

        events = schedule.run(state, ctx)

        assert len(events) == 1
        event = events[0]
        assert event.district_id == district.id
        assert event.location_id == "market"
        assert event.tick == ctx.tick
        assert "shift" in event.text
        for npc_id in npcs:
            assert npcs[npc_id].name in event.text
        for npc in npcs.values():
            assert npc.location_id == "market"

    def test_npcs_arriving_home_at_night_batch_into_one_narration_line(self):
        district = make_district()
        content = make_content(district)
        npcs = {f"npc{i}": make_npc(f"npc{i}", district.id, "market") for i in range(3)}
        # make_npc defaults home_location_id to the given location_id, so
        # override it to "home" -- these NPCs start at market, home is home.
        for npc in npcs.values():
            npc.home_location_id = "home"
        schedules = {
            npc_id: [
                NpcSchedule(
                    npc_id=npc_id, phase=DayPhase.NIGHT.value, location_id="home", weight=1.0
                )
            ]
            for npc_id in npcs
        }
        state = WorldState(
            districts={}, npcs=npcs, npc_schedules=schedules, characters={}, open_shifts=[]
        )
        ctx = make_ctx(content, phase=DayPhase.NIGHT)

        events = schedule.run(state, ctx)

        assert len(events) == 1
        assert events[0].location_id == "home"
        assert "home" in events[0].text
        for npc in npcs.values():
            assert npc.location_id == "home"

    def test_daytime_move_to_a_non_work_non_home_location_is_silent(self):
        district = make_district()
        content = make_content(district)
        npc = make_npc("npc1", district.id, "home")
        schedules = {
            "npc1": [
                NpcSchedule(
                    npc_id="npc1", phase=DayPhase.MORNING.value, location_id="square", weight=1.0
                )
            ]
        }
        state = WorldState(
            districts={}, npcs={"npc1": npc}, npc_schedules=schedules, characters={}, open_shifts=[]
        )
        ctx = make_ctx(content)

        events = schedule.run(state, ctx)

        assert events == []
        assert npc.location_id == "square"

    def test_arriving_at_workplace_outside_the_shift_phase_is_silent(self):
        district = make_district()
        job = make_job(workplace="market", shift_phase="afternoon")
        content = make_content(district, job)
        npc = make_npc("npc1", district.id, "home", job_id=job.id)
        schedules = {
            "npc1": [
                NpcSchedule(
                    npc_id="npc1", phase=DayPhase.MORNING.value, location_id="market", weight=1.0
                )
            ]
        }
        state = WorldState(
            districts={}, npcs={"npc1": npc}, npc_schedules=schedules, characters={}, open_shifts=[]
        )
        ctx = make_ctx(content, phase=DayPhase.MORNING)

        events = schedule.run(state, ctx)

        assert events == []
        assert npc.location_id == "market"

    def test_npc_with_no_weight_change_produces_no_event(self):
        district = make_district()
        content = make_content(district)
        npc = make_npc("npc1", district.id, "home")
        schedules = {
            "npc1": [
                NpcSchedule(
                    npc_id="npc1", phase=DayPhase.MORNING.value, location_id="home", weight=1.0
                )
            ]
        }
        state = WorldState(
            districts={}, npcs={"npc1": npc}, npc_schedules=schedules, characters={}, open_shifts=[]
        )
        ctx = make_ctx(content)

        events = schedule.run(state, ctx)

        assert events == []
        assert npc.location_id == "home"

    def test_npc_with_no_schedule_row_for_phase_does_not_move(self):
        district = make_district()
        content = make_content(district)
        npc = make_npc("npc1", district.id, "home")
        schedules = {
            "npc1": [
                NpcSchedule(
                    npc_id="npc1", phase=DayPhase.NIGHT.value, location_id="home", weight=1.0
                )
            ]
        }
        state = WorldState(
            districts={}, npcs={"npc1": npc}, npc_schedules=schedules, characters={}, open_shifts=[]
        )
        ctx = make_ctx(content, phase=DayPhase.MORNING)

        events = schedule.run(state, ctx)

        assert events == []
        assert npc.location_id == "home"

    def test_moved_npc_is_placed_within_location_radius(self):
        district = make_district()
        content = make_content(district)
        npc = make_npc("npc1", district.id, "home")
        schedules = {
            "npc1": [
                NpcSchedule(
                    npc_id="npc1", phase=DayPhase.MORNING.value, location_id="market", weight=1.0
                )
            ]
        }
        state = WorldState(
            districts={}, npcs={"npc1": npc}, npc_schedules=schedules, characters={}, open_shifts=[]
        )
        ctx = make_ctx(content)

        schedule.run(state, ctx)

        market = next(loc for loc in district.locations if loc.id == "market")
        cx, cy = district.map.location_coords["market"]
        assert npc.x is not None and npc.y is not None
        assert abs(npc.x - cx) <= market.radius
        assert abs(npc.y - cy) <= market.radius

    def test_run_is_deterministic_for_same_tick(self):
        district = make_district()
        content = make_content(district)

        def build_state() -> WorldState:
            npcs = {f"npc{i}": make_npc(f"npc{i}", district.id, "home") for i in range(10)}
            schedules = {
                npc_id: [
                    NpcSchedule(
                        npc_id=npc_id,
                        phase=DayPhase.MORNING.value,
                        location_id="square",
                        weight=0.5,
                    ),
                    NpcSchedule(
                        npc_id=npc_id,
                        phase=DayPhase.MORNING.value,
                        location_id="market",
                        weight=0.5,
                    ),
                ]
                for npc_id in npcs
            }
            return WorldState(
                districts={}, npcs=npcs, npc_schedules=schedules, characters={}, open_shifts=[]
            )

        state1, state2 = build_state(), build_state()
        events1 = schedule.run(state1, make_ctx(content, tick=42))
        events2 = schedule.run(state2, make_ctx(content, tick=42))

        assert [e.model_dump(exclude={"id"}) for e in events1] == [
            e.model_dump(exclude={"id"}) for e in events2
        ]
        for npc_id in state1.npcs:
            assert state1.npcs[npc_id].location_id == state2.npcs[npc_id].location_id
