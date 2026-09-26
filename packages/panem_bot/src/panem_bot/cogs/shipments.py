"""`/shipment` -- rob a contraband shipment sitting at a district's Rail
Station before peacekeepers clear it out."""

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
from panem_bot.services import shipments as shipments_svc
from panem_bot.strings import t
from panem_shared import constants
from panem_shared.db.models import Character, DistrictState, Shipment, WorldClock


def _shipment_result_text(result: shipments_svc.ShipmentResult, name: str) -> str:
    if result.success:
        return t("shipment_ok", name=name, amount=result.amount, good=result.good_name)
    if result.caught:
        return t(
            "shipment_caught",
            name=name,
            fine=constants.SHIPMENT_FINE,
            jail_ticks=constants.SHIPMENT_JAIL_TICKS,
        )
    if result.alerted:
        return t("shipment_alerted_escape", name=name)
    return t("shipment_miss", name=name)


class ShipmentsCog(commands.Cog):
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

    async def _resolve_shipment_text(
        self, char_id: int, shipment_id: int, district_id: int, current_tick: int
    ) -> str:
        """The RNG-fallback skill check (no Activity configured, or the
        player hits Skip) -- shared by the instant path below and the
        launch message's Skip button."""
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await session.get(Character, char_id)
            if char is None:
                return t("character_not_found")
            shipment = await session.get(Shipment, shipment_id)
            if shipment is None:
                return t("shipment_gone")
            district_row = await session.get(DistrictState, district_id)
            result = await shipments_svc.roll_and_apply_shipment(
                session,
                character=char,
                shipment=shipment,
                district_row=district_row,
                current_tick=current_tick,
                rng=random.Random(),
                goods=self.bot.content.goods,  # type: ignore[attr-defined]
            )
            name = char.name
        return _shipment_result_text(result, name)

    @app_commands.command(
        name="shipment", description="Try to rob a contraband shipment at your location"
    )
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def shipment(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            current_tick = await self._current_tick(session)
            found = await shipments_svc.find_shipment_here(session, char, current_tick)
            if found is None:
                await interaction.response.send_message(
                    t("shipment_none_here"), ephemeral=True
                )
                return

            try:
                shipments_svc.check_can_steal_shipment(char, found, current_tick)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            char.last_steal_tick = current_tick
            char_id, char_name = char.id, char.name
            shipment_id, district_id = found.id, found.district_id

        activity_url = self.bot.settings.activity_public_url  # type: ignore[attr-defined]
        if not activity_url:
            text = await self._resolve_shipment_text(char_id, shipment_id, district_id, current_tick)
            await interaction.response.send_message(text, ephemeral=True)
            return

        attempt_id = activity_launch.new_attempt_id()
        await activity_launch.create_crime_attempt(
            self.bot,
            attempt_id,
            {
                "kind": "shipment",
                "character_id": char_id,
                "shipment_id": shipment_id,
                "district_id": district_id,
                "current_tick": current_tick,
            },
        )

        async def on_skip(skip_interaction: discord.Interaction) -> None:
            await activity_launch.forget_crime_attempt(self.bot, attempt_id)
            text = await self._resolve_shipment_text(
                char_id, shipment_id, district_id, current_tick
            )
            await skip_interaction.response.edit_message(
                content=t("shipment_already_tried"), view=None
            )
            await skip_interaction.followup.send(text, ephemeral=True)

        view = activity_launch.crime_launch_view(activity_url, "shipment", attempt_id, on_skip)
        await interaction.response.send_message(
            t("shipment_game_ready", name=char_name), view=view, ephemeral=True
        )
        await activity_launch.remember_crime_interaction(self.bot, attempt_id, interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShipmentsCog(bot))
