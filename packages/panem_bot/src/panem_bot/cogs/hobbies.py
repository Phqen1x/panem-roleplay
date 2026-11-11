"""`/hobby` -- pick up to 3 hobbies and play their shared timing-based
minigame (`HobbyTimingView`) for a chance at a collectible (`/museum`) or,
rarely, a personal event chain (`/event`)."""

from __future__ import annotations

import asyncio
import random
import time

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from panem_bot import autocomplete, utils
from panem_bot.errors import ServiceError
from panem_bot.strings import t
from panem_shared import hobbies as hobbies_svc
from panem_shared import hobby_levels
from panem_shared.db.models import Character, CharacterHobby, Inventory, WorldClock
from panem_shared.enums import OwnerKind

_WINDOW_SECONDS = 2.5
_MIN_DELAY_SECONDS = 2.0
_MAX_DELAY_SECONDS = 5.0


class HobbyTimingView(discord.ui.View):
    """One shared cast-and-reel/till-and-water/mix-and-pull prompt. The
    button starts inert (any click before `arm()` counts as "too early");
    `arm()` flips it live for exactly `_WINDOW_SECONDS` before counting as
    "too late" -- a real, if simple, reaction-timing skill check rather
    than a disguised coin flip."""

    def __init__(self, *, owner_discord_id: int, label: str) -> None:
        super().__init__(timeout=_MAX_DELAY_SECONDS + _WINDOW_SECONDS + 10)
        self.owner_discord_id = owner_discord_id
        self.armed_at: float | None = None
        self.result: str | None = None
        self.done = asyncio.Event()
        self._button.label = label

    def arm(self) -> None:
        self.armed_at = time.monotonic()

    def _finish(self, result: str) -> None:
        if self.result is not None:
            return
        self.result = result
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        self.stop()
        self.done.set()

    async def on_timeout(self) -> None:
        self._finish("late")

    @discord.ui.button(label="Go!", style=discord.ButtonStyle.primary)
    async def _button(
        self, interaction: discord.Interaction, button: discord.ui.Button[HobbyTimingView]
    ) -> None:
        if interaction.user.id != self.owner_discord_id:
            await interaction.response.send_message(t("character_not_yours"), ephemeral=True)
            return
        if self.armed_at is None:
            self._finish("early")
        elif time.monotonic() - self.armed_at > _WINDOW_SECONDS:
            self._finish("late")
        else:
            self._finish("hit")
        await interaction.response.defer()


class HobbiesCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    group = app_commands.Group(name="hobby", description="Pick and play hobbies")

    async def _current_tick(self, session) -> int:  # type: ignore[no-untyped-def]
        clock = await session.get(WorldClock, 1)
        return clock.tick if clock is not None else 0

    @group.command(name="select", description="Pick a hobby (up to 3 per character)")
    @app_commands.describe(character="Character name", hobby="Hobby to pick up")
    @app_commands.autocomplete(character=autocomplete.own_approved, hobby=autocomplete.any_hobby)
    async def select(self, interaction: discord.Interaction, character: str, hobby: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            content = self.bot.content  # type: ignore[attr-defined]
            try:
                await hobbies_svc.select_hobby(session, char, hobby, content)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            hobby_def = content.hobbies[hobby]
            tool_note = ""
            if hobby_def.tool_good_id is not None:
                tool = content.goods[hobby_def.tool_good_id]
                tool_note = t("hobby_tool_note", tool=tool.name)
            text = t("hobby_selected", name=char.name, hobby=hobby_def.name, tool_note=tool_note)
        await interaction.response.send_message(text)

    @group.command(name="forget", description="Drop a hobby to free a slot")
    @app_commands.describe(character="Character name", hobby="Hobby to drop")
    @app_commands.autocomplete(character=autocomplete.own_approved, hobby=autocomplete.any_hobby)
    async def forget(self, interaction: discord.Interaction, character: str, hobby: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            try:
                await hobbies_svc.forget_hobby(session, char, hobby)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            hobby_name = self.bot.content.hobbies[hobby].name  # type: ignore[attr-defined]
            text = t("hobby_forgotten", name=char.name, hobby=hobby_name)
        await interaction.response.send_message(text)

    @group.command(name="list", description="See a character's hobbies")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def list_(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            rows = await hobbies_svc.selected_hobbies(session, char.id)
            if not rows:
                await interaction.response.send_message(
                    t("hobby_list_empty", name=char.name), ephemeral=True
                )
                return
            content = self.bot.content  # type: ignore[attr-defined]
            lines = []
            for row in rows:
                hobby_def = content.hobbies.get(row.hobby_id)
                name = hobby_def.name if hobby_def is not None else row.hobby_id
                level = hobby_levels.hobby_level_for_sessions(row.sessions_played)
                lines.append(f"**{name}** -- {level.value.title()} ({row.sessions_played} sessions)")
            embed = discord.Embed(title=f"{char.name}'s hobbies", description="\n".join(lines))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="play", description="Play one of your character's hobbies")
    @app_commands.describe(character="Character name", hobby="Which hobby to play")
    @app_commands.autocomplete(character=autocomplete.own_approved, hobby=autocomplete.any_hobby)
    async def play(self, interaction: discord.Interaction, character: str, hobby: str) -> None:
        content = self.bot.content  # type: ignore[attr-defined]
        hobby_def = content.hobbies.get(hobby)
        if hobby_def is None:
            await interaction.response.send_message(t("hobby_not_found"), ephemeral=True)
            return

        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            result = await session.execute(
                select(CharacterHobby).where(
                    CharacterHobby.character_id == char.id, CharacterHobby.hobby_id == hobby
                )
            )
            char_hobby = result.scalar_one_or_none()
            if char_hobby is None:
                await interaction.response.send_message(
                    t("hobby_not_selected", name=char.name), ephemeral=True
                )
                return
            current_tick = await self._current_tick(session)
            try:
                hobbies_svc.check_can_play(char_hobby, current_tick)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            owns_tool = False
            if hobby_def.tool_good_id is not None:
                inv = await session.get(
                    Inventory, (OwnerKind.CHARACTER.value, str(char.id), hobby_def.tool_good_id)
                )
                owns_tool = inv is not None and inv.qty > 0
            probability = hobbies_svc.session_success_probability(
                hobby_def, char_hobby.sessions_played, owns_tool
            )
            char_id, char_name = char.id, char.name

        view = HobbyTimingView(owner_discord_id=interaction.user.id, label=hobby_def.verb.title())
        await interaction.response.send_message(
            t("hobby_session_prompt", name=char_name, verb=hobby_def.verb), view=view
        )
        await asyncio.sleep(random.uniform(_MIN_DELAY_SECONDS, _MAX_DELAY_SECONDS))
        if view.result is None:
            view.arm()
            await interaction.edit_original_response(content=t("hobby_session_ready"), view=view)
            try:
                await asyncio.wait_for(view.done.wait(), timeout=_WINDOW_SECONDS + 0.5)
            except TimeoutError:
                view._finish("late")

        timing_hit = view.result == "hit"
        rng = random.Random()
        success = timing_hit and rng.random() < probability

        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await session.get(Character, char_id)
            char_hobby_result = await session.execute(
                select(CharacterHobby).where(
                    CharacterHobby.character_id == char_id, CharacterHobby.hobby_id == hobby
                )
            )
            char_hobby = char_hobby_result.scalar_one()
            outcome = await hobbies_svc.resolve_session(
                session,
                character=char,
                character_hobby=char_hobby,
                hobby=hobby_def,
                content=content,
                current_tick=current_tick,
                success=success,
                rng=rng,
            )

        if view.result == "early":
            text = t("hobby_session_too_early", name=char_name)
        elif not outcome.success:
            text = (
                t("hobby_session_too_late", name=char_name)
                if view.result == "late"
                else t("hobby_session_miss", name=char_name)
            )
        elif outcome.chain_started:
            text = t("hobby_session_hit_chain", name=char_name)
        elif outcome.collectible is not None:
            text = t("hobby_session_hit_item", name=char_name, collectible=outcome.collectible.name)
        else:
            text = t("hobby_session_miss", name=char_name)
        if outcome.leveled_up_to is not None:
            text += t("hobby_level_up", name=char_name, level=outcome.leveled_up_to.value.title(),
                      hobby=hobby_def.name)
        await interaction.followup.send(text)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HobbiesCog(bot))
