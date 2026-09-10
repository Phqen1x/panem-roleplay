"""Staff action logging (Spec §3.1 FR-CHR-3, FR-PRX-4; used by every staff
command per Plan §10)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared.db.models import StaffAction


async def log_staff_action(
    session: AsyncSession,
    *,
    staff_discord_id: int,
    action: str,
    target: str,
    payload: dict | None = None,
) -> StaffAction:
    row = StaffAction(
        staff_discord_id=staff_discord_id,
        action=action,
        target=target,
        payload=payload or {},
    )
    session.add(row)
    await session.flush()
    return row
