"""Staff-authored injury/affliction catalog (`AfflictionType`) admin CRUD.

Mirrors `panem_shared.layers`'s category-management half exactly (see that
module's docstring for the general shape/reasoning) -- there's no upload
or sub-catalog here, this catalog is flat, so it's simpler: one row per
affliction type, staff create/edit/delete them through `panem_api.
dashboard_routes.build_staff_router`'s admin endpoints, and the
Simulation-mode auto-apply/auto-cure system (`panem_sim.systems.needs`)
plus the Life-mode self-inflict command (`/character afflict`, a later
milestone) both read the catalog through `list_types` -- there's no seed
data, so both start out with nothing to apply/offer until staff add some.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared.db.models import AfflictionType
from panem_shared.enums import AfflictionStat
from panem_shared.errors import NotFound, ValidationFailed

NAME_MAX_LEN = 64
DESCRIPTION_MAX_LEN = 400
ALLOWED_STATS = frozenset(s.value for s in AfflictionStat)


async def list_types(session: AsyncSession) -> list[AfflictionType]:
    result = await session.execute(select(AfflictionType).order_by(AfflictionType.name))
    return list(result.scalars().all())


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > NAME_MAX_LEN:
        raise ValidationFailed("invalid_affliction_type_name")
    return name


def _clean_description(description: str) -> str:
    description = description.strip()
    if len(description) > DESCRIPTION_MAX_LEN:
        raise ValidationFailed("invalid_affliction_type_description")
    return description


async def _ensure_name_available(
    session: AsyncSession, name: str, *, exclude_id: int | None = None
) -> None:
    """A friendly `ValidationFailed` instead of a raw `IntegrityError` off
    `AfflictionType.name`'s unique constraint on the (rare) simultaneous-
    submission race -- same reasoning/shape as `characters.ensure_name_
    available`."""
    stmt = (
        select(func.count())
        .select_from(AfflictionType)
        .where(func.lower(AfflictionType.name) == name.lower())
    )
    if exclude_id is not None:
        stmt = stmt.where(AfflictionType.id != exclude_id)
    count = (await session.execute(stmt)).scalar_one()
    if count > 0:
        raise ValidationFailed("affliction_type_name_taken")


def _validate_stat(stat: str | None, *, reason_key: str) -> None:
    if stat is not None and stat not in ALLOWED_STATS:
        raise ValidationFailed(reason_key)


def _validate_cure_and_auto_apply(
    *,
    is_permanent: bool,
    cure_stat: str | None,
    cure_threshold: float | None,
    auto_apply_stat: str | None,
    auto_apply_threshold: float | None,
) -> None:
    _validate_stat(cure_stat, reason_key="invalid_affliction_cure_stat")
    _validate_stat(auto_apply_stat, reason_key="invalid_affliction_auto_apply_stat")
    if is_permanent:
        # "Null/null alongside is_permanent=True means incurable" --
        # `AfflictionType`'s own docstring. A permanent type can still
        # auto-apply (e.g. a scar that never heals but still gets handed
        # out automatically), just never cure.
        if cure_stat is not None or cure_threshold is not None:
            raise ValidationFailed("permanent_affliction_cannot_have_cure")
    elif (cure_stat is None) != (cure_threshold is None):
        raise ValidationFailed("affliction_cure_fields_incomplete")
    if (auto_apply_stat is None) != (auto_apply_threshold is None):
        raise ValidationFailed("affliction_auto_apply_fields_incomplete")


async def create_type(
    session: AsyncSession,
    *,
    name: str,
    description: str,
    is_permanent: bool,
    cure_stat: str | None,
    cure_threshold: float | None,
    auto_apply_stat: str | None,
    auto_apply_threshold: float | None,
) -> AfflictionType:
    _validate_cure_and_auto_apply(
        is_permanent=is_permanent,
        cure_stat=cure_stat,
        cure_threshold=cure_threshold,
        auto_apply_stat=auto_apply_stat,
        auto_apply_threshold=auto_apply_threshold,
    )
    clean_name = _clean_name(name)
    await _ensure_name_available(session, clean_name)
    row = AfflictionType(
        name=clean_name,
        description=_clean_description(description),
        is_permanent=is_permanent,
        cure_stat=cure_stat,
        cure_threshold=cure_threshold,
        auto_apply_stat=auto_apply_stat,
        auto_apply_threshold=auto_apply_threshold,
    )
    session.add(row)
    await session.flush()
    return row


async def update_type(
    session: AsyncSession,
    affliction_type_id: int,
    *,
    name: str | None,
    description: str | None,
    is_permanent: bool,
    cure_stat: str | None,
    cure_threshold: float | None,
    auto_apply_stat: str | None,
    auto_apply_threshold: float | None,
) -> AfflictionType:
    """`name`/`description` are optional-partial (`None` leaves them
    unchanged, same as `layers.update_category`'s `name`/`z_index`) --
    but `is_permanent`/`cure_*`/`auto_apply_*` are always replaced
    together as one unit, since the staff admin panel's edit form always
    shows and resubmits the full cure/auto-apply condition rather than
    editing one half of a pair in isolation, which would risk leaving a
    stat set with no threshold (or vice versa) in the database."""
    row = await session.get(AfflictionType, affliction_type_id)
    if row is None:
        raise NotFound("affliction_type_not_found")
    _validate_cure_and_auto_apply(
        is_permanent=is_permanent,
        cure_stat=cure_stat,
        cure_threshold=cure_threshold,
        auto_apply_stat=auto_apply_stat,
        auto_apply_threshold=auto_apply_threshold,
    )
    if name is not None:
        clean_name = _clean_name(name)
        await _ensure_name_available(session, clean_name, exclude_id=affliction_type_id)
        row.name = clean_name
    if description is not None:
        row.description = _clean_description(description)
    row.is_permanent = is_permanent
    row.cure_stat = cure_stat
    row.cure_threshold = cure_threshold
    row.auto_apply_stat = auto_apply_stat
    row.auto_apply_threshold = auto_apply_threshold
    return row


async def delete_type(session: AsyncSession, affliction_type_id: int) -> None:
    row = await session.get(AfflictionType, affliction_type_id)
    if row is None:
        raise NotFound("affliction_type_not_found")
    await session.delete(row)
