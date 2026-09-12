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
from sqlalchemy import or_, select

from panem_bot.services import characters as characters_svc
from panem_shared.content.errors import ContentValidationError
from panem_shared.db.models import Character, Npc
from panem_shared.enums import CharacterStatus

MAX_CHOICES = 25

#: Sentinel staff type into a clearable text field to blank it back out
#: (see `panem_bot.services.jobs`); surfaced here as a suggested choice.
CLEAR_TEXT = "none"

#: Sentinel staff type into a clearable numeric field to blank it back out.
CLEAR_NUMBER = -1.0


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


async def job_ids(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    all_jobs = await bot.all_jobs()  # type: ignore[attr-defined]
    current_lower = current.lower()
    matches = [
        job
        for job in all_jobs.values()
        if current_lower in job.id.lower() or current_lower in job.title.lower()
    ]
    matches.sort(key=lambda job: job.id)
    return [
        app_commands.Choice(
            name=f"{job.id} - {job.title} ({bot.content.district(job.district).name})",  # type: ignore[attr-defined]
            value=job.id,
        )
        for job in matches[:MAX_CHOICES]
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


def _with_clear_choice(
    choices: list[app_commands.Choice[str]], current: str
) -> list[app_commands.Choice[str]]:
    """Offers the `CLEAR_TEXT` sentinel as a suggestion (never as the only
    allowed value -- dynamic autocomplete lists don't restrict input)."""
    if CLEAR_TEXT.startswith(current.strip().lower()):
        return [
            app_commands.Choice(name=f"{CLEAR_TEXT} (clear)", value=CLEAR_TEXT),
            *choices[: MAX_CHOICES - 1],
        ]
    return choices[:MAX_CHOICES]


async def job_ids_clearable(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Like `job_ids`, plus the `CLEAR_TEXT` sentinel -- for fields that
    reference another job id and can be blanked back out (e.g. ladder_next)."""
    return _with_clear_choice(await job_ids(interaction, current), current)


async def job_workplace(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Locations in whichever district the command's `district` argument is
    currently set to; empty until that argument is filled in."""
    bot = interaction.client
    district_id = interaction.namespace.district
    if district_id is None:
        return []
    try:
        district = bot.content.district(district_id)  # type: ignore[attr-defined]
    except ContentValidationError:
        return []
    current_lower = current.lower()
    matches = [
        loc
        for loc in district.locations
        if current_lower in loc.id.lower() or current_lower in loc.name.lower()
    ]
    return [
        app_commands.Choice(name=f"{loc.id} - {loc.name}", value=loc.id)
        for loc in matches[:MAX_CHOICES]
    ]


async def job_foreman(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """NPCs (scoped to the command's `district` argument, if set) that could
    run a job, plus the `CLEAR_TEXT` sentinel."""
    bot = interaction.client
    district_id = interaction.namespace.district
    stmt = select(Npc.id, Npc.name)
    if district_id is not None:
        stmt = stmt.where(Npc.district_id == district_id)
    if current:
        stmt = stmt.where(or_(Npc.name.ilike(f"%{current}%"), Npc.id.ilike(f"%{current}%")))
    async with bot.db() as session:  # type: ignore[attr-defined]
        rows = (await session.execute(stmt.order_by(Npc.name).limit(MAX_CHOICES))).all()
    choices = [
        app_commands.Choice(name=f"{name} ({npc_id})", value=npc_id) for npc_id, name in rows
    ]
    return _with_clear_choice(choices, current)


async def clearable_number(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[float]]:
    """No fixed set of valid values, but surfaces the `CLEAR_NUMBER` sentinel
    used to blank an optional numeric field back out (min_reputation,
    peacekeeper_attention) -- doesn't restrict what can actually be typed."""
    choices = [app_commands.Choice(name=f"{CLEAR_NUMBER:g} (clear)", value=CLEAR_NUMBER)]
    if current:
        try:
            typed = float(current)
        except ValueError:
            return choices
        if typed != CLEAR_NUMBER:
            choices.insert(0, app_commands.Choice(name=str(typed), value=typed))
    return choices
