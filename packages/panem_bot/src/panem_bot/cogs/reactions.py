"""`/reaction archetype` (check how a character is currently seen) and
`/reaction respond` (a command fallback for answering a pop-up without
relying on its DM'd buttons) -- plus the background trigger loop that
occasionally DMs an eligible character's owner a 3-choice pop-up (Spec
"Reaction Events"). The DM's view and `/reaction respond` both end up
calling the same `panem_shared.reactions.respond`, so whichever gets
there first wins and the other is told it's already answered."""

from __future__ import annotations

import contextlib
import datetime as dt
import random

import discord
from discord import app_commands
from discord.ext import commands, tasks
from sqlalchemy import select

from panem_bot import autocomplete, utils
from panem_bot.errors import ServiceError
from panem_bot.strings import t
from panem_shared import constants
from panem_shared import reactions as reactions_svc
from panem_shared.db.models import Character, ReactionEventLog, User, WorldClock
from panem_shared.enums import CharacterStatus, ReactionChoice


class _ReactionButton(discord.ui.Button["ReactionChoiceView"]):
    def __init__(self, option: reactions_svc.ReactionOption) -> None:
        style = {
            ReactionChoice.GOOD: discord.ButtonStyle.success,
            ReactionChoice.NEUTRAL: discord.ButtonStyle.secondary,
            ReactionChoice.EVIL: discord.ButtonStyle.danger,
        }[option.choice]
        super().__init__(label=option.label, style=style)
        self.option = option

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, ReactionChoiceView)
        if interaction.user.id != view.owner_discord_id:
            await interaction.response.send_message(t("character_not_yours"), ephemeral=True)
            return
        async with view.bot.db() as session:  # type: ignore[attr-defined]
            character = await session.get(Character, view.character_id)
            log = await session.get(ReactionEventLog, view.log_id)
            if character is None or log is None:
                await interaction.response.send_message(
                    t("reaction_none_pending", name="that character"), ephemeral=True
                )
                return
            try:
                await reactions_svc.respond(
                    session, character=character, log=log, choice=self.option.choice
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
        for child in view.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        view.stop()
        await interaction.response.edit_message(
            content=f"You chose: **{self.option.label}**", view=view
        )


class ReactionChoiceView(discord.ui.View):
    def __init__(
        self,
        *,
        bot: commands.Bot,
        owner_discord_id: int,
        character_id: int,
        log_id: int,
        options: tuple[reactions_svc.ReactionOption, ...],
    ) -> None:
        super().__init__(timeout=constants.REACTION_EVENT_CHOICE_SECONDS)
        self.bot = bot
        self.owner_discord_id = owner_discord_id
        self.character_id = character_id
        self.log_id = log_id
        for option in options:
            self.add_item(_ReactionButton(option))

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True


class ReactionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.trigger_loop.start()

    async def cog_unload(self) -> None:
        self.trigger_loop.cancel()

    group = app_commands.Group(name="reaction", description="How your character is seen by others")

    async def _current_tick(self, session) -> int:  # type: ignore[no-untyped-def]
        clock = await session.get(WorldClock, 1)
        return clock.tick if clock is not None else 0

    @group.command(name="archetype", description="See how a character is currently seen")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def archetype(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(
                session, interaction.user.id, character
            )
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            label = reactions_svc.archetype_label(char.archetype_score)
            text = t("reaction_archetype", name=char.name, label=label, score=char.archetype_score)
        await interaction.response.send_message(text, ephemeral=True)

    @group.command(name="respond", description="Answer a pending reaction pop-up by command")
    @app_commands.describe(character="Character name", choice="Good, Neutral, or Evil")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    @app_commands.choices(
        choice=[
            app_commands.Choice(name=c.value.title(), value=c.value)
            for c in (ReactionChoice.GOOD, ReactionChoice.NEUTRAL, ReactionChoice.EVIL)
        ]
    )
    async def respond(self, interaction: discord.Interaction, character: str, choice: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(
                session, interaction.user.id, character
            )
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            now = dt.datetime.now(dt.UTC)
            log = await reactions_svc.latest_pending(session, character_id=char.id, now=now)
            if log is None:
                await interaction.response.send_message(
                    t("reaction_none_pending", name=char.name), ephemeral=True
                )
                return
            try:
                new_score = await reactions_svc.respond(
                    session, character=char, log=log, choice=ReactionChoice(choice)
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            label = reactions_svc.archetype_label(new_score)
        await interaction.response.send_message(
            t("reaction_archetype", name=char.name, label=label, score=new_score), ephemeral=True
        )

    # --------------------------------------------------------------- loop

    async def _fire_for(self, character_id: int) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            character = await session.get(Character, character_id)
            if character is None:
                return
            user = await session.get(User, character.user_id)
            if user is None:
                return
            current_tick = await self._current_tick(session)
            rng = random.Random()
            log = await reactions_svc.trigger(
                session, character=character, current_tick=current_tick, rng=rng
            )
            await session.flush()
            scenario = reactions_svc.SCENARIOS[log.scenario_id]
            discord_id, log_id, character_name = user.discord_id, log.id, character.name

        discord_user = self.bot.get_user(discord_id)
        if discord_user is None:
            try:
                discord_user = await self.bot.fetch_user(discord_id)
            except discord.HTTPException:
                return
        view = ReactionChoiceView(
            bot=self.bot,
            owner_discord_id=discord_id,
            character_id=character_id,
            log_id=log_id,
            options=scenario.options,
        )
        with contextlib.suppress(discord.Forbidden):
            await discord_user.send(
                f"**{character_name}**: {scenario.prompt}\n"
                f"-- you have {constants.REACTION_EVENT_CHOICE_SECONDS} seconds to decide.",
                view=view,
            )

    @tasks.loop(minutes=constants.REACTION_EVENT_POLL_MINUTES)
    async def trigger_loop(self) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            current_tick = await self._current_tick(session)
            result = await session.execute(
                select(Character.id).where(
                    Character.status == CharacterStatus.APPROVED.value,
                    Character.away_since_tick.is_(None),
                    (Character.jailed_until_tick.is_(None))
                    | (Character.jailed_until_tick <= current_tick),
                )
            )
            candidate_ids = [row[0] for row in result.all()]

        rng = random.Random()
        for character_id in candidate_ids:
            if rng.random() < constants.REACTION_EVENT_TRIGGER_CHANCE:
                await self._fire_for(character_id)

    @trigger_loop.before_loop
    async def _before_trigger_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionsCog(bot))
