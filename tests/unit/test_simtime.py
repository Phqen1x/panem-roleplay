from __future__ import annotations

from panem_shared import constants
from panem_shared.enums import DayPhase
from panem_shared.simtime import advance, current, is_phase_boundary, ticks_until_next_phase

PHASE_TICKS = constants.TICKS_PER_DAY // 4  # 6


class TestAdvance:
    def test_first_tick_from_a_fresh_clock(self):
        tick, phase, day, month = advance(0)
        assert (tick, phase, day, month) == (1, DayPhase.NIGHT, 1, 1)

    def test_phase_changes_at_each_phase_boundary(self):
        assert advance(PHASE_TICKS - 1)[1] == DayPhase.MORNING
        assert advance(PHASE_TICKS * 2 - 1)[1] == DayPhase.AFTERNOON
        assert advance(PHASE_TICKS * 3 - 1)[1] == DayPhase.EVENING
        assert advance(PHASE_TICKS * 4 - 1)[1] == DayPhase.NIGHT

    def test_day_and_month_roll_over(self):
        _, _, day, month = advance(constants.TICKS_PER_DAY - 1)
        assert (day, month) == (2, 1)

        last_tick_of_month = constants.TICKS_PER_DAY * constants.DAYS_PER_MONTH - 1
        _, _, day, month = advance(last_tick_of_month)
        assert (day, month) == (1, 2)


class TestCurrent:
    def test_matches_advance_for_the_tick_it_persisted(self):
        advanced = advance(41)
        assert current(advanced[0]) == advanced

    def test_zero_persisted_tick_is_the_very_start(self):
        assert current(0) == (0, DayPhase.NIGHT, 1, 1)


class TestPhaseBoundary:
    def test_true_on_boundary_ticks(self):
        assert is_phase_boundary(0) is True
        assert is_phase_boundary(PHASE_TICKS) is True
        assert is_phase_boundary(PHASE_TICKS * 2) is True

    def test_false_off_boundary(self):
        assert is_phase_boundary(1) is False
        assert is_phase_boundary(PHASE_TICKS - 1) is False


class TestTicksUntilNextPhase:
    def test_zero_on_a_boundary_tick(self):
        assert ticks_until_next_phase(PHASE_TICKS) == 0

    def test_counts_down_toward_the_next_boundary(self):
        assert ticks_until_next_phase(PHASE_TICKS - 1) == 1
        assert ticks_until_next_phase(PHASE_TICKS + 1) == PHASE_TICKS - 1
