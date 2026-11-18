"""Bot-side helper for `panem_shared.feature_flags`: resolving which
characters' *owners* currently lack access to a tab, for a background
loop (`cogs/weather.py`'s exposure/property damage, ...) to exclude them
from a passive effect the same way a disabled/staff-only tab already
excludes them from its own UI and commands. Needs `discord.Member`/
`bot.is_staff` to resolve "is this owner staff", which `panem_shared` has
no access to (no bot/gateway connection there) -- that's the one reason
this doesn't just live in `panem_shared.feature_flags` alongside
everything else it builds on.
"""

from __future__ import annotations

from collections.abc import Iterable

import discord
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import feature_flags as feature_flags_svc
from panem_shared.db.models import Character, User


def _find_member(bot: commands.Bot, discord_id: int) -> discord.Member | None:
    for guild in bot.guilds:
        member = guild.get_member(discord_id)
        if member is not None:
            return member
    return None


async def disallowed_character_ids(
    bot: commands.Bot,
    session: AsyncSession,
    tab_name: str,
    character_ids: Iterable[int],
) -> frozenset[int]:
    """The subset of `character_ids` whose owning user currently lacks
    access to `tab_name` -- empty whenever the tab is ENABLED (the
    overwhelmingly common case), so a caller about to touch a handful of
    characters only pays for the owner lookup once beta-gating is
    actually in use. Meant to be called once per poll pass, not once per
    character -- `tab_name`'s state can't change mid-pass, so there's no
    reason to re-fetch it per character."""
    state = await feature_flags_svc.get_state(session, feature_flags_svc.tab_key(tab_name))
    if state == feature_flags_svc.FlagState.ENABLED:
        return frozenset()
    ids = list({cid for cid in character_ids})
    if not ids:
        return frozenset()
    rows = (
        await session.execute(
            select(Character.id, User.discord_id)
            .join(User, User.id == Character.user_id)
            .where(Character.id.in_(ids))
        )
    ).all()
    disallowed: set[int] = set()
    for character_id, discord_id in rows:
        member = _find_member(bot, discord_id)
        is_staff = member is not None and await bot.is_staff(member)  # type: ignore[attr-defined]
        if not feature_flags_svc.is_allowed(state, is_staff=is_staff):
            disallowed.add(character_id)
    return frozenset(disallowed)
