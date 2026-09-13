"""`/work` (Spec FR-JOB, CMD-16, reworked).

A player's job is a free-typed `Character.job_title` + `shift_phase`, set
at character creation and changed only by staff (`/staff give job`) --
there's no more `jobs.yaml` catalog to apply for, list, or quit
(`/job apply|list|quit` retired along with it), and no more per-job
ladder to check for a promotion offer: `panem_shared.job_levels`'
universal Apprentice->Expert progression replaced it.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete
from panem_bot.services import characters as characters_svc
from panem_bot.services import shifts as shifts_svc
from panem_bot.strings import t
from panem_shared import constants, job_levels, redis_keys
from panem_shared.db.models import Character, Shift, WorldClock
from panem_shared.logging import get_logger

logger = get_logger(component="jobs")


class _SkipButton(discord.ui.Button["discord.ui.View"]):
    """A plain callback button (not a link) offering a flat, neutral wage
    instead of playing the minigame -- same shape as `views.py`'s
    `ShiftPhaseSelect`/`ChangesNoteModal` (an injected coroutine rather
    than a subclass override elsewhere), kept local here since it's
    tightly coupled to `JobsCog._finish_shift`."""

    def __init__(self, on_click: Callable[[discord.Interaction], Awaitable[None]]) -> None:
        super().__init__(label="Skip (neutral wage)", style=discord.ButtonStyle.secondary)
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


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

    def _skip_button(self, shift_id: int, char_id: int) -> _SkipButton:
        """Lets a player opt out of the minigame entirely from the same
        message that offers to launch it -- resolves the shift right away
        for a flat, neutral wage (`resolve_shift_game`'s `neutral` param,
        same mechanism as Solitaire's "Give Up"), no win buff or lose
        debuff either way."""

        async def on_click(skip_interaction: discord.Interaction) -> None:
            async with self.bot.db() as session:  # type: ignore[attr-defined]
                shift = await session.get(Shift, shift_id)
                if shift is None or shift.result is not None:
                    await skip_interaction.response.send_message(
                        t("shift_no_longer_open"), ephemeral=True
                    )
                    return
                char = await session.get(Character, char_id)
                if char is None:
                    await skip_interaction.response.send_message(
                        t("character_not_found"), ephemeral=True
                    )
                    return
                text = await self._finish_shift(session, shift, char, won=False, neutral=True)
            await skip_interaction.response.send_message(text, ephemeral=True)

        return _SkipButton(on_click)

    async def _activity_launch_view(
        self, interaction: discord.Interaction, activity_url: str, shift_id: int, char_id: int
    ) -> tuple[discord.ui.View, bool]:
        """Prefer a real in-Discord Activity launch over a plain browser
        link: an `embedded_application` invite on the player's current
        voice channel, which Discord's client renders as a "Join Activity"
        launch rather than opening an external tab. Discord only ever
        loads the Activity's one configured root URL for that launch,
        appending its own `channel_id`/`guild_id`/`instance_id` query
        params -- never a custom `?shift_id=` -- so the shift is instead
        stashed in Redis keyed by that voice channel
        (`panem_shared.redis_keys.work_pending_key`) for `work.html` to
        look up once it loads. Falls back to the plain link (returning
        `False`) whenever the player isn't in a voice channel, the bot
        lacks permission to create an invite there, or Activities aren't
        enabled for this application in the Developer Portal."""
        member = interaction.user
        voice_state = member.voice if isinstance(member, discord.Member) else None
        voice_channel = voice_state.channel if voice_state is not None else None
        application_id = self.bot.application_id

        if voice_channel is not None and application_id is not None:
            try:
                invite = await voice_channel.create_invite(
                    max_age=redis_keys.WORK_PENDING_TTL_S,
                    target_type=discord.InviteTarget.embedded_application,
                    target_application_id=application_id,
                )
                await self.bot.redis.set(  # type: ignore[attr-defined]
                    redis_keys.work_pending_key(voice_channel.id),
                    str(shift_id),
                    ex=redis_keys.WORK_PENDING_TTL_S,
                )
            except discord.HTTPException:
                logger.warning("work_activity_invite_failed", channel_id=voice_channel.id)
            else:
                view = discord.ui.View()
                view.add_item(
                    discord.ui.Button(
                        label="Launch in Discord", url=invite.url, style=discord.ButtonStyle.link
                    )
                )
                view.add_item(self._skip_button(shift_id, char_id))
                return view, True

        url = f"{activity_url.rstrip('/')}/work.html?shift_id={shift_id}"
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(label="Play for your shift", url=url, style=discord.ButtonStyle.link)
        )
        view.add_item(self._skip_button(shift_id, char_id))
        return view, False

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
            if not shifts_svc.has_job(char):
                await interaction.response.send_message(
                    t("job_none_set", name=char.name), ephemeral=True
                )
                return

            open_shift = (
                await session.execute(
                    select(Shift).where(Shift.character_id == char.id, Shift.result.is_(None))
                )
            ).scalar_one_or_none()
            if open_shift is None:
                member = interaction.user
                is_staff = isinstance(member, discord.Member) and await self.bot.is_staff(  # type: ignore[attr-defined]
                    member
                )
                open_shift = shifts_svc.open_adhoc_shift_override(
                    char, await self._current_tick(session), is_staff=is_staff
                )
                if open_shift is None:
                    await interaction.response.send_message(
                        t("job_no_open_shift", name=char.name), ephemeral=True
                    )
                    return
                session.add(open_shift)
                await session.flush()

            current_tick = await self._current_tick(session)
            if shifts_svc.already_worked_this_tick(open_shift, current_tick):
                await interaction.response.send_message(
                    t("shift_already_worked_this_tick", name=char.name), ephemeral=True
                )
                return

            activity_url = self.bot.settings.activity_public_url  # type: ignore[attr-defined]
            char_id, shift_id, char_name = char.id, open_shift.id, char.name
            if activity_url:
                shifts_svc.start_shift_game(open_shift, current_tick)
                job_title = char.job_title

        if activity_url:
            view, launched_in_discord = await self._activity_launch_view(
                interaction, activity_url, shift_id, char_id
            )
            key = "work_game_ready_activity" if launched_in_discord else "work_game_ready"
            await interaction.response.send_message(
                t(key, name=char_name, title=job_title), view=view, ephemeral=True
            )
            return

        await self._resolve_work_coinflip(interaction, char_id, shift_id)

    async def _resolve_work_coinflip(
        self, interaction: discord.Interaction, char_id: int, shift_id: int
    ) -> None:
        """No `ACTIVITY_PUBLIC_URL` configured -- nothing to actually play,
        so `/work` resolves the shift immediately with a coin-flip instead
        of the old catalog-authored 3-option choice (which had nowhere to
        read its options from now that jobs are free-typed)."""
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            shift = await session.get(Shift, shift_id)
            if shift is None or shift.result is not None:
                await interaction.response.send_message(t("shift_no_longer_open"), ephemeral=True)
                return
            char = await session.get(Character, char_id)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            won = random.random() < constants.NO_ACTIVITY_WORK_WIN_PROBABILITY
            text = await self._finish_shift(session, shift, char, won)

        await interaction.response.send_message(text, ephemeral=True)

    async def _finish_shift(
        self,
        session: AsyncSession,
        shift: Shift,
        char: Character,
        won: bool,
        *,
        neutral: bool = False,
    ) -> str:
        """Resolves `shift` (won, lost, or -- `neutral=True` -- skipped for
        a flat wage with no win buff or lose debuff) and builds the reply
        text, including a level-up line if this shift's completion crosses
        one of `panem_shared.job_levels`' thresholds. Shared by the
        no-Activity coin-flip path and the minigame-launch message's Skip
        button above -- both call this straight from `/work`, so the
        once-per-tick check lives here rather than duplicated in each
        caller."""
        current_tick = await self._current_tick(session)
        if shifts_svc.already_worked_this_tick(shift, current_tick):
            return t("shift_already_worked_this_tick", name=char.name)
        content = self.bot.content  # type: ignore[attr-defined]
        district = content.district(char.district_id)
        market_multiplier = await shifts_svc.market_multiplier_for_district(
            session, content, district
        )
        before_level = job_levels.job_level_for_shifts(char.shifts_completed)
        outcome = shifts_svc.resolve_shift_game(
            char, district, won=won, market_multiplier=market_multiplier, neutral=neutral
        )
        shifts_svc.apply_shift_outcome(shift, char, outcome, tick=current_tick)
        after_level = job_levels.job_level_for_shifts(char.shifts_completed)

        if neutral:
            outcome_word = "skips the shift's minigame"
        elif won:
            outcome_word = "clears the shift"
        else:
            outcome_word = "barely gets through the shift"
        text = t(
            "work_ok",
            name=char.name,
            outcome=outcome_word,
            wage=round(outcome.wage),
            rep_delta=outcome.rep_delta,
        )
        if after_level != before_level:
            text += t("level_up", name=char.name, level=after_level.value.title())
        return text


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JobsCog(bot))
