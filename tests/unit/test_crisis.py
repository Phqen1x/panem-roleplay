from __future__ import annotations

from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import District, DistrictCulture, DistrictMap, Location
from panem_shared.db.models import Character, DistrictState, Npc
from panem_shared.enums import CharacterStatus, DayPhase
from panem_sim.rng import tick_rng
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import crisis


def make_district_row(district_id: int, **overrides: object) -> DistrictState:
    defaults: dict[str, object] = dict(
        district_id=district_id,
        treasury=0.0,
        quota_progress=0.0,
        quota_target=0.0,
        capitol_favor=0.0,
        unrest=0.0,
        peacekeeper_pressure=0.3,
        crisis_level=0,
        crisis_kind=None,
    )
    defaults.update(overrides)
    return DistrictState(**defaults)  # type: ignore[arg-type]


def make_district(district_id: int) -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
    ]
    coords = {"square": (0, 0), "station": (10, 10)}
    return District(
        id=district_id,
        name=f"District {district_id}",
        industry="x",
        produces=[],
        imports=[],
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=100, height=100, location_coords=coords),
    )


def make_content(*district_ids: int) -> ContentBundle:
    return ContentBundle(
        districts={i: make_district(i) for i in district_ids}, goods={}, jobs={}, routes=[]
    )


def make_ctx(*, tick: int, district_ids: tuple[int, ...] = (1,)) -> TickContext:
    return TickContext(
        tick=tick,
        phase=DayPhase.NIGHT,
        day=1,
        month=1,
        rng=tick_rng("seed", tick),
        content=make_content(*district_ids),
    )


def make_npc(id_: str, *, district_id: int = 1, hunger: float = 0.0) -> Npc:
    return Npc(id=id_, district_id=district_id, name=id_, age=30, hunger=hunger)


def make_character(id_: int, *, district_id: int = 1, hunger: float = 0.0) -> Character:
    character = Character(
        user_id=1,
        district_id=district_id,
        current_district_id=district_id,
        name=f"char{id_}",
        age=20,
        status=CharacterStatus.APPROVED.value,
        hunger=hunger,
    )
    character.id = id_
    return character


def make_state(*, districts: dict[int, DistrictState], **overrides: object) -> WorldState:
    defaults: dict[str, object] = dict(
        districts=districts, npcs={}, npc_schedules={}, characters={}, open_shifts=[]
    )
    defaults.update(overrides)
    return WorldState(**defaults)  # type: ignore[arg-type]


class TestOffDayBoundary:
    def test_no_op_off_a_day_boundary(self):
        row = make_district_row(1, unrest=0.5)
        state = make_state(districts={1: row})

        events = crisis.run(state, make_ctx(tick=1))

        assert events == []
        assert row.unrest == 0.5


class TestHungerContribution:
    def test_all_hungry_population_raises_unrest(self):
        row = make_district_row(1, unrest=0.0)
        npcs = {
            f"n{i}": make_npc(f"n{i}", hunger=constants.HEALTH_DECAY_HUNGER_THRESHOLD)
            for i in range(5)
        }
        state = make_state(districts={1: row}, npcs=npcs)

        crisis.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert row.unrest == crisis.HUNGER_UNREST_WEIGHT

    def test_well_fed_population_adds_nothing(self):
        row = make_district_row(1, unrest=0.0)
        npcs = {f"n{i}": make_npc(f"n{i}", hunger=0.0) for i in range(5)}
        state = make_state(districts={1: row}, npcs=npcs)

        crisis.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert row.unrest == 0.0

    def test_mixed_population_counts_only_the_hungry_fraction(self):
        row = make_district_row(1, unrest=0.0)
        npcs = {
            "hungry": make_npc("hungry", hunger=constants.HEALTH_DECAY_HUNGER_THRESHOLD),
            "fed": make_npc("fed", hunger=0.0),
        }
        state = make_state(districts={1: row}, npcs=npcs)

        crisis.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert row.unrest == 0.5 * crisis.HUNGER_UNREST_WEIGHT

    def test_characters_and_npcs_both_count_toward_the_population(self):
        row = make_district_row(1, unrest=0.0)
        npcs = {"n": make_npc("n", hunger=constants.HEALTH_DECAY_HUNGER_THRESHOLD)}
        characters = {
            1: make_character(1, hunger=constants.HEALTH_DECAY_HUNGER_THRESHOLD),
        }
        state = make_state(districts={1: row}, npcs=npcs, characters=characters)

        crisis.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert row.unrest == crisis.HUNGER_UNREST_WEIGHT

    def test_only_the_matching_district_counts(self):
        row1 = make_district_row(1, unrest=0.0)
        row2 = make_district_row(2, unrest=0.0)
        npcs = {"n": make_npc("n", district_id=2, hunger=constants.HEALTH_DECAY_HUNGER_THRESHOLD)}
        state = make_state(districts={1: row1, 2: row2}, npcs=npcs)

        crisis.run(state, make_ctx(tick=constants.TICKS_PER_DAY, district_ids=(1, 2)))

        assert row1.unrest == 0.0
        assert row2.unrest == crisis.HUNGER_UNREST_WEIGHT


