from __future__ import annotations

import random

from panem_shared.content.names import sample_names


class TestSampleNames:
    def test_returns_the_requested_count(self):
        names = sample_names(random.Random(1), 23)
        assert len(names) == 23

    def test_names_are_unique(self):
        names = sample_names(random.Random(1), 23)
        assert len(set(names)) == len(names)

    def test_each_name_is_first_and_last(self):
        names = sample_names(random.Random(1), 5)
        for name in names:
            assert len(name.split(" ")) == 2

    def test_deterministic_for_the_same_rng_state(self):
        assert sample_names(random.Random(42), 10) == sample_names(random.Random(42), 10)
