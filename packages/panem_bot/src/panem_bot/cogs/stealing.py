"""`/steal` -- pickpocketing players and NPCs (contraband system)."""

from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete
from panem_bot.errors import ServiceError
from panem_bot.services import characters as characters_svc
from panem_bot.services import stealing as stealing_svc
from panem_bot.strings import t
from panem_shared import constants
from panem_shared.db.models import Character, Npc, Property, WorldClock
from panem_shared.enums import CharacterStatus, OwnerKind, PropertyKind


def _strip_at(name: str) -> str:
    """Lets a player type a target the way they'd @-mention someone
    elsewhere in the server (`@Commodus`) instead of picking an
    autocomplete suggestion -- names are never actually stored with a
    leading `@`, so this is stripped before matching either way."""
    return name[1:] if name.startswith("@") else name


class StealingCog(commands.Cog):
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

    async def _resolve_target(
        self, session: AsyncSession, char: Character, target_name: str
    ) -> Character | Npc | None:
        """A player character (anyone's but the thief's own) at the same
        location first, then an NPC -- matching `/talk`'s own free-typed
        name resolution against the district's residents."""
        target_char = (
            await session.execute(
                select(Character).where(
                    Character.name == target_name,
                    Character.status == CharacterStatus.APPROVED.value,
                    Character.current_district_id == char.current_district_id,
                    Character.location_id == char.location_id,
                    Character.id != char.id,
                )
            )
        ).scalar_one_or_none()
        if target_char is not None:
            return target_char
        return (
            await session.execute(
                select(Npc).where(
                    Npc.name == target_name,
                    Npc.district_id == char.current_district_id,
                    Npc.location_id == char.location_id,
                )
            )
        ).scalar_one_or_none()

    @app_commands.command(name="steal", description="Try to pickpocket a player or NPC")
    @app_commands.describe(
        character="Character name", target="Who to steal from (or @mention their name)"
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def steal(self, interaction: discord.Interaction, character: str, target: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            victim = await self._resolve_target(session, char, _strip_at(target))
            if victim is None:
                await interaction.response.send_message(t("steal_target_not_found"), ephemeral=True)
                return

            current_tick = await self._current_tick(session)
            try:
                result = await stealing_svc.resolve_steal(
                    session,
                    character=char,
                    victim=victim,
                    district_id=char.current_district_id,
                    current_tick=current_tick,
                    rng=random.Random(),
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            name, target_name = char.name, victim.name

        if result.success:
            text = t("steal_ok", name=name, amount=result.amount, target=target_name)
        elif result.caught:
            text = t(
                "steal_caught",
                name=name,
                target=target_name,
                fine=constants.STEAL_FINE,
                jail_ticks=constants.STEAL_JAIL_TICKS,
            )
        elif result.alerted:
            text = t("steal_alerted_escape", name=name, target=target_name)
        else:
            text = t("steal_miss", name=name)
        await interaction.response.send_message(text, ephemeral=True)

    @steal.autocomplete("target")
    async def steal_target_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Only ever people the invoker could actually steal from right
        now -- other approved characters and NPCs sharing both their
        district *and* their exact location, matching `_resolve_target`'s
        own scope (Spec: "Only steal from people in the same location as
        you")."""
        character_name = getattr(interaction.namespace, "character", None)
        if not character_name:
            return []
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character_name)
            if char is None:
                return []
            char_names = (
                (
                    await session.execute(
                        select(Character.name).where(
                            Character.status == CharacterStatus.APPROVED.value,
                            Character.current_district_id == char.current_district_id,
                            Character.location_id == char.location_id,
                            Character.id != char.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            npc_names = (
                (
                    await session.execute(
                        select(Npc.name).where(
                            Npc.district_id == char.current_district_id,
                            Npc.location_id == char.location_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        current_lower = _strip_at(current).lower()
        candidates = [(name, "player") for name in char_names] + [
            (name, "NPC") for name in npc_names
        ]
        matches = sorted(pair for pair in candidates if current_lower in pair[0].lower())
        return [
            app_commands.Choice(name=f"{name} ({kind})", value=name)
            for name, kind in matches[: autocomplete.MAX_CHOICES]
        ]

    @app_commands.command(name="burgle", description="Try to break into another character's house")
    @app_commands.describe(character="Character name", owner="Name of the house's owner")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def burgle(self, interaction: discord.Interaction, character: str, owner: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            owner_char = (
                await session.execute(
                    select(Character).where(
                        Character.name == owner,
                        Character.current_district_id == char.current_district_id,
                    )
                )
            ).scalar_one_or_none()
            house = None
            if owner_char is not None:
                house = (
                    await session.execute(
                        select(Property).where(
                            Property.kind == PropertyKind.HOUSE.value,
                            Property.owner_kind == OwnerKind.CHARACTER.value,
                            Property.owner_id == owner_char.id,
                            Property.district_id == char.current_district_id,
                        )
                    )
                ).scalar_one_or_none()
            if house is None:
                await interaction.response.send_message(t("burgle_owner_not_found"), ephemeral=True)
                return

            current_tick = await self._current_tick(session)
            try:
                result = await stealing_svc.resolve_burgle(
                    session,
                    character=char,
                    house=house,
                    current_tick=current_tick,
                    rng=random.Random(),
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            name = char.name

        if result.success:
            text = t("burgle_ok", name=name, owner=owner, amount=result.amount)
        elif result.caught:
            text = t(
                "burgle_caught",
                name=name,
                owner=owner,
                fine=constants.STEAL_FINE,
                jail_ticks=constants.STEAL_JAIL_TICKS,
            )
        elif result.alerted:
            text = t("burgle_alerted_escape", name=name, owner=owner)
        else:
            text = t("burgle_miss", name=name)
        await interaction.response.send_message(text, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StealingCog(bot))
