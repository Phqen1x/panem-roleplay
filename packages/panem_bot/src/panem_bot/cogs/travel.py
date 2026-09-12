"""`/travel` and `/where` (Spec FR-LOC-2, CMD-14/15). Intra-district
travel only; cross-district travel is Milestone D scope."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete
from panem_bot.errors import NotAllowed, NotFound
from panem_bot.services import characters as characters_svc
from panem_bot.services import travel as travel_svc
from panem_bot.strings import t
from panem_shared.db.models import Character, WorldClock
from panem_shared.simtime import current as current_sim_time
from panem_shared.simtime import ticks_until_next_phase


class TravelCog(commands.Cog):
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

    @app_commands.command(
        name="travel", description="Move your character to a different location in their district"
    )
    @app_commands.describe(character="Character name", location="Destination location")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def travel(self, interaction: discord.Interaction, character: str, location: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            district = self.bot.content.district(char.current_district_id)  # type: ignore[attr-defined]
            try:
                loc = travel_svc.resolve_location(district, location)
                travel_svc.check_can_travel(character=char, location=loc)
            except (NotFound, NotAllowed) as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

            char.location_id = loc.id
            placed = travel_svc.place(district, loc)
            if placed is not None:
                char.x, char.y = placed
            name, location_name = char.name, loc.name

        await interaction.response.send_message(
            t("travel_ok", name=name, location=location_name), ephemeral=True
        )

    @travel.autocomplete("location")
    async def travel_location_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        character_name = getattr(interaction.namespace, "character", None)
        if not character_name:
            return []
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character_name)
        if char is None:
            return []
        district = self.bot.content.district(char.current_district_id)  # type: ignore[attr-defined]
        current_lower = current.lower()
        matches = [
            loc
            for loc in district.locations
            if current_lower in loc.name.lower() or current_lower in loc.id.lower()
        ]
        return [
            app_commands.Choice(name=f"{loc.name} ({loc.id})", value=loc.id) for loc in matches[:25]
        ]

    @app_commands.command(name="where", description="Show where your character currently is")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def where(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            if char.location_id is None:
                await interaction.response.send_message(
                    t("no_location_set", name=char.name), ephemeral=True
                )
                return

            district = self.bot.content.district(char.current_district_id)  # type: ignore[attr-defined]
            location = next((loc for loc in district.locations if loc.id == char.location_id), None)
            location_name = location.name if location else char.location_id
            name, district_name = char.name, district.name

        await interaction.response.send_message(
            t("where_ok", name=name, location=location_name, district=district_name),
            ephemeral=True,
        )

    @app_commands.command(name="time", description="Show the current in-world day, phase, and tick")
    async def time(self, interaction: discord.Interaction) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            clock = await session.get(WorldClock, 1)
            persisted_tick = clock.tick if clock is not None else 0

        tick, phase, day, month = current_sim_time(persisted_tick)
        until_next = ticks_until_next_phase(tick)
        embed = discord.Embed(title="The Long Year")
        embed.add_field(name="Month", value=str(month))
        embed.add_field(name="Day", value=str(day))
        embed.add_field(name="Phase", value=phase.value.capitalize())
        embed.add_field(name="Tick", value=str(tick))
        embed.add_field(
            name="Next phase in",
            value="now" if until_next == 0 else f"{until_next} tick(s)",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TravelCog(bot))
