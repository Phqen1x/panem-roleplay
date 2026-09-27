from __future__ import annotations

from panem_shared import constants
from panem_shared.db.models import RelationshipRow
from panem_shared.enums import OwnerKind, Stance
from panem_shared.relationships import (
    apply_affinity_delta,
    crash_to_hated,
    get_or_create_relationship,
    relationship_key,
    stance_for_affinity,
)


class TestRelationshipKey:
    def test_same_pair_gives_the_same_key_regardless_of_argument_order(self):
        a = ("character", "1")
        b = ("npc", "d1_npc_001")
        assert relationship_key(a, b) == relationship_key(b, a)

    def test_key_shape_matches_relationship_row_columns(self):
        key = relationship_key(("character", "1"), ("npc", "d1_npc_001"))
        assert key == ("character", "1", "npc", "d1_npc_001")

    def test_character_sorts_before_npc(self):
        # "character" < "npc" lexicographically, so for any NPC/character
        # pair the character is always the canonical subject.
        key = relationship_key(("npc", "z"), ("character", "a"))
        assert key[:2] == ("character", "a")


class TestStanceForAffinity:
    def test_never_interacted_is_always_a_stranger(self):
        assert stance_for_affinity(9999, 0) == Stance.STRANGER.value

    def test_extreme_low_needs_enough_interactions_to_read_as_hates(self):
        assert stance_for_affinity(-70, 1) == Stance.DISLIKES.value
        assert (
            stance_for_affinity(-70, constants.STANCE_MIN_INTERACTIONS_EXTREME)
            == Stance.HATES.value
        )

    def test_extreme_high_needs_enough_interactions_to_read_as_loves(self):
        assert stance_for_affinity(70, 1) == Stance.LIKES.value
        assert (
            stance_for_affinity(70, constants.STANCE_MIN_INTERACTIONS_EXTREME) == Stance.LOVES.value
        )

    def test_middle_band_is_neutral(self):
        assert stance_for_affinity(0, 3) == Stance.NEUTRAL.value


class TestGetOrCreateRelationship:
    async def test_creates_a_fresh_row_when_none_exists(self, db_session):
        row = await get_or_create_relationship(
            db_session, (OwnerKind.CHARACTER.value, "1"), (OwnerKind.NPC.value, "npc1")
        )
        assert row.affinity == 0
        assert row.interaction_count == 0
        assert row.stance == Stance.STRANGER.value

    async def test_returns_the_existing_row_unchanged(self, db_session):
        existing = RelationshipRow(
            subject_kind=OwnerKind.CHARACTER.value,
            subject_id="1",
            object_kind=OwnerKind.NPC.value,
            object_id="npc1",
            affinity=15,
            interaction_count=4,
            stance=Stance.LIKES.value,
        )
        db_session.add(existing)
        await db_session.flush()

        row = await get_or_create_relationship(
            db_session, (OwnerKind.CHARACTER.value, "1"), (OwnerKind.NPC.value, "npc1")
        )
        assert row.affinity == 15
        assert row.interaction_count == 4


class TestApplyAffinityDelta:
    def test_decrements_affinity_and_bumps_interaction_count(self):
        row = RelationshipRow(
            subject_kind=OwnerKind.CHARACTER.value,
            subject_id="1",
            object_kind=OwnerKind.NPC.value,
            object_id="npc1",
            affinity=10,
            interaction_count=2,
            stance=Stance.NEUTRAL.value,
        )
        apply_affinity_delta(row, -40, current_tick=100)
        assert row.affinity == -30
        assert row.interaction_count == 3
        assert row.stance_updated_tick == 100

    def test_recomputes_stance_immediately(self):
        row = RelationshipRow(
            subject_kind=OwnerKind.CHARACTER.value,
            subject_id="1",
            object_kind=OwnerKind.NPC.value,
            object_id="npc1",
            affinity=0,
            interaction_count=1,
            stance=Stance.NEUTRAL.value,
        )
        apply_affinity_delta(row, -40, current_tick=1)
        assert row.stance == Stance.DISLIKES.value

    def test_no_tick_leaves_stance_updated_tick_untouched(self):
        row = RelationshipRow(
            subject_kind=OwnerKind.CHARACTER.value,
            subject_id="1",
            object_kind=OwnerKind.NPC.value,
            object_id="npc1",
            affinity=0,
            interaction_count=0,
            stance=Stance.STRANGER.value,
            stance_updated_tick=5,
        )
        apply_affinity_delta(row, -5, current_tick=None)
        assert row.stance_updated_tick == 5


class TestCrashToHated:
    def test_drives_affinity_to_the_floor_and_stance_to_hates(self):
        row = RelationshipRow(
            subject_kind=OwnerKind.CHARACTER.value,
            subject_id="1",
            object_kind=OwnerKind.NPC.value,
            object_id="npc1",
            affinity=30,
            interaction_count=1,
            stance=Stance.LIKES.value,
        )
        crash_to_hated(row, current_tick=50)
        assert row.affinity == constants.AFFINITY_FLOOR
        assert row.stance == Stance.HATES.value
        assert row.interaction_count >= constants.STANCE_MIN_INTERACTIONS_EXTREME
        assert row.stance_updated_tick == 50
