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
from panem_bot.services import jail as jail_svc
from panem_shared import constants
from panem_shared.content.schemas import District, Good, Location
from panem_shared.db.models import Character, DistrictState, Inventory, MarketOrder, MarketPrice
from panem_shared.enums import CharacterStatus, LocationKind, OwnerKind

ILLICIT_PRESSURE_DELTA = 0.05
"""Placeholder bump to `DistrictState.peacekeeper_pressure` per illicit-
market catch -- `panem_sim.systems.crisis` decays it back toward
baseline over `CRISIS_RECOVERY_DAYS`. Spec §7's real weighting wasn't
available in this session's context."""


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
        raise NotAllowed("market_not_at_market", name=character.name)
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


async def _reserve_stock(session: AsyncSession, district_id: int, good: Good, qty: int) -> None:
    """`MarketPrice.supply` doubles as today's remaining purchasable stock
    (`panem_sim.systems.economy`'s module docstring), so a buy has to
    check and consume it -- "a finite amount of goods each day" means a
    district that's already sold out of something today can't sell more
    of it, however much money a buyer has. No row yet (a fresh world, or
    a good `panem_sim.systems.economy` hasn't priced today) means nothing
    has been allocated to check against yet, so this is a no-op rather
    than manufacturing a stock figure of its own."""
    row = await session.get(MarketPrice, (district_id, good.id))
    if row is None:
        return
    if row.supply < qty:
        raise NotAllowed("market_insufficient_stock", good=good.name)
    row.supply -= qty


async def _return_stock(session: AsyncSession, district_id: int, good: Good, qty: int) -> None:
    """The other half of `_reserve_stock` -- a sale puts units back into
    local circulation for someone else to buy the same day."""
    row = await session.get(MarketPrice, (district_id, good.id))
    if row is not None:
        row.supply += qty


async def _adjust_inventory(
    session: AsyncSession, character: Character, good_id: str, delta: int
) -> int:
    """Applies `delta` to a character's `good_id` stock, creating the row
    if needed. Raises `NotAllowed` if a negative delta would go below
    zero (selling more than owned). Returns the new quantity."""
    owner_id = str(character.id)
    row = await session.get(Inventory, (OwnerKind.CHARACTER.value, owner_id, good_id))
    current = row.qty if row is not None else 0
    new_qty = current + delta
    if new_qty < 0:
        raise NotAllowed("market_insufficient_inventory", name=character.name)
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


async def _apply_illicit_consequence(
    session: AsyncSession, character: Character, district_id: int
) -> None:
    character.money = max(0, character.money - constants.MARKET_ILLICIT_FINE)
    jail_svc.commit_to_jail(character, constants.MARKET_ILLICIT_JAIL_TICKS)
    character.reputation -= constants.REP_ILLICIT_CAUGHT_PENALTY

    district_row = await session.get(DistrictState, district_id)
    if district_row is not None:
        district_row.peacekeeper_pressure = min(
            1.0, district_row.peacekeeper_pressure + ILLICIT_PRESSURE_DELTA
        )


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
        raise NotAllowed("market_insufficient_funds", name=character.name)
    await _reserve_stock(session, district.id, good, qty)

    character.money -= total
    await _adjust_inventory(session, character, good_id, qty)
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
        await _apply_illicit_consequence(session, character, district.id)
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

    await _adjust_inventory(session, character, good_id, -qty)
    await _return_stock(session, district.id, good, qty)
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
        await _apply_illicit_consequence(session, character, district.id)
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
