"""`/staff ...` moderation and scene-management commands (Plan §10)."""

from __future__ import annotations

import contextlib
import datetime as dt
import re

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete, redis_keys
from panem_bot.errors import ServiceError
from panem_bot.services import characters as characters_svc
from panem_bot.services import jobs as jobs_svc
from panem_bot.services.staff import log_staff_action
from panem_bot.strings import t
from panem_shared.db.models import Character, DistrictState, Inventory, Scene, User
from panem_shared.enums import CharacterStatus, DayPhase, OwnerKind, Position, SceneStatus

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
    job_group = app_commands.Group(
        name="job", description="Edit jobs per district without touching code", parent=group
    )
    give_group = app_commands.Group(
        name="give", description="Grant money or items to a character", parent=group
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

    @group.command(name="district", description="See a district's crisis/economy state (Phase 4)")
    @app_commands.describe(district="District number (0 = The Capitol)")
    @app_commands.check(_is_staff)
    async def district(
        self, interaction: discord.Interaction, district: app_commands.Range[int, 0, 12]
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            row = await session.get(DistrictState, district)
        if row is None:
            await interaction.response.send_message(
                "No district_state row yet -- the sim hasn't seeded it.", ephemeral=True
            )
            return

        district_name = self.bot.content.district(district).name  # type: ignore[attr-defined]
        embed = discord.Embed(title=f"{district_name} -- district state")
        embed.add_field(
            name="Crisis level", value=f"{row.crisis_level} ({row.crisis_kind or 'calm'})"
        )
        embed.add_field(name="Unrest", value=f"{row.unrest:.2f}")
        embed.add_field(name="Peacekeeper pressure", value=f"{row.peacekeeper_pressure:.2f}")
        embed.add_field(name="Morale", value=f"{row.morale:.0f}")
        embed.add_field(name="Capitol favor", value=f"{row.capitol_favor:+.1f}")
        embed.add_field(name="Quota", value=f"{row.quota_progress:.0f} / {row.quota_target:.0f}")
        embed.add_field(name="Treasury", value=f"{row.treasury:.0f}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
                session,
                bot=self.bot,
                staff_discord_id=interaction.user.id,
                action="ban",
                target=str(user.id),
            )
        await interaction.response.send_message(f"Banned {user.mention}.", ephemeral=True)

    @group.command(
        name="character_limit",
        description="Override how many active characters a user may have at once",
    )
    @app_commands.describe(
        user="User to override",
        limit="Max active characters (omit to reset to the guild default)",
    )
    @app_commands.check(_is_staff)
    async def character_limit(
        self, interaction: discord.Interaction, user: discord.Member, limit: int | None = None
    ) -> None:
        if limit is not None and limit < 0:
            await interaction.response.send_message("Limit must be 0 or more.", ephemeral=True)
            return
        async with self.bot.db() as session:
            row = await characters_svc.get_or_create_user(session, user.id)
            row.max_characters_override = limit
            await log_staff_action(
                session,
                bot=self.bot,
                staff_discord_id=interaction.user.id,
                action="character_limit",
                target=str(user.id),
                payload={"limit": limit},
            )
        if limit is None:
            await interaction.response.send_message(
                f"Reset {user.mention}'s character limit to the default "
                f"({self.bot.settings.max_characters_per_user}).",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Set {user.mention}'s character limit to {limit}.", ephemeral=True
            )

    @group.command(name="kill", description="Kill a character")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.any_approved)
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
                session,
                bot=self.bot,
                staff_discord_id=interaction.user.id,
                action="kill",
                target=str(row.id),
            )
        await interaction.response.send_message(f"**{character}** has died.", ephemeral=True)

    @group.command(name="note", description="Attach a staff note to a character")
    @app_commands.describe(character="Character name", text="Note text")
    @app_commands.autocomplete(character=autocomplete.any_approved)
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
                bot=self.bot,
                staff_discord_id=interaction.user.id,
                action="note",
                target=str(row.id),
                payload={"text": text},
            )
        await interaction.response.send_message("Note logged.", ephemeral=True)

    @group.command(name="delete_pending", description="Delete a pending character application")
    @app_commands.describe(
        character="Character name (pending only)",
        reason="Optional reason, sent to the applicant",
    )
    @app_commands.autocomplete(character=autocomplete.any_pending)
    @app_commands.check(_is_staff)
    async def delete_pending(
        self, interaction: discord.Interaction, character: str, reason: str | None = None
    ) -> None:
        async with self.bot.db() as session:
            row = (
                await session.execute(select(Character).where(Character.name == character))
            ).scalar_one_or_none()
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            if row.status != CharacterStatus.PENDING.value:
                await interaction.response.send_message(t("not_pending"), ephemeral=True)
                return
            char_id = row.id
            char_name = row.name
            user_row = await session.get(User, row.user_id)
            applicant_discord_id = user_row.discord_id if user_row else None
            await session.delete(row)
            await log_staff_action(
                session,
                bot=self.bot,
                staff_discord_id=interaction.user.id,
                action="delete_pending",
                target=str(char_id),
                payload={"name": char_name, "reason": reason},
            )
        await interaction.response.send_message(
            f"Deleted pending application **{char_name}**.", ephemeral=True
        )

        member = (
            interaction.guild.get_member(applicant_discord_id)
            if interaction.guild and applicant_discord_id
            else None
        )
        if member:
            note = f" Reason: {reason}" if reason else ""
            with contextlib.suppress(discord.Forbidden):
                await member.send(
                    f"Your pending character application for **{char_name}** was deleted by "
                    f"staff.{note}"
                )

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
                bot=self.bot,
                staff_discord_id=interaction.user.id,
                action="scene_lock",
                target=str(thread.id),
            )
        await interaction.response.send_message("Scene locked.", ephemeral=True)
        await thread.edit(locked=True, reason=f"Locked by {interaction.user}")

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
                bot=self.bot,
                staff_discord_id=interaction.user.id,
                action="scene_archive",
                target=str(thread.id),
            )
        # Reply before archiving: an interaction response into an
        # already-archived thread is refused with 403 "Thread is archived".
        await interaction.response.send_message("Scene archived.", ephemeral=True)
        await thread.edit(archived=True, reason=f"Archived by {interaction.user}")

    @scene_group.command(name="move", description="Move this scene to another location")
    @app_commands.describe(location="New location id")
    @app_commands.check(_is_staff)
    async def scene_move(self, interaction: discord.Interaction, location: str) -> None:
        scenes_cog = self.bot.get_cog("SceneCog")
        if scenes_cog is None:
            await interaction.response.send_message("Scene cog not loaded.", ephemeral=True)
            return
        await scenes_cog.move.callback(scenes_cog, interaction, location)  # type: ignore[attr-defined]

    @scene_move.autocomplete("location")
    async def scene_move_location_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        # This command's own body just delegates to /scene move's callback,
        # since it's the same operation with a staff-only check in front --
        # its autocomplete has to be registered here too, though, since
        # Discord ties autocomplete to the specific command that owns the
        # option, not to whatever that command's body happens to call.
        scenes_cog = self.bot.get_cog("SceneCog")
        if scenes_cog is None:
            return []
        return await scenes_cog.move_location_autocomplete(interaction, current)  # type: ignore[attr-defined]

    @job_group.command(
        name="list", description="List catalog jobs (NPCs only -- players free-type their own)"
    )
    @app_commands.describe(district="District number (0 = The Capitol)")
    @app_commands.autocomplete(district=autocomplete.districts)
    @app_commands.check(_is_staff)
    async def job_list(self, interaction: discord.Interaction, district: int) -> None:
        async with self.bot.db() as session:
            jobs = await jobs_svc.jobs_for_district(session, self.bot.content, district)
        if not jobs:
            await interaction.response.send_message(
                f"No jobs in district {district}.", ephemeral=True
            )
            return
        lines = [f"**{j.id}** - {j.title} @ {j.workplace} ({j.slots} slots)" for j in jobs]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    async def _find_character(self, session: AsyncSession, name: str) -> Character | None:
        return (
            await session.execute(select(Character).where(Character.name == name))
        ).scalar_one_or_none()

    @give_group.command(name="money", description="Grant (or deduct) a character's money")
    @app_commands.describe(
        character="Character name", amount="Amount to add -- use a negative number to deduct"
    )
    @app_commands.autocomplete(character=autocomplete.any_approved)
    @app_commands.check(_is_staff)
    async def give_money(
        self, interaction: discord.Interaction, character: str, amount: int
    ) -> None:
        async with self.bot.db() as session:
            row = await self._find_character(session, character)
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            row.money = max(0, row.money + amount)
            await log_staff_action(
                session,
                bot=self.bot,
                staff_discord_id=interaction.user.id,
                action="give_money",
                target=str(row.id),
                payload={"amount": amount},
            )
            new_balance = row.money
        await interaction.response.send_message(
            f"**{character}** now has {new_balance} money.", ephemeral=True
        )

    @give_group.command(name="item", description="Grant (or remove) an inventory item")
    @app_commands.describe(
        character="Character name",
        good="Good id",
        qty="Quantity to add -- use a negative number to remove",
    )
    @app_commands.autocomplete(character=autocomplete.any_approved)
    @app_commands.check(_is_staff)
    async def give_item(
        self, interaction: discord.Interaction, character: str, good: str, qty: int
    ) -> None:
        if good not in self.bot.content.goods:  # type: ignore[attr-defined]
            await interaction.response.send_message(t("staff_good_not_found"), ephemeral=True)
            return
        async with self.bot.db() as session:
            row = await self._find_character(session, character)
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            owner_id = str(row.id)
            inv = await session.get(Inventory, (OwnerKind.CHARACTER.value, owner_id, good))
            current = inv.qty if inv is not None else 0
            new_qty = max(0, current + qty)
            if inv is None:
                inv = Inventory(
                    owner_kind=OwnerKind.CHARACTER.value,
                    owner_id=owner_id,
                    good_id=good,
                    qty=new_qty,
                )
                session.add(inv)
            else:
                inv.qty = new_qty
            await log_staff_action(
                session,
                bot=self.bot,
                staff_discord_id=interaction.user.id,
                action="give_item",
                target=str(row.id),
                payload={"good": good, "qty": qty},
            )
        await interaction.response.send_message(
            f"**{character}** now has {new_qty}x **{good}**.", ephemeral=True
        )

    @give_item.autocomplete("good")
    async def give_item_good_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        current_lower = current.lower()
        matches = [
            good
            for good in self.bot.content.goods.values()  # type: ignore[attr-defined]
            if current_lower in good.id.lower() or current_lower in good.name.lower()
        ]
        return [
            app_commands.Choice(name=f"{good.name} ({good.id})", value=good.id)
            for good in matches[:25]
        ]

    @give_group.command(name="position", description="Grant or revoke a special position")
    @app_commands.describe(
        character="Character name",
        position="Victor, Gamemaker, or Governor",
        grant="True to grant, false to revoke",
    )
    @app_commands.autocomplete(character=autocomplete.any_approved)
    @app_commands.check(_is_staff)
    async def give_position(
        self,
        interaction: discord.Interaction,
        character: str,
        position: Position,
        grant: bool = True,
    ) -> None:
        async with self.bot.db() as session:
            row = await self._find_character(session, character)
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            held = set(row.positions)
            if grant:
                held.add(position.value)
            else:
                held.discard(position.value)
            row.positions = sorted(held)
            await log_staff_action(
                session,
                bot=self.bot,
                staff_discord_id=interaction.user.id,
                action="give_position",
                target=str(row.id),
                payload={"position": position.value, "grant": grant},
            )
            current = row.positions
        verb = "now holds" if grant else "no longer holds"
        summary = ", ".join(p.title() for p in current) or "none"
        await interaction.response.send_message(
            f"**{character}** {verb} **{position.value.title()}**. Current positions: {summary}.",
            ephemeral=True,
        )

    @give_group.command(
        name="job", description="Set (or change) a character's job title and shift, staff-only"
    )
    @app_commands.describe(
        character="Character name",
        job_title="Free-typed job title -- same field the player set at character creation",
        shift_phase="When they work their shift",
    )
    @app_commands.autocomplete(character=autocomplete.any_approved)
    @app_commands.check(_is_staff)
    async def give_job(
        self,
        interaction: discord.Interaction,
        character: str,
        job_title: str,
        shift_phase: DayPhase,
    ) -> None:
        try:
            characters_svc.validate_job_title(job_title)
        except ServiceError as exc:
            await interaction.response.send_message(t(exc.reason_key, **exc.fmt), ephemeral=True)
            return
        async with self.bot.db() as session:
            row = await self._find_character(session, character)
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            row.job_title = job_title
            row.shift_phase = shift_phase.value
            row.job_started_tick = None
            row.consecutive_missed = 0
            await log_staff_action(
                session,
                bot=self.bot,
                staff_discord_id=interaction.user.id,
                action="give_job",
                target=str(row.id),
                payload={"job_title": job_title, "shift_phase": shift_phase.value},
            )
        await interaction.response.send_message(
            f"**{character}** is now working as **{job_title}** ({shift_phase.value} shift).",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StaffCog(bot))
