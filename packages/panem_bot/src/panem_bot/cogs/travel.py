"""`/travel` and `/where` (Spec FR-LOC-2/7/8/9, CMD-14/14b/15)."""

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
from panem_shared import constants
from panem_shared.db.models import Character, WorldClock
from panem_shared.simtime import clock_string, seconds_until_next_tick
from panem_shared.simtime import current as current_sim_time


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
        name="travel",
        description="Move within your district, or board a train to another district",
    )
    @app_commands.describe(
        character="Character name",
        location="Destination location in your current district",
        district="Destination district number (0 = The Capitol) -- boards a train from a station",
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def travel(
        self,
        interaction: discord.Interaction,
        character: str,
        location: str | None = None,
        district: app_commands.Range[int, 0, 12] | None = None,
    ) -> None:
        if (location is None) == (district is None):
            await interaction.response.send_message(t("travel_pick_one"), ephemeral=True)
            return
        if district is not None:
            await self._travel_district(interaction, character, district)
            return
        await self._travel_location(interaction, character, location)  # type: ignore[arg-type]

    async def _travel_location(
        self, interaction: discord.Interaction, character: str, location: str
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            district_content = self.bot.content.district(char.current_district_id)  # type: ignore[attr-defined]
            clock = await session.get(WorldClock, 1)
            current_tick = clock.tick if clock is not None else 0
            try:
                loc = travel_svc.resolve_location(district_content, location)
                travel_svc.check_can_travel(character=char, location=loc, current_tick=current_tick)
            except (NotFound, NotAllowed) as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

            char.location_id = loc.id
            placed = travel_svc.place(district_content, loc)
            if placed is not None:
                char.x, char.y = placed
            name, location_name = char.name, loc.name

        await interaction.response.send_message(
            t("travel_ok", name=name, location=location_name), ephemeral=True
        )

    async def _travel_district(
        self, interaction: discord.Interaction, character: str, destination_id: int
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            clock = await session.get(WorldClock, 1)
            current_tick = clock.tick if clock is not None else 0
            origin_district = self.bot.content.district(char.current_district_id)  # type: ignore[attr-defined]

            try:
                travel_svc.check_can_travel_district(
                    character=char,
                    district=origin_district,
                    destination_id=destination_id,
                    current_tick=current_tick,
                )
                if not travel_svc.is_free_route(char, origin_district.id, destination_id):
                    await travel_svc.spend_transport(session, char)
            except (NotFound, NotAllowed) as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

            destination_district = self.bot.content.district(destination_id)  # type: ignore[attr-defined]
            char.in_transit_until_tick = current_tick + constants.TRANSIT_TICKS
            char.transit_destination_id = destination_id
            if char.current_district_id == char.district_id:
                char.away_since_tick = current_tick
            name, destination_name = char.name, destination_district.name

        await interaction.response.send_message(
            t(
                "travel_district_ok",
                name=name,
                district=destination_name,
                ticks=constants.TRANSIT_TICKS,
            ),
            ephemeral=True,
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

    @travel.autocomplete("district")
    async def travel_district_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        current_lower = current.lower()
        matches = [
            d
            for d in self.bot.content.districts.values()  # type: ignore[attr-defined]
            if current_lower in d.name.lower() or current_lower in str(d.id)
        ]
        matches.sort(key=lambda d: d.id)
        return [app_commands.Choice(name=d.name, value=d.id) for d in matches[:25]]

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

    @app_commands.command(name="time", description="Show the current in-world day and time")
    async def time(self, interaction: discord.Interaction) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            clock = await session.get(WorldClock, 1)
            persisted_tick = clock.tick if clock is not None else 0
            updated_at = clock.updated_at if clock is not None else None

        tick, phase, day, month = current_sim_time(persisted_tick)
        remaining = "unknown"
        if updated_at is not None:
            seconds = seconds_until_next_tick(
                updated_at,
                self.bot.settings.tick_interval_seconds,  # type: ignore[attr-defined]
            )
            remaining = "any moment now" if seconds <= 0 else f"{round(seconds)}s"

        embed = discord.Embed(title="The Long Year")
        embed.add_field(name="Month", value=str(month))
        embed.add_field(name="Day", value=str(day))
        embed.add_field(name="Time", value=clock_string(tick))
        embed.add_field(name="Phase", value=phase.value.capitalize())
        embed.add_field(name="Time changes in", value=remaining)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TravelCog(bot))
