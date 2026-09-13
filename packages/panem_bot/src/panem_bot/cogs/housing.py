"""`/housing` (buying houses, apartment units/complexes, inn stays,
renting, moving out, financed purchases, refinancing, listing/auctioning
a property you own) and `/sleep` (the fatigue/rest mechanic housing ties
into). Staff price overrides live in `panem_bot.cogs.staff` instead, next
to the rest of the staff toolkit.
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
from panem_shared.db.models import ApartmentLease, Character, Property, PropertyAuction, WorldClock
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
    @app_commands.describe(
        character="Character name",
        property_id="Property ID from /housing list",
        financed="Houses only: pay a down payment and finance the rest instead of cash in full",
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def buy(
        self,
        interaction: discord.Interaction,
        character: str,
        property_id: int,
        financed: bool = False,
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
            try:
                housing_svc.check_can_buy_property(character=char, property_=property_)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

            price = round(housing_svc.quoted_price(property_, char))
            current_tick = await self._current_tick(session)
            # Financing only makes sense for a house (a real mortgage) --
            # an inn's `financed` request is treated as a plain cash buy,
            # since its `mortgage_*` fields are already spoken for by its
            # own daily maintenance charge, set further down.
            is_financed = financed and property_.kind == PropertyKind.HOUSE.value

            if is_financed:
                terms = housing_svc.financed_purchase_terms(price)
                down_payment = round(terms.down_payment)
                if char.money < down_payment:
                    await interaction.response.send_message(
                        t("housing_down_payment_too_much", name=char.name), ephemeral=True
                    )
                    return
                char.money -= down_payment
                property_.mortgage_principal = terms.principal
                property_.mortgage_payment = terms.payment
                property_.mortgage_next_due_tick = (
                    current_tick + constants.MORTGAGE_PAYMENT_INTERVAL_TICKS
                )
                property_.mortgage_missed_payments = 0
            else:
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
            elif property_.kind == PropertyKind.INN.value:
                property_.mortgage_payment = constants.INN_DAILY_MAINTENANCE_COST
                property_.mortgage_next_due_tick = (
                    current_tick + constants.MORTGAGE_PAYMENT_INTERVAL_TICKS
                )
                property_.mortgage_missed_payments = 0

            name, kind = char.name, property_.kind
            payment = round(property_.mortgage_payment) if is_financed else None
        if is_financed:
            await interaction.response.send_message(
                t(
                    "housing_financed_ok",
                    name=name,
                    kind=kind,
                    property_id=property_id,
                    down_payment=down_payment,
                    payment=payment,
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                t("housing_bought_ok", name=name, kind=kind, property_id=property_id, price=price),
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

    @group.command(name="refinance", description="Borrow cash against a property you own")
    @app_commands.describe(
        character="Character name",
        property_id="Property ID you own",
        amount="How much to borrow",
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def refinance(
        self, interaction: discord.Interaction, character: str, property_id: int, amount: float
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
            try:
                housing_svc.check_can_refinance(character=char, property_=property_, amount=amount)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

            current_tick = await self._current_tick(session)
            payment = round(housing_svc.apply_refinance(property_, amount, tick=current_tick))
            char.money += round(amount)
            name, total = char.name, round(amount)
        await interaction.response.send_message(
            t(
                "housing_refinanced_ok",
                name=name,
                property_id=property_id,
                amount=total,
                payment=payment,
            ),
            ephemeral=True,
        )

    @group.command(name="sell", description="List (or delist) a property you own")
    @app_commands.describe(
        character="Character name",
        property_id="Property ID you own",
        price="Asking price -- omit to take it off the market",
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def sell(
        self,
        interaction: discord.Interaction,
        character: str,
        property_id: int,
        price: float | None = None,
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
            try:
                housing_svc.check_owns_property(character=char, property_=property_)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

            if price is None:
                property_.for_sale = False
                property_.asking_price = None
            else:
                property_.for_sale = True
                property_.asking_price = price
            name = char.name
        if price is None:
            await interaction.response.send_message(
                t("housing_delisted_ok", name=name, property_id=property_id), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                t("housing_sold_ok", name=name, property_id=property_id, price=round(price)),
                ephemeral=True,
            )

    @group.command(name="rent-out", description="Reprice a vacant unit you're the landlord of")
    @app_commands.describe(
        character="Character name", property_id="Apartment unit ID", price="New daily rent"
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def rent_out(
        self, interaction: discord.Interaction, character: str, property_id: int, price: float
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            property_ = await session.get(Property, property_id)
            if property_ is None or property_.kind != PropertyKind.APARTMENT.value:
                await interaction.response.send_message(
                    t("housing_not_an_apartment"), ephemeral=True
                )
                return
            try:
                housing_svc.check_owns_property(character=char, property_=property_)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            existing_lease = (
                await session.execute(
                    select(ApartmentLease).where(ApartmentLease.property_id == property_id)
                )
            ).scalar_one_or_none()
            if existing_lease is not None:
                await interaction.response.send_message(
                    t("housing_unit_already_leased"), ephemeral=True
                )
                return

            property_.asking_price = price
            name = char.name
        await interaction.response.send_message(
            t("housing_rent_out_ok", name=name, property_id=property_id, price=round(price)),
            ephemeral=True,
        )

    @group.command(name="auction-start", description="Put a property you own up for auction")
    @app_commands.describe(
        character="Character name", property_id="Property ID you own", minimum_bid="Starting bid"
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def auction_start(
        self,
        interaction: discord.Interaction,
        character: str,
        property_id: int,
        minimum_bid: float,
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
            existing_auction = (
                await session.execute(
                    select(PropertyAuction).where(
                        PropertyAuction.property_id == property_id,
                        PropertyAuction.status == "open",
                    )
                )
            ).scalar_one_or_none()
            try:
                housing_svc.check_can_start_auction(
                    character=char, property_=property_, existing_auction=existing_auction
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

            current_tick = await self._current_tick(session)
            session.add(
                PropertyAuction(
                    property_id=property_.id,
                    seller_kind=OwnerKind.CHARACTER.value,
                    seller_id=char.id,
                    minimum_bid=minimum_bid,
                    ends_at_tick=current_tick + constants.AUCTION_DURATION_TICKS_DEFAULT,
                    status="open",
                )
            )
            property_.for_sale = False
            name = char.name
        await interaction.response.send_message(
            t(
                "housing_auction_started_ok",
                name=name,
                property_id=property_id,
                minimum=round(minimum_bid),
            ),
            ephemeral=True,
        )

    @group.command(name="auction-bid", description="Bid on an open property auction")
    @app_commands.describe(
        character="Character name",
        property_id="Property ID with an open auction",
        amount="Your bid",
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def auction_bid(
        self, interaction: discord.Interaction, character: str, property_id: int, amount: float
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            auction = (
                await session.execute(
                    select(PropertyAuction).where(
                        PropertyAuction.property_id == property_id,
                        PropertyAuction.status == "open",
                    )
                )
            ).scalar_one_or_none()
            if auction is None:
                await interaction.response.send_message(
                    t("housing_auction_not_found"), ephemeral=True
                )
                return
            try:
                housing_svc.check_can_bid(character=char, auction=auction, amount=amount)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return

            auction.current_bid = amount
            auction.current_bidder_id = char.id
            name = char.name
        await interaction.response.send_message(
            t("housing_bid_ok", name=name, property_id=property_id, amount=round(amount)),
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
