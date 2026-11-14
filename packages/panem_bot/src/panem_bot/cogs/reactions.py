"""`/reaction archetype` (check how a character is currently seen) and
`/reaction respond` (answer a pending pop-up by command) (Spec "Reaction
Events"). Triggering a fresh pop-up in the first place is no longer this
cog's job at all: `panem_shared.reactions.maybe_trigger_from_dashboard`
rolls one straight from `/activity/dashboard/reactions/{character_id}/
status`, which only ever gets polled while the Activity dashboard is
actually open in front of a player (`app.js`'s own global poll) -- a pop-
up DMed to someone who isn't even looking at Discord right now, which is
what this cog used to do on a background loop, defeats the point of a
pop-up. `/reaction respond` stays as the Discord-side way to answer one
that's already pending (e.g. rolled while the dashboard was open, but the
player has since switched to Discord), calling the exact same
`panem_shared.reactions.respond` the dashboard's own `/respond` endpoint
does, so whichever side answers first wins and the other is told it's
already answered."""

from __future__ import annotations

import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands

from panem_bot import autocomplete, utils
from panem_bot.errors import ServiceError
from panem_bot.strings import t
from panem_shared import reactions as reactions_svc
from panem_shared.enums import ReactionChoice


class ReactionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    group = app_commands.Group(name="reaction", description="How your character is seen by others")

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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionsCog(bot))
