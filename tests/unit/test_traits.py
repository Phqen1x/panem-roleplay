from __future__ import annotations

import random

from panem_shared.content.traits import TRAITS_PER_NPC, sample_traits, speech_tone


class TestSampleTraits:
    def test_returns_the_configured_count(self):
        traits = sample_traits(random.Random(1))
        assert len(traits) == TRAITS_PER_NPC

    def test_traits_are_unique_within_one_npc(self):
        traits = sample_traits(random.Random(1))
        assert len(set(traits)) == len(traits)

    def test_deterministic_for_the_same_rng_state(self):
        assert sample_traits(random.Random(42)) == sample_traits(random.Random(42))


class TestSpeechTone:
    def test_warm_trait_wins(self):
        assert speech_tone(["kind", "reckless"]) == "warm"

    def test_blunt_trait_without_warm(self):
        assert speech_tone(["hot-tempered", "stoic"]) == "blunt"

    def test_reserved_trait_without_warm_or_blunt(self):
        assert speech_tone(["stoic", "witty"]) == "reserved"

    def test_falls_back_to_plain(self):
        assert speech_tone(["witty", "ambitious"]) == "plain"
