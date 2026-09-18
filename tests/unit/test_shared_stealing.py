"""Smoke test that panem_shared.stealing works standalone (no panem_bot
dependency) -- panem_api's crime-attempt endpoints import it directly,
same reason panem_shared.jail/shifts do.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared import stealing as shared_stealing


class TestStealDifficulty:
    def test_a_player_mark_is_harder_than_an_npc(self):
        assert shared_stealing.steal_difficulty(is_npc=False) > shared_stealing.steal_difficulty(
            is_npc=True
        )

    def test_matches_the_rng_fallback_s_own_odds(self):
        assert shared_stealing.steal_difficulty(is_npc=True) == (
            1.0 - constants.STEAL_FROM_NPC_BASE_SUCCESS
        )
        assert shared_stealing.steal_difficulty(is_npc=False) == (
            1.0 - constants.STEAL_FROM_PLAYER_BASE_SUCCESS
        )


class TestBurgleDifficulty:
    def test_matches_the_rng_fallback_s_own_odds(self):
        assert shared_stealing.burgle_difficulty() == 1.0 - constants.BURGLE_BASE_SUCCESS

    def test_a_house_is_at_least_as_hard_as_stealing_from_a_player(self):
        assert shared_stealing.burgle_difficulty() >= shared_stealing.steal_difficulty(is_npc=False)
