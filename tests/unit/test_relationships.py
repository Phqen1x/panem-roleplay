from __future__ import annotations

from panem_shared.relationships import relationship_key


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
