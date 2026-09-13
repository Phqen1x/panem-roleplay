"""Apprentice -> Expert wage-level progression, replacing the old
per-job `ladder_next` (Job rework: a free-typed `Character.job_title`
has nowhere to author a ladder against). Pure functions over
`Character.shifts_completed`, shared by `panem_bot` (`/work`'s wage calc
and level-up announcement) and `panem_api` (the minigame result
endpoint), same reason `panem_shared.shifts` lives here rather than in
`panem_bot`.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared.enums import JobLevel

_ORDERED_LEVELS: list[JobLevel] = [
    JobLevel.APPRENTICE,
    JobLevel.NOVICE,
    JobLevel.JOURNEYMAN,
    JobLevel.MASTER,
    JobLevel.EXPERT,
]


def job_level_for_shifts(shifts_completed: int) -> JobLevel:
    """The highest level whose shift threshold `shifts_completed` meets."""
    level = JobLevel.APPRENTICE
    for candidate in _ORDERED_LEVELS:
        if shifts_completed >= constants.JOB_LEVEL_SHIFT_THRESHOLDS[candidate.value]:
            level = candidate
    return level


def wage_multiplier_for_level(level: JobLevel) -> float:
    return constants.JOB_LEVEL_WAGE_MULTIPLIERS[level.value]


def shifts_to_next_level(shifts_completed: int) -> int | None:
    """How many more completed shifts until the next level up; `None` at
    `JobLevel.EXPERT`, the top of the ladder."""
    current = job_level_for_shifts(shifts_completed)
    current_index = _ORDERED_LEVELS.index(current)
    if current_index == len(_ORDERED_LEVELS) - 1:
        return None
    next_level = _ORDERED_LEVELS[current_index + 1]
    return constants.JOB_LEVEL_SHIFT_THRESHOLDS[next_level.value] - shifts_completed
