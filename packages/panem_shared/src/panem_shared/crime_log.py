"""The `/steal`/`/burgle`/`/poach`/`/shipment` activity log (`CrimeLog`) --
one row per resolved attempt, written from the single funnel every attempt
already passes through (`panem_shared.stealing.apply_steal_outcome`/
`apply_burgle_outcome`, `panem_shared.poaching.apply_poach_outcome`,
`panem_shared.shipments.apply_shipment_outcome`), so both the RNG-fallback
roll and the Activity minigame's own result log identically without either
call site needing to remember to do it.

Lives here (not `panem_bot`) for the same reason every other contraband-
system module already moved: `panem_api`'s crime-attempt result endpoint
resolves the same outcomes and needs the same log, and `panem_api` cannot
depend on `panem_bot` to get it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared.db.models import CrimeLog

DEFAULT_LOG_LIMIT = 20


async def record_crime_log(
    session: AsyncSession,
    *,
    character_id: int,
    kind: str,
    tick: int,
    success: bool,
    caught: bool,
    target_name: str | None = None,
    good_name: str | None = None,
    amount: int = 0,
) -> None:
    session.add(
        CrimeLog(
            character_id=character_id,
            kind=kind,
            tick=tick,
            success=success,
            caught=caught,
            target_name=target_name,
            good_name=good_name,
            amount=amount,
        )
    )
    await session.flush()


async def list_crime_log(
    session: AsyncSession, character_id: int, *, limit: int = DEFAULT_LOG_LIMIT
) -> list[CrimeLog]:
    """Most recent attempts first."""
    rows = await session.execute(
        select(CrimeLog)
        .where(CrimeLog.character_id == character_id)
        .order_by(CrimeLog.id.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())
