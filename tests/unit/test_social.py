from __future__ import annotations

from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.db.models import Character, Npc
from panem_shared.enums import CharacterStatus, DayPhase, Stance
from panem_sim.rng import tick_rng
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import social


def make_content() -> ContentBundle:
    return ContentBundle(districts={}, goods={}, jobs={}, routes=[])


def make_npc(id_: str, *, district_id: int = 1, location_id: str | None = "square") -> Npc:
    return Npc(id=id_, district_id=district_id, name=id_, age=30, location_id=location_id)


def make_character(
    id_: int, *, district_id: int = 1, location_id: str | None = "square"
) -> Character:
    character = Character(
        user_id=1,
        district_id=district_id,
        current_district_id=district_id,
        name=f"char{id_}",
        age=20,
        status=CharacterStatus.APPROVED.value,
        location_id=location_id,
    )
    character.id = id_
    return character


def make_ctx(*, tick: int) -> TickContext:
    return TickContext(
        tick=tick,
        phase=DayPhase.MORNING,
        day=1,
        month=1,
        rng=tick_rng("seed", tick),
        content=make_content(),
    )


def make_state(*npcs: Npc, characters: tuple[Character, ...] = ()) -> WorldState:
    return WorldState(
        districts={},
        npcs={npc.id: npc for npc in npcs},
        npc_schedules={},
        characters={c.id: c for c in characters},
        open_shifts=[],
    )


class TestOccupantGrouping:
    def test_two_npcs_at_the_same_location_form_a_relationship(self):
        state = make_state(make_npc("a"), make_npc("b"))

        social.run(state, make_ctx(tick=1))

        assert len(state.new_relationships) == 1
        row = state.new_relationships[0]
        assert {row.subject_id, row.object_id} == {"a", "b"}
        assert row.interaction_count == 1

    def test_lone_npc_creates_no_relationship(self):
        state = make_state(make_npc("a"))

        social.run(state, make_ctx(tick=1))

        assert state.new_relationships == []

    def test_npcs_at_different_locations_do_not_interact(self):
        state = make_state(make_npc("a", location_id="square"), make_npc("b", location_id="market"))

        social.run(state, make_ctx(tick=1))

        assert state.new_relationships == []

    def test_character_character_pair_is_never_tracked(self):
        state = make_state(characters=(make_character(1), make_character(2)))

        social.run(state, make_ctx(tick=1))

        assert state.new_relationships == []

    def test_npc_character_pair_does_interact(self):
        state = make_state(make_npc("a"), characters=(make_character(1),))

        social.run(state, make_ctx(tick=1))

        assert len(state.new_relationships) == 1

    def test_npc_with_no_location_is_excluded(self):
        state = make_state(make_npc("a", location_id=None), make_npc("b"))

        social.run(state, make_ctx(tick=1))

        assert state.new_relationships == []


class TestInteractionAccumulation:
    def test_repeated_ticks_accumulate_affinity_and_count(self):
        state = make_state(make_npc("a"), make_npc("b"))

        social.run(state, make_ctx(tick=1))
        social.run(state, make_ctx(tick=2))

        row = state.new_relationships[0]
        assert row.interaction_count == 2
        assert row.affinity == social.AFFINITY_STEP * 2

    def test_a_pair_is_only_processed_once_per_tick_regardless_of_order(self):
        state = make_state(make_npc("a"), make_npc("b"), make_npc("c"))

        social.run(state, make_ctx(tick=1))

        # 3 occupants at one location -> 3 unique pairs, not 6.
        assert len(state.new_relationships) == 3
        for row in state.new_relationships:
            assert row.interaction_count == 1


class TestStanceFor:
    """`_stance_for` is pure -- exercised directly rather than through
    `run()`'s simulated ticks, since crossing a day boundary along the
    way would apply decay and make the exact affinity reached fragile
    to predict."""

    def test_zero_interactions_is_a_stranger(self):
        assert social._stance_for(0, 0) == Stance.STRANGER.value

    def test_neutral_band(self):
        lo, hi = constants.STANCE_THRESHOLDS[1], constants.STANCE_THRESHOLDS[2]
        assert social._stance_for((lo + hi) // 2, 1) == Stance.NEUTRAL.value

    def test_likes_band(self):
        hi = constants.STANCE_THRESHOLDS[2]
        assert social._stance_for(hi, 1) == Stance.LIKES.value

    def test_loves_requires_the_minimum_interaction_count(self):
        hi_extreme = constants.STANCE_THRESHOLDS[3]
        assert (
            social._stance_for(hi_extreme, constants.STANCE_MIN_INTERACTIONS_EXTREME - 1)
            == Stance.LIKES.value
        )
        assert (
            social._stance_for(hi_extreme, constants.STANCE_MIN_INTERACTIONS_EXTREME)
            == Stance.LOVES.value
        )

    def test_hates_requires_the_minimum_interaction_count(self):
        lo_extreme = constants.STANCE_THRESHOLDS[0]
        assert (
            social._stance_for(lo_extreme, constants.STANCE_MIN_INTERACTIONS_EXTREME - 1)
            == Stance.DISLIKES.value
        )
        assert (
            social._stance_for(lo_extreme, constants.STANCE_MIN_INTERACTIONS_EXTREME)
            == Stance.HATES.value
        )


class TestStanceChangeIntegration:
    def test_stance_change_records_a_notable_event(self):
        state = make_state(make_npc("a"), make_npc("b"))
        hi = constants.STANCE_THRESHOLDS[2]
        ticks_needed = -(-hi // social.AFFINITY_STEP)  # ceil div, stays well under a day boundary

        for tick in range(1, ticks_needed + 1):
            social.run(state, make_ctx(tick=tick))

        row = state.new_relationships[0]
        assert row.stance == Stance.LIKES.value
        assert any(note.kind == "stance_change" for note in state.notable_events)


class TestDecay:
    def test_decay_only_applies_at_a_day_boundary(self):
        state = make_state(make_npc("a"), make_npc("b"))
        social.run(state, make_ctx(tick=1))
        row = state.new_relationships[0]
        row.affinity = constants.AFFINITY_DECAY_FLOOR + 10

        social._decay_relationships(state, make_ctx(tick=constants.TICKS_PER_DAY - 1))
        assert row.affinity == constants.AFFINITY_DECAY_FLOOR + 10

        social._decay_relationships(state, make_ctx(tick=constants.TICKS_PER_DAY))
        assert row.affinity == constants.AFFINITY_DECAY_FLOOR + 10 - social.DECAY_STEP

    def test_decay_never_crosses_below_the_floor(self):
        state = make_state(make_npc("a"), make_npc("b"))
        social.run(state, make_ctx(tick=1))
        row = state.new_relationships[0]
        row.affinity = constants.AFFINITY_DECAY_FLOOR

        social._decay_relationships(state, make_ctx(tick=constants.TICKS_PER_DAY))

        assert row.affinity == constants.AFFINITY_DECAY_FLOOR
