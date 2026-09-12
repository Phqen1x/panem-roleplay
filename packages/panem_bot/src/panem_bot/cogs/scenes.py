"""`/scene ...` commands and forum thread lifecycle listeners (FR-SCN)."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands, tasks
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from panem_bot import autocomplete, redis_keys
from panem_bot.services import characters as characters_svc
from panem_bot.services import scenes as scenes_svc
from panem_bot.strings import t
from panem_shared.db.models import Character, DiscordChannel, Scene, User
from panem_shared.enums import ChannelKind, CharacterStatus, SceneKind, SceneStatus

OPEN_TAG = "Open"
CLOSED_TAG = "Closed"
UNTAGGED_GRACE_SECONDS = 60


def _find_tag(forum: discord.ForumChannel, name: str) -> discord.ForumTag | None:
    return discord.utils.get(forum.available_tags, name=name)


class SceneCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.archive_idle_scenes.start()

    async def cog_unload(self) -> None:
        self.archive_idle_scenes.cancel()

    # ------------------------------------------------------------- helpers

    async def _district_for_channel(self, session, channel_id: int) -> int | None:
        row = (
            await session.execute(
                select(DiscordChannel).where(DiscordChannel.channel_id == channel_id)
            )
        ).scalar_one_or_none()
        return row.district_id if row else None

    @staticmethod
    def _forum_channel_id(channel: discord.abc.GuildChannel | discord.Thread | None) -> int | None:
        """Only the forum channel itself is registered in `discord_channels`, not
        each thread inside it -- resolve to the parent forum's id so `/scene
        start` and its autocomplete work whether run from the forum channel's
        own compose bar or from inside one of its threads."""
        if isinstance(channel, discord.Thread):
            return channel.parent_id
        return channel.id if channel is not None else None

    async def _forum_for_district(self, session, district_id: int) -> DiscordChannel | None:
        return (
            await session.execute(
                select(DiscordChannel).where(
                    DiscordChannel.district_id == district_id,
                    DiscordChannel.kind == ChannelKind.FORUM.value,
                )
            )
        ).scalar_one_or_none()

    async def _open_scene_count(self, session, district_id: int) -> int:
        result = await session.execute(
            select(func.count())
            .select_from(Scene)
            .where(
                Scene.district_id == district_id,
                Scene.status == SceneStatus.OPEN.value,
                Scene.kind != SceneKind.AMBIENT.value,
            )
        )
        return int(result.scalar_one())

    async def _user_has_open_scene(self, session, discord_user_id: int) -> bool:
        """One open self-created scene per person at a time, across all districts."""
        user = await characters_svc.get_or_create_user(session, discord_user_id)
        result = await session.execute(
            select(func.count())
            .select_from(Scene)
            .join(Character, Character.id == Scene.created_by_character_id)
            .where(Character.user_id == user.id, Scene.status == SceneStatus.OPEN.value)
        )
        return int(result.scalar_one()) > 0

    async def _actor_can_manage(
        self, session, *, discord_user_id: int, scene: Scene, is_staff: bool
    ) -> bool:
        """FR-SCN-6: `/scene close`/`/scene move` are creator-or-staff only."""
        if is_staff:
            return True
        if scene.created_by_character_id is None:
            return False
        user = await characters_svc.get_or_create_user(session, discord_user_id)
        owned = (
            await session.execute(
                select(Character.id).where(
                    Character.id == scene.created_by_character_id, Character.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        return owned is not None

    async def _caller_character(
        self, session, *, discord_user_id: int, district_id: int, name: str | None
    ) -> Character | None:
        stmt = select(Character).where(
            Character.status == CharacterStatus.APPROVED.value,
            Character.district_id == district_id,
        )
        user = await characters_svc.get_or_create_user(session, discord_user_id)
        stmt = stmt.where(Character.user_id == user.id)
        if name:
            stmt = stmt.where(Character.name == name)
        rows = (await session.execute(stmt)).scalars().all()
        return rows[0] if len(rows) == 1 else None

    # -------------------------------------------------------------- /scene

    group = app_commands.Group(name="scene", description="Manage roleplay scenes")

    @group.command(name="start", description="Start a new scene at a location")
    @app_commands.describe(
        location="Location id",
        title="Scene title",
        character="Which character (if you have several here)",
    )
    async def start(
        self,
        interaction: discord.Interaction,
        location: str,
        title: str,
        character: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        forum_channel_id = self._forum_channel_id(interaction.channel)
        async with self.bot.db() as session:
            district_id = (
                await self._district_for_channel(session, forum_channel_id)
                if forum_channel_id is not None
                else None
            )
            if district_id is None:
                await interaction.response.send_message(
                    "Use this in a district channel.", ephemeral=True
                )
                return

            if await self._user_has_open_scene(session, interaction.user.id):
                await interaction.response.send_message(t("scene_already_open"), ephemeral=True)
                return

            district = self.bot.content.district(district_id)
            if location not in {loc.id for loc in district.locations}:
                await interaction.response.send_message(
                    f"Unknown location `{location}`.", ephemeral=True
                )
                return

            char = await self._caller_character(
                session,
                discord_user_id=interaction.user.id,
                district_id=district_id,
                name=character,
            )
            if char is None:
                await interaction.response.send_message(
                    "Specify `character` — you have more than one approved character here, or none.",
                    ephemeral=True,
                )
                return

            if scenes_svc.is_scene_cap_reached(
                open_non_ambient_count=await self._open_scene_count(session, district_id),
                cap=self.bot.settings.max_active_scenes_per_district,
            ):
                await interaction.response.send_message(t("scene_at_cap"), ephemeral=True)
                return

            forum_row = await self._forum_for_district(session, district_id)
            if forum_row is None:
                await interaction.response.send_message(
                    "This district has no forum configured.", ephemeral=True
                )
                return

        forum = interaction.guild.get_channel(forum_row.channel_id)
        if not isinstance(forum, discord.ForumChannel):
            await interaction.response.send_message("Forum channel not found.", ephemeral=True)
            return

        loc = next(loc for loc in district.locations if loc.id == location)
        tags = [
            t_ for t_ in (_find_tag(forum, loc.name), _find_tag(forum, OPEN_TAG)) if t_ is not None
        ]

        await interaction.response.defer(ephemeral=True, thinking=True)
        thread_with_message = await forum.create_thread(
            name=title,
            content=f"*{char.name} arrives at {loc.name}.*",
            applied_tags=tags,
        )
        thread = thread_with_message.thread

        async with self.bot.db() as session:
            # forum.create_thread() dispatches a gateway `on_thread_create`
            # event the moment the thread exists on Discord's side, which can
            # race this insert and get there first (see below) -- upsert so
            # this command's data (the real creator, title, and location)
            # always wins over that listener's best-effort stub, regardless
            # of which one lands first.
            values = {
                "district_id": district_id,
                "location_id": location,
                "thread_id": thread.id,
                "forum_channel_id": forum.id,
                "kind": SceneKind.PLAYER.value,
                "title": title,
                "created_by_character_id": char.id,
                "status": SceneStatus.OPEN.value,
                "last_message_at": dt.datetime.now(dt.UTC),
            }
            stmt = pg_insert(Scene).values(**values)
            stmt = stmt.on_conflict_do_update(index_elements=["thread_id"], set_=values)
            await session.execute(stmt)

        await self.bot.redis.set(
            redis_keys.session_key(interaction.user.id, thread.id),
            str(char.id),
            ex=redis_keys.SESSION_TTL_S,
        )
        await interaction.followup.send(f"Scene created: {thread.mention}", ephemeral=True)

    @start.autocomplete("location")
    async def start_location_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        forum_channel_id = self._forum_channel_id(interaction.channel)
        if forum_channel_id is None:
            return []
        async with self.bot.db() as session:
            district_id = await self._district_for_channel(session, forum_channel_id)
        if district_id is None:
            return []
        district = self.bot.content.district(district_id)
        current_lower = current.lower()
        matches = [
            loc
            for loc in district.locations
            if current_lower in loc.name.lower() or current_lower in loc.id.lower()
        ]
        return [
            app_commands.Choice(name=f"{loc.name} ({loc.id})", value=loc.id) for loc in matches[:25]
        ]

    @start.autocomplete("character")
    async def start_character_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        forum_channel_id = self._forum_channel_id(interaction.channel)
        if forum_channel_id is None:
            return []
        async with self.bot.db() as session:
            district_id = await self._district_for_channel(session, forum_channel_id)
            if district_id is None:
                return []
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            stmt = select(Character.name).where(
                Character.user_id == user.id,
                Character.district_id == district_id,
                Character.status == CharacterStatus.APPROVED.value,
            )
            if current:
                stmt = stmt.where(Character.name.ilike(f"%{current}%"))
            names = (await session.execute(stmt.limit(25))).scalars().all()
        return [app_commands.Choice(name=name, value=name) for name in names]

    @group.command(name="close", description="Close this scene")
    async def close(self, interaction: discord.Interaction) -> None:
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message("Use this inside a scene.", ephemeral=True)
            return

        is_staff = isinstance(interaction.user, discord.Member) and await self.bot.is_staff(
            interaction.user
        )
        async with self.bot.db() as session:
            scene = (
                await session.execute(select(Scene).where(Scene.thread_id == thread.id))
            ).scalar_one_or_none()
            if scene is None:
                await interaction.response.send_message("Not a registered scene.", ephemeral=True)
                return
            if not await self._actor_can_manage(
                session, discord_user_id=interaction.user.id, scene=scene, is_staff=is_staff
            ):
                await interaction.response.send_message(t("scene_not_yours"), ephemeral=True)
                return
            scene.status = SceneStatus.ARCHIVED.value

        forum = thread.parent
        closed_tag = (
            _find_tag(forum, CLOSED_TAG) if isinstance(forum, discord.ForumChannel) else None
        )
        new_tags = [tg for tg in thread.applied_tags if tg.name != OPEN_TAG]
        if closed_tag is not None:
            new_tags.append(closed_tag)
        # Reply before archiving: an interaction response into an
        # already-archived thread is refused with 403 "Thread is archived".
        await interaction.response.send_message(t("scene_closed"), ephemeral=True)
        await thread.edit(archived=True, applied_tags=new_tags, reason="Scene closed")

    @group.command(name="move", description="Change this scene's location tag")
    @app_commands.describe(location="New location id")
    async def move(self, interaction: discord.Interaction, location: str) -> None:
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message("Use this inside a scene.", ephemeral=True)
            return

        is_staff = isinstance(interaction.user, discord.Member) and await self.bot.is_staff(
            interaction.user
        )
        async with self.bot.db() as session:
            scene = (
                await session.execute(select(Scene).where(Scene.thread_id == thread.id))
            ).scalar_one_or_none()
            if scene is None:
                await interaction.response.send_message("Not a registered scene.", ephemeral=True)
                return
            district = self.bot.content.district(scene.district_id)
            if location not in {loc.id for loc in district.locations}:
                await interaction.response.send_message(
                    f"Unknown location `{location}`.", ephemeral=True
                )
                return
            if not await self._actor_can_manage(
                session, discord_user_id=interaction.user.id, scene=scene, is_staff=is_staff
            ):
                await interaction.response.send_message(t("scene_not_yours"), ephemeral=True)
                return
            scene.location_id = location

        loc = next(loc for loc in district.locations if loc.id == location)
        forum = thread.parent
        if isinstance(forum, discord.ForumChannel):
            location_names = {loc2.name for loc2 in district.locations}
            new_tags = [tg for tg in thread.applied_tags if tg.name not in location_names]
            new_tag = _find_tag(forum, loc.name)
            if new_tag is not None:
                new_tags.append(new_tag)
            await thread.edit(applied_tags=new_tags)
        await interaction.response.send_message(t("scene_moved", location=loc.name), ephemeral=True)

    @move.autocomplete("location")
    async def move_location_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            return []
        async with self.bot.db() as session:
            scene = (
                await session.execute(select(Scene).where(Scene.thread_id == thread.id))
            ).scalar_one_or_none()
            if scene is None:
                return []
            district = self.bot.content.district(scene.district_id)
        current_lower = current.lower()
        matches = [
            loc
            for loc in district.locations
            if current_lower in loc.name.lower() or current_lower in loc.id.lower()
        ]
        return [
            app_commands.Choice(name=f"{loc.name} ({loc.id})", value=loc.id) for loc in matches[:25]
        ]

    @group.command(name="invite", description="Invite a character or NPC into this scene")
    @app_commands.describe(character="Character to invite (mentions their player)")
    @app_commands.autocomplete(character=autocomplete.any_approved)
    async def invite(self, interaction: discord.Interaction, character: str) -> None:
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message("Use this inside a scene.", ephemeral=True)
            return
        async with self.bot.db() as session:
            row = (
                await session.execute(
                    select(Character).where(
                        Character.name == character,
                        Character.status == CharacterStatus.APPROVED.value,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            user = await session.get(User, row.user_id)
        await interaction.response.send_message(
            f"<@{user.discord_id}>, you've been invited to this scene."
        )

    # ---------------------------------------------------------------- listeners

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        if not isinstance(thread.parent, discord.ForumChannel):
            return
        async with self.bot.db() as session:
            district_id = await self._district_for_channel(session, thread.parent_id)
            if district_id is None:
                return
            existing = (
                await session.execute(select(Scene).where(Scene.thread_id == thread.id))
            ).scalar_one_or_none()
            if existing is not None:
                return  # already inserted by /scene start

            district = self.bot.content.district(district_id)
            tag_names = [tg.name for tg in thread.applied_tags]
            resolution = scenes_svc.resolve_location_tag(tag_names, district)
            if resolution.error is not None:
                with contextlib.suppress(discord.HTTPException):
                    await thread.send(t("scene_needs_tag"))
                self.bot.loop.create_task(self._archive_if_still_untagged(thread.id, district_id))
                return

            # /scene start's own thread creation dispatches this same event,
            # and can still be mid-flight here -- DO NOTHING on conflict
            # rather than raising, since /scene start's insert carries the
            # real creator/title and is the one that should win either way.
            stmt = pg_insert(Scene).values(
                district_id=district_id,
                location_id=resolution.location_id,
                thread_id=thread.id,
                forum_channel_id=thread.parent_id,
                kind=SceneKind.PLAYER.value,
                title=thread.name,
                created_by_character_id=None,
                status=SceneStatus.OPEN.value,
                last_message_at=dt.datetime.now(dt.UTC),
            )
            stmt = stmt.on_conflict_do_nothing(index_elements=["thread_id"])
            await session.execute(stmt)

    async def _archive_if_still_untagged(self, thread_id: int, district_id: int) -> None:
        await asyncio.sleep(UNTAGGED_GRACE_SECONDS)
        thread = self.bot.get_channel(thread_id)
        if not isinstance(thread, discord.Thread) or thread.archived:
            return
        district = self.bot.content.district(district_id)
        tag_names = [tg.name for tg in thread.applied_tags]
        if scenes_svc.resolve_location_tag(tag_names, district).error is not None:
            await thread.edit(archived=True, reason="No valid location tag within grace period")

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread) -> None:
        async with self.bot.db() as session:
            scene = (
                await session.execute(select(Scene).where(Scene.thread_id == after.id))
            ).scalar_one_or_none()
            if scene is None:
                return
            if scene.kind == SceneKind.AMBIENT.value and after.archived:
                await after.edit(archived=False, reason="Ambient posts stay open")
                scene.status = SceneStatus.OPEN.value
                return
            if after.locked:
                scene.status = SceneStatus.LOCKED.value
            elif after.archived:
                scene.status = SceneStatus.ARCHIVED.value
            else:
                scene.status = SceneStatus.OPEN.value

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread) -> None:
        async with self.bot.db() as session:
            scene = (
                await session.execute(select(Scene).where(Scene.thread_id == thread.id))
            ).scalar_one_or_none()
            if scene is not None:
                scene.status = SceneStatus.DELETED.value

    # --------------------------------------------------------------- upkeep

    @tasks.loop(minutes=10)
    async def archive_idle_scenes(self) -> None:
        async with self.bot.db() as session:
            for district_id in self.bot.content.districts:
                scenes = (
                    (await session.execute(select(Scene).where(Scene.district_id == district_id)))
                    .scalars()
                    .all()
                )
                to_archive = scenes_svc.pick_scenes_to_archive(
                    scenes,
                    cap=self.bot.settings.max_active_scenes_per_district,
                    now=dt.datetime.now(dt.UTC),
                )
                for scene_id in to_archive:
                    scene = next(s for s in scenes if s.id == scene_id)
                    thread = self.bot.get_channel(scene.thread_id)
                    if isinstance(thread, discord.Thread):
                        try:
                            await thread.edit(archived=True, reason="Idle scene, district at cap")
                        except discord.HTTPException:
                            continue
                    scene.status = SceneStatus.ARCHIVED.value

    @archive_idle_scenes.before_loop
    async def _before_archive_idle_scenes(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SceneCog(bot))
