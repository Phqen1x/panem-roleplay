"""Staff action logging (Spec §3.1 FR-CHR-3, FR-PRX-4; used by every staff
command per Plan §10)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared.db.models import StaffAction
from panem_shared.logging import get_logger

if TYPE_CHECKING:
    from panem_bot.bot import PanemBot

logger = get_logger(component="staff")


async def log_staff_action(
    session: AsyncSession,
    *,
    staff_discord_id: int,
    action: str,
    target: str,
    payload: dict[str, object] | None = None,
    bot: PanemBot | None = None,
) -> StaffAction:
    row = StaffAction(
        staff_discord_id=staff_discord_id,
        action=action,
        target=target,
        payload=payload or {},
    )
    session.add(row)
    await session.flush()
    if bot is not None:
        await _post_to_log_channel(bot, row)
    return row


async def _post_to_log_channel(bot: PanemBot, row: StaffAction) -> None:
    """Every staff action already lands in the `staff_actions` table
    (queryable, but not something anyone's watching live); this also
    posts a one-line summary to `Settings.log_channel_id` so staff
    moderation is actually visible in Discord as it happens, not just
    on request. A missing/misconfigured log channel is a no-op, not an
    error -- staff actions must never fail because logging did."""
    channel = bot.get_channel(bot.settings.log_channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    detail = f" `{row.payload}`" if row.payload else ""
    try:
        await channel.send(
            f"**Staff action:** <@{row.staff_discord_id}> `{row.action}` → `{row.target}`{detail}"
        )
    except discord.HTTPException:
        logger.exception("staff_action_log_post_failed", action=row.action)
