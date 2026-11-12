"""`/rumor list` and `/rumor investigate` (Spec "Rumors"). The background
loop that occasionally spawns a free-floating rumor per district lives
here too; a rumor tied to a real upcoming weather/global event is
spawned directly by whatever created that event instead (`panem_bot.
cogs.weather`'s poll, `/staff event create`) so it can pass the real
`related_kind`/`related_id`."""

from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands, tasks

from panem_bot import autocomplete, utils
from panem_bot.errors import ServiceError
from panem_bot.strings import t
from panem_shared import constants
from panem_shared import rumors as rumors_svc
from panem_shared.db.models import WorldClock


class RumorsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.spawn_loop.start()

    async def cog_unload(self) -> None:
        self.spawn_loop.cancel()

    group = app_commands.Group(name="rumor", description="What's going around")

    async def _current_tick(self, session) -> int:  # type: ignore[no-untyped-def]
        clock = await session.get(WorldClock, 1)
        return clock.tick if clock is not None else 0

    @group.command(name="list", description="Hear what's going around your district")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def list_(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(
                session, interaction.user.id, character
            )
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            rumors = await rumors_svc.district_rumors(session, char.current_district_id)
            if not rumors:
                await interaction.response.send_message(
                    t("rumor_list_empty", district_id=char.current_district_id), ephemeral=True
                )
                return
            embed = discord.Embed(title=f"Rumors in District {char.current_district_id}")
            for rumor in rumors[:10]:
                embed.add_field(
                    name=f"#{rumor.id} -- {rumor.reliability_pct}% reliable",
                    value=f"{rumor.text} ({rumor.origin})",
                    inline=False,
                )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="investigate", description="Ask around / look into a rumor")
    @app_commands.describe(character="Character name", rumor="Which rumor")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    @app_commands.autocomplete(rumor=autocomplete.district_rumor)
    async def investigate(
        self, interaction: discord.Interaction, character: str, rumor: int
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(
                session, interaction.user.id, character
            )
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            try:
                rumor_row = await rumors_svc.get_rumor(session, rumor)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            current_tick = await self._current_tick(session)
            try:
                log = await rumors_svc.investigate(
                    session,
                    character_id=char.id,
                    rumor=rumor_row,
                    current_tick=current_tick,
                    rng=random.Random(),
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            text = t(
                "rumor_investigated", finding=log.finding, reliability=rumor_row.reliability_pct
            )
        await interaction.response.send_message(text, ephemeral=True)

    # --------------------------------------------------------------- loop

    @tasks.loop(minutes=constants.RUMOR_SPAWN_POLL_MINUTES)
    async def spawn_loop(self) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            current_tick = await self._current_tick(session)
            content = self.bot.content  # type: ignore[attr-defined]
            rng = random.Random()
            for district_id in content.districts:
                if rng.random() < constants.RUMOR_SPAWN_CHANCE:
                    await rumors_svc.spawn_rumor(
                        session, district_id=district_id, current_tick=current_tick, rng=rng
                    )

    @spawn_loop.before_loop
    async def _before_spawn_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RumorsCog(bot))
