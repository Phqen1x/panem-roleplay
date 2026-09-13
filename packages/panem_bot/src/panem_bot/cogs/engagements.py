"""`/engage` -- group RP threads with one or more NPCs and, once accepted,
other players' characters. An engagement is a `Scene` (`SceneKind.
ENGAGEMENT`); ordinary proxying (`panem_bot.cogs.proxy`) already works
against any `Scene` by `thread_id`, so posting into one, pinning a
character's location to it, and RP/fatigue crediting all just work.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands, tasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete
from panem_bot.errors import ServiceError
from panem_bot.services import characters as characters_svc
from panem_bot.services import engagements as engagements_svc
from panem_bot.services import proxy as proxy_svc
from panem_bot.services import travel as travel_svc
from panem_bot.strings import t
from panem_shared import constants, simtime
from panem_shared.db.models import (
    Character,
    DiscordChannel,
    EngagementSettings,
    Npc,
    Scene,
    User,
    WorldClock,
)
from panem_shared.enums import ChannelKind, CharacterStatus, SceneKind, SceneStatus

from .scenes import CLOSED_TAG, OPEN_TAG, _find_tag


class _InviteResponseButton(discord.ui.Button["discord.ui.View"]):
    """A plain callback button restricted to the one player it's for --
    same injected-coroutine shape as `cogs/jobs.py`'s `_SkipButton`."""

    def __init__(
        self,
        *,
        label: str,
        style: discord.ButtonStyle,
        target_discord_id: int,
        on_click: Callable[[discord.Interaction], Awaitable[None]],
    ) -> None:
        super().__init__(label=label, style=style)
        self._target_discord_id = target_discord_id
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._target_discord_id:
            await interaction.response.send_message(
                t("engagement_invite_not_yours"), ephemeral=True
            )
            return
        await self._on_click(interaction)


class EngagementCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.close_idle_engagements.start()

    async def cog_unload(self) -> None:
        self.close_idle_engagements.cancel()

    # ------------------------------------------------------------- helpers

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

    async def _forum_for_district(
        self, session: AsyncSession, district_id: int
    ) -> DiscordChannel | None:
        return (
            await session.execute(
                select(DiscordChannel).where(
                    DiscordChannel.district_id == district_id,
                    DiscordChannel.kind == ChannelKind.FORUM.value,
                )
            )
        ).scalar_one_or_none()

    async def _actor_can_manage(
        self, session: AsyncSession, *, discord_user_id: int, scene: Scene, is_staff: bool
    ) -> bool:
        """Creator-or-staff, mirroring `SceneCog._actor_can_manage` exactly."""
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

    def _invite_view(
        self, *, scene_id: int, character_id: int, character_name: str, target_discord_id: int
    ) -> discord.ui.View:
        """Two buttons, only clickable by the invited player. Accept moves
        the character from `pending_characters` to `characters` in the
        scene's roster; decline just drops them from `pending_characters`.
        Not persisted across a bot restart (no custom_id registration,
        matching every other button in this codebase) -- an invite that
        outlives a restart just needs to be re-sent."""

        async def _respond(interaction: discord.Interaction, *, accepted: bool) -> None:
            async with self.bot.db() as session:  # type: ignore[attr-defined]
                scene = await session.get(Scene, scene_id)
                if scene is None:
                    return
                participants = dict(scene.participants)
                pending = list(participants.get("pending_characters", []))
                if character_id not in pending:
                    return
                pending.remove(character_id)
                participants["pending_characters"] = pending
                if accepted:
                    participants["characters"] = [
                        *participants.get("characters", []),
                        character_id,
                    ]
                scene.participants = participants
            key = "engagement_invite_accepted" if accepted else "engagement_invite_declined"
            await interaction.response.edit_message(
                content=t(key, character=character_name), view=None
            )

        view = discord.ui.View(timeout=None)
        view.add_item(
            _InviteResponseButton(
                label="Accept",
                style=discord.ButtonStyle.success,
                target_discord_id=target_discord_id,
                on_click=lambda i: _respond(i, accepted=True),
            )
        )
        view.add_item(
            _InviteResponseButton(
                label="Decline",
                style=discord.ButtonStyle.danger,
                target_discord_id=target_discord_id,
                on_click=lambda i: _respond(i, accepted=False),
            )
        )
        return view

    # -------------------------------------------------------------- /engage

    group = app_commands.Group(
        name="engage",
        description="Start or manage a group RP engagement with NPCs and/or players",
    )

    @group.command(name="start", description="Start an engagement with NPCs and/or players")
    @app_commands.describe(
        character="Your character",
        participants="Comma-separated NPC and/or character names to include",
        title="Thread title",
        location="Location id (defaults to your character's current location)",
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def start(
        self,
        interaction: discord.Interaction,
        character: str,
        participants: str,
        title: str,
        location: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.followup.send(t("character_not_found"), ephemeral=True)
                return
            try:
                engagements_svc.check_can_start(character=char)
            except ServiceError as exc:
                await interaction.followup.send(t(exc.reason_key, **exc.fmt), ephemeral=True)
                return

            district_id = char.current_district_id
            district = self.bot.content.district(district_id)  # type: ignore[attr-defined]
            loc_id = location or char.location_id
            if loc_id is None:
                await interaction.followup.send(
                    t("engagement_no_location", name=char.name), ephemeral=True
                )
                return
            loc = next((loc_ for loc_ in district.locations if loc_.id == loc_id), None)
            if loc is None:
                await interaction.followup.send(f"Unknown location `{loc_id}`.", ephemeral=True)
                return
            if not proxy_svc.can_rp_at_location(char, loc_id):
                await interaction.followup.send(
                    t("engagement_not_traveled", name=char.name, location=loc.name),
                    ephemeral=True,
                )
                return

            names = engagements_svc.parse_participants(participants)
            if not names:
                await interaction.followup.send(t("engagement_needs_participants"), ephemeral=True)
                return

            npc_candidates = (
                (await session.execute(select(Npc).where(Npc.district_id == district_id)))
                .scalars()
                .all()
            )
            matched_npcs, remaining_names = engagements_svc.resolve_npc_participants(
                names, list(npc_candidates)
            )

            char_candidates = (
                (
                    await session.execute(
                        select(Character).where(
                            Character.status == CharacterStatus.APPROVED.value,
                            Character.location_id == loc_id,
                            Character.id != char.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            matched_chars, unresolved_names = engagements_svc.resolve_character_participants(
                remaining_names, list(char_candidates)
            )

            forum_row = await self._forum_for_district(session, district_id)
            if forum_row is None:
                await interaction.followup.send(
                    "This district has no forum configured.", ephemeral=True
                )
                return

            current_tick = await self._current_tick(session)
            _tick, phase, _day, _month = simtime.current(current_tick)
            content = self.bot.content  # type: ignore[attr-defined]

            free_npc_ids: list[str] = []
            busy_lines: list[str] = []
            for npc in matched_npcs:
                job = content.jobs.get(npc.job_id) if npc.job_id else None
                reason = engagements_svc.npc_is_busy(npc, job, phase)
                if reason is None:
                    free_npc_ids.append(npc.id)
                elif reason == "shift" and job is not None:
                    workplace = next(
                        (loc_.name for loc_ in district.locations if loc_.id == job.workplace),
                        job.workplace,
                    )
                    busy_lines.append(
                        t("engagement_npc_busy_shift", name=npc.name, location=workplace)
                    )
                else:
                    busy_lines.append(t("engagement_npc_busy_sleep", name=npc.name))

            if not free_npc_ids and not matched_chars:
                summary = "\n".join(busy_lines) or t("engagement_nobody_available")
                await interaction.followup.send(summary, ephemeral=True)
                return

            forum_channel_id = forum_row.channel_id
            joined_npc_names = [npc.name for npc in matched_npcs if npc.id in free_npc_ids]
            char_name, char_id = char.name, char.id

        forum = interaction.guild.get_channel(forum_channel_id)
        if not isinstance(forum, discord.ForumChannel):
            await interaction.followup.send("Forum channel not found.", ephemeral=True)
            return

        tags = [
            t_ for t_ in (_find_tag(forum, loc.name), _find_tag(forum, OPEN_TAG)) if t_ is not None
        ]
        thread_with_message = await forum.create_thread(
            name=title,
            content=f"*{char_name} arrives at {loc.name}.*",
            applied_tags=tags,
        )
        thread = thread_with_message.thread

        async with self.bot.db() as session:  # type: ignore[attr-defined]
            participants_json: dict[str, list[object]] = {
                "characters": [char_id],
                "pending_characters": [c.id for c in matched_chars],
                "npcs": list(free_npc_ids),
            }
            scene = Scene(
                district_id=district_id,
                location_id=loc_id,
                thread_id=thread.id,
                forum_channel_id=forum.id,
                kind=SceneKind.ENGAGEMENT.value,
                title=title,
                created_by_character_id=char_id,
                status=SceneStatus.OPEN.value,
                last_message_at=dt.datetime.now(dt.UTC),
                participants=participants_json,
            )
            session.add(scene)
            await session.flush()

            for npc_id in free_npc_ids:
                npc_row = await session.get(Npc, npc_id)
                if npc_row is None:
                    continue
                placed = travel_svc.place(district, loc)
                npc_row.location_id = loc_id
                if placed is not None:
                    npc_row.x, npc_row.y = placed
                npc_row.engagement_id = scene.id

            scene_id = scene.id
            pending_chars = [(c.id, c.name, c.user_id) for c in matched_chars]

        lines = [t("engagement_started_ok", thread=thread.mention)]
        if joined_npc_names:
            lines.append("Joined: " + ", ".join(joined_npc_names))
        if pending_chars:
            lines.append("Invited (awaiting accept): " + ", ".join(c[1] for c in pending_chars))
        lines.extend(busy_lines)
        if unresolved_names:
            lines.append("Not found here: " + ", ".join(unresolved_names))
        await interaction.followup.send("\n".join(lines), ephemeral=True)

        if not pending_chars:
            return
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            user_rows = (
                await session.execute(
                    select(User).where(User.id.in_({uid for _, _, uid in pending_chars}))
                )
            ).scalars()
            discord_ids = {row.id: row.discord_id for row in user_rows}
        for pending_char_id, pending_char_name, user_id in pending_chars:
            target_discord_id = discord_ids.get(user_id)
            if target_discord_id is None:
                continue
            view = self._invite_view(
                scene_id=scene_id,
                character_id=pending_char_id,
                character_name=pending_char_name,
                target_discord_id=target_discord_id,
            )
            await thread.send(
                t(
                    "engagement_invite_prompt",
                    mention=f"<@{target_discord_id}>",
                    character=pending_char_name,
                ),
                view=view,
            )

    @start.autocomplete("location")
    async def start_location_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        character_name = getattr(interaction.namespace, "character", None)
        if not character_name:
            return []
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character_name)
            if char is None:
                return []
            district_id = char.current_district_id
        district = self.bot.content.district(district_id)  # type: ignore[attr-defined]
        current_lower = current.lower()
        matches = [
            loc
            for loc in district.locations
            if current_lower in loc.name.lower() or current_lower in loc.id.lower()
        ]
        return [
            app_commands.Choice(name=f"{loc.name} ({loc.id})", value=loc.id) for loc in matches[:25]
        ]

    @group.command(name="end", description="End this engagement")
    async def end(self, interaction: discord.Interaction) -> None:
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message(
                "Use this inside an engagement.", ephemeral=True
            )
            return

        is_staff = isinstance(interaction.user, discord.Member) and await self.bot.is_staff(  # type: ignore[attr-defined]
            interaction.user
        )
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            scene = (
                await session.execute(select(Scene).where(Scene.thread_id == thread.id))
            ).scalar_one_or_none()
            if scene is None or scene.kind != SceneKind.ENGAGEMENT.value:
                await interaction.response.send_message(
                    "Not a registered engagement.", ephemeral=True
                )
                return
            if not await self._actor_can_manage(
                session, discord_user_id=interaction.user.id, scene=scene, is_staff=is_staff
            ):
                await interaction.response.send_message(t("engagement_not_yours"), ephemeral=True)
                return

            for npc_id in scene.participants.get("npcs", []):
                npc_row = await session.get(Npc, npc_id)
                if npc_row is not None:
                    npc_row.engagement_id = None
            scene.status = SceneStatus.ARCHIVED.value

        closed_tag = (
            _find_tag(thread.parent, CLOSED_TAG)
            if isinstance(thread.parent, discord.ForumChannel)
            else None
        )
        new_tags = [tg for tg in thread.applied_tags if tg.name != OPEN_TAG]
        if closed_tag is not None:
            new_tags.append(closed_tag)
        await thread.send(t("engagement_closing_line"))
        await interaction.response.send_message(t("engagement_ended_ok"), ephemeral=True)
        await thread.edit(archived=True, applied_tags=new_tags, reason="Engagement ended")

    @group.command(name="join", description="Join an engagement you're physically present for")
    @app_commands.describe(character="Which character")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def join(self, interaction: discord.Interaction, character: str) -> None:
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message(
                "Use this inside an engagement.", ephemeral=True
            )
            return
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            scene = (
                await session.execute(select(Scene).where(Scene.thread_id == thread.id))
            ).scalar_one_or_none()
            if scene is None or scene.kind != SceneKind.ENGAGEMENT.value:
                await interaction.response.send_message(
                    "Not a registered engagement.", ephemeral=True
                )
                return
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            if not proxy_svc.can_rp_at_location(char, scene.location_id):
                district = self.bot.content.district(scene.district_id)  # type: ignore[attr-defined]
                loc = next(
                    (loc_ for loc_ in district.locations if loc_.id == scene.location_id), None
                )
                await interaction.response.send_message(
                    t(
                        "engagement_join_not_here",
                        name=char.name,
                        location=loc.name if loc is not None else scene.location_id,
                    ),
                    ephemeral=True,
                )
                return
            participants = dict(scene.participants)
            already_in = participants.get("characters", [])
            if char.id in already_in:
                await interaction.response.send_message(
                    t("engagement_join_already_in", name=char.name), ephemeral=True
                )
                return
            participants["characters"] = [*already_in, char.id]
            scene.participants = participants
            name = char.name
        await interaction.response.send_message(t("engagement_join_ok", name=name))

    # --------------------------------------------------------------- upkeep

    @tasks.loop(minutes=constants.ENGAGEMENT_IDLE_CHECK_INTERVAL_MINUTES)
    async def close_idle_engagements(self) -> None:
        """Mirrors `SceneCog.archive_idle_scenes`'s `tasks.loop` pattern,
        but closes *every* idle engagement (no district cap) once no
        player has spoken in `EngagementSettings.idle_timeout_minutes` --
        read fresh every pass, so a staff change via `/staff engagement
        set-timeout` takes effect on the very next check, not just for
        engagements started afterward."""
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            settings_row = await session.get(EngagementSettings, 1)
            timeout_minutes = (
                settings_row.idle_timeout_minutes
                if settings_row is not None
                else constants.ENGAGEMENT_DEFAULT_IDLE_TIMEOUT_MINUTES
            )
            scenes = (
                (
                    await session.execute(
                        select(Scene).where(Scene.kind == SceneKind.ENGAGEMENT.value)
                    )
                )
                .scalars()
                .all()
            )
            to_close = engagements_svc.scenes_to_close(
                list(scenes), timeout_minutes=timeout_minutes, now=dt.datetime.now(dt.UTC)
            )
            for scene_id in to_close:
                scene = next(s for s in scenes if s.id == scene_id)
                for npc_id in scene.participants.get("npcs", []):
                    npc_row = await session.get(Npc, npc_id)
                    if npc_row is not None:
                        npc_row.engagement_id = None
                scene.status = SceneStatus.ARCHIVED.value

                thread = self.bot.get_channel(scene.thread_id)
                if not isinstance(thread, discord.Thread):
                    continue
                try:
                    await thread.send(t("engagement_closing_line"))
                    closed_tag = (
                        _find_tag(thread.parent, CLOSED_TAG)
                        if isinstance(thread.parent, discord.ForumChannel)
                        else None
                    )
                    new_tags = [tg for tg in thread.applied_tags if tg.name != OPEN_TAG]
                    if closed_tag is not None:
                        new_tags.append(closed_tag)
                    await thread.edit(
                        archived=True, applied_tags=new_tags, reason="Engagement idle timeout"
                    )
                except discord.HTTPException:
                    continue

    @close_idle_engagements.before_loop
    async def _before_close_idle_engagements(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EngagementCog(bot))
