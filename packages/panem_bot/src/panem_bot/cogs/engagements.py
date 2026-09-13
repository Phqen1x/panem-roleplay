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
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
        participant_1="An NPC or character name to include",
        title="Thread title",
        location="Location id (defaults to your character's current location)",
        participant_2="Another NPC or character name to include",
        participant_3="Another NPC or character name to include",
        participant_4="Another NPC or character name to include",
        participant_5="Another NPC or character name to include",
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def start(
        self,
        interaction: discord.Interaction,
        character: str,
        participant_1: str,
        title: str,
        location: str | None = None,
        participant_2: str | None = None,
        participant_3: str | None = None,
        participant_4: str | None = None,
        participant_5: str | None = None,
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

            # If this is run from inside a thread that already has a scene
            # registered (an ambient thread, an open /scene, or a prior
            # engagement), pull the named NPCs/characters into *that*
            # conversation instead of spinning up a separate thread -- the
            # player is already there. `location` is ignored in that case;
            # the existing scene's own location is what counts. Only when
            # there's no such thread do we fall back to creating a
            # dedicated engagement thread at the given (or default) location.
            here_scene = None
            if isinstance(interaction.channel, discord.Thread):
                here_scene = (
                    await session.execute(
                        select(Scene).where(Scene.thread_id == interaction.channel.id)
                    )
                ).scalar_one_or_none()

            if here_scene is not None:
                district_id = here_scene.district_id
                district = self.bot.content.district(district_id)  # type: ignore[attr-defined]
                loc_id = here_scene.location_id
            else:
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

            names = [
                name.strip()
                for name in (
                    participant_1,
                    participant_2,
                    participant_3,
                    participant_4,
                    participant_5,
                )
                if name and name.strip()
            ]
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

            char_rows = (
                (
                    await session.execute(
                        select(Character).where(
                            Character.status == CharacterStatus.APPROVED.value,
                            Character.id != char.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            # A named character doesn't need to already be standing at the
            # location -- same district-eligibility rule `/travel`/proxying
            # already use (`can_rp_in_district`: their home district, or
            # wherever `/travel district:<id>` last took them). They still
            # must accept before joining either way; on accept they're
            # relocated to the engagement's location the same free, instant
            # way `/travel location:<id>` already works within a district.
            char_candidates = [c for c in char_rows if proxy_svc.can_rp_in_district(c, district_id)]
            matched_chars, unresolved_names = engagements_svc.resolve_character_participants(
                remaining_names, char_candidates
            )

            forum_channel_id: int | None = None
            if here_scene is None:
                forum_row = await self._forum_for_district(session, district_id)
                if forum_row is None:
                    await interaction.followup.send(
                        "This district has no forum configured.", ephemeral=True
                    )
                    return
                forum_channel_id = forum_row.channel_id

            current_tick = await self._current_tick(session)
            _tick, phase, _day, _month = simtime.current(current_tick)
            content = self.bot.content  # type: ignore[attr-defined]

            free_npc_ids: list[str] = []
            busy_lines: list[str] = []
            for npc in matched_npcs:
                # An NPC only converses in its own assigned district, and
                # never in a location their profession doesn't grant them
                # access to (e.g. a restricted Justice Building) -- unlike
                # a player's character, an NPC has no travel system to
                # justify appearing somewhere they otherwise couldn't.
                if not proxy_svc.npc_has_location_access(npc, loc):
                    busy_lines.append(
                        t("engagement_npc_no_access", name=npc.name, location=loc.name)
                    )
                    continue
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

            joined_npc_names = [npc.name for npc in matched_npcs if npc.id in free_npc_ids]
            char_name, char_id = char.name, char.id
            here_scene_id = here_scene.id if here_scene is not None else None

        if here_scene_id is not None:
            assert isinstance(interaction.channel, discord.Thread)
            thread = interaction.channel
            async with self.bot.db() as session:  # type: ignore[attr-defined]
                scene = await session.get(Scene, here_scene_id)
                assert scene is not None
                participants = dict(scene.participants or {})
                npcs_here = list(participants.get("npcs", []))
                for npc_id in free_npc_ids:
                    if npc_id not in npcs_here:
                        npcs_here.append(npc_id)
                participants["npcs"] = npcs_here
                chars_here = list(participants.get("characters", []))
                if char_id not in chars_here:
                    chars_here.append(char_id)
                participants["characters"] = chars_here
                pending_here = list(participants.get("pending_characters", []))
                for c in matched_chars:
                    if c.id not in pending_here and c.id not in chars_here:
                        pending_here.append(c.id)
                participants["pending_characters"] = pending_here
                scene.participants = participants

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
        else:
            assert forum_channel_id is not None
            forum = interaction.guild.get_channel(forum_channel_id)
            if not isinstance(forum, discord.ForumChannel):
                await interaction.followup.send("Forum channel not found.", ephemeral=True)
                return

            tags = [
                t_
                for t_ in (_find_tag(forum, loc.name), _find_tag(forum, OPEN_TAG))
                if t_ is not None
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
                # forum.create_thread() dispatches a gateway `on_thread_create`
                # event the moment the thread exists on Discord's side
                # (`SceneCog.on_thread_create`), which races this insert and can
                # get there first, registering the thread as an ordinary
                # SceneKind.PLAYER scene -- upsert, exactly like `/scene start`
                # does, so this command's data (the real kind, creator, title,
                # and participants) always wins regardless of which one lands
                # first. (This used to be a plain insert that crashed with a
                # duplicate-key error when the listener won the race; worse, an
                # earlier fix that merely adopted the existing row left `kind`
                # wrong, so `/engage end`/`/engage join` and NPC replies all
                # silently treated the thread as a non-engagement scene.)
                values = {
                    "district_id": district_id,
                    "location_id": loc_id,
                    "thread_id": thread.id,
                    "forum_channel_id": forum.id,
                    "kind": SceneKind.ENGAGEMENT.value,
                    "title": title,
                    "created_by_character_id": char_id,
                    "status": SceneStatus.OPEN.value,
                    "last_message_at": dt.datetime.now(dt.UTC),
                    "participants": participants_json,
                }
                stmt = pg_insert(Scene).values(**values)
                stmt = stmt.on_conflict_do_update(index_elements=["thread_id"], set_=values)
                await session.execute(stmt)
                scene = (
                    await session.execute(select(Scene).where(Scene.thread_id == thread.id))
                ).scalar_one()

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

    @start.autocomplete("participant_1")
    @start.autocomplete("participant_2")
    @start.autocomplete("participant_3")
    @start.autocomplete("participant_4")
    @start.autocomplete("participant_5")
    async def start_participant_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """One callback shared by every `participant_N` slot -- suggests
        both NPC names in the character's district and other approved
        characters physically at the (already-picked-or-defaulted)
        location, excluding whichever names are already sitting in the
        *other* slots so the same person can't be picked twice."""
        character_name = getattr(interaction.namespace, "character", None)
        if not character_name:
            return []
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character_name)
            if char is None:
                return []
            district_id = char.current_district_id
            loc_id = getattr(interaction.namespace, "location", None) or char.location_id
            npc_names = (
                (await session.execute(select(Npc.name).where(Npc.district_id == district_id)))
                .scalars()
                .all()
            )
            char_names: list[str] = []
            if loc_id is not None:
                char_names = (
                    (
                        await session.execute(
                            select(Character.name).where(
                                Character.status == CharacterStatus.APPROVED.value,
                                Character.location_id == loc_id,
                                Character.id != char.id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
        already_chosen = {
            getattr(interaction.namespace, f"participant_{i}", None) for i in range(1, 6)
        }
        already_chosen.discard(None)
        already_chosen.discard(current)
        current_lower = current.lower()
        matches = sorted(
            name
            for name in {*npc_names, *char_names}
            if current_lower in name.lower() and name not in already_chosen
        )
        return [app_commands.Choice(name=name, value=name) for name in matches[:25]]

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
            # "Engaged" means NPC participants are actually present, not
            # merely `kind == ENGAGEMENT` -- `/talk`/`/engage start` can
            # attach NPCs to any scene (an ambient thread, an open
            # `/scene`), not just a dedicated engagement thread.
            if scene is None or not scene.participants.get("npcs"):
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
            participants = dict(scene.participants)
            participants["npcs"] = []
            scene.participants = participants
            # Only a dedicated engagement thread gets archived -- an
            # ambient thread or an ordinary `/scene` that merely had NPCs
            # attached is permanent and stays open once they're released.
            is_dedicated_engagement = scene.kind == SceneKind.ENGAGEMENT.value
            if is_dedicated_engagement:
                scene.status = SceneStatus.ARCHIVED.value

        await thread.send(t("engagement_closing_line"))
        if not is_dedicated_engagement:
            await interaction.response.send_message(t("engagement_ended_ok"), ephemeral=True)
            return

        closed_tag = (
            _find_tag(thread.parent, CLOSED_TAG)
            if isinstance(thread.parent, discord.ForumChannel)
            else None
        )
        new_tags = [tg for tg in thread.applied_tags if tg.name != OPEN_TAG]
        if closed_tag is not None:
            new_tags.append(closed_tag)
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
            if scene is None or not scene.participants.get("npcs"):
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
            # Not filtered to `kind == ENGAGEMENT` -- `/talk`/`/engage
            # start` can attach NPCs to any open scene (an ambient thread,
            # an open `/scene`), so idle-release has to scan all of them;
            # `scenes_to_close` itself is what actually checks for NPC
            # participants.
            scenes = (
                (await session.execute(select(Scene).where(Scene.status == SceneStatus.OPEN.value)))
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
                participants = dict(scene.participants)
                participants["npcs"] = []
                scene.participants = participants
                # Only a dedicated engagement thread gets archived -- an
                # ambient thread or an ordinary `/scene` that merely had
                # NPCs attached is permanent and stays open once released.
                is_dedicated_engagement = scene.kind == SceneKind.ENGAGEMENT.value
                if is_dedicated_engagement:
                    scene.status = SceneStatus.ARCHIVED.value

                thread = self.bot.get_channel(scene.thread_id)
                if not isinstance(thread, discord.Thread):
                    continue
                try:
                    await thread.send(t("engagement_closing_line"))
                    if not is_dedicated_engagement:
                        continue
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
