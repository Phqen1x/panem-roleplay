"""Staged profile edits (`Character.pending_profile_*`) awaiting staff
approval -- `/profile edit`'s counterpart to `panem_shared.rp_modes`'s
staged mode switch: separate explicit columns, never touching the
character's real fields until staff approve, so the character keeps
playing with its current submission the whole time an edit is pending.

Lives in `panem_shared`, not `panem_bot`, for the same reason `panem_
shared.characters`/`rp_modes` do: the dashboard's own character routes
(`panem_api.dashboard_routes`) will eventually want this too, and
`panem_api` cannot import `panem_bot`.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared.characters import (
    ensure_name_available,
    validate_avatar_url,
    validate_character_fields,
    validate_job_title,
)
from panem_shared.db.models import Character
from panem_shared.enums import Gender
from panem_shared.errors import NotAllowed, ValidationFailed

# `(label, field_name)` pairs, in the order a diff/embed should list them --
# `field_name` is the suffix shared by the real column (`Character.
# {field_name}`) and its staging column (`Character.pending_profile_
# {field_name}`).
PROFILE_EDIT_FIELDS: tuple[tuple[str, str], ...] = (
    ("Name", "name"),
    ("Age", "age"),
    ("Gender", "gender"),
    ("Appearance", "appearance"),
    ("Backstory", "backstory"),
    ("Occupation", "job_title"),
    ("Faceclaim", "avatar_url"),
)


def check_can_stage_profile_edit(character: Character) -> None:
    if character.pending_profile_name is not None:
        raise NotAllowed("profile_edit_already_pending")


async def stage_profile_edit(
    session: AsyncSession,
    character: Character,
    *,
    name: str,
    age: int,
    gender: str | None,
    appearance: str,
    backstory: str,
    job_title: str | None,
    avatar_url: str,
) -> None:
    """Validates the full proposed profile (every field, not just the ones
    that changed -- same "resubmit everything" shape `/character edit`
    already uses) and stages it. Callers must have already called `check_
    can_stage_profile_edit` (this raises nothing of its own for that) and
    resolved `avatar_url` themselves (an unchanged faceclaim still needs a
    real URL here -- the character's own current one, if the player didn't
    upload or link a new one -- since staging always carries the complete
    proposed state, not a sparse diff)."""
    if gender is not None and gender not in {g.value for g in Gender}:
        raise ValidationFailed("invalid_gender")
    validate_character_fields(
        district_id=character.district_id,
        name=name,
        age=age,
        appearance=appearance,
        backstory=backstory,
    )
    await ensure_name_available(session, name, exclude_character_id=character.id)
    if job_title is not None:
        validate_job_title(job_title)
    validate_avatar_url(avatar_url)

    if (
        name == character.name
        and age == character.age
        and gender == character.gender
        and appearance == character.appearance
        and backstory == character.backstory
        and job_title == character.job_title
        and avatar_url == character.avatar_url
    ):
        raise ValidationFailed("no_profile_changes")

    character.pending_profile_name = name
    character.pending_profile_age = age
    character.pending_profile_gender = gender
    character.pending_profile_appearance = appearance
    character.pending_profile_backstory = backstory
    character.pending_profile_job_title = job_title
    character.pending_profile_avatar_url = avatar_url


def profile_edit_diff(character: Character) -> list[tuple[str, str, str]]:
    """`(label, before, after)` for every field that actually differs
    between the real and staged values -- what the approval embed and
    the decline DM both show. Callers are expected to have already
    confirmed `pending_profile_name is not None`."""
    assert character.pending_profile_name is not None
    diff: list[tuple[str, str, str]] = []
    for label, field in PROFILE_EDIT_FIELDS:
        before = getattr(character, field)
        after = getattr(character, f"pending_profile_{field}")
        if before != after:
            diff.append((label, _display(before), _display(after)))
    return diff


def _display(value: object) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def apply_staged_profile_edit(character: Character) -> None:
    """Staff approved a staged edit (`stage_profile_edit`) -- copy the
    staged fields onto the real ones and clear the staging columns.
    Callers are expected to have already confirmed `pending_profile_name
    is not None`."""
    assert character.pending_profile_name is not None
    character.name = character.pending_profile_name
    character.age = character.pending_profile_age  # type: ignore[assignment]
    character.gender = character.pending_profile_gender
    character.appearance = character.pending_profile_appearance  # type: ignore[assignment]
    character.backstory = character.pending_profile_backstory  # type: ignore[assignment]
    character.job_title = character.pending_profile_job_title
    character.avatar_url = character.pending_profile_avatar_url
    discard_staged_profile_edit(character)


def discard_staged_profile_edit(character: Character) -> None:
    """Staff declined a staged edit, or it's otherwise being abandoned --
    clears the staging columns without touching any of the character's
    real submitted fields, so nothing about the profile it already had
    is lost."""
    character.pending_profile_name = None
    character.pending_profile_age = None
    character.pending_profile_gender = None
    character.pending_profile_appearance = None
    character.pending_profile_backstory = None
    character.pending_profile_job_title = None
    character.pending_profile_avatar_url = None
