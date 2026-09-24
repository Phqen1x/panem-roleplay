"""District context/history admin CRUD (the Activity's staff-only History
tab) plus the read side `dialogue.build_request_context` uses to color an
NPC's reply.

One `DistrictLore` row per district, created lazily on first save -- there
is no seed data, same posture as `AfflictionType`/`LayerCategory`: staff
build this up over time through `panem_api.dashboard_routes.build_
district_lore_router`, and it's fine for a district to have no row at all
until someone edits it (`get_lore`/`prompt_summary` both treat "no row" the
same as "a row with everything blank").

`prompt_summary` is the *only* thing of this that ever reaches an NPC's
prompt, and only a capped excerpt of it (`constants.DISTRICT_LORE_PROMPT_
MAX_LEN`) -- per the feature's own ask, this is reference material staff
maintain richly, not something every dialogue reply should lean on. It is
deliberately not exhaustive: adjectives, classification and the accent/
urban-rural notes shape *tone*, so they're included; the free-text `games_
history`/`opinions`/`regime_notes`/`misc_notes` fields are for staff (and,
one day, a "recall district history" tool) to consult directly, not to
compress into every line an NPC says.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from panem_shared import constants
from panem_shared.db.models import DistrictLore, DistrictLorePerson
from panem_shared.enums import DistrictClassification
from panem_shared.errors import NotFound, ValidationFailed

ADJECTIVES_MAX_COUNT = 12
ADJECTIVE_MAX_LEN = 32
NOTES_MAX_LEN = 4000
ACADEMY_NAME_MAX_LEN = 120
PERSON_NAME_MAX_LEN = 80
PERSON_NOTES_MAX_LEN = 1000
ALLOWED_CLASSIFICATIONS = frozenset(c.value for c in DistrictClassification)
ALLOWED_PERSON_ROLES = frozenset({"victor", "mentor"})


async def get_lore(session: AsyncSession, district_id: int) -> DistrictLore | None:
    return await session.get(DistrictLore, district_id)


async def list_lore(session: AsyncSession) -> list[DistrictLore]:
    result = await session.execute(select(DistrictLore).order_by(DistrictLore.district_id))
    return list(result.scalars().all())


async def list_people(session: AsyncSession, district_id: int) -> list[DistrictLorePerson]:
    result = await session.execute(
        select(DistrictLorePerson)
        .where(DistrictLorePerson.district_id == district_id)
        .options(selectinload(DistrictLorePerson.character))
        .order_by(DistrictLorePerson.role, DistrictLorePerson.name)
    )
    return list(result.scalars().all())


async def list_people_all(session: AsyncSession) -> list[DistrictLorePerson]:
    result = await session.execute(
        select(DistrictLorePerson)
        .options(selectinload(DistrictLorePerson.character))
        .order_by(DistrictLorePerson.district_id, DistrictLorePerson.role, DistrictLorePerson.name)
    )
    return list(result.scalars().all())


def _clean_notes(text: str | None, *, field: str) -> str:
    text = (text or "").strip()
    if len(text) > NOTES_MAX_LEN:
        raise ValidationFailed(f"district_lore_{field}_too_long")
    return text


def _clean_adjectives(adjectives: list[str] | None) -> list[str]:
    if not adjectives:
        return []
    cleaned: list[str] = []
    for raw in adjectives:
        word = raw.strip()
        if not word:
            continue
        if len(word) > ADJECTIVE_MAX_LEN:
            raise ValidationFailed("district_lore_adjective_too_long")
        cleaned.append(word)
    if len(cleaned) > ADJECTIVES_MAX_COUNT:
        raise ValidationFailed("district_lore_too_many_adjectives")
    return cleaned


def _clean_opinions(opinions: dict[str, str] | None) -> dict[str, str]:
    if not opinions:
        return {}
    cleaned: dict[str, str] = {}
    for raw_key, raw_value in opinions.items():
        try:
            other_id = int(raw_key)
        except (TypeError, ValueError) as exc:
            raise ValidationFailed("district_lore_invalid_opinion_district") from exc
        if other_id < 0 or other_id > 12:
            raise ValidationFailed("district_lore_invalid_opinion_district")
        value = (raw_value or "").strip()
        if len(value) > NOTES_MAX_LEN:
            raise ValidationFailed("district_lore_opinion_too_long")
        if value:
            cleaned[str(other_id)] = value
    return cleaned


def _clean_classification(classification: str | None) -> str | None:
    if classification is None or classification == "":
        return None
    if classification not in ALLOWED_CLASSIFICATIONS:
        raise ValidationFailed("district_lore_invalid_classification")
    return classification


def _clean_academy_name(name: str | None) -> str | None:
    if name is None:
        return None
    name = name.strip()
    if not name:
        return None
    if len(name) > ACADEMY_NAME_MAX_LEN:
        raise ValidationFailed("district_lore_academy_name_too_long")
    return name


async def upsert_lore(
    session: AsyncSession,
    district_id: int,
    *,
    classification: str | None,
    adjectives: list[str] | None,
    accent_notes: str | None,
    urban_rural_notes: str | None,
    academy_name: str | None,
    academy_notes: str | None,
    games_history: str | None,
    regime_notes: str | None,
    opinions: dict[str, str] | None,
    misc_notes: str | None,
    updated_by: int,
) -> DistrictLore:
    if district_id < 0 or district_id > 12:
        raise ValidationFailed("invalid_district_id")
    row = await session.get(DistrictLore, district_id)
    if row is None:
        row = DistrictLore(district_id=district_id)
        session.add(row)
    row.classification = _clean_classification(classification)
    row.adjectives = _clean_adjectives(adjectives)
    row.accent_notes = _clean_notes(accent_notes, field="accent_notes")
    row.urban_rural_notes = _clean_notes(urban_rural_notes, field="urban_rural_notes")
    row.academy_name = _clean_academy_name(academy_name)
    row.academy_notes = _clean_notes(academy_notes, field="academy_notes")
    row.games_history = _clean_notes(games_history, field="games_history")
    row.regime_notes = _clean_notes(regime_notes, field="regime_notes")
    row.opinions = _clean_opinions(opinions)
    row.misc_notes = _clean_notes(misc_notes, field="misc_notes")
    row.updated_by = updated_by
    await session.flush()
    return row


async def create_person(
    session: AsyncSession,
    *,
    district_id: int,
    role: str,
    name: str,
    character_id: int | None,
    is_active: bool,
    notes: str,
) -> DistrictLorePerson:
    if district_id < 0 or district_id > 12:
        raise ValidationFailed("invalid_district_id")
    if role not in ALLOWED_PERSON_ROLES:
        raise ValidationFailed("district_lore_invalid_person_role")
    clean_name = name.strip()
    if not clean_name or len(clean_name) > PERSON_NAME_MAX_LEN:
        raise ValidationFailed("district_lore_invalid_person_name")
    row = DistrictLorePerson(
        district_id=district_id,
        role=role,
        name=clean_name,
        character_id=character_id,
        is_active=is_active,
        notes=_clean_notes(notes, field="person_notes"),
    )
    session.add(row)
    await session.flush()
    return row


async def update_person(
    session: AsyncSession,
    person_id: int,
    *,
    role: str | None,
    name: str | None,
    character_id: int | None,
    character_id_set: bool,
    is_active: bool | None,
    notes: str | None,
) -> DistrictLorePerson:
    """`character_id`/`character_id_set` split lets a caller explicitly
    clear the link (`character_id=None, character_id_set=True`) without
    that being indistinguishable from "leave it alone" (`character_id_
    set=False`) -- every other optional field here uses `None` for that
    purpose, but `None` is also this field's own valid "no character"
    value."""
    row = await session.get(DistrictLorePerson, person_id)
    if row is None:
        raise NotFound("district_lore_person_not_found")
    if role is not None:
        if role not in ALLOWED_PERSON_ROLES:
            raise ValidationFailed("district_lore_invalid_person_role")
        row.role = role
    if name is not None:
        clean_name = name.strip()
        if not clean_name or len(clean_name) > PERSON_NAME_MAX_LEN:
            raise ValidationFailed("district_lore_invalid_person_name")
        row.name = clean_name
    if character_id_set:
        row.character_id = character_id
    if is_active is not None:
        row.is_active = is_active
    if notes is not None:
        row.notes = _clean_notes(notes, field="person_notes")
    return row


async def delete_person(session: AsyncSession, person_id: int) -> None:
    row = await session.get(DistrictLorePerson, person_id)
    if row is None:
        raise NotFound("district_lore_person_not_found")
    await session.delete(row)
    await session.flush()


def prompt_summary(lore: DistrictLore | None) -> str | None:
    """A short, single-line condensation of `lore` for `dialogue.build_
    request_context`'s `[SCENE] ... lore` field -- tone-setting only
    (classification, adjectives, urban/rural feel, academy name), capped
    to `constants.DISTRICT_LORE_PROMPT_MAX_LEN` with an ellipsis, mirroring
    `NPC_BACKGROUND_PROMPT_MAX_LEN`'s truncation. Returns `None` when there
    is nothing worth saying, so an empty/never-edited row adds no line at
    all (same "missing block is just omitted" behavior `render_request_
    header` already gives every other empty block)."""
    if lore is None:
        return None
    parts: list[str] = []
    if lore.classification == DistrictClassification.INNER.value:
        parts.append("an inner district")
    elif lore.classification == DistrictClassification.OUTLIER.value:
        parts.append("an outlier district")
    if lore.adjectives:
        parts.append("known as " + ", ".join(lore.adjectives[:5]))
    if lore.urban_rural_notes:
        parts.append(lore.urban_rural_notes)
    if lore.academy_name:
        parts.append(f"trains tributes at the {lore.academy_name}")
    if not parts:
        return None
    summary = "; ".join(parts)
    if len(summary) > constants.DISTRICT_LORE_PROMPT_MAX_LEN:
        summary = summary[: constants.DISTRICT_LORE_PROMPT_MAX_LEN - 1].rstrip() + "…"
    return summary
