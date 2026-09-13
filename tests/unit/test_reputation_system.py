from __future__ import annotations

from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.db.models import Character, RelationshipRow
from panem_shared.enums import CharacterStatus, DayPhase, OwnerKind
from panem_sim.rng import tick_rng
from panem_sim.state import TickContext, WorldState
from panem_sim.systems import reputation

CHECK_INTERVAL_TICKS = constants.REP_RELATIONSHIP_CHECK_INTERVAL_DAYS * constants.TICKS_PER_DAY


def make_content() -> ContentBundle:
    return ContentBundle(districts={}, goods={}, jobs={}, routes=[])


def make_character(id_: int, *, reputation: float = 0.0) -> Character:
    character = Character(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name=f"char{id_}",
        age=20,
        status=CharacterStatus.APPROVED.value,
        reputation=reputation,
    )
    character.id = id_
    return character


def make_relationship(
    character_id: int, npc_id: str, *, affinity: int, as_subject: bool = True
) -> RelationshipRow:
    character_pair = (OwnerKind.CHARACTER.value, str(character_id))
    npc_pair = (OwnerKind.NPC.value, npc_id)
    subject, obj = (character_pair, npc_pair) if as_subject else (npc_pair, character_pair)
    return RelationshipRow(
        subject_kind=subject[0],
        subject_id=subject[1],
        object_kind=obj[0],
        object_id=obj[1],
        affinity=affinity,
        interaction_count=5,
    )


def make_ctx(*, tick: int) -> TickContext:
    return TickContext(
        tick=tick,
        phase=DayPhase.MORNING,
        day=1,
        month=1,
        rng=tick_rng("seed", tick),
        content=make_content(),
    )


def make_state(character: Character, *relationships: RelationshipRow) -> WorldState:
    return WorldState(
        districts={},
        npcs={},
        npc_schedules={},
        characters={character.id: character},
        open_shifts=[],
        relationships={
            (row.subject_kind, row.subject_id, row.object_kind, row.object_id): row
            for row in relationships
        },
    )


class TestReputationFromRelationships:
    def test_good_relationship_raises_reputation(self):
        character = make_character(1)
        good_row = make_relationship(1, "npc1", affinity=constants.STANCE_THRESHOLDS[2])
        state = make_state(character, good_row)

        reputation.run(state, make_ctx(tick=CHECK_INTERVAL_TICKS))

        assert character.reputation == constants.REP_RELATIONSHIP_DELTA

    def test_bad_relationship_lowers_reputation(self):
        character = make_character(1)
        bad_row = make_relationship(1, "npc1", affinity=constants.STANCE_THRESHOLDS[1])
        state = make_state(character, bad_row)

        reputation.run(state, make_ctx(tick=CHECK_INTERVAL_TICKS))

        assert character.reputation == -constants.REP_RELATIONSHIP_DELTA

    def test_neutral_relationship_does_not_move_reputation(self):
        character = make_character(1)
        neutral_row = make_relationship(1, "npc1", affinity=0)
        state = make_state(character, neutral_row)

        reputation.run(state, make_ctx(tick=CHECK_INTERVAL_TICKS))

        assert character.reputation == 0.0

    def test_counts_relationships_regardless_of_subject_object_direction(self):
        character = make_character(1)
        good_row = make_relationship(
            1, "npc1", affinity=constants.STANCE_THRESHOLDS[2], as_subject=False
        )
        state = make_state(character, good_row)

        reputation.run(state, make_ctx(tick=CHECK_INTERVAL_TICKS))

        assert character.reputation == constants.REP_RELATIONSHIP_DELTA

    def test_multiple_good_relationships_stack(self):
        character = make_character(1)
        rows = [
            make_relationship(1, f"npc{i}", affinity=constants.STANCE_THRESHOLDS[2])
            for i in range(3)
        ]
        state = make_state(character, *rows)

        reputation.run(state, make_ctx(tick=CHECK_INTERVAL_TICKS))

        assert character.reputation == 3 * constants.REP_RELATIONSHIP_DELTA

    def test_good_and_bad_relationships_net_out(self):
        character = make_character(1)
        good_row = make_relationship(1, "npc1", affinity=constants.STANCE_THRESHOLDS[2])
        bad_row = make_relationship(1, "npc2", affinity=constants.STANCE_THRESHOLDS[1])
        state = make_state(character, good_row, bad_row)

        reputation.run(state, make_ctx(tick=CHECK_INTERVAL_TICKS))

        assert character.reputation == 0.0

    def test_only_runs_on_the_weekly_boundary(self):
        character = make_character(1)
        good_row = make_relationship(1, "npc1", affinity=constants.STANCE_THRESHOLDS[2])
        state = make_state(character, good_row)

        reputation.run(state, make_ctx(tick=CHECK_INTERVAL_TICKS - 1))

        assert character.reputation == 0.0

    def test_ignores_relationships_belonging_to_a_different_character(self):
        character = make_character(1)
        other_characters_row = make_relationship(2, "npc1", affinity=constants.STANCE_THRESHOLDS[2])
        state = make_state(character, other_characters_row)

        reputation.run(state, make_ctx(tick=CHECK_INTERVAL_TICKS))

        assert character.reputation == 0.0
