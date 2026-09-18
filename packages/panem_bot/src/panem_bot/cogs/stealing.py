"""`/steal`/`/burgle` -- pickpocketing and burglary (contraband system)."""

from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import activity_launch, autocomplete
from panem_bot.errors import ServiceError
from panem_bot.services import characters as characters_svc
from panem_bot.services import stealing as stealing_svc
from panem_bot.strings import t
from panem_shared import constants
from panem_shared.db.models import Character, DistrictState, Npc, Property, WorldClock
from panem_shared.enums import CharacterStatus, OwnerKind, PropertyKind
from panem_shared.stealing import StealResult, StealVictim


def _strip_at(name: str) -> str:
    """Lets a player type a target the way they'd @-mention someone
    elsewhere in the server (`@Commodus`) instead of picking an
    autocomplete suggestion -- names are never actually stored with a
    leading `@`, so this is stripped before matching either way."""
    return name[1:] if name.startswith("@") else name


def _steal_result_text(result: StealResult, name: str, target_name: str) -> str:
    if result.success:
        return t("steal_ok", name=name, amount=result.amount, target=target_name)
    if result.caught:
        return t(
            "steal_caught",
            name=name,
            target=target_name,
            fine=constants.STEAL_FINE,
            jail_ticks=constants.STEAL_JAIL_TICKS,
        )
    if result.alerted:
        return t("steal_alerted_escape", name=name, target=target_name)
    return t("steal_miss", name=name)


def _burgle_result_text(result: StealResult, name: str, owner_name: str) -> str:
    if result.success:
        return t("burgle_ok", name=name, owner=owner_name, amount=result.amount)
    if result.caught:
        return t(
            "burgle_caught",
            name=name,
            owner=owner_name,
            fine=constants.STEAL_FINE,
            jail_ticks=constants.STEAL_JAIL_TICKS,
        )
    if result.alerted:
        return t("burgle_alerted_escape", name=name, owner=owner_name)
    return t("burgle_miss", name=name)


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
    ) -> StealVictim | None:
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

    # ------------------------------------------------------------------ /steal

    async def _resolve_steal_text(
        self,
        char_id: int,
        victim_kind: str,
        victim_id: str,
        district_id: int,
        current_tick: int,
    ) -> str:
        """The RNG-fallback skill check (no Activity configured, or the
        player hits Skip) -- shared by the instant path below and the
        launch message's Skip button."""
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await session.get(Character, char_id)
            if char is None:
                return t("character_not_found")
            victim: StealVictim | None
            if victim_kind == "npc":
                victim = await session.get(Npc, victim_id)
            else:
                victim = await session.get(Character, int(victim_id))
            if victim is None:
                return t("steal_target_not_found")
            district_row = await session.get(DistrictState, district_id)
            result = await stealing_svc.roll_and_apply_steal(
                session,
                character=char,
                victim=victim,
                district_row=district_row,
                current_tick=current_tick,
                rng=random.Random(),
            )
            name, target_name = char.name, victim.name
        return _steal_result_text(result, name, target_name)

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
                stealing_svc.check_can_steal(char, victim, current_tick)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            char.last_steal_tick = current_tick
            char_id, char_name = char.id, char.name
            district_id = char.current_district_id
            victim_kind = "npc" if isinstance(victim, Npc) else "character"
            victim_id, victim_name = str(victim.id), victim.name

        activity_url = self.bot.settings.activity_public_url  # type: ignore[attr-defined]
        if not activity_url:
            text = await self._resolve_steal_text(
                char_id, victim_kind, victim_id, district_id, current_tick
            )
            await interaction.response.send_message(text, ephemeral=True)
            return

        attempt_id = activity_launch.new_attempt_id()
        await activity_launch.create_crime_attempt(
            self.bot,
            attempt_id,
            {
                "kind": "steal",
                "character_id": char_id,
                "district_id": district_id,
                "current_tick": current_tick,
                "victim_kind": victim_kind,
                "victim_id": victim_id,
            },
        )

        async def on_skip(skip_interaction: discord.Interaction) -> None:
            await activity_launch.forget_crime_attempt(self.bot, attempt_id)
            text = await self._resolve_steal_text(
                char_id, victim_kind, victim_id, district_id, current_tick
            )
            await skip_interaction.response.edit_message(
                content=t("steal_already_tried"), view=None
            )
            await skip_interaction.followup.send(text, ephemeral=True)

        view = activity_launch.crime_launch_view(activity_url, "steal", attempt_id, on_skip)
        await interaction.response.send_message(
            t("steal_game_ready", name=char_name, target=victim_name), view=view, ephemeral=True
        )
        await activity_launch.remember_crime_interaction(self.bot, attempt_id, interaction)

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

    # ------------------------------------------------------------------ /burgle

    async def _resolve_burgle_text(
        self, char_id: int, house_id: int, district_id: int, current_tick: int, owner_name: str
    ) -> str:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await session.get(Character, char_id)
            if char is None:
                return t("character_not_found")
            house = await session.get(Property, house_id)
            if house is None:
                return t("burgle_owner_not_found")
            district_row = await session.get(DistrictState, district_id)
            result = await stealing_svc.roll_and_apply_burgle(
                session,
                character=char,
                house_value=house.suggested_price,
                district_row=district_row,
                current_tick=current_tick,
                rng=random.Random(),
            )
            name = char.name
        return _burgle_result_text(result, name, owner_name)

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
                stealing_svc.check_can_burgle(char, house, current_tick, owner=owner_char)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            char.last_steal_tick = current_tick
            char_id, char_name = char.id, char.name
            house_id, district_id = house.id, house.district_id

        activity_url = self.bot.settings.activity_public_url  # type: ignore[attr-defined]
        if not activity_url:
            text = await self._resolve_burgle_text(
                char_id, house_id, district_id, current_tick, owner
            )
            await interaction.response.send_message(text, ephemeral=True)
            return

        attempt_id = activity_launch.new_attempt_id()
        await activity_launch.create_crime_attempt(
            self.bot,
            attempt_id,
            {
                "kind": "burgle",
                "character_id": char_id,
                "property_id": house_id,
                "district_id": district_id,
                "current_tick": current_tick,
            },
        )

        async def on_skip(skip_interaction: discord.Interaction) -> None:
            await activity_launch.forget_crime_attempt(self.bot, attempt_id)
            text = await self._resolve_burgle_text(
                char_id, house_id, district_id, current_tick, owner
            )
            await skip_interaction.response.edit_message(
                content=t("burgle_already_tried"), view=None
            )
            await skip_interaction.followup.send(text, ephemeral=True)

        view = activity_launch.crime_launch_view(activity_url, "burgle", attempt_id, on_skip)
        await interaction.response.send_message(
            t("burgle_game_ready", name=char_name, owner=owner), view=view, ephemeral=True
        )
        await activity_launch.remember_crime_interaction(self.bot, attempt_id, interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StealingCog(bot))
