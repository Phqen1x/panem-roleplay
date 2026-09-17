"""`/steal` -- pickpocketing players and NPCs (contraband system)."""

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
from panem_bot.services import stealing as stealing_svc
from panem_bot.strings import t
from panem_shared import constants
from panem_shared.db.models import Character, Npc, WorldClock
from panem_shared.enums import CharacterStatus


class StealingCog(commands.Cog):
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

    async def _resolve_target(
        self, session: AsyncSession, char: Character, target_name: str
    ) -> Character | Npc | None:
        """A player character (anyone's but the thief's own) at the same
        location first, then an NPC -- matching `/talk`'s own free-typed
        name resolution against the district's residents."""
        target_char = (
            await session.execute(
                select(Character).where(
                    Character.name == target_name,
                    Character.status == CharacterStatus.APPROVED.value,
                    Character.current_district_id == char.current_district_id,
                    Character.location_id == char.location_id,
                    Character.id != char.id,
                )
            )
        ).scalar_one_or_none()
        if target_char is not None:
            return target_char
        return (
            await session.execute(
                select(Npc).where(
                    Npc.name == target_name,
                    Npc.district_id == char.current_district_id,
                    Npc.location_id == char.location_id,
                )
            )
        ).scalar_one_or_none()

    @app_commands.command(name="steal", description="Try to pickpocket a player or NPC")
    @app_commands.describe(character="Character name", target="Who to steal from")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def steal(self, interaction: discord.Interaction, character: str, target: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            victim = await self._resolve_target(session, char, target)
            if victim is None:
                await interaction.response.send_message(t("steal_target_not_found"), ephemeral=True)
                return

            current_tick = await self._current_tick(session)
            try:
                result = await stealing_svc.resolve_steal(
                    session,
                    character=char,
                    victim=victim,
                    district_id=char.current_district_id,
                    current_tick=current_tick,
                    rng=random.Random(),
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            name, target_name = char.name, victim.name

        if result.success:
            text = t("steal_ok", name=name, amount=result.amount, target=target_name)
        elif result.caught:
            text = t(
                "steal_caught",
                name=name,
                target=target_name,
                fine=constants.STEAL_FINE,
                jail_ticks=constants.STEAL_JAIL_TICKS,
            )
        elif result.alerted:
            text = t("steal_alerted_escape", name=name, target=target_name)
        else:
            text = t("steal_miss", name=name)
        await interaction.response.send_message(text, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StealingCog(bot))
