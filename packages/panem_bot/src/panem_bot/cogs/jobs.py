"""`/work`, `/job list|apply|quit`, `/tesserae claim` (Spec FR-JOB, FR-ECO-7,
CMD-16/17/18/19/24)."""

from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete, redis_keys
from panem_bot.errors import ServiceError
from panem_bot.services import characters as characters_svc
from panem_bot.services import jobs as jobs_svc
from panem_bot.services import shifts as shifts_svc
from panem_bot.strings import t
from panem_bot.views import WorkOptionView
from panem_shared import constants
from panem_shared.content.schemas import Job
from panem_shared.db.models import Character, JobHistory, Shift, WorldClock


class JobsCog(commands.Cog):
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

    async def _current_tick(self, session: AsyncSession) -> int:
        clock = await session.get(WorldClock, 1)
        return clock.tick if clock is not None else 0

    # ------------------------------------------------------------------ /work

    @app_commands.command(name="work", description="Work your currently open shift")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def work(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            open_shift = (
                await session.execute(
                    select(Shift).where(Shift.character_id == char.id, Shift.result.is_(None))
                )
            ).scalar_one_or_none()
            if open_shift is None:
                await interaction.response.send_message(
                    t("job_no_open_shift", name=char.name), ephemeral=True
                )
                return

            job = await jobs_svc.get_job(session, self.bot.content, open_shift.job_id)  # type: ignore[attr-defined]
            if job is None:
                await interaction.response.send_message(t("job_not_found"), ephemeral=True)
                return

            char_id, shift_id, char_name = char.id, open_shift.id, char.name
            labels = [option.label for option in job.options]

        async def on_choose(select_interaction: discord.Interaction, option_index: int) -> None:
            await self._resolve_work(select_interaction, char_id, shift_id, option_index)

        await interaction.response.send_message(
            f"Choose how **{char_name}** works this shift:",
            view=WorkOptionView(labels, on_choose),
            ephemeral=True,
        )

    async def _resolve_work(
        self, interaction: discord.Interaction, char_id: int, shift_id: int, option_index: int
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            shift = await session.get(Shift, shift_id)
            if shift is None or shift.result is not None:
                await interaction.response.send_message(t("shift_no_longer_open"), ephemeral=True)
                return
            char = await session.get(Character, char_id)
            job = await jobs_svc.get_job(session, self.bot.content, shift.job_id)  # type: ignore[attr-defined]
            if char is None or job is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            current_tick = await self._current_tick(session)
            outcome = shifts_svc.resolve_shift(
                job, option_index, is_player=True, rng=random.Random()
            )
            shifts_svc.apply_shift_outcome(shift, char, outcome, tick=current_tick)

            next_title: str | None = None
            if job.ladder_next and shifts_svc.check_promotion_eligible(char, job):
                next_job = await jobs_svc.get_job(session, self.bot.content, job.ladder_next)  # type: ignore[attr-defined]
                next_title = next_job.title if next_job else job.ladder_next

            name, wage, rep_delta = char.name, round(outcome.wage), outcome.rep_delta

        text = t("work_ok", name=name, wage=wage, rep_delta=rep_delta)
        if next_title:
            text += t("promotion_available", name=name, next_title=next_title)
        await interaction.response.send_message(text, ephemeral=True)

    # ------------------------------------------------------------------- /job

    group = app_commands.Group(name="job", description="Manage a character's job")

    @group.command(name="list", description="List jobs available in a character's district")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def job_list(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            content = self.bot.content  # type: ignore[attr-defined]
            district_jobs = await jobs_svc.jobs_for_district(
                session, content, char.current_district_id
            )

        if not district_jobs:
            await interaction.response.send_message(t("no_jobs_in_district"), ephemeral=True)
            return

        lines = []
        for job in district_jobs:
            requirement = f", needs {job.min_reputation:g}+ rep" if job.min_reputation else ""
            lines.append(f"**{job.title}** (`{job.id}`) — wage {job.wage:g}{requirement}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @group.command(name="apply", description="Apply for a job")
    @app_commands.describe(character="Character name", job_id="Job id (see /job list)")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def job_apply(
        self, interaction: discord.Interaction, character: str, job_id: str
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            job = await jobs_svc.get_job(session, self.bot.content, job_id)  # type: ignore[attr-defined]
            if job is None:
                await interaction.response.send_message(t("job_not_found"), ephemeral=True)
                return

            try:
                shifts_svc.check_can_apply(char, job)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

            current_tick = await self._current_tick(session)
            shifts_svc.apply_for_job(char, job, tick=current_tick)
            name, title = char.name, job.title

        await interaction.response.send_message(
            t("job_applied", name=name, title=title), ephemeral=True
        )

    @job_apply.autocomplete("job_id")
    async def job_apply_job_id_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        character_name = getattr(interaction.namespace, "character", None)
        if not character_name:
            return []
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character_name)
            if char is None:
                return []
            content = self.bot.content  # type: ignore[attr-defined]
            district_jobs = await jobs_svc.jobs_for_district(
                session, content, char.current_district_id
            )
        current_lower = current.lower()
        matches: list[Job] = [
            job
            for job in district_jobs
            if current_lower in job.title.lower() or current_lower in job.id.lower()
        ]
        return [
            app_commands.Choice(name=f"{job.title} ({job.id})", value=job.id)
            for job in matches[:25]
        ]

    @group.command(name="quit", description="Quit your current job")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def job_quit(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            try:
                job_id, started_tick = shifts_svc.quit_job(char)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

            current_tick = await self._current_tick(session)
            session.add(
                JobHistory(
                    character_id=char.id,
                    job_id=job_id,
                    started_tick=started_tick or current_tick,
                    ended_tick=current_tick,
                    reason="quit",
                )
            )
            name = char.name

        await interaction.response.send_message(t("job_quit", name=name), ephemeral=True)

    # ------------------------------------------------------------- /tesserae

    tesserae_group = app_commands.Group(name="tesserae", description="Claim tesserae")

    @tesserae_group.command(name="claim", description="Claim today's tesserae")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def tesserae_claim(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            if char.district_id == constants.CAPITOL_DISTRICT_ID:
                await interaction.response.send_message(t("tesserae_not_eligible"), ephemeral=True)
                return
            char_id, name = char.id, char.name

        claim_key = redis_keys.tesserae_key(char_id)
        already_claimed = await self.bot.redis.get(claim_key)  # type: ignore[attr-defined]
        if already_claimed is not None:
            await interaction.response.send_message(
                t("tesserae_already_claimed", name=name), ephemeral=True
            )
            return

        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await session.get(Character, char_id)
            assert char is not None
            char.tesserae_count += 1
            char.money += constants.TICKET_BASE
            count = char.tesserae_count

        await self.bot.redis.set(claim_key, "1", ex=redis_keys.TESSERAE_TTL_S)  # type: ignore[attr-defined]
        await interaction.response.send_message(
            t("tesserae_claimed", name=name, amount=constants.TICKET_BASE, count=count),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JobsCog(bot))
