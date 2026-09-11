"""`/rp`, `/ooc`, and proxy posting (FR-PRX)."""

from __future__ import annotations

import contextlib
import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from panem_bot import redis_keys
from panem_bot.outbound import OutboundMessage, SendPriority
from panem_bot.services import characters as characters_svc
from panem_bot.services import proxy as proxy_svc
from panem_bot.strings import t
from panem_shared.db.models import Character, DiscordChannel, Scene, User
from panem_shared.enums import ChannelKind, CharacterStatus, SceneKind


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
                location_id=scene.location_id if scene else None,
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
            stmt = select(Character.name).where(
                Character.user_id == user.id,
                Character.district_id == forum_registered.district_id,
                Character.status == CharacterStatus.APPROVED.value,
            )
            if current:
                stmt = stmt.where(Character.name.ilike(f"%{current}%"))
            names = (await session.execute(stmt.limit(25))).scalars().all()
        return [app_commands.Choice(name=name, value=name) for name in names]

    @app_commands.command(name="ooc", description="Clear your active character for this scene")
    async def ooc(self, interaction: discord.Interaction) -> None:
        if isinstance(interaction.channel, discord.Thread):
            await self.bot.redis.delete(
                redis_keys.session_key(interaction.user.id, interaction.channel.id)
            )
        await interaction.response.send_message(t("ooc_cleared"), ephemeral=True)

    # ------------------------------------------------------------ proxying

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

            district = self.bot.content.district(character.district_id)
            refusal = proxy_svc.check_can_proxy(
                character=character,
                district=district,
                location_id=scene.location_id if scene else None,
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
                if scene.kind != SceneKind.STAFF.value or scene.pins_location:
                    character.location_id = scene.location_id

        with contextlib.suppress(discord.HTTPException):
            await message.delete()

        if webhook_row.webhook_id is None or webhook_row.webhook_token is None:
            return
        webhook = discord.Webhook.partial(
            webhook_row.webhook_id, webhook_row.webhook_token, client=self.bot
        )
        chunks = proxy_svc.split_for_webhook(content or "​")

        async def send_chunk(chunk: str, files: list[discord.File]) -> None:
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

        for i, chunk in enumerate(chunks):
            files = attachments if i == len(chunks) - 1 else []
            await self.bot.outbound.enqueue(
                OutboundMessage(
                    thread_id=thread.id,
                    forum_channel_id=thread.parent_id,
                    priority=SendPriority.PLAYER,
                    send=(lambda c=chunk, f=files: send_chunk(c, f)),
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
                    staff_discord_id=payload.user_id,
                    action="proxy_delete",
                    target=str(payload.message_id),
                    payload={"thread_id": payload.channel_id},
                )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProxyCog(bot))
