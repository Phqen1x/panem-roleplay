"""`/staff ...` moderation and scene-management commands (Plan §10)."""

from __future__ import annotations

import datetime as dt
import re

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from panem_bot import redis_keys
from panem_bot.services.staff import log_staff_action
from panem_bot.strings import t
from panem_shared.db.models import Character, Scene, User
from panem_shared.enums import CharacterStatus, SceneStatus

MESSAGE_LINK_RE = re.compile(r"/channels/(\d+)/(\d+)/(\d+)$")


async def _is_staff(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    bot = interaction.client
    ok = await bot.is_staff(interaction.user)  # type: ignore[attr-defined]
    if not ok:
        await interaction.response.send_message(t("staff_only"), ephemeral=True)
    return ok


class StaffCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    group = app_commands.Group(name="staff", description="Staff tools", default_permissions=None)
    scene_group = app_commands.Group(
        name="scene", description="Staff scene management", parent=group
    )

    @group.command(name="whois", description="Look up who a proxied message belongs to")
    @app_commands.describe(message_link="Link to the proxied message")
    @app_commands.check(_is_staff)
    async def whois(self, interaction: discord.Interaction, message_link: str) -> None:
        match = MESSAGE_LINK_RE.search(message_link)
        if not match:
            await interaction.response.send_message("Not a message link.", ephemeral=True)
            return
        _guild_id, _channel_id, message_id = (int(x) for x in match.groups())

        mapping = await self.bot.redis.get(redis_keys.proxy_key(message_id))
        if mapping is None:
            await interaction.response.send_message(
                "No proxy record for that message.", ephemeral=True
            )
            return
        user_id_str, character_id_str = mapping.split(",")
        async with self.bot.db() as session:
            character = await session.get(Character, int(character_id_str))
        name = character.name if character else "?"
        await interaction.response.send_message(
            f"<@{user_id_str}> playing **{name}**", ephemeral=True
        )

    @group.command(name="ban", description="Ban a user from the bot")
    @app_commands.describe(user="User to ban")
    @app_commands.check(_is_staff)
    async def ban(self, interaction: discord.Interaction, user: discord.Member) -> None:
        async with self.bot.db() as session:
            row = (
                await session.execute(select(User).where(User.discord_id == user.id))
            ).scalar_one_or_none()
            if row is None:
                row = User(discord_id=user.id)
                session.add(row)
                await session.flush()
            row.banned_at = dt.datetime.now(dt.UTC)
            await log_staff_action(
                session, staff_discord_id=interaction.user.id, action="ban", target=str(user.id)
            )
        await interaction.response.send_message(f"Banned {user.mention}.", ephemeral=True)

    @group.command(name="kill", description="Kill a character")
    @app_commands.describe(character="Character name")
    @app_commands.check(_is_staff)
    async def kill(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:
            row = (
                await session.execute(select(Character).where(Character.name == character))
            ).scalar_one_or_none()
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            row.status = CharacterStatus.DEAD.value
            await log_staff_action(
                session, staff_discord_id=interaction.user.id, action="kill", target=str(row.id)
            )
        await interaction.response.send_message(f"**{character}** has died.", ephemeral=True)

    @group.command(name="note", description="Attach a staff note to a character")
    @app_commands.describe(character="Character name", text="Note text")
    @app_commands.check(_is_staff)
    async def note(self, interaction: discord.Interaction, character: str, text: str) -> None:
        async with self.bot.db() as session:
            row = (
                await session.execute(select(Character).where(Character.name == character))
            ).scalar_one_or_none()
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            await log_staff_action(
                session,
                staff_discord_id=interaction.user.id,
                action="note",
                target=str(row.id),
                payload={"text": text},
            )
        await interaction.response.send_message("Note logged.", ephemeral=True)

    @scene_group.command(name="lock", description="Lock this scene")
    @app_commands.check(_is_staff)
    async def scene_lock(self, interaction: discord.Interaction) -> None:
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message("Use this inside a scene.", ephemeral=True)
            return
        async with self.bot.db() as session:
            scene = (
                await session.execute(select(Scene).where(Scene.thread_id == thread.id))
            ).scalar_one_or_none()
            if scene is not None:
                scene.status = SceneStatus.LOCKED.value
            await log_staff_action(
                session,
                staff_discord_id=interaction.user.id,
                action="scene_lock",
                target=str(thread.id),
            )
        await thread.edit(locked=True, reason=f"Locked by {interaction.user}")
        await interaction.response.send_message("Scene locked.", ephemeral=True)

    @scene_group.command(name="archive", description="Archive this scene")
    @app_commands.check(_is_staff)
    async def scene_archive(self, interaction: discord.Interaction) -> None:
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message("Use this inside a scene.", ephemeral=True)
            return
        async with self.bot.db() as session:
            scene = (
                await session.execute(select(Scene).where(Scene.thread_id == thread.id))
            ).scalar_one_or_none()
            if scene is not None:
                scene.status = SceneStatus.ARCHIVED.value
            await log_staff_action(
                session,
                staff_discord_id=interaction.user.id,
                action="scene_archive",
                target=str(thread.id),
            )
        await thread.edit(archived=True, reason=f"Archived by {interaction.user}")
        await interaction.response.send_message("Scene archived.", ephemeral=True)

    @scene_group.command(name="move", description="Move this scene to another location")
    @app_commands.describe(location="New location id")
    @app_commands.check(_is_staff)
    async def scene_move(self, interaction: discord.Interaction, location: str) -> None:
        scenes_cog = self.bot.get_cog("SceneCog")
        if scenes_cog is None:
            await interaction.response.send_message("Scene cog not loaded.", ephemeral=True)
            return
        await scenes_cog.move.callback(scenes_cog, interaction, location)  # type: ignore[attr-defined]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StaffCog(bot))
