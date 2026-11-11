"""`/museum` -- donate collectibles found via hobbies/events, browse and
organize the result, and look at (or point someone else at) any
character's museum."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete, utils
from panem_bot.errors import ServiceError
from panem_bot.strings import t
from panem_shared import constants
from panem_shared import museum as museum_svc
from panem_shared.db.models import Character, CollectibleInstance, WorldClock


async def _find_any_character(session: AsyncSession, name: str) -> Character | None:
    """Unlike `utils.get_character_case_insensitive`, not scoped to a
    particular Discord account -- `/museum view` is explicitly meant to
    look at *anyone's* collection (Spec: "share your museum with other
    people")."""
    result = await session.execute(
        select(Character).where(func.lower(Character.name) == name.strip().lower())
    )
    return result.scalar_one_or_none()


async def _undonated_choices(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    character_name = getattr(interaction.namespace, "character", None)
    if not character_name:
        return []
    bot = interaction.client
    async with bot.db() as session:  # type: ignore[attr-defined]
        char = await utils.get_character_case_insensitive(session, interaction.user.id, character_name)
        if char is None:
            return []
        content = bot.content  # type: ignore[attr-defined]
        instances = await museum_svc.undonated_instances(session, char.id)
    current_lower = current.lower()
    choices = []
    for instance in instances:
        collectible = content.collectibles.get(instance.collectible_id)
        name = collectible.name if collectible is not None else instance.collectible_id
        if current_lower in name.lower():
            choices.append((name, instance.id))
    choices.sort(key=lambda pair: pair[0])
    return [
        app_commands.Choice(name=f"{name} (#{iid})", value=iid) for name, iid in choices[:25]
    ]


async def _own_slot_choices(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    character_name = getattr(interaction.namespace, "character", None)
    if not character_name:
        return []
    bot = interaction.client
    async with bot.db() as session:  # type: ignore[attr-defined]
        char = await utils.get_character_case_insensitive(session, interaction.user.id, character_name)
        if char is None:
            return []
        content = bot.content  # type: ignore[attr-defined]
        slots = await museum_svc.museum_slots(session, char.id)
        instance_ids = [slot.collectible_instance_id for slot in slots]
        instances = {}
        if instance_ids:
            for iid in instance_ids:
                row = await session.get(CollectibleInstance, iid)
                if row is not None:
                    instances[iid] = row
    current_lower = current.lower()
    choices = []
    for slot in slots:
        instance = instances.get(slot.collectible_instance_id)
        collectible = content.collectibles.get(instance.collectible_id) if instance else None
        name = collectible.name if collectible is not None else str(slot.collectible_instance_id)
        label = f"{name} ({slot.wing})"
        if current_lower in label.lower():
            choices.append((label, slot.id))
    return [app_commands.Choice(name=label, value=sid) for label, sid in choices[:25]]


class MuseumCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    group = app_commands.Group(name="museum", description="Donate and browse museum collections")

    async def _current_tick(self, session) -> int:  # type: ignore[no-untyped-def]
        clock = await session.get(WorldClock, 1)
        return clock.tick if clock is not None else 0

    @group.command(name="donate", description="Donate a collectible to the museum")
    @app_commands.describe(character="Character name", collectible="Which undonated find to give")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    @app_commands.autocomplete(collectible=_undonated_choices)
    async def donate(
        self, interaction: discord.Interaction, character: str, collectible: int
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            content = self.bot.content  # type: ignore[attr-defined]
            current_tick = await self._current_tick(session)
            try:
                item, reward = await museum_svc.donate(
                    session,
                    character=char,
                    instance_id=collectible,
                    content=content,
                    current_tick=current_tick,
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            text = t("museum_donated", name=char.name, collectible=item.name, reward=reward)
        await interaction.response.send_message(text)

    @group.command(name="view", description="View a character's museum")
    @app_commands.describe(character="Whose museum to view (defaults to your own)")
    @app_commands.autocomplete(character=autocomplete.any_approved)
    async def view(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await _find_any_character(session, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            content = self.bot.content  # type: ignore[attr-defined]
            slots = await museum_svc.museum_slots(session, char.id)
            if not slots:
                await interaction.response.send_message(
                    t("museum_empty", name=char.name), ephemeral=True
                )
                return
            instances = {}
            for slot in slots:
                row = await session.get(CollectibleInstance, slot.collectible_instance_id)
                if row is not None:
                    instances[slot.id] = row
            by_wing: dict[str, list[str]] = {}
            for slot in slots:
                instance = instances.get(slot.id)
                collectible = (
                    content.collectibles.get(instance.collectible_id) if instance else None
                )
                name = collectible.name if collectible is not None else "???"
                rarity = f" ({collectible.rarity.value})" if collectible is not None else ""
                by_wing.setdefault(slot.wing, []).append(f"{name}{rarity}")
            embed = discord.Embed(title=f"{char.name}'s Museum")
            for wing, items in by_wing.items():
                embed.add_field(name=wing, value="\n".join(items), inline=False)
        await interaction.response.send_message(embed=embed)

    @group.command(name="organize", description="Move one donated piece to a different wing/spot")
    @app_commands.describe(
        character="Character name", slot="Which piece to move", wing="New wing name",
        position="Order within the wing (lower = earlier)",
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    @app_commands.autocomplete(slot=_own_slot_choices)
    async def organize(
        self,
        interaction: discord.Interaction,
        character: str,
        slot: int,
        wing: str,
        position: int = 0,
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            try:
                await museum_svc.move(session, character=char, slot_id=slot, wing=wing, position=position)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
        await interaction.response.send_message(t("museum_moved", wing=wing), ephemeral=True)

    @group.command(name="preset", description="Auto-organize a character's whole museum")
    @app_commands.describe(character="Character name", preset="How to sort it")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    @app_commands.choices(
        preset=[app_commands.Choice(name=p.replace("_", " ").title(), value=p) for p in constants.MUSEUM_PRESETS]
    )
    async def preset(self, interaction: discord.Interaction, character: str, preset: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await utils.get_character_case_insensitive(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            content = self.bot.content  # type: ignore[attr-defined]
            try:
                await museum_svc.apply_preset(
                    session, character_id=char.id, preset=preset, content=content
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            text = t("museum_preset_applied", name=char.name, preset=preset.replace("_", " "))
        await interaction.response.send_message(text)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MuseumCog(bot))
