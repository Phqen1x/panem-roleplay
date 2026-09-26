"""`/bail` and `/lockpick` -- getting out of jail (contraband system)."""

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
from panem_bot.services import jail as jail_svc
from panem_bot.strings import t
from panem_shared import constants
from panem_shared.db.models import Character, WorldClock


class JailCog(commands.Cog):
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

    @app_commands.command(name="bail", description="Pay to get a jailed character out right now")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def bail(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            current_tick = await self._current_tick(session)
            try:
                cost = jail_svc.pay_bail(char, current_tick)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            name = char.name
        await interaction.response.send_message(t("bail_ok", name=name, cost=cost), ephemeral=True)

    async def _resolve_lockpick_text(self, char_id: int) -> str:
        """The RNG-fallback skill check (no Activity configured, or the
        player hits Skip) -- shared by the instant path below and the
        launch message's Skip button."""
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await session.get(Character, char_id)
            if char is None:
                return t("character_not_found")
            current_tick = await self._current_tick(session)
            try:
                success = jail_svc.attempt_lockpick(char, current_tick, rng=random.Random())
            except ServiceError as exc:
                return t(exc.reason_key, **exc.fmt)
            name = char.name
            tries_left = constants.LOCKPICK_MAX_TRIES - char.jail_lockpick_tries_used

        if success:
            return t("lockpick_success", name=name)
        return t("lockpick_fail", name=name, tries_left=tries_left)

    @app_commands.command(name="lockpick", description="Try to pick the lock and escape jail")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def lockpick(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            current_tick = await self._current_tick(session)
            try:
                jail_svc.check_can_attempt_lockpick(char, current_tick)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            char_id, name = char.id, char.name

        activity_url = self.bot.settings.activity_public_url  # type: ignore[attr-defined]
        if not activity_url:
            text = await self._resolve_lockpick_text(char_id)
            await interaction.response.send_message(text, ephemeral=True)
            return

        attempt_id = activity_launch.new_attempt_id()
        await activity_launch.create_crime_attempt(
            self.bot, attempt_id, {"kind": "lockpick", "character_id": char_id}
        )

        async def on_skip(skip_interaction: discord.Interaction) -> None:
            await activity_launch.forget_crime_attempt(self.bot, attempt_id)
            text = await self._resolve_lockpick_text(char_id)
            await skip_interaction.response.edit_message(
                content=t("lockpick_already_tried"), view=None
            )
            await skip_interaction.followup.send(text, ephemeral=True)

        view = activity_launch.crime_launch_view(activity_url, "lockpick", attempt_id, on_skip)
        await interaction.response.send_message(
            t("lockpick_game_ready", name=name), view=view, ephemeral=True
        )
        await activity_launch.remember_crime_interaction(self.bot, attempt_id, interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JailCog(bot))
