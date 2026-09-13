"""`/talk` -- LLM-driven NPC dialogue (Plan Phase 6, `lemonade/README.md`).

Folded into the engagement system: talking to an NPC opens (or reuses) a
one-NPC `SceneKind.ENGAGEMENT` thread and posts both the player's line and
the NPC's reply into it through the district forum's webhook, exactly like
any other proxied conversation -- not an ephemeral, thread-less one-shot
any more. `panem_bot.cogs.proxy.ProxyCog.post_engagement_replies` does the
actual reply generation/posting/history-recording; this command's own job
is just finding or creating the thread and posting the player's opening
line into it, since (unlike a normal proxied message) there's no raw
channel message here for `on_message` to intercept.
"""

from __future__ import annotations

import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete
from panem_bot.errors import NotAllowed, NotFound
from panem_bot.services import characters as characters_svc
from panem_bot.services import dialogue as dialogue_svc
from panem_bot.strings import t
from panem_shared.db.models import Character, DiscordChannel, Npc, Scene, WorldClock
from panem_shared.enums import ChannelKind, SceneKind, SceneStatus

from .scenes import OPEN_TAG, _find_tag


class DialogueCog(commands.Cog):
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

    @app_commands.command(name="talk", description="Talk to a resident NPC at your location")
    @app_commands.describe(
        character="Character name",
        resident="Resident's name",
        message="What your character says to them",
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def talk(
        self, interaction: discord.Interaction, character: str, resident: str, message: str
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.followup.send(t("character_not_found"), ephemeral=True)
                return

            district_id = char.current_district_id
            npc = (
                await session.execute(
                    select(Npc).where(Npc.district_id == district_id, Npc.name == resident)
                )
            ).scalar_one_or_none()
            if npc is None:
                await interaction.followup.send(t("resident_not_found"), ephemeral=True)
                return

            try:
                dialogue_svc.check_can_talk(char)
                if npc.location_id != char.location_id:
                    raise NotAllowed("talk_not_here", name=npc.name)

                clock = await session.get(WorldClock, 1)
                tick = clock.tick if clock is not None else 0
                await dialogue_svc.check_and_spend_stamina(
                    self.bot.redis,  # type: ignore[attr-defined]
                    npc=npc,
                    tick=tick,
                    ttl_seconds=self.bot.settings.tick_interval_seconds * 2,  # type: ignore[attr-defined]
                )
            except (NotAllowed, NotFound) as exc:
                await interaction.followup.send(t(exc.reason_key, **exc.fmt), ephemeral=True)
                return

            content_bundle = self.bot.content  # type: ignore[attr-defined]
            district = content_bundle.district(district_id)
            location = next((loc for loc in district.locations if loc.id == npc.location_id), None)
            if location is None:
                await interaction.followup.send(t("talk_not_here", name=npc.name), ephemeral=True)
                return

            # Reuse an already-open, exactly-this-pair engagement rather
            # than spawning a new thread every time the same two talk
            # again -- a real conversation lives in one place.
            open_engagements = (
                (
                    await session.execute(
                        select(Scene).where(
                            Scene.district_id == district_id,
                            Scene.location_id == npc.location_id,
                            Scene.kind == SceneKind.ENGAGEMENT.value,
                            Scene.status == SceneStatus.OPEN.value,
                        )
                    )
                )
                .scalars()
                .all()
            )
            existing_scene = next(
                (
                    s
                    for s in open_engagements
                    if s.participants.get("characters") == [char.id]
                    and s.participants.get("npcs") == [npc.id]
                    and not s.participants.get("pending_characters")
                ),
                None,
            )

            forum_row = await self._forum_for_district(session, district_id)
            if forum_row is None or forum_row.webhook_id is None or forum_row.webhook_token is None:
                await interaction.followup.send(
                    "This district has no forum configured.", ephemeral=True
                )
                return

            char_name, char_id, char_avatar = char.name, char.id, char.avatar_url
            npc_id, npc_name = npc.id, npc.name
            scene_id = existing_scene.id if existing_scene is not None else None
            thread_id = existing_scene.thread_id if existing_scene is not None else None
            forum_channel_id = forum_row.channel_id
            webhook_id, webhook_token = forum_row.webhook_id, forum_row.webhook_token

        webhook = discord.Webhook.partial(webhook_id, webhook_token, client=self.bot)

        if thread_id is not None:
            thread = self.bot.get_channel(thread_id)
            if not isinstance(thread, discord.Thread):
                thread = await interaction.guild.fetch_channel(thread_id)
        else:
            forum = interaction.guild.get_channel(forum_channel_id)
            if not isinstance(forum, discord.ForumChannel):
                await interaction.followup.send("Forum channel not found.", ephemeral=True)
                return
            tags = [
                tg
                for tg in (_find_tag(forum, location.name), _find_tag(forum, OPEN_TAG))
                if tg is not None
            ]
            thread_with_message = await forum.create_thread(
                name=f"{char_name} & {npc_name}",
                content=f"*{char_name} approaches {npc_name} at {location.name}.*",
                applied_tags=tags,
            )
            thread = thread_with_message.thread
            async with self.bot.db() as session:  # type: ignore[attr-defined]
                scene = Scene(
                    district_id=district_id,
                    location_id=npc.location_id,
                    thread_id=thread.id,
                    forum_channel_id=forum.id,
                    kind=SceneKind.ENGAGEMENT.value,
                    title=f"{char_name} & {npc_name}",
                    created_by_character_id=char_id,
                    status=SceneStatus.OPEN.value,
                    last_message_at=dt.datetime.now(dt.UTC),
                    participants={
                        "characters": [char_id],
                        "pending_characters": [],
                        "npcs": [npc_id],
                    },
                )
                session.add(scene)
                await session.flush()
                npc_row = await session.get(Npc, npc_id)
                if npc_row is not None:
                    npc_row.engagement_id = scene.id
                scene_id = scene.id

        assert isinstance(thread, discord.Thread)
        sent = await webhook.send(
            message,
            username=char_name,
            avatar_url=char_avatar or discord.utils.MISSING,
            thread=thread,
            wait=True,
        )

        proxy_cog = self.bot.get_cog("ProxyCog")
        if proxy_cog is not None:
            await proxy_cog.post_engagement_replies(  # type: ignore[attr-defined]
                webhook=webhook,
                thread=thread,
                scene_id=scene_id,
                speaker_character_id=char_id,
                speaker_message_id=sent.id,
                message_content=message,
            )
        await interaction.followup.send(f"Posted in {thread.mention}.", ephemeral=True)

    @talk.autocomplete("resident")
    async def talk_resident_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        character_name = getattr(interaction.namespace, "character", None)
        if not character_name:
            return []
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character_name)
            if char is None:
                return []
            names = (
                (
                    await session.execute(
                        select(Npc.name).where(Npc.district_id == char.current_district_id)
                    )
                )
                .scalars()
                .all()
            )
        current_lower = current.lower()
        matches = sorted(n for n in names if current_lower in n.lower())
        return [app_commands.Choice(name=n, value=n) for n in matches[:25]]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DialogueCog(bot))
