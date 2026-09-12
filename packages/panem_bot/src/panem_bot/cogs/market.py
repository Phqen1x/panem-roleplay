"""`/market prices|buy|sell`, `/inventory` (Spec FR-ECO-3/4)."""

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
from panem_bot.services import market as market_svc
from panem_bot.strings import t
from panem_shared import constants
from panem_shared.db.models import Character, WorldClock


class MarketCog(commands.Cog):
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

    def _traded_goods(self, district_id: int) -> list[str]:
        district = self.bot.content.district(district_id)  # type: ignore[attr-defined]
        return sorted(set(district.produces) | set(district.imports))

    # ---------------------------------------------------------------- /market

    group = app_commands.Group(name="market", description="Buy and sell at a district's market")

    @group.command(name="prices", description="Show current prices in a character's district")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def prices(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            content = self.bot.content  # type: ignore[attr-defined]
            good_ids = self._traded_goods(char.current_district_id)
            if not good_ids:
                await interaction.response.send_message(t("market_no_goods_traded"), ephemeral=True)
                return

            lines = []
            for good_id in good_ids:
                good = content.goods.get(good_id)
                if good is None:
                    continue
                price = await market_svc.get_price(session, char.current_district_id, good)
                lines.append(f"**{good.name}** (`{good.id}`) -- {price:.2f}")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @group.command(name="buy", description="Buy a good at the market")
    @app_commands.describe(
        character="Character name", good="Good id (see /market prices)", qty="Quantity"
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def buy(
        self, interaction: discord.Interaction, character: str, good: str, qty: int
    ) -> None:
        await self._trade(interaction, character, good, qty, side="buy")

    @group.command(name="sell", description="Sell a good at the market")
    @app_commands.describe(
        character="Character name", good="Good id (see /inventory)", qty="Quantity"
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def sell(
        self, interaction: discord.Interaction, character: str, good: str, qty: int
    ) -> None:
        await self._trade(interaction, character, good, qty, side="sell")

    async def _trade(
        self, interaction: discord.Interaction, character: str, good_id: str, qty: int, *, side: str
    ) -> None:
        if qty <= 0:
            await interaction.response.send_message(t("market_invalid_qty"), ephemeral=True)
            return

        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            content = self.bot.content  # type: ignore[attr-defined]
            district = content.district(char.current_district_id)
            tick = await self._current_tick(session)
            trade = market_svc.buy if side == "buy" else market_svc.sell
            try:
                result = await trade(
                    session,
                    character=char,
                    district=district,
                    goods=content.goods,
                    good_id=good_id,
                    qty=qty,
                    tick=tick,
                    rng=random.Random(),
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            name = char.name
            good_name = content.goods[good_id].name

        key = "market_bought_ok" if side == "buy" else "market_sold_ok"
        text = t(key, name=name, qty=result.qty, good=good_name, total=result.total)
        if result.caught:
            text += t(
                "market_caught_illicit",
                fine=constants.MARKET_ILLICIT_FINE,
                jail_ticks=constants.MARKET_ILLICIT_JAIL_TICKS,
            )
        await interaction.response.send_message(text, ephemeral=True)

    async def _good_choices(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        character_name = getattr(interaction.namespace, "character", None)
        if not character_name:
            return []
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character_name)
            if char is None:
                return []
            good_ids = self._traded_goods(char.current_district_id)
        content = self.bot.content  # type: ignore[attr-defined]
        current_lower = current.lower()
        matches = [
            good_id
            for good_id in good_ids
            if current_lower in good_id.lower()
            or current_lower in content.goods[good_id].name.lower()
        ]
        return [
            app_commands.Choice(name=f"{content.goods[g].name} ({g})", value=g)
            for g in matches[:25]
        ]

    @buy.autocomplete("good")
    async def buy_good_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._good_choices(interaction, current)

    @sell.autocomplete("good")
    async def sell_good_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._good_choices(interaction, current)

    # -------------------------------------------------------------- /inventory

    @app_commands.command(name="inventory", description="Show a character's carried goods")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def inventory(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            content = self.bot.content  # type: ignore[attr-defined]
            rows = await market_svc.list_inventory(session, char.id)
            if not rows:
                await interaction.response.send_message(
                    t("inventory_empty", name=char.name), ephemeral=True
                )
                return

            lines = [
                f"**{content.goods[row.good_id].name}** x{row.qty}"
                for row in rows
                if row.good_id in content.goods
            ]

        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MarketCog(bot))
