"""`/character ...` commands and the create -> approve/reject flow (FR-CHR)."""

from __future__ import annotations

import contextlib

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select

from panem_bot import autocomplete
from panem_bot.errors import ServiceError, ValidationFailed
from panem_bot.services import characters as characters_svc
from panem_bot.services import jobs as jobs_svc
from panem_bot.strings import t
from panem_bot.views import (
    CHAR_ID_FOOTER_PREFIX,
    ApprovalView,
    DistrictSelectView,
    JobSelectView,
)
from panem_shared import constants
from panem_shared.db.models import Character, User
from panem_shared.enums import CharacterStatus


def _district_role(guild: discord.Guild, district_name: str) -> discord.Role | None:
    return discord.utils.get(guild.roles, name=district_name)


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

    async def _interaction_is_staff(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        return await self.bot.is_staff(interaction.user)

    # ---------------------------------------------------------------- create

    group = app_commands.Group(name="character", description="Manage your characters")

    @group.command(name="create", description="Create a new character")
    async def create(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        async with self.bot.db() as session:
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            if user.banned_at is not None:
                await interaction.response.send_message(t("banned"), ephemeral=True)
                return
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
            if int(count.scalar_one()) >= self.bot.settings.max_characters_per_user:
                await interaction.response.send_message(
                    t("too_many_characters", limit=self.bot.settings.max_characters_per_user),
                    ephemeral=True,
                )
                return

        districts = [(d.id, d.name) for d in self.bot.content.districts.values()]

        async def on_district_chosen(
            select_interaction: discord.Interaction, district_id: int
        ) -> None:
            await self._prompt_details(select_interaction, district_id)

        await interaction.response.send_message(
            "Which district is this character from?",
            view=DistrictSelectView(districts, on_district_chosen),
            ephemeral=True,
        )

    async def _prompt_details(self, interaction: discord.Interaction, district_id: int) -> None:
        from panem_bot.modals import CharacterDetailsModal

        async def on_submit(
            modal_interaction: discord.Interaction,
            name: str,
            age_str: str,
            appearance: str,
            backstory: str,
        ) -> None:
            await self._prompt_job(
                modal_interaction, district_id, name, age_str, appearance, backstory
            )

        max_age = characters_svc.max_age_for_district(district_id)
        placeholder = f"{constants.CHARACTER_AGE_MIN}-{max_age}"
        await interaction.response.send_modal(
            CharacterDetailsModal(on_submit=on_submit, age_placeholder=placeholder)
        )

    async def _prompt_job(
        self,
        interaction: discord.Interaction,
        district_id: int,
        name: str,
        age_str: str,
        appearance: str,
        backstory: str,
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
            open_jobs = await self._open_legal_jobs(session, district_id)

        async def on_job_chosen(job_interaction: discord.Interaction, job_id: str | None) -> None:
            await self._finish_create(
                job_interaction, district_id, name, age, appearance, backstory, job_id
            )

        await interaction.response.send_message(
            "Desired job (if a slot isn't free, you'll start unemployed):",
            view=JobSelectView(open_jobs, on_job_chosen),
            ephemeral=True,
        )

    async def _open_legal_jobs(self, session, district_id: int) -> list[tuple[str, str]]:
        all_district_jobs = await jobs_svc.jobs_for_district(session, self.bot.content, district_id)
        jobs = [j for j in all_district_jobs if j.legal]
        if not jobs:
            return []
        counts = await session.execute(
            select(Character.job_id, func.count())
            .where(
                Character.job_id.in_([j.id for j in jobs]),
                Character.status == CharacterStatus.APPROVED.value,
            )
            .group_by(Character.job_id)
        )
        count_map = dict(counts.all())
        return [(j.id, j.title) for j in jobs if count_map.get(j.id, 0) < j.slots]

    async def _finish_create(
        self,
        interaction: discord.Interaction,
        district_id: int,
        name: str,
        age: int,
        appearance: str,
        backstory: str,
        job_id: str | None,
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
                    desired_job_id=job_id,
                    max_characters=self.bot.settings.max_characters_per_user,
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            character_id = character.id

        await interaction.response.send_message(t("character_created", name=name), ephemeral=True)
        await self._post_approval_embed(interaction, character_id)

    async def _post_approval_embed(
        self, interaction: discord.Interaction, character_id: int
    ) -> None:
        channel = interaction.client.get_channel(self.bot.settings.approval_channel_id)
        if channel is None:
            return
        async with self.bot.db() as session:
            character = await characters_svc.get_character(session, character_id)
            district = self.bot.content.district(character.district_id)
            job = (
                await jobs_svc.get_job(session, self.bot.content, character.job_id)
                if character.job_id
                else None
            )
            embed = discord.Embed(
                title=f"Character Application: {character.name}", color=discord.Color.blurple()
            )
            embed.add_field(name="Applicant", value=f"<@{interaction.user.id}>", inline=True)
            embed.add_field(name="District", value=district.name, inline=True)
            embed.add_field(name="Age", value=str(character.age), inline=True)
            embed.add_field(
                name="Desired Job", value=job.title if job else "Unemployed", inline=True
            )
            embed.add_field(name="Appearance", value=character.appearance or "-", inline=False)
            embed.add_field(name="Backstory", value=character.backstory or "-", inline=False)
            embed.set_footer(text=f"{CHAR_ID_FOOTER_PREFIX}{character.id}")

        await channel.send(embed=embed, view=self.approval_view)

    # --------------------------------------------------------- approval flow

    async def _handle_approve(self, interaction: discord.Interaction, character_id: int) -> None:
        async with self.bot.db() as session:
            try:
                character = await characters_svc.get_character(session, character_id)
                district = self.bot.content.district(character.district_id)
                district_jobs = await jobs_svc.jobs_for_district(
                    session, self.bot.content, district.id
                )
                job_slots = {j.id: j.slots for j in district_jobs}
                await characters_svc.approve_character(
                    session, character, district=district, job_slots=job_slots
                )
            except ServiceError:
                await interaction.response.send_message("Already handled.", ephemeral=True)
                return
            user = await session.get(User, character.user_id)
            char_name, discord_id = character.name, user.discord_id

        await self._disable_approval_message(interaction, f"Approved by {interaction.user.mention}")
        await interaction.response.send_message(f"Approved **{char_name}**.", ephemeral=True)

        guild = interaction.guild
        assert guild is not None
        role = _district_role(guild, district.name)
        member = guild.get_member(discord_id)
        if member and role:
            await member.add_roles(role, reason="Character approved")
        if member:
            with contextlib.suppress(discord.Forbidden):
                await member.send(t("character_approved_dm", name=char_name, district=district.id))

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
            char_name, discord_id = (
                character.name,
                (await session.get(User, character.user_id)).discord_id,
            )

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
        lines = [f"**{c.name}** — District {c.district_id} — {c.status}" for c in rows]
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
            prefill = {
                "name": row.name,
                "age": str(row.age),
                "appearance": row.appearance,
                "backstory": row.backstory,
            }

        from panem_bot.modals import CharacterDetailsModal

        async def on_submit(
            modal_interaction: discord.Interaction,
            name: str,
            age_str: str,
            appearance: str,
            backstory: str,
        ) -> None:
            await self._handle_edit_submit(
                modal_interaction, character_id, district_id, name, age_str, appearance, backstory
            )

        max_age = characters_svc.max_age_for_district(district_id)
        placeholder = f"{constants.CHARACTER_AGE_MIN}-{max_age}"
        await interaction.response.send_modal(
            CharacterDetailsModal(on_submit=on_submit, age_placeholder=placeholder, prefill=prefill)
        )

    async def _handle_edit_submit(
        self,
        interaction: discord.Interaction,
        character_id: int,
        district_id: int,
        name: str,
        age_str: str,
        appearance: str,
        backstory: str,
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
            row.name = name
            row.age = age
            row.appearance = appearance
            row.backstory = backstory

        await interaction.response.send_message(
            f"**{name}** updated and resubmitted for approval.", ephemeral=True
        )
        await self._post_approval_embed(interaction, character_id)

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
            keep_role = await characters_svc.other_approved_characters_in_district(
                session, user_id=user.id, district_id=row.district_id, exclude_character_id=row.id
            )
            district = self.bot.content.district(row.district_id)
            name = row.name

        await interaction.response.send_message(t("character_retired", name=name), ephemeral=True)
        if not keep_role and interaction.guild and isinstance(interaction.user, discord.Member):
            role = _district_role(interaction.guild, district.name)
            if role and role in interaction.user.roles:
                await interaction.user.remove_roles(
                    role, reason="No remaining approved characters here"
                )

    @group.command(name="status", description="Show a character's status")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_any)
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
        embed = discord.Embed(title=row.name)
        embed.add_field(name="Status", value=row.status)
        embed.add_field(name="Money", value=str(row.money))
        embed.add_field(name="Hunger", value=str(row.hunger))
        embed.add_field(name="Health", value=str(row.health))
        embed.add_field(name="Job", value=row.job_id or "Unemployed")
        embed.add_field(name="Location", value=row.location_id or "-")
        embed.add_field(name="Reputation", value=f"{row.reputation:.1f}")
        embed.add_field(name="Jailed", value="Yes" if row.jailed_until_tick else "No")
        embed.add_field(name="Tesserae", value=str(row.tesserae_count))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="avatar", description="Set a character's avatar image")
    @app_commands.describe(
        character="Character name", url="Image URL (https, .png/.jpg/.jpeg/.webp/.gif)"
    )
    @app_commands.autocomplete(character=autocomplete.own_any)
    async def avatar(self, interaction: discord.Interaction, character: str, url: str) -> None:
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
        await interaction.response.send_message("Avatar updated.", ephemeral=True)

    @group.command(
        name="tag", description="Set a character's proxy tag (e.g. `md:` messages post as them)"
    )
    @app_commands.describe(
        character="Character name", prefix="1-12 chars, can't start with / or (("
    )
    @app_commands.autocomplete(character=autocomplete.own_any)
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
