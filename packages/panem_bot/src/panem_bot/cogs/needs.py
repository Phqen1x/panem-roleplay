"""`/eat`, `/drink`, `/heal`, `/use`, `/entertain` -- Simulation mode's proactive meter
relief, alongside `/sleep` (`panem_bot.cogs.housing`) for fatigue.

`/eat`/`/drink`/`/heal`/`/use` are inventory-based (Vitals tab feature): pick a specific
owned good, relieved/restored by that good's own `hunger_value`/`thirst_value`/`heal_value`
(`panem_shared.content.schemas.Good`) rather than a flat amount -- the
autocomplete below scopes the `good` choice to what the invoking
character actually owns and can consume, mirroring `cogs/market.py`'s
own `_good_choices` cog-local-autocomplete shape. No cooking/baking
minigame here -- that's the Activity's Vitals tab only (`static/
vitals.html`'s `?kind=cook|bake`); a straight `/eat`/`/drink` always
passes `bonus=False`.

`/entertain` stays a flat `SANITY_RELIEF_PER_ENTERTAIN` credit, unlike
the Vitals tab's per-minigame-value Entertainment panel -- Discord has no
minigames to play, so this is the lesser option for a player not in the
Activity right now.

Kept as its own small cog rather than folded into `housing.py`: these
commands aren't part of the housing/property system at all, a
sibling need-relief mechanic that happens to share `/sleep`'s
"Simulation mode" shape.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete, utils
from panem_bot.errors import ServiceError
from panem_bot.services import afflictions as afflictions_svc
from panem_bot.services import characters as characters_svc
from panem_bot.services import sustenance as sustenance_svc
from panem_bot.strings import t
from panem_shared.db.models import Character, WorldClock


class NeedsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _current_tick(self, session: AsyncSession) -> int:
        clock = await session.get(WorldClock, 1)
        return clock.tick if clock is not None else 0

    async def _cured_note(self, session: AsyncSession, char: Character) -> str:
        cured = await afflictions_svc.check_and_cure(session, char)
        if not cured:
            return ""
        names = ", ".join(row.affliction_type.name for row in cured)
        return f" Also cured: {names}."

    async def _consumable_choices(
        self,
        interaction: discord.Interaction,
        current: str,
        *,
        drinkable: bool = False,
        healable: bool = False,
    ) -> list[app_commands.Choice[str]]:
        character_name = getattr(interaction.namespace, "character", None)
        if not character_name:
            return []
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(session, interaction.user.id, character_name)
            if char is None:
                return []
            content = self.bot.content  # type: ignore[attr-defined]
            pairs = await sustenance_svc.owned_consumables(session, char, content.goods)
        current_lower = current.lower()

        def _is_match(good: object) -> bool:
            if healable:
                return getattr(good, "heal_value", 0.0) > 0
            if drinkable:
                return getattr(good, "thirst_value", 0.0) > 0
            return getattr(good, "hunger_value", 0.0) > 0

        matches = [
            good
            for _row, good in pairs
            if _is_match(good)
            and (current_lower in good.id.lower() or current_lower in good.name.lower())
        ]
        matches.sort(key=lambda g: g.name)
        return [app_commands.Choice(name=f"{g.name} ({g.id})", value=g.id) for g in matches[:25]]

    @app_commands.command(
        name="eat", description="Eat an owned good to relieve hunger (Simulation mode)"
    )
    @app_commands.describe(character="Character name", good="An edible good you own")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def eat(self, interaction: discord.Interaction, character: str, good: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            content = self.bot.content  # type: ignore[attr-defined]
            good_row = content.goods.get(good)
            if good_row is None:
                await interaction.response.send_message(
                    t("good_not_edible", name=char.name, good=good), ephemeral=True
                )
                return
            current_tick = await self._current_tick(session)
            try:
                hunger = await sustenance_svc.eat(session, char, good_row, current_tick)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            note = await self._cured_note(session, char)
            name = char.name
        await interaction.response.send_message(
            t("eat_ok", name=name, good=good_row.name, hunger=round(hunger)) + note, ephemeral=True
        )

    @eat.autocomplete("good")
    async def eat_good_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._consumable_choices(interaction, current, drinkable=False)

    @app_commands.command(
        name="drink", description="Drink an owned good to relieve thirst (Simulation mode)"
    )
    @app_commands.describe(character="Character name", good="A drinkable good you own")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def drink(self, interaction: discord.Interaction, character: str, good: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            content = self.bot.content  # type: ignore[attr-defined]
            good_row = content.goods.get(good)
            if good_row is None:
                await interaction.response.send_message(
                    t("good_not_drinkable", name=char.name, good=good), ephemeral=True
                )
                return
            current_tick = await self._current_tick(session)
            try:
                thirst = await sustenance_svc.drink(session, char, good_row, current_tick)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            note = await self._cured_note(session, char)
            name = char.name
        await interaction.response.send_message(
            t("drink_ok", name=name, good=good_row.name, thirst=round(thirst)) + note,
            ephemeral=True,
        )

    @drink.autocomplete("good")
    async def drink_good_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._consumable_choices(interaction, current, drinkable=True)

    @app_commands.command(
        name="heal", description="Use an owned medical good to heal health (Simulation mode)"
    )
    @app_commands.describe(character="Character name", good="A medical good you own")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def heal(self, interaction: discord.Interaction, character: str, good: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            content = self.bot.content  # type: ignore[attr-defined]
            good_row = content.goods.get(good)
            if good_row is None:
                await interaction.response.send_message(
                    t("good_not_healing", name=char.name, good=good), ephemeral=True
                )
                return
            current_tick = await self._current_tick(session)
            try:
                health = await sustenance_svc.heal(session, char, good_row, current_tick)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            note = await self._cured_note(session, char)
            name = char.name
        await interaction.response.send_message(
            t("heal_ok", name=name, good=good_row.name, health=round(health)) + note,
            ephemeral=True,
        )

    @heal.autocomplete("good")
    async def heal_good_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._consumable_choices(interaction, current, healable=True)

    @app_commands.command(
        name="use", description="Use an owned consumable good (e.g. medicine to heal) (Simulation mode)"
    )
    @app_commands.describe(character="Character name", good="A consumable good you own")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def use(self, interaction: discord.Interaction, character: str, good: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            content = self.bot.content  # type: ignore[attr-defined]
            good_row = content.goods.get(good)
            if good_row is None:
                await interaction.response.send_message(
                    t("sustenance_no_inventory", name=char.name, good=good), ephemeral=True
                )
                return
            current_tick = await self._current_tick(session)
            try:
                if good_row.heal_value > 0:
                    health = await sustenance_svc.heal(session, char, good_row, current_tick)
                    msg_key = "heal_ok"
                    val: dict[str, object] = dict(health=round(health))
                elif good_row.hunger_value > 0:
                    hunger = await sustenance_svc.eat(session, char, good_row, current_tick)
                    msg_key = "eat_ok"
                    val = dict(hunger=round(hunger))
                elif good_row.thirst_value > 0:
                    thirst = await sustenance_svc.drink(session, char, good_row, current_tick)
                    msg_key = "drink_ok"
                    val = dict(thirst=round(thirst))
                else:
                    await interaction.response.send_message(
                        t("good_not_healing", name=char.name, good=good), ephemeral=True
                    )
                    return
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            note = await self._cured_note(session, char)
            name = char.name
        await interaction.response.send_message(
            t(msg_key, name=name, good=good_row.name, **val) + note, ephemeral=True
        )

    @use.autocomplete("good")
    async def use_good_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        character_name = getattr(interaction.namespace, "character", None)
        if not character_name:
            return []
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(session, interaction.user.id, character_name)
            if char is None:
                return []
            content = self.bot.content  # type: ignore[attr-defined]
            pairs = await sustenance_svc.owned_consumables(session, char, content.goods)
        current_lower = current.lower()
        matches = [
            good
            for _row, good in pairs
            if (current_lower in good.id.lower() or current_lower in good.name.lower())
        ]
        matches.sort(key=lambda g: g.name)
        return [app_commands.Choice(name=f"{g.name} ({g.id})", value=g.id) for g in matches[:25]]

    @app_commands.command(
        name="entertain", description="Entertain yourself to relieve sanity loss (Simulation mode)"
    )
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def entertain(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            current_tick = await self._current_tick(session)
            try:
                sanity = sustenance_svc.entertain(char, None, current_tick)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            note = await self._cured_note(session, char)
            name = char.name
        await interaction.response.send_message(
            t("entertain_ok", name=name, sanity=round(sanity)) + note, ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NeedsCog(bot))
