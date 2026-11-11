"""Apprentice -> Expert mastery progression for hobbies, mirroring
`panem_shared.job_levels` exactly but driven by `CharacterHobby.
sessions_played` instead of `Character.shifts_completed` -- reusing the
same `JobLevel` enum rather than inventing a parallel one, since both are
just "count of successful attempts -> one of 5 named rungs"."""

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


def hobby_level_for_sessions(sessions_played: int) -> JobLevel:
    level = JobLevel.APPRENTICE
    for candidate in _ORDERED_LEVELS:
        if sessions_played >= constants.HOBBY_LEVEL_SESSION_THRESHOLDS[candidate.value]:
            level = candidate
    return level


def success_bonus_for_level(level: JobLevel) -> float:
    return constants.HOBBY_LEVEL_SUCCESS_BONUS[level.value]


def level_index(level: JobLevel) -> int:
    return _ORDERED_LEVELS.index(level)


def meets_min_level(sessions_played: int, min_level: str) -> bool:
    """Whether a `CollectibleDrop.min_level` (a plain `JobLevel` string
    from content, not yet an enum) is met by `sessions_played`."""
    current = level_index(hobby_level_for_sessions(sessions_played))
    required = level_index(JobLevel(min_level))
    return current >= required


def sessions_to_next_level(sessions_played: int) -> int | None:
    current = hobby_level_for_sessions(sessions_played)
    current_index = _ORDERED_LEVELS.index(current)
    if current_index == len(_ORDERED_LEVELS) - 1:
        return None
    next_level = _ORDERED_LEVELS[current_index + 1]
    return constants.HOBBY_LEVEL_SESSION_THRESHOLDS[next_level.value] - sessions_played
