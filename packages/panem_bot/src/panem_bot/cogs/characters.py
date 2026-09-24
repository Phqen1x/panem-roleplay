"""`/character ...` commands and the create -> approve/reject flow (FR-CHR)."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands, tasks
from sqlalchemy import func, select

from panem_bot import autocomplete
from panem_bot.errors import ServiceError, ValidationFailed
from panem_bot.services import afflictions as afflictions_svc
from panem_bot.services import characters as characters_svc
from panem_bot.services import rp_modes as rp_modes_svc
from panem_bot.strings import t
from panem_bot.views import (
    CHAR_ID_FOOTER_PREFIX,
    RP_MODE_DESCRIPTIONS,
    SHIFT_PHASE_LABELS,
    ApprovalView,
    ConfirmView,
    IllicitDeclareView,
    RpModeSelectView,
    ShiftPhaseSelectView,
)
from panem_shared import constants, job_levels
from panem_shared.db.models import (
    AfflictionType,
    Character,
    CharacterAffliction,
    Shift,
    User,
    WorldClock,
)
from panem_shared.enums import CharacterStatus, DayPhase, RpMode
from panem_shared.logging import get_logger
from panem_shared.redis_keys import CHARACTER_PENDING_CHANNEL
from panem_shared.simtime import clock_string, phase_time_range

logger = get_logger(component="characters")

EMBED_FIELD_VALUE_LIMIT = 1024


def _field_value(text: str) -> str:
    """Backstory allows more characters than a Discord embed field value
    does (1024) -- truncate rather than let `channel.send` raise."""
    if len(text) <= EMBED_FIELD_VALUE_LIMIT:
        return text
    return text[: EMBED_FIELD_VALUE_LIMIT - 1] + "…"


class CharacterCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # `add_view` alone only makes the bot route interactions for these
        # static custom_ids; a message still needs `view=self.approval_view`
        # passed explicitly when sent, or it has no components at all.
        self.approval_view = ApprovalView(
            is_staff=self._interaction_is_staff,
            on_approve=self._handle_approve,
            on_changes=self._handle_changes,
            on_reject=self._handle_reject,
        )
        self.bot.add_view(self.approval_view)
        self._announce_pending_characters.start()
        self._pending_listener_task = asyncio.create_task(self._listen_for_pending_characters())

    async def cog_unload(self) -> None:
        self._announce_pending_characters.cancel()
        self._pending_listener_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._pending_listener_task

    async def _interaction_is_staff(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        return await self.bot.is_staff(interaction.user)

    # ---------------------------------------------------------------- create

    group = app_commands.Group(name="character", description="Manage your characters")

    @group.command(name="create", description="Create a new character")
    async def create(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        matches = self.bot.districts_for_member(interaction.user)
        if len(matches) != 1:
            key = "no_district_role" if not matches else "ambiguous_district_role"
            await interaction.response.send_message(t(key), ephemeral=True)
            return
        district_id = matches[0]

        async with self.bot.db() as session:
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            if user.banned_at is not None:
                await interaction.response.send_message(t("banned"), ephemeral=True)
                return
            max_characters = characters_svc.effective_max_characters(user, self.bot.settings)
            count = await session.execute(
                select(func.count())
                .select_from(Character)
                .where(
                    Character.user_id == user.id,
                    Character.status.in_(
                        [CharacterStatus.PENDING.value, CharacterStatus.APPROVED.value]
                    ),
                )
            )
            if int(count.scalar_one()) >= max_characters:
                await interaction.response.send_message(
                    t("too_many_characters", limit=max_characters), ephemeral=True
                )
                return

        await self._prompt_mode(interaction, district_id)

    async def _prompt_mode(self, interaction: discord.Interaction, district_id: int) -> None:
        async def on_mode_chosen(mode_interaction: discord.Interaction, rp_mode: str) -> None:
            await self._prompt_details(mode_interaction, district_id, rp_mode)

        mode_lines = "\n".join(
            f"**{mode.title()}** -- {desc}" for mode, desc in RP_MODE_DESCRIPTIONS.items()
        )
        await interaction.response.send_message(
            "**Choose your character's roleplay mode** (changeable later, with a "
            f"{constants.MODE_SWITCH_COOLDOWN_DAYS}-real-day cooldown):\n{mode_lines}",
            view=RpModeSelectView(on_mode_chosen),
            ephemeral=True,
        )

    async def _prompt_details(
        self, interaction: discord.Interaction, district_id: int, rp_mode: str
    ) -> None:
        from panem_bot.modals import CharacterDetailsModal

        async def on_submit(
            modal_interaction: discord.Interaction,
            name: str,
            age_str: str,
            appearance: str,
            backstory: str,
            job_title: str,
        ) -> None:
            await self._validate_details_and_prompt_shift_phase(
                modal_interaction,
                district_id,
                rp_mode,
                name,
                age_str,
                appearance,
                backstory,
                job_title,
            )

        max_age = characters_svc.max_age_for_district(district_id)
        placeholder = f"{constants.CHARACTER_AGE_MIN}-{max_age}"
        is_story = rp_mode == RpMode.STORY.value
        await interaction.response.send_modal(
            CharacterDetailsModal(
                on_submit=on_submit, age_placeholder=placeholder, is_story=is_story
            )
        )

    async def _validate_details_and_prompt_shift_phase(
        self,
        interaction: discord.Interaction,
        district_id: int,
        rp_mode: str,
        name: str,
        age_str: str,
        appearance: str,
        backstory: str,
        job_title: str,
    ) -> None:
        try:
            age = int(age_str)
        except ValueError:
            max_age = characters_svc.max_age_for_district(district_id)
            await interaction.response.send_message(
                t("invalid_age", min=constants.CHARACTER_AGE_MIN, max=max_age), ephemeral=True
            )
            return
        is_story = rp_mode == RpMode.STORY.value
        try:
            characters_svc.validate_character_fields(
                district_id=district_id,
                name=name,
                age=age,
                appearance=appearance,
                backstory=backstory,
            )
            if not is_story:
                characters_svc.validate_job_title(job_title)
        except ValidationFailed as exc:
            await interaction.response.send_message(t(exc.reason_key, **exc.fmt), ephemeral=True)
            return

        async with self.bot.db() as session:
            try:
                await characters_svc.ensure_name_available(session, name)
            except ValidationFailed as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

        if is_story:
            # Story-mode characters never work -- skip both the shift-phase
            # and illicit-declaration steps entirely.
            await self._finish_create(
                interaction,
                district_id,
                rp_mode,
                name,
                age,
                appearance,
                backstory,
                None,
                None,
                False,
            )
            return

        async def on_phase_chosen(phase_interaction: discord.Interaction, shift_phase: str) -> None:
            await self._prompt_illicit(
                phase_interaction,
                district_id,
                rp_mode,
                name,
                age,
                appearance,
                backstory,
                job_title,
                shift_phase,
            )

        await interaction.response.send_message(
            "When does your character work their shift?",
            view=ShiftPhaseSelectView(on_phase_chosen),
            ephemeral=True,
        )

    async def _prompt_illicit(
        self,
        interaction: discord.Interaction,
        district_id: int,
        rp_mode: str,
        name: str,
        age: int,
        appearance: str,
        backstory: str,
        job_title: str,
        shift_phase: str,
    ) -> None:
        async def on_illicit_chosen(
            illicit_interaction: discord.Interaction, job_is_illicit: bool
        ) -> None:
            await self._finish_create(
                illicit_interaction,
                district_id,
                rp_mode,
                name,
                age,
                appearance,
                backstory,
                job_title,
                shift_phase,
                job_is_illicit,
            )

        await interaction.response.send_message(
            "Is this job illicit -- under-the-table work the Capitol doesn't sanction "
            "(smuggling, black-market trading, and the like)?",
            view=IllicitDeclareView(on_illicit_chosen),
            ephemeral=True,
        )

    async def _finish_create(
        self,
        interaction: discord.Interaction,
        district_id: int,
        rp_mode: str,
        name: str,
        age: int,
        appearance: str,
        backstory: str,
        job_title: str | None,
        shift_phase: str | None,
        job_is_illicit: bool,
    ) -> None:
        async with self.bot.db() as session:
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            try:
                character = await characters_svc.create_character(
                    session,
                    user=user,
                    district_id=district_id,
                    name=name,
                    age=age,
                    appearance=appearance,
                    backstory=backstory,
                    job_title=job_title,
                    shift_phase=shift_phase,
                    job_is_illicit=job_is_illicit,
                    max_characters=characters_svc.effective_max_characters(user, self.bot.settings),
                    rp_mode=rp_mode,
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            character_id = character.id

        await interaction.response.send_message(t("character_created", name=name), ephemeral=True)
        await self._post_approval_embed(character_id, applicant_discord_id=interaction.user.id)

    async def _post_approval_embed(self, character_id: int, *, applicant_discord_id: int) -> None:
        """Posts the staff-approval embed and stamps `approval_notified_at`
        so `_announce_pending_characters` doesn't post it again. Takes a
        bare `applicant_discord_id` rather than an `interaction` so the
        background task (announcing a character the web dashboard created,
        with no interaction of its own) can call this too."""
        channel = self.bot.get_channel(self.bot.settings.approval_channel_id)
        if channel is None:
            return
        async with self.bot.db() as session:
            character = await characters_svc.get_character(session, character_id)
            if character.approval_notified_at is not None:
                # Already posted -- both the poll and the instant Redis
                # listener below can end up calling this for the same
                # character in a rare near-simultaneous race; this makes
                # the method idempotent regardless of caller.
                return
            district = self.bot.content.district(character.district_id)
            embed = discord.Embed(
                title=f"Character Application: {character.name}", color=discord.Color.blurple()
            )
            embed.add_field(name="Applicant", value=f"<@{applicant_discord_id}>", inline=True)
            embed.add_field(name="District", value=district.name, inline=True)
            embed.add_field(name="Age", value=str(character.age), inline=True)
            embed.add_field(name="RP Mode", value=character.rp_mode.title(), inline=True)
            if character.job_title is not None:
                shift_label = SHIFT_PHASE_LABELS.get(
                    character.shift_phase or "", character.shift_phase
                )
                illicit_suffix = " [illicit]" if character.job_is_illicit else ""
                job_value = f"{character.job_title} ({shift_label} shift){illicit_suffix}"
            else:
                job_value = "-- (Story mode)"
            embed.add_field(name="Desired Job", value=job_value, inline=True)
            embed.add_field(name="Appearance", value=character.appearance or "-", inline=False)
            embed.add_field(
                name="Backstory", value=_field_value(character.backstory or "-"), inline=False
            )
            if character.avatar_url:
                embed.set_thumbnail(url=character.avatar_url)
            embed.set_footer(text=f"{CHAR_ID_FOOTER_PREFIX}{character.id}")
            character.approval_notified_at = dt.datetime.now(dt.UTC)

        await channel.send(embed=embed, view=self.approval_view)

    @tasks.loop(minutes=constants.CHARACTER_APPROVAL_POLL_INTERVAL_MINUTES)
    async def _announce_pending_characters(self) -> None:
        """Picks up characters the web dashboard created (`panem_api.
        dashboard_routes`, no bot token of its own to post an embed with)
        and announces them exactly like `/character create` announces its
        own -- everything else about approval (the buttons, `/staff
        approve`, ...) is unchanged either way."""
        async with self.bot.db() as session:
            rows = (
                await session.execute(
                    select(Character.id, User.discord_id)
                    .join(User, User.id == Character.user_id)
                    .where(
                        Character.status == CharacterStatus.PENDING.value,
                        Character.approval_notified_at.is_(None),
                    )
                )
            ).all()
        for character_id, discord_id in rows:
            await self._post_approval_embed(character_id, applicant_discord_id=discord_id)

    @_announce_pending_characters.before_loop
    async def _before_announce_pending_characters(self) -> None:
        await self.bot.wait_until_ready()

    async def _try_announce(self, character_id: int) -> None:
        """`_listen_for_pending_characters`'s per-message handler: looks up
        the applicant's `discord_id` and confirms the character is still
        pending and unannounced before calling `_post_approval_embed` --
        same shape as `_announce_pending_characters`'s own query, just for
        one character instead of every outstanding one."""
        async with self.bot.db() as session:
            row = (
                await session.execute(
                    select(Character.id, User.discord_id)
                    .join(User, User.id == Character.user_id)
                    .where(
                        Character.id == character_id,
                        Character.status == CharacterStatus.PENDING.value,
                        Character.approval_notified_at.is_(None),
                    )
                )
            ).first()
        if row is None:
            return
        _, discord_id = row
        await self._post_approval_embed(character_id, applicant_discord_id=discord_id)

    async def _listen_for_pending_characters(self) -> None:
        """Instant counterpart to `_announce_pending_characters`'s poll:
        `panem_api.dashboard_routes`'s character-creation endpoint
        publishes the new character's id on `CHARACTER_PENDING_CHANNEL`
        the moment it's created, so a dashboard-made character gets
        announced right away instead of waiting up to `constants.
        CHARACTER_APPROVAL_POLL_INTERVAL_MINUTES` for the poll's next
        pass (which stays running as a fallback for a publish that never
        reaches a listening bot, e.g. it was down at that moment)."""
        await self.bot.wait_until_ready()
        pubsub = self.bot.redis.pubsub()
        await pubsub.subscribe(CHARACTER_PENDING_CHANNEL)
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    character_id = int(message["data"])
                except (TypeError, ValueError):
                    logger.warning("pending_character_bad_payload", data=message["data"])
                    continue
                try:
                    await self._try_announce(character_id)
                except Exception:
                    logger.exception("pending_character_announce_failed", character_id=character_id)
        finally:
            await pubsub.unsubscribe(CHARACTER_PENDING_CHANNEL)
            await pubsub.aclose()

    # --------------------------------------------------------- approval flow

    async def _handle_approve(self, interaction: discord.Interaction, character_id: int) -> None:
        async with self.bot.db() as session:
            try:
                character = await characters_svc.get_character(session, character_id)
                district = self.bot.content.district(character.district_id)
                await characters_svc.approve_character(session, character, district=district)
            except ServiceError:
                await interaction.response.send_message("Already handled.", ephemeral=True)
                return
            user = await session.get(User, character.user_id)
            char_name, discord_id = character.name, user.discord_id

        await self._disable_approval_message(interaction, f"Approved by {interaction.user.mention}")
        await interaction.response.send_message(f"Approved **{char_name}**.", ephemeral=True)

        guild = interaction.guild
        assert guild is not None
        member = guild.get_member(discord_id)
        if member:
            with contextlib.suppress(discord.Forbidden):
                await member.send(
                    t("character_approved_dm", name=char_name, district=district.name)
                )

    async def _handle_changes(
        self, interaction: discord.Interaction, character_id: int, note: str
    ) -> None:
        async with self.bot.db() as session:
            character = await characters_svc.get_character(session, character_id)
            characters_svc.request_changes(character)
            char_name, discord_id = (
                character.name,
                (await session.get(User, character.user_id)).discord_id,
            )

        await self._disable_approval_message(
            interaction, f"Changes requested by {interaction.user.mention}"
        )
        await interaction.response.send_message("Requested changes.", ephemeral=True)

        member = interaction.guild.get_member(discord_id) if interaction.guild else None
        if member:
            with contextlib.suppress(discord.Forbidden):
                await member.send(t("character_changes_dm", name=char_name, note=note))

    async def _handle_reject(
        self, interaction: discord.Interaction, character_id: int, reason: str
    ) -> None:
        async with self.bot.db() as session:
            character = await characters_svc.get_character(session, character_id)
            characters_svc.reject_character(character)
            district = self.bot.content.district(character.district_id)
            discord_id = (await session.get(User, character.user_id)).discord_id
            char_name = character.name

            log_embed = discord.Embed(
                title=f"Character Rejected: {char_name}", color=discord.Color.red()
            )
            log_embed.add_field(name="Applicant", value=f"<@{discord_id}>", inline=True)
            log_embed.add_field(name="District", value=district.name, inline=True)
            log_embed.add_field(name="Age", value=str(character.age), inline=True)
            log_embed.add_field(
                name="Appearance", value=_field_value(character.appearance or "-"), inline=False
            )
            log_embed.add_field(
                name="Backstory", value=_field_value(character.backstory or "-"), inline=False
            )
            log_embed.add_field(name="Rejected by", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Reason", value=_field_value(reason or "-"), inline=True)

            # Rejected applications never became real characters -- log the
            # details to #panem-log for the record, then drop the row rather
            # than keeping a `rejected` character around forever.
            await session.delete(character)

        log_channel = interaction.client.get_channel(self.bot.settings.log_channel_id)
        if isinstance(log_channel, discord.TextChannel):
            await log_channel.send(embed=log_embed)

        await self._disable_approval_message(interaction, f"Rejected by {interaction.user.mention}")
        await interaction.response.send_message("Rejected.", ephemeral=True)

        member = interaction.guild.get_member(discord_id) if interaction.guild else None
        if member:
            with contextlib.suppress(discord.Forbidden):
                await member.send(t("character_rejected_dm", name=char_name, note=reason))

    async def _disable_approval_message(self, interaction: discord.Interaction, note: str) -> None:
        message = interaction.message
        if message is None:
            return
        embed = message.embeds[0] if message.embeds else discord.Embed()
        embed.add_field(name="Resolution", value=note, inline=False)
        await message.edit(embed=embed, view=None)

    # ------------------------------------------------------------ commands

    @group.command(name="list", description="List your characters")
    async def list_characters(self, interaction: discord.Interaction) -> None:
        async with self.bot.db() as session:
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            rows = (
                (await session.execute(select(Character).where(Character.user_id == user.id)))
                .scalars()
                .all()
            )
        if not rows:
            await interaction.response.send_message("You have no characters yet.", ephemeral=True)
            return
        lines = [
            f"**{c.name}** — {self.bot.content.district(c.district_id).name} — {c.status}"
            for c in rows
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @group.command(name="edit", description="Edit a pending character and resubmit for approval")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_pending)
    async def edit(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            row = (
                await session.execute(
                    select(Character).where(
                        Character.user_id == user.id, Character.name == character
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            if row.status != CharacterStatus.PENDING.value:
                await interaction.response.send_message(t("not_pending"), ephemeral=True)
                return
            character_id = row.id
            district_id = row.district_id
            current_shift_phase = row.shift_phase
            prefill = {
                "name": row.name,
                "age": str(row.age),
                "appearance": row.appearance,
                "backstory": row.backstory,
                "job_title": row.job_title or "",
            }

        from panem_bot.modals import CharacterDetailsModal

        async def on_submit(
            modal_interaction: discord.Interaction,
            name: str,
            age_str: str,
            appearance: str,
            backstory: str,
            job_title: str,
        ) -> None:
            await self._validate_edit_and_prompt_shift_phase(
                modal_interaction,
                character_id,
                district_id,
                name,
                age_str,
                appearance,
                backstory,
                job_title,
                current_shift_phase,
            )

        max_age = characters_svc.max_age_for_district(district_id)
        placeholder = f"{constants.CHARACTER_AGE_MIN}-{max_age}"
        await interaction.response.send_modal(
            CharacterDetailsModal(on_submit=on_submit, age_placeholder=placeholder, prefill=prefill)
        )

    async def _validate_edit_and_prompt_shift_phase(
        self,
        interaction: discord.Interaction,
        character_id: int,
        district_id: int,
        name: str,
        age_str: str,
        appearance: str,
        backstory: str,
        job_title: str,
        current_shift_phase: str | None,
    ) -> None:
        try:
            age = int(age_str)
        except ValueError:
            max_age = characters_svc.max_age_for_district(district_id)
            await interaction.response.send_message(
                t("invalid_age", min=constants.CHARACTER_AGE_MIN, max=max_age), ephemeral=True
            )
            return
        try:
            characters_svc.validate_character_fields(
                district_id=district_id,
                name=name,
                age=age,
                appearance=appearance,
                backstory=backstory,
            )
            characters_svc.validate_job_title(job_title)
        except ValidationFailed as exc:
            await interaction.response.send_message(t(exc.reason_key, **exc.fmt), ephemeral=True)
            return

        async with self.bot.db() as session:
            row = await characters_svc.get_character(session, character_id)
            if row.status != CharacterStatus.PENDING.value:
                await interaction.response.send_message(t("not_pending"), ephemeral=True)
                return
            try:
                await characters_svc.ensure_name_available(
                    session, name, exclude_character_id=character_id
                )
            except ValidationFailed as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

        async def on_phase_chosen(phase_interaction: discord.Interaction, shift_phase: str) -> None:
            await self._handle_edit_submit(
                phase_interaction,
                character_id,
                name,
                age,
                appearance,
                backstory,
                job_title,
                shift_phase,
            )

        await interaction.response.send_message(
            "When does your character work their shift?",
            view=ShiftPhaseSelectView(on_phase_chosen, current_phase=current_shift_phase),
            ephemeral=True,
        )

    async def _handle_edit_submit(
        self,
        interaction: discord.Interaction,
        character_id: int,
        name: str,
        age: int,
        appearance: str,
        backstory: str,
        job_title: str,
        shift_phase: str,
    ) -> None:
        async with self.bot.db() as session:
            row = await characters_svc.get_character(session, character_id)
            if row.status != CharacterStatus.PENDING.value:
                await interaction.response.send_message(t("not_pending"), ephemeral=True)
                return
            row.name = name
            row.age = age
            row.appearance = appearance
            row.backstory = backstory
            row.job_title = job_title
            row.shift_phase = shift_phase

        await interaction.response.send_message(
            f"**{name}** updated and resubmitted for approval.", ephemeral=True
        )
        await self._post_approval_embed(character_id, applicant_discord_id=interaction.user.id)

    @group.command(name="retire", description="Retire an approved character")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def retire(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            row = (
                await session.execute(
                    select(Character).where(
                        Character.user_id == user.id, Character.name == character
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            try:
                await characters_svc.retire_character(session, row)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            name = row.name

        await interaction.response.send_message(t("character_retired", name=name), ephemeral=True)

    @group.command(name="mode", description="Switch a character's RP mode (real-day cooldown)")
    @app_commands.describe(character="Character name", new_mode="The mode to switch to")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def mode(
        self, interaction: discord.Interaction, character: str, new_mode: RpMode
    ) -> None:
        async with self.bot.db() as session:
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            row = (
                await session.execute(
                    select(Character).where(
                        Character.user_id == user.id, Character.name == character
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            try:
                rp_modes_svc.check_can_switch_mode(row, new_mode, dt.datetime.now(dt.UTC))
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            character_id, current_mode, name = row.id, row.rp_mode, row.name

        async def on_confirm(confirm_interaction: discord.Interaction) -> None:
            async with self.bot.db() as confirm_session:
                char = await confirm_session.get(Character, character_id)
                if char is None:
                    await confirm_interaction.response.edit_message(
                        content=t("character_not_found"), view=None
                    )
                    return
                try:
                    rp_modes_svc.switch_mode(char, new_mode, dt.datetime.now(dt.UTC))
                except ServiceError as exc:
                    await confirm_interaction.response.edit_message(
                        content=t(exc.reason_key, **exc.fmt), view=None
                    )
                    return
            await confirm_interaction.response.edit_message(
                content=(
                    f"**{name}** is now in **{new_mode.value.title()}** mode. "
                    f"Next switch available in {constants.MODE_SWITCH_COOLDOWN_DAYS} real days."
                ),
                view=None,
            )

        description = RP_MODE_DESCRIPTIONS[new_mode.value]
        await interaction.response.send_message(
            f"**{name}** is currently in **{current_mode.title()}** mode.\n"
            f"Switching to **{new_mode.value.title()}** mode: {description}\n\n"
            f"This locks in for {constants.MODE_SWITCH_COOLDOWN_DAYS} real days before you can "
            "switch again. Are you sure?",
            view=ConfirmView(target_discord_id=interaction.user.id, on_confirm=on_confirm),
            ephemeral=True,
        )

    @group.command(name="crime", description="Enable or disable committing/being targeted by crime")
    @app_commands.describe(character="Character name", enabled="Whether crime should be enabled")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def crime(self, interaction: discord.Interaction, character: str, enabled: bool) -> None:
        async with self.bot.db() as session:
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            row = (
                await session.execute(
                    select(Character).where(
                        Character.user_id == user.id, Character.name == character
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            try:
                rp_modes_svc.check_can_toggle_crime(row, enabled, dt.datetime.now(dt.UTC))
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            character_id, name = row.id, row.name

        async def on_confirm(confirm_interaction: discord.Interaction) -> None:
            async with self.bot.db() as confirm_session:
                char = await confirm_session.get(Character, character_id)
                if char is None:
                    await confirm_interaction.response.edit_message(
                        content=t("character_not_found"), view=None
                    )
                    return
                try:
                    rp_modes_svc.toggle_crime(char, enabled, dt.datetime.now(dt.UTC))
                except ServiceError as exc:
                    await confirm_interaction.response.edit_message(
                        content=t(exc.reason_key, **exc.fmt), view=None
                    )
                    return
            verb = "enabled" if enabled else "disabled"
            await confirm_interaction.response.edit_message(
                content=(
                    f"**{name}** has crime {verb}. Next toggle available in "
                    f"{constants.CRIME_TOGGLE_COOLDOWN_DAYS} real day."
                ),
                view=None,
            )

        verb = "enable" if enabled else "disable"
        consequence = (
            "You won't be able to commit crime, and no one will be able to commit crime "
            "against you."
            if not enabled
            else "You'll be able to commit crime again, and others will be able to target you."
        )
        await interaction.response.send_message(
            f"{verb.title()} crime for **{name}**? {consequence}\n\n"
            f"This locks in for {constants.CRIME_TOGGLE_COOLDOWN_DAYS} real day before you can "
            "toggle again. Are you sure?",
            view=ConfirmView(target_discord_id=interaction.user.id, on_confirm=on_confirm),
            ephemeral=True,
        )

    @group.command(
        name="afflict", description="Self-inflict an injury or affliction (Life mode only)"
    )
    @app_commands.describe(
        character="Character name",
        type="Affliction type (from the staff-authored catalog)",
        cause="What happened, and how",
    )
    @app_commands.autocomplete(
        character=autocomplete.own_approved, type=autocomplete.affliction_types
    )
    async def afflict(
        self, interaction: discord.Interaction, character: str, type: str, cause: str
    ) -> None:
        async with self.bot.db() as session:
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            row = (
                await session.execute(
                    select(Character).where(
                        Character.user_id == user.id, Character.name == character
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            if row.rp_mode != RpMode.LIFE.value:
                await interaction.response.send_message(t("affliction_wrong_mode"), ephemeral=True)
                return
            affliction_type = (
                await session.execute(select(AfflictionType).where(AfflictionType.name == type))
            ).scalar_one_or_none()
            if affliction_type is None:
                await interaction.response.send_message(
                    t("affliction_type_not_found"), ephemeral=True
                )
                return
            character_id, affliction_type_id, name = row.id, affliction_type.id, row.name
            affliction_name, description = affliction_type.name, affliction_type.description
            cure_note = (
                "PERMANENT -- cannot be cured."
                if affliction_type.is_permanent
                else (
                    f"Cured once {affliction_type.cure_stat} rises above "
                    f"{affliction_type.cure_threshold:g}."
                    if affliction_type.cure_stat is not None
                    else "No automatic cure condition set -- ask staff."
                )
            )

        async def on_confirm(confirm_interaction: discord.Interaction) -> None:
            async with self.bot.db() as confirm_session:
                char = await confirm_session.get(Character, character_id)
                affliction_type_row = await confirm_session.get(AfflictionType, affliction_type_id)
                if char is None or affliction_type_row is None:
                    await confirm_interaction.response.edit_message(
                        content=t("character_not_found"), view=None
                    )
                    return
                try:
                    await afflictions_svc.apply_manual_affliction(
                        confirm_session,
                        character=char,
                        affliction_type=affliction_type_row,
                        cause=cause,
                    )
                except ServiceError as exc:
                    await confirm_interaction.response.edit_message(
                        content=t(exc.reason_key, **exc.fmt), view=None
                    )
                    return
            await confirm_interaction.response.edit_message(
                content=t("affliction_ok", name=name, affliction=affliction_name, cause=cause),
                view=None,
            )

        await interaction.response.send_message(
            f"Afflict **{name}** with **{affliction_name}**? {description}\n"
            f"{cure_note}\n\nCause: {cause}\n\nThis can't be undone by yourself -- are you sure?",
            view=ConfirmView(target_discord_id=interaction.user.id, on_confirm=on_confirm),
            ephemeral=True,
        )

    @group.command(name="die", description="End your character's life (Life mode only)")
    @app_commands.describe(character="Character name", cause="How it happened (optional)")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def die(
        self, interaction: discord.Interaction, character: str, cause: str | None = None
    ) -> None:
        async with self.bot.db() as session:
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            row = (
                await session.execute(
                    select(Character).where(
                        Character.user_id == user.id, Character.name == character
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            if row.rp_mode != RpMode.LIFE.value:
                await interaction.response.send_message(t("death_wrong_mode"), ephemeral=True)
                return
            if row.status == CharacterStatus.DEAD.value:
                await interaction.response.send_message(
                    t("character_already_dead", name=row.name), ephemeral=True
                )
                return
            character_id, name = row.id, row.name

        async def on_confirm(confirm_interaction: discord.Interaction) -> None:
            async with self.bot.db() as confirm_session:
                char = await confirm_session.get(Character, character_id)
                if char is None:
                    await confirm_interaction.response.edit_message(
                        content=t("character_not_found"), view=None
                    )
                    return
                try:
                    afflictions_svc.mark_dead(char, cause)
                except ServiceError as exc:
                    await confirm_interaction.response.edit_message(
                        content=t(exc.reason_key, **exc.fmt), view=None
                    )
                    return
            await confirm_interaction.response.edit_message(
                content=t("death_ok", name=name), view=None
            )

        await interaction.response.send_message(
            f"End **{name}**'s life permanently? This cannot be undone.\n\n"
            + (f"Cause: {cause}" if cause else "No cause given."),
            view=ConfirmView(
                target_discord_id=interaction.user.id,
                on_confirm=on_confirm,
                confirm_label="End character",
            ),
            ephemeral=True,
        )

    @group.command(name="status", description="Show a character's status")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def status(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            row = (
                await session.execute(
                    select(Character).where(
                        Character.user_id == user.id, Character.name == character
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            job_name = row.job_title or "Unemployed"
            if row.job_title and row.job_is_illicit:
                job_name += " (illicit)"
            location_name = "-"
            if row.location_id:
                district = self.bot.content.district(row.current_district_id)
                location = next(
                    (loc for loc in district.locations if loc.id == row.location_id), None
                )
                location_name = location.name if location else row.location_id

            open_shift = (
                await session.execute(
                    select(Shift).where(Shift.character_id == row.id, Shift.result.is_(None))
                )
            ).scalar_one_or_none()
            if open_shift is not None:
                clock = await session.get(WorldClock, 1)
                current_tick = clock.tick if clock is not None else 0
                due_time = clock_string(open_shift.tick_due)
                if current_tick >= open_shift.tick_due:
                    shift_value = f"Overdue since {due_time} -- work it now!"
                else:
                    shift_value = f"Open, due by {due_time}"
            elif row.shift_phase is not None:
                shift_value = (
                    f"No shift open -- works {phase_time_range(DayPhase(row.shift_phase))}"
                )
            else:
                shift_value = "No job"

            level = job_levels.job_level_for_shifts(row.shifts_completed)
            shifts_left = job_levels.shifts_to_next_level(row.shifts_completed)
            level_value = (
                f"{level.value.title()} ({shifts_left} shifts to next level)"
                if shifts_left is not None
                else f"{level.value.title()} (max level)"
            )

            district_value = self._district_status_value(row)
            jailed_value = (
                f"Until {clock_string(row.jailed_until_tick)}" if row.jailed_until_tick else "No"
            )
            active_afflictions = (
                await session.execute(
                    select(CharacterAffliction, AfflictionType)
                    .join(
                        AfflictionType, CharacterAffliction.affliction_type_id == AfflictionType.id
                    )
                    .where(
                        CharacterAffliction.character_id == row.id,
                        CharacterAffliction.cured_at.is_(None),
                    )
                    .order_by(CharacterAffliction.applied_at)
                )
            ).all()
        embed = discord.Embed(title=row.name)
        embed.add_field(name="Status", value=row.status)
        embed.add_field(name="RP Mode", value=row.rp_mode.title())
        embed.add_field(name="Money", value=str(row.money))
        embed.add_field(name="Hunger", value=str(row.hunger))
        embed.add_field(name="Health", value=str(row.health))
        if row.rp_mode == RpMode.SIMULATION.value:
            embed.add_field(name="Thirst", value=str(row.thirst))
            embed.add_field(name="Sanity", value=str(row.sanity))
        embed.add_field(name="Job", value=job_name)
        embed.add_field(name="Level", value=level_value)
        embed.add_field(name="Shift", value=shift_value)
        embed.add_field(name="District", value=district_value)
        embed.add_field(name="Location", value=location_name)
        embed.add_field(name="Reputation", value=f"{row.reputation:.1f}")
        embed.add_field(name="Jailed", value=jailed_value)
        if row.positions:
            embed.add_field(
                name="Positions", value=", ".join(p.title() for p in sorted(row.positions))
            )
        if row.status == CharacterStatus.DEAD.value and row.death_cause:
            embed.add_field(name="Cause of death", value=row.death_cause, inline=False)
        if active_afflictions:
            embed.add_field(
                name="Afflictions",
                value="\n".join(
                    f"**{affliction_type.name}**"
                    + (" (permanent)" if affliction_type.is_permanent else "")
                    for _affliction, affliction_type in active_afflictions
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def _district_status_value(self, character: Character) -> str:
        """FR-LOC-9: distinguishes "on a train," "visiting away from
        home," and "home" -- `/travel`'s "already on a train, check
        `/character status`" refusal specifically promises this exists."""
        current_district = self.bot.content.district(character.current_district_id)  # type: ignore[attr-defined]
        if character.in_transit_until_tick is not None:
            destination_name = "?"
            if character.transit_destination_id is not None:
                destination = self.bot.content.district(  # type: ignore[attr-defined]
                    character.transit_destination_id
                )
                destination_name = destination.name
            eta = clock_string(character.in_transit_until_tick)
            return f"On a train to **{destination_name}** -- arriving by {eta}"
        if character.current_district_id != character.district_id:
            home_district = self.bot.content.district(character.district_id)  # type: ignore[attr-defined]
            return f"Visiting **{current_district.name}** (home: {home_district.name})"
        return current_district.name

    @group.command(name="avatar", description="Set a character's avatar image")
    @app_commands.describe(
        character="Character name",
        url="Image URL (https, .png/.jpg/.jpeg/.webp/.gif) -- omit if uploading a file",
        image="Upload an image file -- expires in ~24h, prefer a URL for something permanent",
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def avatar(
        self,
        interaction: discord.Interaction,
        character: str,
        url: str | None = None,
        image: discord.Attachment | None = None,
    ) -> None:
        if (url is None) == (image is None):
            await interaction.response.send_message(
                "Provide either a URL or an uploaded image, not both.", ephemeral=True
            )
            return
        if image is not None:
            if image.content_type is None or not image.content_type.startswith("image/"):
                await interaction.response.send_message(t("invalid_avatar_url"), ephemeral=True)
                return
            # Discord's CDN signs attachment URLs with a ~24h expiry regardless
            # of which message holds them (there's no way to host a permanent
            # link through Discord itself), so this will need re-uploading
            # periodically -- warned about in the command description below.
            url = image.url
        assert url is not None
        try:
            characters_svc.validate_avatar_url(url)
        except ValidationFailed as exc:
            await interaction.response.send_message(t(exc.reason_key, **exc.fmt), ephemeral=True)
            return
        async with self.bot.db() as session:
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            row = (
                await session.execute(
                    select(Character).where(
                        Character.user_id == user.id, Character.name == character
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            row.avatar_url = url
        note = (
            " (uploaded images expire in ~24h -- re-run this command with a fresh "
            "upload, or switch to a permanent URL, if it stops showing up)"
            if image is not None
            else ""
        )
        await interaction.response.send_message(f"Avatar updated.{note}", ephemeral=True)

    @group.command(
        name="tag", description="Set a character's proxy tag (e.g. `md:` messages post as them)"
    )
    @app_commands.describe(
        character="Character name", prefix="1-12 chars, can't start with / or (("
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def tag(self, interaction: discord.Interaction, character: str, prefix: str) -> None:
        try:
            characters_svc.validate_proxy_tag(prefix)
        except ValidationFailed as exc:
            await interaction.response.send_message(t(exc.reason_key, **exc.fmt), ephemeral=True)
            return
        async with self.bot.db() as session:
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            row = (
                await session.execute(
                    select(Character).where(
                        Character.user_id == user.id, Character.name == character
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            existing = (
                await session.execute(
                    select(Character).where(
                        Character.user_id == user.id,
                        Character.proxy_tag == prefix,
                        Character.id != row.id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                await interaction.response.send_message(t("proxy_tag_taken"), ephemeral=True)
                return
            row.proxy_tag = prefix
        await interaction.response.send_message(f"Tag set to `{prefix}:`.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CharacterCog(bot))