class TestUnrestDecay:
    def test_unrest_decays_toward_zero_each_day(self):
        row = make_district_row(1, unrest=0.6)
        state = make_state(districts={1: row})

        crisis.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        expected = 0.6 - 0.6 / constants.CRISIS_RECOVERY_DAYS
        assert row.unrest == expected

    def test_unrest_never_goes_negative(self):
        row = make_district_row(1, unrest=0.0)
        state = make_state(districts={1: row})

        crisis.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert row.unrest == 0.0


class TestPeacekeeperPressureRelaxation:
    def test_pressure_relaxes_toward_baseline_from_above(self):
        row = make_district_row(1, peacekeeper_pressure=0.9)
        state = make_state(districts={1: row})

        crisis.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert row.peacekeeper_pressure < 0.9
        assert row.peacekeeper_pressure > crisis.PEACEKEEPER_PRESSURE_BASELINE

    def test_pressure_relaxes_toward_baseline_from_below(self):
        row = make_district_row(1, peacekeeper_pressure=0.0)
        state = make_state(districts={1: row})

        crisis.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert row.peacekeeper_pressure > 0.0
        assert row.peacekeeper_pressure < crisis.PEACEKEEPER_PRESSURE_BASELINE


class TestCrisisLevel:
    def test_calm_district_stays_at_level_zero(self):
        row = make_district_row(1, unrest=0.0, crisis_level=0)
        state = make_state(districts={1: row})

        events = crisis.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert row.crisis_level == 0
        assert events == []

    def test_escalation_bumps_level_and_emits_a_bulletin(self):
        row = make_district_row(1, unrest=0.0, crisis_level=0)
        npcs = {
            f"n{i}": make_npc(f"n{i}", hunger=constants.HEALTH_DECAY_HUNGER_THRESHOLD)
            for i in range(100)
        }
        state = make_state(districts={1: row}, npcs=npcs)
        # Force unrest above the first threshold directly -- reaching it
        # through the small per-day hunger contribution alone would take
        # many simulated days.
        row.unrest = constants.STANCE_THRESHOLDS[0] * 0 + constants.CRISIS_THRESHOLDS[0]

        events = crisis.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert row.crisis_level == 1
        assert row.crisis_kind == "unrest"
        assert len(events) == 1
        assert events[0].district_id == 1
        assert "escalates" in events[0].text

    def test_de_escalation_emits_a_bulletin_too(self):
        row = make_district_row(
            1, unrest=constants.CRISIS_THRESHOLDS[0], crisis_level=1, crisis_kind="unrest"
        )
        state = make_state(districts={1: row})

        events = crisis.run(state, make_ctx(tick=constants.TICKS_PER_DAY * 100))

        assert row.crisis_level == 0
        assert row.crisis_kind is None
        assert len(events) == 1
        assert "eases" in events[0].text

    def test_no_district_state_row_is_skipped_without_error(self):
        state = make_state(districts={})

        events = crisis.run(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert events == []
