"""Buying/selling at a district's market (Spec FR-ECO-3/4).

`panem_sim.systems.economy` owns the daily supply/demand price update;
this only reads/writes the same `market_prices`/`inventories` rows from
the bot's side of a live trade, and creates a price row on the fly (at
that good's `base_price`) if a player gets there before the sim's first
tick has -- a fresh world otherwise has no price at all to trade at.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot.errors import NotAllowed, NotFound
from panem_shared import constants
from panem_shared.content.schemas import District, Good, Location
from panem_shared.db.models import Character, Inventory, MarketOrder, MarketPrice
from panem_shared.enums import CharacterStatus, LocationKind, OwnerKind


@dataclass(frozen=True, slots=True)
class TradeResult:
    qty: int
    unit_price: float
    total: float
    caught: bool
    """Whether an illicit-location trade tripped detection (FR-ECO-4)."""


def resolve_market_location(character: Character, district: District) -> Location:
    """FR-ECO-3: a character must be physically at one of their district's
    market-kind locations to trade -- `/market` doesn't work district-wide
    from anywhere the way price-checking alone reasonably could, since
    `Location.illicit` (and so *which* market you're caught at) only means
    something if presence at a specific location is required."""
    location = next(
        (loc for loc in district.locations if loc.id == character.location_id),
        None,
    )
    if location is None or location.kind != LocationKind.MARKET:
        raise NotAllowed("market_not_at_market")
    return location


def check_can_trade(character: Character) -> None:
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")


def resolve_good(district: District, goods: dict[str, Good], good_id: str) -> Good:
    if good_id not in set(district.produces) | set(district.imports):
        raise NotFound("market_good_not_traded")
    good = goods.get(good_id)
    if good is None:
        raise NotFound("market_good_not_traded")
    return good


async def get_price(session: AsyncSession, district_id: int, good: Good) -> float:
    row = await session.get(MarketPrice, (district_id, good.id))
    return row.price if row is not None else good.base_price


async def _adjust_inventory(
    session: AsyncSession, character_id: int, good_id: str, delta: int
) -> int:
    """Applies `delta` to a character's `good_id` stock, creating the row
    if needed. Raises `NotAllowed` if a negative delta would go below
    zero (selling more than owned). Returns the new quantity."""
    owner_id = str(character_id)
    row = await session.get(Inventory, (OwnerKind.CHARACTER.value, owner_id, good_id))
    current = row.qty if row is not None else 0
    new_qty = current + delta
    if new_qty < 0:
        raise NotAllowed("market_insufficient_inventory")
    if row is None:
        row = Inventory(
            owner_kind=OwnerKind.CHARACTER.value, owner_id=owner_id, good_id=good_id, qty=new_qty
        )
        session.add(row)
    else:
        row.qty = new_qty
    return new_qty


def _roll_illicit_detection(location: Location, rng: random.Random) -> bool:
    return location.illicit and rng.random() < constants.MARKET_ILLICIT_DETECTION_PROB


def _apply_illicit_consequence(character: Character) -> None:
    character.money = max(0, character.money - constants.MARKET_ILLICIT_FINE)
    base_tick = character.jailed_until_tick or 0
    character.jailed_until_tick = base_tick + constants.MARKET_ILLICIT_JAIL_TICKS


async def buy(
    session: AsyncSession,
    *,
    character: Character,
    district: District,
    goods: dict[str, Good],
    good_id: str,
    qty: int,
    tick: int,
    rng: random.Random,
) -> TradeResult:
    check_can_trade(character)
    location = resolve_market_location(character, district)
    good = resolve_good(district, goods, good_id)
    price = await get_price(session, district.id, good)
    total = round(qty * price)
    if character.money < total:
        raise NotAllowed("market_insufficient_funds")

    character.money -= total
    await _adjust_inventory(session, character.id, good_id, qty)
    session.add(
        MarketOrder(
            district_id=district.id,
            good_id=good_id,
            owner_kind=OwnerKind.CHARACTER.value,
            owner_id=str(character.id),
            side="buy",
            qty=qty,
            price=price,
            tick=tick,
        )
    )

    caught = _roll_illicit_detection(location, rng)
    if caught:
        _apply_illicit_consequence(character)
    return TradeResult(qty=qty, unit_price=price, total=total, caught=caught)


async def sell(
    session: AsyncSession,
    *,
    character: Character,
    district: District,
    goods: dict[str, Good],
    good_id: str,
    qty: int,
    tick: int,
    rng: random.Random,
) -> TradeResult:
    check_can_trade(character)
    location = resolve_market_location(character, district)
    good = resolve_good(district, goods, good_id)
    price = await get_price(session, district.id, good) * constants.SELL_DISCOUNT
    total = round(qty * price)

    await _adjust_inventory(session, character.id, good_id, -qty)
    character.money += total
    session.add(
        MarketOrder(
            district_id=district.id,
            good_id=good_id,
            owner_kind=OwnerKind.CHARACTER.value,
            owner_id=str(character.id),
            side="sell",
            qty=qty,
            price=price,
            tick=tick,
        )
    )

    caught = _roll_illicit_detection(location, rng)
    if caught:
        _apply_illicit_consequence(character)
    return TradeResult(qty=qty, unit_price=price, total=total, caught=caught)


async def list_inventory(session: AsyncSession, character_id: int) -> list[Inventory]:
    rows = (
        await session.execute(
            select(Inventory).where(
                Inventory.owner_kind == OwnerKind.CHARACTER.value,
                Inventory.owner_id == str(character_id),
                Inventory.qty > 0,
            )
        )
    ).scalars()
    return list(rows)
