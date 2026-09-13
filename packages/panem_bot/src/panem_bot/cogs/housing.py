"""`/housing` (buying houses, apartment units/complexes, inn stays,
renting, moving out) and `/sleep` (the fatigue/rest mechanic housing ties
into). Mortgages, refinancing, staff price overrides, and auctions are a
later addition to this same cog.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete
from panem_bot.errors import ServiceError
from panem_bot.services import characters as characters_svc
from panem_bot.services import housing as housing_svc
from panem_bot.strings import t
from panem_shared import constants, simtime
from panem_shared.db.models import ApartmentLease, Character, Property, WorldClock
from panem_shared.enums import OwnerKind, PropertyKind


class HousingCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    group = app_commands.Group(name="housing", description="Buy, rent, and manage real estate")

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

    def _listing_line(self, property_: Property, buyer: Character) -> str:
        label = property_.kind.title()
        if property_.kind == PropertyKind.HOUSE.value:
            price = round(housing_svc.quoted_price(property_, buyer))
            return f"`#{property_.id}` {label} ({property_.tier.title()}) -- {price} money"
        if property_.kind == PropertyKind.INN.value:
            price = round(housing_svc.quoted_price(property_, buyer))
            return f"`#{property_.id}` {label} -- {price} money/night"
        price = round(housing_svc.quoted_price(property_, buyer))
        complex_note = f", complex `{property_.complex_id}`" if property_.complex_id else ""
        return f"`#{property_.id}` {label} unit{complex_note} -- {price} money/day rent"

    # ------------------------------------------------------------- /housing

    @group.command(name="list", description="See housing for sale or rent where you are")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def list_housing(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            leased_unit_ids = select(ApartmentLease.property_id)
            rows = (
                (
                    await session.execute(
                        select(Property).where(
                            Property.district_id == char.current_district_id,
                            (
                                (Property.kind != PropertyKind.APARTMENT.value)
                                & Property.for_sale.is_(True)
                            )
                            | (
                                (Property.kind == PropertyKind.APARTMENT.value)
                                & Property.id.not_in(leased_unit_ids)
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            lines = [
                self._listing_line(p, char)
                for p in sorted(rows, key=lambda p: (p.kind, p.tier, p.id))
            ]
        text = "\n".join(lines) if lines else t("housing_nothing_available")
        await interaction.response.send_message(text, ephemeral=True)

    @group.command(name="buy", description="Buy a house or an inn outright")
    @app_commands.describe(character="Character name", property_id="Property ID from /housing list")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def buy(self, interaction: discord.Interaction, character: str, property_id: int) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            property_ = await session.get(Property, property_id)
            if property_ is None:
                await interaction.response.send_message(t("housing_not_found"), ephemeral=True)
                return
            try:
                housing_svc.check_can_buy_property(character=char, property_=property_)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

            price = round(housing_svc.quoted_price(property_, char))
            if char.money < price:
                await interaction.response.send_message(
                    t("housing_insufficient_funds", name=char.name), ephemeral=True
                )
                return

            char.money -= price
            property_.owner_kind = OwnerKind.CHARACTER.value
            property_.owner_id = char.id
            property_.for_sale = False
            property_.asking_price = None
            if property_.kind == PropertyKind.HOUSE.value:
                char.housing_property_id = property_.id
            name, kind, total = char.name, property_.kind, price
        await interaction.response.send_message(
            t("housing_bought_ok", name=name, kind=kind, property_id=property_id, price=total),
            ephemeral=True,
        )

    @group.command(name="buy-complex", description="Buy out every unit in an apartment complex")
    @app_commands.describe(
        character="Character name", complex_id="Complex ID shown in /housing list"
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def buy_complex(
        self, interaction: discord.Interaction, character: str, complex_id: str
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            units = (
                (await session.execute(select(Property).where(Property.complex_id == complex_id)))
                .scalars()
                .all()
            )
            try:
                housing_svc.check_can_buy_complex(character=char, units=list(units))
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

            price = round(housing_svc.complex_purchase_price(list(units)))
            if char.money < price:
                await interaction.response.send_message(
                    t("housing_insufficient_funds", name=char.name), ephemeral=True
                )
                return

            char.money -= price
            for unit in units:
                unit.owner_kind = OwnerKind.CHARACTER.value
                unit.owner_id = char.id
            name, unit_count, total = char.name, len(units), price
        await interaction.response.send_message(
            t(
                "housing_complex_bought_ok",
                name=name,
                complex_id=complex_id,
                units=unit_count,
                price=total,
            ),
            ephemeral=True,
        )

    @group.command(name="rent", description="Sign a lease on a vacant apartment unit")
    @app_commands.describe(character="Character name", property_id="Property ID from /housing list")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def rent(
        self, interaction: discord.Interaction, character: str, property_id: int
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            property_ = await session.get(Property, property_id)
            if property_ is None:
                await interaction.response.send_message(t("housing_not_found"), ephemeral=True)
                return
            existing_lease = (
                await session.execute(
                    select(ApartmentLease).where(ApartmentLease.property_id == property_id)
                )
            ).scalar_one_or_none()
            try:
                housing_svc.check_can_rent(
                    character=char, property_=property_, existing_lease=existing_lease
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

            landlord = (
                await session.get(Character, property_.owner_id)
                if property_.owner_kind == OwnerKind.CHARACTER.value
                else None
            )
            rent_price = round(housing_svc.quoted_price(property_, char, seller=landlord))
            current_tick = await self._current_tick(session)
            session.add(
                ApartmentLease(
                    property_id=property_.id,
                    tenant_character_id=char.id,
                    rent_price=rent_price,
                    started_tick=current_tick,
                    next_rent_due_tick=current_tick + constants.TICKS_PER_DAY,
                )
            )
            char.housing_property_id = property_.id
            name = char.name
        await interaction.response.send_message(
            t("housing_rented_ok", name=name, property_id=property_id, price=rent_price),
            ephemeral=True,
        )

    @group.command(name="move-out", description="End your current apartment lease")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def move_out(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            if char.housing_property_id is None:
                await interaction.response.send_message(
                    t("housing_no_home", name=char.name), ephemeral=True
                )
                return
            lease = (
                await session.execute(
                    select(ApartmentLease).where(ApartmentLease.tenant_character_id == char.id)
                )
            ).scalar_one_or_none()
            if lease is None:
                await interaction.response.send_message(
                    t("housing_not_a_tenant", name=char.name), ephemeral=True
                )
                return
            await session.delete(lease)
            char.housing_property_id = None
            name = char.name
        await interaction.response.send_message(
            t("housing_moved_out_ok", name=name), ephemeral=True
        )

    @group.command(name="status", description="See your housing situation and fatigue")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def status(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            home_text = "no fixed home"
            if char.housing_property_id is not None:
                home_property = await session.get(Property, char.housing_property_id)
                if home_property is not None:
                    home_text = (
                        f"{home_property.kind.title()} `#{home_property.id}` "
                        f"in District {home_property.district_id}"
                    )
            name, fatigue = char.name, round(char.fatigue)
        await interaction.response.send_message(
            t("housing_status", name=name, home=home_text, fatigue=fatigue), ephemeral=True
        )

    @group.command(name="inn-stay", description="Pay for a night (and a meal) at an inn")
    @app_commands.describe(character="Character name", property_id="Inn's property ID")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def inn_stay(
        self, interaction: discord.Interaction, character: str, property_id: int
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            inn = await session.get(Property, property_id)
            if inn is None or inn.kind != PropertyKind.INN.value:
                await interaction.response.send_message(t("housing_not_an_inn"), ephemeral=True)
                return

            owner = (
                await session.get(Character, inn.owner_id)
                if inn.owner_kind == OwnerKind.CHARACTER.value
                else None
            )
            price = round(housing_svc.quoted_price(inn, char, seller=owner))
            if char.money < price:
                await interaction.response.send_message(
                    t("housing_insufficient_funds", name=char.name), ephemeral=True
                )
                return

            char.money -= price
            if owner is not None:
                owner.money += price
            housing_svc.apply_fatigue_restoration(char, simtime.TICKS_PER_PHASE, has_bed=True)
            char.hunger = max(constants.HUNGER_MIN, char.hunger - constants.HUNGER_DECREASE_MET)
            name, total = char.name, price
        await interaction.response.send_message(
            t("housing_inn_stay_ok", name=name, price=total), ephemeral=True
        )

    # --------------------------------------------------------------- /sleep

    @app_commands.command(name="sleep", description="Sleep to restore fatigue (night phase only)")
    @app_commands.describe(
        character="Character name",
        ticks="How many ticks to sleep (default: the rest of the night)",
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def sleep(
        self, interaction: discord.Interaction, character: str, ticks: int | None = None
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            current_tick = await self._current_tick(session)
            _tick, phase, _day, _month = simtime.current(current_tick)
            try:
                housing_svc.check_can_sleep(phase)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, name=char.name, **exc.fmt), ephemeral=True
                )
                return

            max_ticks = simtime.ticks_remaining_in_phase(current_tick)
            sleep_ticks = max(1, min(ticks, max_ticks)) if ticks is not None else max_ticks
            restored = housing_svc.apply_fatigue_restoration(
                char, sleep_ticks, has_bed=housing_svc.has_a_bed(char)
            )
            name, fatigue = char.name, round(char.fatigue)
        await interaction.response.send_message(
            t("sleep_ok", name=name, ticks=sleep_ticks, restored=round(restored), fatigue=fatigue),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HousingCog(bot))
