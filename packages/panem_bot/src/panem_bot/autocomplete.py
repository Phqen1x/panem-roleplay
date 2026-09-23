"""Shared `app_commands` autocomplete callbacks.

Anything a player or staff member would otherwise have to type blind and
get exactly right (a character name, a job id, a district) gets a
suggestion list here instead. District- or scene-scoped cases (which need
a bound `self` to resolve context) live next to their commands in
`cogs/scenes.py`; everything else lives here so it isn't duplicated across
`cogs/characters.py` and `cogs/staff.py`.
"""

from __future__ import annotations

import discord
from discord import app_commands
from sqlalchemy import select

from panem_bot.services import characters as characters_svc
from panem_shared.db.models import AfflictionType, Character, Npc
from panem_shared.enums import CharacterStatus

MAX_CHOICES = 25


async def _characters(
    interaction: discord.Interaction,
    current: str,
    *,
    own_only: bool,
    statuses: list[str] | None,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    async with bot.db() as session:  # type: ignore[attr-defined]
        stmt = select(Character.name, Character.district_id, Character.status)
        if own_only:
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            stmt = stmt.where(Character.user_id == user.id)
        if statuses:
            stmt = stmt.where(Character.status.in_(statuses))
        if current:
            stmt = stmt.where(Character.name.ilike(f"%{current}%"))
        rows = (await session.execute(stmt.order_by(Character.name).limit(MAX_CHOICES))).all()
    return [
        app_commands.Choice(
            name=f"{name} ({bot.content.district(district_id).name}, {status})",  # type: ignore[attr-defined]
            value=name,
        )
        for name, district_id, status in rows
    ]


async def own_pending(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await _characters(
        interaction, current, own_only=True, statuses=[CharacterStatus.PENDING.value]
    )


async def own_approved(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await _characters(
        interaction, current, own_only=True, statuses=[CharacterStatus.APPROVED.value]
    )


async def any_approved(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await _characters(
        interaction, current, own_only=False, statuses=[CharacterStatus.APPROVED.value]
    )


async def any_pending(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await _characters(
        interaction, current, own_only=False, statuses=[CharacterStatus.PENDING.value]
    )


async def any_npc(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Any NPC in any district, searched by name -- for staff commands
    (`/staff npc ...`), which need to reach any resident, not just ones
    the invoking staff member's own character shares a district with.

    The synthetic name pool (`panem_shared.content.names`) is sampled
    independently per district, so the same name showing up in two
    districts is expected, not a bug -- the choice's `value` is the
    NPC's actual unique `id`, not its name, so picking a suggestion
    always resolves to exactly the one NPC shown, never an ambiguous
    name shared by several."""
    bot = interaction.client
    async with bot.db() as session:  # type: ignore[attr-defined]
        stmt = select(Npc.id, Npc.name, Npc.district_id)
        if current:
            stmt = stmt.where(Npc.name.ilike(f"%{current}%"))
        rows = (await session.execute(stmt.order_by(Npc.name).limit(MAX_CHOICES))).all()
    return [
        app_commands.Choice(
            name=f"{name} ({bot.content.district(district_id).name})",  # type: ignore[attr-defined]
            value=npc_id,
        )
        for npc_id, name, district_id in rows
    ]


async def affliction_types(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """The staff-authored catalog (`panem_shared.affliction_types`, built
    through the dashboard's Staff tab) -- `/character afflict`'s `type`
    field. Choice `value` is the type's name (unique, same as a good/job
    id elsewhere in this module), which the cog re-looks-up by name at
    submit time rather than carrying a numeric id through the option."""
    bot = interaction.client
    async with bot.db() as session:  # type: ignore[attr-defined]
        stmt = select(AfflictionType.name, AfflictionType.is_permanent)
        if current:
            stmt = stmt.where(AfflictionType.name.ilike(f"%{current}%"))
        rows = (await session.execute(stmt.order_by(AfflictionType.name).limit(MAX_CHOICES))).all()
    return [
        app_commands.Choice(name=f"{name} (permanent)" if is_permanent else name, value=name)
        for name, is_permanent in rows
    ]


async def any_good(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Every good in the content catalog, regardless of district --
    `/trade offer`'s `give_good`/`want_good` fields aren't location-scoped
    the way `/market`'s are (a trade is between two characters, wherever
    they each are), so this searches the whole catalog rather than one
    district's `produces`/`imports` like `market.py`'s own autocomplete."""
    bot = interaction.client
    content = bot.content  # type: ignore[attr-defined]
    current_lower = current.lower()
    matches = [
        good
        for good_id, good in content.goods.items()
        if current_lower in good_id.lower() or current_lower in good.name.lower()
    ]
    matches.sort(key=lambda g: g.name)
    return [
        app_commands.Choice(name=f"{g.name} ({g.id})", value=g.id) for g in matches[:MAX_CHOICES]
    ]


async def districts(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    bot = interaction.client
    current_lower = current.lower()
    matches = [
        d
        for d in bot.content.districts.values()  # type: ignore[attr-defined]
        if current_lower in d.name.lower() or current in str(d.id)
    ]
    matches.sort(key=lambda d: d.id)
    return [
        app_commands.Choice(name=f"{d.id} - {d.name}", value=d.id) for d in matches[:MAX_CHOICES]
    ]
