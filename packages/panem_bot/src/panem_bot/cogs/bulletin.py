"""`/bulletin` -- Spec's bulletin board. Every announcement (weather,
global events, rumors) and player/NPC post in one place, with per-viewer
interest marking, drag-organize, and DM reminders. The background loop
here only sends due reminders (`panem_shared.bulletin.due_reminders`);
everything else posts to the board by calling `panem_shared.bulletin.
record_system_entry` directly from wherever it happens (`panem_bot.cogs.
weather`'s poll, `panem_bot.cogs.rumors`'s spawn loop, `/staff event
create`/`conclude`)."""

from __future__ import annotations

import contextlib

import discord
from discord import app_commands
from discord.ext import commands, tasks

from panem_bot import autocomplete, utils
from panem_bot.errors import ServiceError
from panem_bot.services import characters as characters_svc
from panem_bot.strings import t
from panem_shared import bulletin as bulletin_svc
from panem_shared.db.models import BulletinEntry, WorldClock
from panem_shared.enums import BulletinSourceKind

_SOURCE_TAGS: dict[str, str] = {
    BulletinSourceKind.ANNOUNCEMENT.value: "Announcement",
    BulletinSourceKind.WEATHER_FORECAST.value: "Weather",
    BulletinSourceKind.WEATHER_EVENT.value: "Weather alert",
    BulletinSourceKind.GLOBAL_EVENT.value: "Admin hosted",
    BulletinSourceKind.RUMOR.value: "Rumor",
    BulletinSourceKind.PLAYER_POST.value: "Player hosted",
    BulletinSourceKind.NPC_POST.value: "NPC hosted",
}


def _display_tag(entry: BulletinEntry) -> str:
    return entry.tag or _SOURCE_TAGS.get(
        entry.source_kind, entry.source_kind.replace("_", " ").title()
    )


class BulletinCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.reminder_loop.start()

    async def cog_unload(self) -> None:
        self.reminder_loop.cancel()

    group = app_commands.Group(name="bulletin", description="The district bulletin board")

    async def _current_tick(self, session) -> int:  # type: ignore[no-untyped-def]
        clock = await session.get(WorldClock, 1)
        return clock.tick if clock is not None else 0

    @group.command(name="board", description="See the bulletin board")
    @app_commands.describe(character="Character name (sets which district's board you see)")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def board(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(
                session, interaction.user.id, character
            )
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            rows = await bulletin_svc.board_for(
                session, district_id=char.current_district_id, viewer_user_id=user.id
            )
            if not rows:
                await interaction.response.send_message(t("bulletin_empty"), ephemeral=True)
                return
            embed = discord.Embed(title=f"District {char.current_district_id} Bulletin Board")
            for entry, state in rows[:15]:
                when = f"tick {entry.event_tick}" if entry.event_tick is not None else "ongoing"
                interested = " ★" if state is not None and state.interested else ""
                value = entry.body or "​"
                if entry.requirements:
                    value += f"\nRequires: {entry.requirements}"
                if entry.danger_level is not None:
                    value += f"\nDanger: category {entry.danger_level}"
                embed.add_field(
                    name=f"#{entry.id} [{_display_tag(entry)}] {entry.title} ({when}){interested}",
                    value=value,
                    inline=False,
                )
        await interaction.response.send_message(embed=embed)

    @group.command(name="post", description="Post something to the bulletin board")
    @app_commands.describe(
        character="Character name (sets which district this is posted to)",
        title="Short title",
        body="Details",
        event_tick="When it happens (tick number), if any",
        requirements="Who can join / what's needed, if any",
        npc_hosted="Tag as NPC-hosted",
        world_wide="Post for every district, not just your character's",
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def post(
        self,
        interaction: discord.Interaction,
        character: str,
        title: str,
        body: str = "",
        event_tick: int | None = None,
        requirements: str | None = None,
        npc_hosted: bool = False,
        world_wide: bool = False,
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(
                session, interaction.user.id, character
            )
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            current_tick = await self._current_tick(session)
            try:
                await bulletin_svc.post_player_entry(
                    session,
                    user=user,
                    tag="NPC hosted" if npc_hosted else "Player hosted",
                    title=title,
                    body=body,
                    district_id=None if world_wide else char.current_district_id,
                    location_id=char.location_id if not world_wide else None,
                    posted_tick=current_tick,
                    event_tick=event_tick,
                    requirements=requirements,
                    npc_hosted=npc_hosted,
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
        await interaction.response.send_message(t("bulletin_posted"))

    @group.command(name="retract", description="Take down your own bulletin post")
    @app_commands.describe(entry="Which post")
    async def retract(self, interaction: discord.Interaction, entry: int) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            try:
                await bulletin_svc.retract(session, user=user, entry_id=entry)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
        await interaction.response.send_message(t("bulletin_retracted"), ephemeral=True)

    @group.command(name="interested", description="Mark (or unmark) yourself as interested")
    @app_commands.describe(entry="Which post", interested="Interested, or no longer interested")
    async def interested(
        self, interaction: discord.Interaction, entry: int, interested: bool = True
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            try:
                entry_row = await bulletin_svc.set_interest(
                    session, user=user, entry_id=entry, interested=interested
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            key = "bulletin_interest_marked" if interested else "bulletin_interest_unmarked"
            text = t(key, title=entry_row.title)
        await interaction.response.send_message(text, ephemeral=True)

    @group.command(name="organize", description="Move a post to a spot/category on your own board")
    @app_commands.describe(
        entry="Which post", position="Lower sorts earlier", category="Your own label for it"
    )
    async def organize(
        self,
        interaction: discord.Interaction,
        entry: int,
        position: int,
        category: str | None = None,
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            try:
                await bulletin_svc.move_entry(
                    session, user=user, entry_id=entry, position=position, category=category
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
        await interaction.response.send_message(t("bulletin_moved"), ephemeral=True)

    @group.command(
        name="reset-organize", description="Reset your bulletin board back to default order"
    )
    async def reset_organize(self, interaction: discord.Interaction) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            await bulletin_svc.reset_organization(session, user_id=user.id)
        await interaction.response.send_message(t("bulletin_reset"), ephemeral=True)

    @group.command(name="remind", description="Get a DM ahead of a scheduled bulletin entry")
    @app_commands.describe(entry="Which post", lead_ticks="How many ticks ahead (default 1)")
    async def remind(
        self, interaction: discord.Interaction, entry: int, lead_ticks: int = 1
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            entry_row = await session.get(BulletinEntry, entry)
            if entry_row is None or entry_row.retracted:
                await interaction.response.send_message(
                    t("bulletin_entry_not_found"), ephemeral=True
                )
                return
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            current_tick = await self._current_tick(session)
            try:
                await bulletin_svc.schedule_reminder(
                    session,
                    user=user,
                    entry=entry_row,
                    lead_ticks=lead_ticks,
                    current_tick=current_tick,
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            title = entry_row.title
        await interaction.response.send_message(
            t("bulletin_reminder_set", title=title), ephemeral=True
        )

    # --------------------------------------------------------------- loop

    @tasks.loop(minutes=2)
    async def reminder_loop(self) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            current_tick = await self._current_tick(session)
            due = await bulletin_svc.due_reminders(session, current_tick)
            for reminder, entry, user in due:
                discord_user = self.bot.get_user(user.discord_id)
                if discord_user is None:
                    try:
                        discord_user = await self.bot.fetch_user(user.discord_id)
                    except discord.HTTPException:
                        discord_user = None
                if discord_user is not None:
                    with contextlib.suppress(discord.Forbidden):
                        await discord_user.send(f"Reminder: **{entry.title}** -- {entry.body}")
                await session.delete(reminder)

    @reminder_loop.before_loop
    async def _before_reminder_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BulletinCog(bot))
