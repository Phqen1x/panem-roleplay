"""`/eat`, `/drink`, `/entertain` -- Simulation mode's proactive meter
relief, alongside `/sleep` (`panem_bot.cogs.housing`) for fatigue. Kept as
its own small cog rather than folded into `housing.py`: these three
commands aren't part of the housing/property system at all, they're a
sibling need-relief mechanic that happens to share `/sleep`'s "Simulation
mode, once per sim-day" shape.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete
from panem_bot.errors import ServiceError
from panem_bot.services import afflictions as afflictions_svc
from panem_bot.services import characters as characters_svc
from panem_bot.services import sustenance as sustenance_svc
from panem_bot.strings import t
from panem_shared.db.models import Character, WorldClock


class NeedsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _get_character(
        self, session: AsyncSession, user_id: int, name: str
    ) -> Character | None:
        user = await characters_svc.get_or_create_user(session, user_id)
        return (
            await session.execute(
                select(Character).where(Character.user_id == user.id, Character.name == name)
            )
        ).scalar_one_or_none()

    async def _current_tick(self, session: AsyncSession) -> int:
        clock = await session.get(WorldClock, 1)
        return clock.tick if clock is not None else 0

    async def _cured_note(self, session: AsyncSession, char: Character) -> str:
        cured = await afflictions_svc.check_and_cure(session, char)
        if not cured:
            return ""
        names = ", ".join(row.affliction_type.name for row in cured)
        return f" Also cured: {names}."

    @app_commands.command(name="eat", description="Eat to relieve hunger (Simulation mode)")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def eat(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            current_tick = await self._current_tick(session)
            try:
                hunger = sustenance_svc.eat(char, current_tick)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            note = await self._cured_note(session, char)
            name = char.name
        await interaction.response.send_message(
            t("eat_ok", name=name, hunger=round(hunger)) + note, ephemeral=True
        )

    @app_commands.command(name="drink", description="Drink to relieve thirst (Simulation mode)")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def drink(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            current_tick = await self._current_tick(session)
            try:
                thirst = sustenance_svc.drink(char, current_tick)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            note = await self._cured_note(session, char)
            name = char.name
        await interaction.response.send_message(
            t("drink_ok", name=name, thirst=round(thirst)) + note, ephemeral=True
        )

    @app_commands.command(
        name="entertain", description="Entertain yourself to relieve sanity loss (Simulation mode)"
    )
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def entertain(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            current_tick = await self._current_tick(session)
            try:
                sanity = sustenance_svc.entertain(char, current_tick)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            note = await self._cured_note(session, char)
            name = char.name
        await interaction.response.send_message(
            t("entertain_ok", name=name, sanity=round(sanity)) + note, ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NeedsCog(bot))
