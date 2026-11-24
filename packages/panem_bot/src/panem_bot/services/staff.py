"""Staff action logging (Spec §3.1 FR-CHR-3, FR-PRX-4; used by every staff
command per Plan §10)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared.db.models import StaffAction
from panem_shared.logging import get_logger
from panem_shared.staff_log import build_staff_log_embed

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
    target_label: str | None = None,
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
        await _post_to_log_channel(bot, row, target_label=target_label)
    return row


async def _post_to_log_channel(
    bot: PanemBot, row: StaffAction, *, target_label: str | None
) -> None:
    """Every staff action already lands in the `staff_actions` table
    (queryable, but not something anyone's watching live); this also
    posts a human-readable embed summary to `Settings.log_channel_id` so
    staff moderation is actually visible in Discord as it happens, not
    just on request -- see `panem_shared.staff_log` for why the
    formatting itself lives there rather than here. A missing/
    misconfigured log channel is a no-op, not an error -- staff actions
    must never fail because logging did."""
    channel = bot.get_channel(bot.settings.log_channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    embed = discord.Embed.from_dict(
        build_staff_log_embed(
            action=row.action,
            staff_discord_id=row.staff_discord_id,
            target=row.target,
            payload=row.payload,
            target_label=target_label,
        )
    )
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        logger.exception("staff_action_log_post_failed", action=row.action)
