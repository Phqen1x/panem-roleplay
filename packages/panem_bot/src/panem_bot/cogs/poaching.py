"""`/poach` -- illegal hunting/gathering at a district's outskirts."""

from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete
from panem_bot.errors import ServiceError
from panem_bot.services import characters as characters_svc
from panem_bot.services import poaching as poaching_svc
from panem_bot.strings import t
from panem_shared import constants
from panem_shared.db.models import Character, WorldClock


class PoachingCog(commands.Cog):
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

    @app_commands.command(
        name="poach", description="Try to poach food at your district's outskirts"
    )
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def poach(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            content = self.bot.content  # type: ignore[attr-defined]
            district = content.district(char.current_district_id)
            current_tick = await self._current_tick(session)
            try:
                result = await poaching_svc.resolve_poach(
                    session,
                    character=char,
                    district=district,
                    goods=content.goods,
                    current_tick=current_tick,
                    rng=random.Random(),
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            name = char.name

        if result.caught:
            text = t(
                "poach_caught",
                name=name,
                fine=constants.POACH_FINE,
                jail_ticks=constants.POACH_JAIL_TICKS,
            )
        else:
            assert result.good is not None
            text = t("poach_ok", name=name, qty=constants.POACH_YIELD_QTY, good=result.good.name)
        await interaction.response.send_message(text, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PoachingCog(bot))
