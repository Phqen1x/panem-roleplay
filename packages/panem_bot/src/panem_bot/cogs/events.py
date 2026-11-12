"""`/event` -- personal event chains (Spec's own example: "fishing up a
toy -> who does this belong to? -> analyze -> find the person -> deliver
-> get a reward") and checking in on a running Global Event. There's no
"join" subcommand for the latter: the first hobby session you play while
an event is active and in range joins you automatically
(`panem_bot.cogs.hobbies`), matching the Hypixel-Jacob's-Contest shape
Spec calls out. Staff create/conclude global events via `/staff event
...` (`panem_bot.cogs.staff`) instead of here.

The background loop also lives here: it's what actually flips a
`GlobalEvent` from `SCHEDULED` to `ACTIVE` to `FINISHED` (rewarding the
leaderboard on the way) off its own `starts_tick`/`ends_tick`, and sweeps
expired personal event chains -- the same real-time poll pattern
`panem_shared.weather`'s module docstring already explains, not
`panem_sim`'s per-tick `WorldState` loop."""

from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands, tasks

from panem_bot import autocomplete, utils
from panem_bot.errors import ServiceError
from panem_bot.strings import t
from panem_shared import bulletin as bulletin_svc
from panem_shared import global_events as global_events_svc
from panem_shared import personal_events
from panem_shared.db.models import Character, GlobalEvent, PersonalEventChain, WorldClock
from panem_shared.enums import BulletinSourceKind, GlobalEventStatus


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_tick: int | None = None

    async def cog_load(self) -> None:
        self.lifecycle_loop.start()

    async def cog_unload(self) -> None:
        self.lifecycle_loop.cancel()

    group = app_commands.Group(name="event", description="Personal mysteries and running events")

    async def _current_tick(self, session) -> int:  # type: ignore[no-untyped-def]
        clock = await session.get(WorldClock, 1)
        return clock.tick if clock is not None else 0

    def _lost_item_chain(self, chains: list[PersonalEventChain]) -> PersonalEventChain | None:
        return next((c for c in chains if c.chain_kind == "lost_item"), None)

    @group.command(name="status", description="See any mystery your character is in the middle of")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def status(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(
                session, interaction.user.id, character
            )
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            chains = await personal_events.active_chains(session, char.id)
            chain = self._lost_item_chain(chains)
            if chain is None:
                await interaction.response.send_message(
                    t("chain_none_active", name=char.name), ephemeral=True
                )
                return
            state = chain.state
            if chain.step == 0:
                text = t(
                    "chain_mystery_found",
                    name=char.name,
                    item_name=state["item_name"],
                    found_how=state["found_how"],
                )
            else:
                text = t(
                    "chain_investigate_result",
                    owner=state.get("target_npc_name", "someone"),
                    clue=f"Deliver it to them in District {state.get('found_district_id')}.",
                )
        await interaction.response.send_message(text, ephemeral=True)

    @group.command(name="mystery", description="Look into who a strange find might belong to")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def mystery(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(
                session, interaction.user.id, character
            )
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            chains = await personal_events.active_chains(session, char.id)
            chain = self._lost_item_chain(chains)
            if chain is None:
                await interaction.response.send_message(
                    t("chain_none_active", name=char.name), ephemeral=True
                )
                return
            current_tick = await self._current_tick(session)
            try:
                result = await personal_events.investigate(
                    session, chain=chain, current_tick=current_tick, rng=random.Random()
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            text = t("chain_investigate_result", owner=result.owner_npc_name, clue=result.clue)
        await interaction.response.send_message(text, ephemeral=True)

    @group.command(name="deliver", description="Return a found item to whoever it belongs to")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def deliver(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(
                session, interaction.user.id, character
            )
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            chains = await personal_events.active_chains(session, char.id)
            chain = self._lost_item_chain(chains)
            if chain is None:
                await interaction.response.send_message(
                    t("chain_none_active", name=char.name), ephemeral=True
                )
                return
            current_tick = await self._current_tick(session)
            item_name = chain.state.get("item_name", "it")
            try:
                result = await personal_events.deliver(
                    session, character=char, chain=chain, current_tick=current_tick
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            text = t(
                "chain_deliver_ok",
                name=char.name,
                item_name=item_name,
                npc=result.npc_name,
                money=result.money,
                reputation=result.reputation,
            )
        await interaction.response.send_message(text)

    @group.command(name="info", description="Check a running (or scheduled) global event")
    @app_commands.describe(character="Character name", event="Which event")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    @app_commands.autocomplete(event=autocomplete.any_global_event)
    async def info(self, interaction: discord.Interaction, character: str, event: int) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(
                session, interaction.user.id, character
            )
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            global_event = await session.get(GlobalEvent, event)
            if global_event is None:
                await interaction.response.send_message(t("global_event_not_found"), ephemeral=True)
                return
            hobby_def = self.bot.content.hobbies.get(global_event.hobby_id)  # type: ignore[attr-defined]
            hobby_name = hobby_def.name if hobby_def is not None else global_event.hobby_id

            embed = discord.Embed(title=global_event.name, description=global_event.description)
            embed.add_field(name="Hobby", value=hobby_name, inline=True)
            embed.add_field(name="Status", value=global_event.status.title(), inline=True)
            scope = (
                f"District {global_event.district_id}"
                if global_event.district_id is not None
                else "World-wide"
            )
            embed.add_field(name="Where", value=scope, inline=True)
            if global_event.status != GlobalEventStatus.ACTIVE.value:
                embed.add_field(name="Note", value=t("global_event_not_active"), inline=False)
            elif (
                global_event.district_id is not None
                and char.current_district_id != global_event.district_id
            ):
                embed.add_field(
                    name="Note",
                    value=t("global_event_wrong_place"),
                    inline=False,
                )
        await interaction.response.send_message(embed=embed)

    @group.command(name="leaderboard", description="See the standings for a global event")
    @app_commands.describe(event="Which event")
    @app_commands.autocomplete(event=autocomplete.any_global_event)
    async def leaderboard(self, interaction: discord.Interaction, event: int) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            global_event = await session.get(GlobalEvent, event)
            if global_event is None:
                await interaction.response.send_message(t("global_event_not_found"), ephemeral=True)
                return
            entries = await global_events_svc.leaderboard(session, event)
            if not entries:
                await interaction.response.send_message(
                    t("global_event_leaderboard_empty"), ephemeral=True
                )
                return
            lines = []
            for rank, entry in enumerate(entries, start=1):
                char = await session.get(Character, entry.character_id)
                name = char.name if char is not None else f"#{entry.character_id}"
                lines.append(f"**{rank}.** {name} -- {entry.score:.1f}")
            embed = discord.Embed(
                title=f"{global_event.name} standings", description="\n".join(lines)
            )
        await interaction.response.send_message(embed=embed)

    # --------------------------------------------------------------- loop

    @tasks.loop(minutes=5)
    async def lifecycle_loop(self) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            clock = await session.get(WorldClock, 1)
            tick = clock.tick if clock is not None else 0
            if self._last_tick is not None and tick <= self._last_tick:
                return
            start_tick = tick if self._last_tick is None else self._last_tick + 1

            for step_tick in range(start_tick, tick + 1):
                newly_active, newly_finished = await global_events_svc.due_transitions(
                    session, current_tick=step_tick
                )
                for event in newly_active:
                    await bulletin_svc.record_system_entry(
                        session,
                        source_kind=BulletinSourceKind.GLOBAL_EVENT,
                        source_id=event.id,
                        tag="Admin hosted",
                        title=f"{event.name} is underway",
                        body=event.description,
                        district_id=event.district_id,
                        posted_tick=step_tick,
                        event_tick=event.ends_tick,
                    )
                for event in newly_finished:
                    await bulletin_svc.record_system_entry(
                        session,
                        source_kind=BulletinSourceKind.GLOBAL_EVENT,
                        source_id=event.id,
                        tag="Admin hosted",
                        title=f"{event.name} has concluded",
                        district_id=event.district_id,
                        posted_tick=step_tick,
                    )
                await personal_events.expire_stale_chains(session, step_tick)

            self._last_tick = tick

    @lifecycle_loop.before_loop
    async def _before_lifecycle_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventsCog(bot))
