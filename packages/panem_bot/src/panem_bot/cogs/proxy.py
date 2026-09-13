"""`/rp`, `/ooc`, and proxy posting (FR-PRX)."""

from __future__ import annotations

import contextlib
import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import redis_keys
from panem_bot.errors import NotAllowed
from panem_bot.outbound import OutboundMessage, SendPriority
from panem_bot.services import characters as characters_svc
from panem_bot.services import dialogue as dialogue_svc
from panem_bot.services import engagements as engagements_svc
from panem_bot.services import housing as housing_svc
from panem_bot.services import proxy as proxy_svc
from panem_bot.services import shifts as shifts_svc
from panem_bot.strings import t
from panem_shared import constants
from panem_shared.db.models import (
    Character,
    DialogueLog,
    DiscordChannel,
    DistrictState,
    Memory,
    Npc,
    RelationshipRow,
    Scene,
    SceneMessage,
    Shift,
    User,
    WorldClock,
)
from panem_shared.enums import ChannelKind, CharacterStatus, OwnerKind
from panem_shared.relationships import relationship_key


class ProxyCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------ commands

    @app_commands.command(name="rp", description="Set your active character for this scene")
    @app_commands.describe(character="Character name")
    async def rp(self, interaction: discord.Interaction, character: str) -> None:
        if not isinstance(interaction.channel, discord.Thread) or not isinstance(
            interaction.channel.parent, discord.ForumChannel
        ):
            await interaction.response.send_message(t("rp_needs_thread"), ephemeral=True)
            return
        async with self.bot.db() as session:
            forum_registered = (
                await session.execute(
                    select(DiscordChannel).where(
                        DiscordChannel.channel_id == interaction.channel.parent_id,
                        DiscordChannel.kind == ChannelKind.FORUM.value,
                    )
                )
            ).scalar_one_or_none()
            if forum_registered is None:
                await interaction.response.send_message(t("rp_needs_thread"), ephemeral=True)
                return

            user = await session.execute(select(User).where(User.discord_id == interaction.user.id))
            user_row = user.scalar_one_or_none()
            row = None
            if user_row is not None:
                row = (
                    await session.execute(
                        select(Character).where(
                            Character.user_id == user_row.id,
                            Character.name == character,
                            Character.status == CharacterStatus.APPROVED.value,
                        )
                    )
                ).scalar_one_or_none()
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            scene = (
                await session.execute(
                    select(Scene).where(Scene.thread_id == interaction.channel.id)
                )
            ).scalar_one_or_none()
            district = self.bot.content.district(forum_registered.district_id)
            refusal = proxy_svc.check_can_proxy(
                character=row,
                district=district,
                location_id=proxy_svc.scene_location_id(scene),
                current_tick=0,
            )
            if refusal is not None:
                await interaction.response.send_message(
                    t("proxy_no_access", name=row.name, reason=t(refusal.reason_key)),
                    ephemeral=True,
                )
                return

        await self.bot.redis.set(
            redis_keys.session_key(interaction.user.id, interaction.channel.id),
            str(row.id),
            ex=redis_keys.SESSION_TTL_S,
        )
        await interaction.response.send_message(t("rp_session_set", name=row.name), ephemeral=True)

    @rp.autocomplete("character")
    async def rp_character_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if not isinstance(interaction.channel, discord.Thread):
            return []
        async with self.bot.db() as session:
            forum_registered = (
                await session.execute(
                    select(DiscordChannel).where(
                        DiscordChannel.channel_id == interaction.channel.parent_id,
                        DiscordChannel.kind == ChannelKind.FORUM.value,
                    )
                )
            ).scalar_one_or_none()
            if forum_registered is None:
                return []
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            stmt = select(Character).where(
                Character.user_id == user.id,
                Character.status == CharacterStatus.APPROVED.value,
            )
            if current:
                stmt = stmt.where(Character.name.ilike(f"%{current}%"))
            rows = (await session.execute(stmt)).scalars().all()
            names = [
                c.name
                for c in rows
                if proxy_svc.can_rp_in_district(c, forum_registered.district_id)
            ][:25]
        return [app_commands.Choice(name=name, value=name) for name in names]

    @app_commands.command(name="ooc", description="Clear your active character for this scene")
    async def ooc(self, interaction: discord.Interaction) -> None:
        if isinstance(interaction.channel, discord.Thread):
            await self.bot.redis.delete(
                redis_keys.session_key(interaction.user.id, interaction.channel.id)
            )
        await interaction.response.send_message(t("ooc_cleared"), ephemeral=True)

    # ------------------------------------------------------------ proxying

    async def _apply_rp_credit(
        self, session: AsyncSession, character: Character, scene: Scene, content: str
    ) -> None:
        """FR-PRX-7: a long-enough proxied message completes an open shift
        with no separate command needed, counted as a win. Jobs are
        free-typed now (no catalog `workplace` to tag a scene against), so
        this no longer gates on `scene`/location -- any proxied RP while a
        shift is open counts, same as `can_earn_rp_credit_anywhere` already
        gave Gamemakers before the rework. Also touches `last_active_tick`
        (the district economy's active-player signal) regardless of
        whether there's an open shift to credit -- proxying at all counts
        as "active".

        A qualifying message also docks `FATIGUE_COST_PER_INTERACTION`
        regardless of whether there's an open shift to credit -- fatigue
        "goes down... based on how many times they work and interact with
        other players and NPCs," and RP is the interaction signal this
        codebase already has a length gate for (`meets_rp_credit`), so it
        doubles as the fatigue-interaction gate too rather than inventing
        a second threshold."""
        clock = await session.get(WorldClock, 1)
        current_tick = clock.tick if clock is not None else 0
        character.last_active_tick = current_tick

        qualifies = shifts_svc.meets_rp_credit(content)
        if qualifies:
            housing_svc.dock_fatigue(character, constants.FATIGUE_COST_PER_INTERACTION)

        open_shift = (
            await session.execute(
                select(Shift).where(Shift.character_id == character.id, Shift.result.is_(None))
            )
        ).scalar_one_or_none()
        if open_shift is None:
            return
        if not qualifies:
            return

        bot_content = self.bot.content  # type: ignore[attr-defined]
        district = bot_content.district(character.district_id)
        market_multiplier = await shifts_svc.market_multiplier_for_district(
            session, bot_content, district
        )
        outcome = shifts_svc.resolve_shift_game(
            character, district, won=True, market_multiplier=market_multiplier
        )
        shifts_svc.apply_shift_outcome(open_shift, character, outcome, won=True, tick=current_tick)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not isinstance(message.channel, discord.Thread):
            return
        if not isinstance(message.channel.parent, discord.ForumChannel):
            return

        thread = message.channel
        async with self.bot.db() as session:
            forum_registered = (
                await session.execute(
                    select(DiscordChannel).where(
                        DiscordChannel.channel_id == thread.parent_id,
                        DiscordChannel.kind == ChannelKind.FORUM.value,
                    )
                )
            ).scalar_one_or_none()
            if forum_registered is None:
                return

            scene = (
                await session.execute(select(Scene).where(Scene.thread_id == thread.id))
            ).scalar_one_or_none()

            session_raw = await self.bot.redis.get(
                redis_keys.session_key(message.author.id, thread.id)
            )
            session_character_id = int(session_raw) if session_raw is not None else None

            user_row = (
                await session.execute(select(User).where(User.discord_id == message.author.id))
            ).scalar_one_or_none()
            user_tags: dict[str, int] = {}
            if user_row is not None:
                tagged = (
                    await session.execute(
                        select(Character.proxy_tag, Character.id).where(
                            Character.user_id == user_row.id, Character.proxy_tag.is_not(None)
                        )
                    )
                ).all()
                user_tags = {tag: cid for tag, cid in tagged if tag}

            target = proxy_svc.resolve_proxy_target(
                content=message.content,
                session_character_id=session_character_id,
                user_tags=user_tags,
            )
            if target is None:
                # Never delete a forum post's own starter message -- doing so
                # deletes the whole thread, and `/rp` can't be run before the
                # thread (and this message) already exist.
                if not proxy_svc.is_ooc(message.content) and message.id != thread.id:
                    with contextlib.suppress(discord.HTTPException):
                        await message.delete()
                    # A DM keeps this private to the one person who needs to
                    # see it, rather than flashing a public reminder in front
                    # of everyone else in the scene (Discord has no
                    # "ephemeral" outside of interaction responses, so a DM
                    # is the closest equivalent for a plain message event).
                    try:
                        await message.author.send(t("rp_character_required"))
                    except discord.Forbidden:
                        notice = await thread.send(
                            f"{message.author.mention} {t('rp_character_required')}"
                        )
                        await notice.delete(delay=8)
                return

            character = await session.get(Character, target.character_id)
            if character is None:
                return

            district = self.bot.content.district(forum_registered.district_id)
            refusal = proxy_svc.check_can_proxy(
                character=character,
                district=district,
                location_id=proxy_svc.scene_location_id(scene),
                current_tick=0,
            )
            if refusal is not None:
                notice = await thread.send(
                    f"{message.author.mention} {t('proxy_no_access', name=character.name, reason=t(refusal.reason_key))}"
                )
                await notice.delete(delay=8)
                return

            content = message.content
            if target.via_tag:
                tag = next(tag for tag, cid in user_tags.items() if cid == character.id)
                content = proxy_svc.strip_tag_prefix(content, tag)

            webhook_row = forum_registered
            attachments = [await a.to_file() for a in message.attachments]

            if scene is not None:
                scene.last_message_at = dt.datetime.now(dt.UTC)
                pinned_location = proxy_svc.scene_location_id(scene)
                if pinned_location is not None:
                    character.location_id = pinned_location
                await self._apply_rp_credit(session, character, scene, content)

        with contextlib.suppress(discord.HTTPException):
            await message.delete()

        if webhook_row.webhook_id is None or webhook_row.webhook_token is None:
            return
        webhook = discord.Webhook.partial(
            webhook_row.webhook_id, webhook_row.webhook_token, client=self.bot
        )
        chunks = proxy_svc.split_for_webhook(content or "​")
        # "Engaged" means this scene currently has NPC participants
        # (`/talk`/`/engage` can attach NPCs to *any* scene -- an ambient
        # thread, an open `/scene`, not just a dedicated `SceneKind.
        # ENGAGEMENT` thread -- see `cogs/dialogue.py`'s docstring), not
        # merely that the scene's `kind` is ENGAGEMENT.
        is_engagement = scene is not None and bool(scene.participants.get("npcs"))

        async def send_chunk(chunk: str, files: list[discord.File], *, is_last: bool) -> None:
            sent = await webhook.send(
                chunk,
                username=character.name,
                avatar_url=character.avatar_url or discord.utils.MISSING,
                thread=thread,
                files=files,
                wait=True,
            )
            await self.bot.redis.set(
                redis_keys.proxy_key(sent.id),
                f"{message.author.id},{character.id}",
                ex=redis_keys.PROXY_TTL_S,
            )
            await self.bot.redis.sadd(redis_keys.scene_presence_key(thread.id), str(character.id))
            await self.bot.redis.expire(
                redis_keys.scene_presence_key(thread.id), redis_keys.PRESENCE_TTL_S
            )
            if is_last and is_engagement:
                assert scene is not None
                await self.post_engagement_replies(
                    webhook=webhook,
                    thread=thread,
                    scene_id=scene.id,
                    speaker_character_id=character.id,
                    speaker_message_id=sent.id,
                    message_content=content,
                )

        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            files = attachments if is_last else []
            await self.bot.outbound.enqueue(
                OutboundMessage(
                    thread_id=thread.id,
                    forum_channel_id=thread.parent_id,
                    priority=SendPriority.PLAYER,
                    send=(lambda c=chunk, f=files, last=is_last: send_chunk(c, f, is_last=last)),
                )
            )

    async def post_engagement_replies(
        self,
        *,
        webhook: discord.Webhook,
        thread: discord.Thread,
        scene_id: int,
        speaker_character_id: int,
        speaker_message_id: int,
        message_content: str,
    ) -> None:
        """After a player's line lands in an `ENGAGEMENT`-kind scene, let
        whichever NPCs should respond (`engagements_svc.npcs_that_should_
        reply` -- every joined NPC in a strict 1:1, only the ones actually
        named otherwise) generate and post a reply through the same
        webhook, as themselves -- exactly how the player's own line was
        just proxied, only NPC-authored. Not underscore-private: `/talk`
        (`panem_bot.cogs.dialogue`) posts its own player line directly
        (it isn't a raw channel message `on_message` can intercept) and
        calls this via `bot.get_cog("ProxyCog")` to trigger the NPC's
        reply the same way, rather than duplicating this whole method.
        Records both the player's line and each reply as a `SceneMessage`
        (read back as `history` for the next reply) and each NPC reply as
        a `DialogueLog` row -- the first
        real use of either table. Deliberately never touches `scene.
        last_message_at`: that column means "last time a *player* spoke"
        for `EngagementCog`'s idle-timeout background task, and an NPC
        replying forever must not keep an empty engagement alive."""
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            scene = await session.get(Scene, scene_id)
            speaker = await session.get(Character, speaker_character_id)
            if scene is None or speaker is None:
                return

            session.add(
                SceneMessage(
                    scene_id=scene.id,
                    thread_id=thread.id,
                    district_id=scene.district_id,
                    discord_message_id=speaker_message_id,
                    author_kind="character",
                    author_id=str(speaker.id),
                    author_name=speaker.name,
                    avatar_url=speaker.avatar_url,
                    content=message_content,
                    ts=dt.datetime.now(dt.UTC),
                )
            )

            participants = scene.participants
            npc_ids = participants.get("npcs", [])
            if not npc_ids:
                return
            npcs = (await session.execute(select(Npc).where(Npc.id.in_(npc_ids)))).scalars().all()
            is_one_on_one = len(participants.get("characters", [])) == 1 and len(npc_ids) == 1
            speaking = engagements_svc.npcs_that_should_reply(
                list(npcs), message_content, is_one_on_one=is_one_on_one
            )
            if not speaking:
                return

            other_character_ids = [
                cid for cid in participants.get("characters", []) if cid != speaker.id
            ]
            other_character_names = (
                (
                    await session.execute(
                        select(Character.name).where(Character.id.in_(other_character_ids))
                    )
                )
                .scalars()
                .all()
                if other_character_ids
                else []
            )

            history_rows = list(
                reversed(
                    (
                        await session.execute(
                            select(SceneMessage)
                            .where(SceneMessage.scene_id == scene.id)
                            .order_by(SceneMessage.ts.desc())
                            .limit(constants.MAX_ENGAGEMENT_HISTORY_TURNS)
                        )
                    )
                    .scalars()
                    .all()
                )
            )

            clock = await session.get(WorldClock, 1)
            current_tick = clock.tick if clock is not None else 0
            content_bundle = self.bot.content  # type: ignore[attr-defined]
            district = content_bundle.district(scene.district_id)
            location = next(
                (loc for loc in district.locations if loc.id == scene.location_id), None
            )
            if location is None:
                return

            # Everything an NPC's reply should actually be shaped by
            # beyond stance -- their own background, the speaker's
            # standing, and the district's state -- gathered once per
            # message rather than per NPC, since none of it changes
            # between the NPCs replying to the same line.
            district_state = await session.get(DistrictState, scene.district_id)
            character_job_title = speaker.job_title
            character_home_district = content_bundle.district(speaker.district_id)

            for npc in speaking:
                try:
                    await dialogue_svc.check_and_spend_stamina(
                        self.bot.redis,  # type: ignore[attr-defined]
                        npc=npc,
                        tick=current_tick,
                        ttl_seconds=self.bot.settings.tick_interval_seconds * 2,  # type: ignore[attr-defined]
                    )
                except NotAllowed:
                    continue

                key = relationship_key(
                    (OwnerKind.CHARACTER.value, str(speaker.id)), (OwnerKind.NPC.value, npc.id)
                )
                relationship = await session.get(RelationshipRow, key)
                stance = relationship.stance if relationship is not None else "stranger"
                memory_rows = (
                    (
                        await session.execute(
                            select(Memory).where(
                                Memory.owner_kind == "npc", Memory.owner_id == npc.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

                history = [
                    {
                        "role": "assistant"
                        if row.author_kind == "npc" and row.author_id == npc.id
                        else "user",
                        "content": row.content
                        if row.author_kind == "npc" and row.author_id == npc.id
                        else f"{row.author_name}: {row.content}",
                    }
                    for row in history_rows
                ]
                present = [other.name for other in npcs if other.id != npc.id] + list(
                    other_character_names
                )

                npc_job = content_bundle.jobs.get(npc.job_id) if npc.job_id else None
                npc_content = content_bundle.npcs.get(npc.id)
                npc_background = None
                if npc_content is not None:
                    npc_background = npc_content.backstory[
                        : constants.NPC_BACKGROUND_PROMPT_MAX_LEN
                    ]
                    if len(npc_content.backstory) > constants.NPC_BACKGROUND_PROMPT_MAX_LEN:
                        npc_background += "…"

                reply = await dialogue_svc.generate_reply(
                    npc=npc,
                    district=district,
                    location=location,
                    character=speaker,
                    stance=stance,
                    memories=list(memory_rows),
                    message=message_content,
                    settings=self.bot.settings,  # type: ignore[attr-defined]
                    history=history,
                    present=present,
                    npc_job_title=npc_job.title if npc_job is not None else None,
                    npc_background=npc_background,
                    district_state=district_state,
                    character_job_title=character_job_title,
                    character_home_district=character_home_district,
                )

                sent = await webhook.send(
                    reply,
                    username=npc.name,
                    avatar_url=npc.avatar_url or discord.utils.MISSING,
                    thread=thread,
                    wait=True,
                )
                reply_row = SceneMessage(
                    scene_id=scene.id,
                    thread_id=thread.id,
                    district_id=scene.district_id,
                    discord_message_id=sent.id,
                    author_kind="npc",
                    author_id=npc.id,
                    author_name=npc.name,
                    avatar_url=npc.avatar_url,
                    content=reply,
                    ts=dt.datetime.now(dt.UTC),
                )
                session.add(reply_row)
                history_rows.append(reply_row)
                session.add(
                    DialogueLog(
                        tick=current_tick,
                        npc_id=npc.id,
                        character_id=speaker.id,
                        scene_id=scene.id,
                        provider=dialogue_svc.resolve_provider(
                            npc,
                            self.bot.settings,  # type: ignore[attr-defined]
                        ),
                        context={"message": message_content},
                        output=reply,
                    )
                )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if str(payload.emoji) != "❌":
            return
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return

        mapping = await self.bot.redis.get(redis_keys.proxy_key(payload.message_id))
        if mapping is None:
            return
        origin_user_id_str, _character_id_str = mapping.split(",")
        origin_user_id = int(origin_user_id_str)

        is_origin_author = payload.user_id == origin_user_id
        is_staff = False
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        member = guild.get_member(payload.user_id) if guild else None
        if member is not None:
            is_staff = await self.bot.is_staff(member)

        if not (is_origin_author or is_staff):
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.Thread):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
            await message.delete()
        except discord.HTTPException:
            return
        await self.bot.redis.delete(redis_keys.proxy_key(payload.message_id))

        if is_staff and not is_origin_author:
            from panem_bot.services.staff import log_staff_action

            async with self.bot.db() as session:
                await log_staff_action(
                    session,
                    bot=self.bot,
                    staff_discord_id=payload.user_id,
                    action="proxy_delete",
                    target=str(payload.message_id),
                    payload={"thread_id": payload.channel_id},
                )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProxyCog(bot))
