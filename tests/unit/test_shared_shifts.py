"""Smoke test that panem_shared.shifts works standalone (no panem_bot
dependency) -- panem_api's /work minigame endpoint imports it directly.
"""

from __future__ import annotations

from panem_shared import shifts as shared_shifts
from panem_shared.content.schemas import Job, JobOption


def make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = dict(
        id="miner",
        district=1,
        title="Miner",
        workplace="mine",
        wage=10.0,
        produces={"coal": 5.0},
        shift_phase="morning",
        slots=5,
        options=[JobOption(label="a"), JobOption(label="b"), JobOption(label="c")],
    )
    defaults.update(overrides)
    return Job(**defaults)  # type: ignore[arg-type]


class TestResolveShiftGame:
    def test_win_beats_loss(self):
        job = make_job()
        win = shared_shifts.resolve_shift_game(job, True)
        lose = shared_shifts.resolve_shift_game(job, False)
        assert win.wage > lose.wage
