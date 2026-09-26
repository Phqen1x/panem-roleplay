"""`/poach` -- illegal hunting/gathering at a district's outskirts."""

from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import activity_launch, autocomplete
from panem_bot.errors import ServiceError
from panem_bot.services import characters as characters_svc
from panem_bot.services import poaching as poaching_svc
from panem_bot.strings import t
from panem_shared import constants
from panem_shared.content.schemas import Good
from panem_shared.db.models import Character, WorldClock


def _poach_result_text(result: poaching_svc.PoachResult, name: str) -> str:
    if result.caught:
        return t(
            "poach_caught",
            name=name,
            fine=constants.POACH_FINE,
            jail_ticks=constants.POACH_JAIL_TICKS,
        )
    if result.good is None:
        return t("poach_miss", name=name)
    return t("poach_ok", name=name, qty=constants.POACH_YIELD_QTY, good=result.good.name)


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

    async def _resolve_poach_text(
        self, char_id: int, good_id: str, district_id: int, current_tick: int
    ) -> str:
        """The RNG-fallback skill check (no Activity configured, or the
        player hits Skip) -- shared by the instant path below and the
        launch message's Skip button."""
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await session.get(Character, char_id)
            if char is None:
                return t("character_not_found")
            content = self.bot.content  # type: ignore[attr-defined]
            good = content.goods[good_id]
            result = await poaching_svc.roll_and_apply_poach(
                session,
                character=char,
                good=good,
                district_id=district_id,
                current_tick=current_tick,
                rng=random.Random(),
            )
            name = char.name
        return _poach_result_text(result, name)

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
            good: Good
            try:
                good = poaching_svc.check_can_poach(char, district, content.goods, current_tick)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            char.last_poach_tick = current_tick
            char_id, char_name = char.id, char.name
            district_id, good_id = district.id, good.id

        activity_url = self.bot.settings.activity_public_url  # type: ignore[attr-defined]
        if not activity_url:
            text = await self._resolve_poach_text(char_id, good_id, district_id, current_tick)
            await interaction.response.send_message(text, ephemeral=True)
            return

        attempt_id = activity_launch.new_attempt_id()
        await activity_launch.create_crime_attempt(
            self.bot,
            attempt_id,
            {
                "kind": "poach",
                "character_id": char_id,
                "district_id": district_id,
                "good_id": good_id,
                "current_tick": current_tick,
            },
        )

        async def on_skip(skip_interaction: discord.Interaction) -> None:
            await activity_launch.forget_crime_attempt(self.bot, attempt_id)
            text = await self._resolve_poach_text(char_id, good_id, district_id, current_tick)
            await skip_interaction.response.edit_message(
                content=t("poach_already_tried"), view=None
            )
            await skip_interaction.followup.send(text, ephemeral=True)

        view = activity_launch.crime_launch_view(activity_url, "poach", attempt_id, on_skip)
        await interaction.response.send_message(
            t("poach_game_ready", name=char_name), view=view, ephemeral=True
        )
        await activity_launch.remember_crime_interaction(self.bot, attempt_id, interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PoachingCog(bot))
