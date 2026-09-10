"""Scene lifecycle logic (Spec §3.3 FR-SCN). No Discord I/O: the cog owns
thread creation/tagging/archiving and calls into this module to decide
*what* to do."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from panem_shared.constants import SCENE_IDLE_ARCHIVE_HOURS
from panem_shared.content.schemas import District
from panem_shared.db.models import Scene
from panem_shared.enums import SceneKind, SceneStatus


@dataclass(frozen=True, slots=True)
class TagResolution:
    location_id: str | None
    error: str | None  # "no_tag" | "multiple_tags" | None


def resolve_location_tag(applied_tag_names: list[str], district: District) -> TagResolution:
    """FR-SCN-3: a new forum post must carry exactly one location tag.

    Discord forum tags carry the location's `name` (FR-SCN-1: "tag name =
    location `name`"), not its `id`, so matching is by name and the
    resolved value is the location's `id`."""
    by_name = {loc.name: loc.id for loc in district.locations}
    matched = [by_name[name] for name in applied_tag_names if name in by_name]
    if len(matched) == 0:
        return TagResolution(None, "no_tag")
    if len(matched) > 1:
        return TagResolution(None, "multiple_tags")
    return TagResolution(matched[0], None)


def is_scene_cap_reached(*, open_non_ambient_count: int, cap: int) -> bool:
    """FR-SCN-7."""
    return open_non_ambient_count >= cap


def pick_scenes_to_archive(
    scenes: list[Scene],
    *,
    cap: int,
    now: dt.datetime,
    idle_hours: int = SCENE_IDLE_ARCHIVE_HOURS,
) -> list[int]:
    """FR-SCN-7: oldest idle non-ambient open scenes first, once a district is
    over `cap`. Returns scene ids to archive, never ambient scenes."""
    eligible = [
        s
        for s in scenes
        if s.kind != SceneKind.AMBIENT.value
        and s.status == SceneStatus.OPEN.value
        and s.last_message_at is not None
        and (now - s.last_message_at) >= dt.timedelta(hours=idle_hours)
    ]
    open_count = sum(
        1
        for s in scenes
        if s.kind != SceneKind.AMBIENT.value and s.status == SceneStatus.OPEN.value
    )
    over_cap = max(0, open_count - cap)
    if over_cap == 0:
        return []
    eligible.sort(key=lambda s: s.last_message_at)  # type: ignore[arg-type,return-value]
    return [s.id for s in eligible[:over_cap]]


def can_manage_scene(*, actor_character_id: int | None, scene: Scene, is_staff: bool) -> bool:
    """FR-SCN-6: `/scene close` and `/scene move` are creator-or-staff only."""
    if is_staff:
        return True
    return (
        scene.created_by_character_id is not None
        and actor_character_id == scene.created_by_character_id
    )
