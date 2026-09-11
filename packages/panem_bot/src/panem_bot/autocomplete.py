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
from panem_shared.db.models import Character
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


async def own_any(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return await _characters(interaction, current, own_only=True, statuses=None)


async def any_character(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await _characters(interaction, current, own_only=False, statuses=None)


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
