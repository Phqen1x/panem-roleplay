"""The black market: a district's illicit goods (contraband system),
gated behind good relations with that district's fence NPC. Mirrors
`panem_shared.market`'s buy/sell shape closely -- same finite
`MarketPrice.supply` stock, same illicit-detection/consequence pattern
-- but trades only `District.illicit_produces` goods, fed purely by
local illicit work (`panem_sim.systems.economy`'s module docstring
point 8), never the national legal market.

Re-exported from `panem_bot.services.blackmarket` for every existing
call site -- moved here (same reasoning as every other `panem_shared`
move this session) because the web dashboard's Market tab needs this
too and `panem_api` cannot import `panem_bot`. Nothing here has ever
depended on discord.py; this was a package-boundary accident, not a
real coupling.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.content.schemas import District, Good, Location, NpcContent
from panem_shared.db.models import (
    Character,
    DistrictState,
    Inventory,
    MarketOrder,
    MarketPrice,
    RelationshipRow,
)
from panem_shared.enums import CharacterStatus, LocationKind, OwnerKind, Stance
from panem_shared.errors import NotAllowed, NotFound
from panem_shared.jail import commit_to_jail, crackdown_bad_odds
from panem_shared.relationships import relationship_key

BLACKMARKET_PRESSURE_DELTA = 0.05
"""Mirrors `panem_shared.market.ILLICIT_PRESSURE_DELTA` exactly -- same
placeholder-weighting caveat (Spec §7's real number wasn't available in
this session's context)."""

TRUSTED_STANCES = {Stance.LIKES.value, Stance.LOVES.value}
"""The `RelationshipRow.stance` values that count as "good relations"
with a fence -- gates `/blackmarket` access."""


@dataclass(frozen=True, slots=True)
class BlackMarketTradeResult:
    qty: int
    unit_price: float
    total: float
    caught: bool


def resolve_fence(district_id: int, npcs: dict[str, NpcContent]) -> NpcContent:
    fence = next(
        (npc for npc in npcs.values() if npc.district == district_id and npc.black_market_contact),
        None,
    )
    if fence is None:
        raise NotFound("blackmarket_no_fence")
    return fence


def resolve_black_market_location(character: Character, district: District) -> Location:
    """A character must be physically at one of their district's illicit
    market locations to trade -- the same physical-presence requirement
    `market.py`'s legal trading has, narrowed to only the illicit ones."""
    location = next(
        (loc for loc in district.locations if loc.id == character.location_id),
        None,
    )
    if location is None or location.kind != LocationKind.MARKET or not location.illicit:
        raise NotAllowed("blackmarket_not_at_market", name=character.name)
    return location


async def check_can_trade(session: AsyncSession, character: Character, fence: NpcContent) -> None:
    """Raises `NotAllowed` unless `character` is approved and has good
    relations (`TRUSTED_STANCES`) with `fence` specifically -- a stranger
    to the fence, or someone they merely tolerate, gets nothing."""
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")
    key = relationship_key(
        (OwnerKind.CHARACTER.value, str(character.id)), (OwnerKind.NPC.value, fence.id)
    )
    relationship = await session.get(RelationshipRow, key)
    stance = relationship.stance if relationship is not None else Stance.STRANGER.value
    if stance not in TRUSTED_STANCES:
        raise NotAllowed("blackmarket_not_trusted", name=character.name, fence=fence.name)


def resolve_good(district: District, goods: dict[str, Good], good_id: str) -> Good:
    if good_id not in district.illicit_produces:
        raise NotFound("blackmarket_good_not_traded")
    good = goods.get(good_id)
    if good is None:
        raise NotFound("blackmarket_good_not_traded")
    return good


async def get_price(session: AsyncSession, district_id: int, good: Good) -> float:
    row = await session.get(MarketPrice, (district_id, good.id))
    return row.price if row is not None else good.base_price


async def _reserve_stock(session: AsyncSession, district_id: int, good: Good, qty: int) -> None:
    """Unlike the legal market's `_reserve_stock`, a missing row means
    zero stock, not "not priced yet" -- illicit supply only ever exists
    because `panem_sim.systems.economy` wrote it after a day someone
    actually worked the job (module docstring point 8), so no row really
    does mean nothing to sell."""
    row = await session.get(MarketPrice, (district_id, good.id))
    if row is None or row.supply < qty:
        raise NotAllowed("blackmarket_insufficient_stock", good=good.name)
    row.supply -= qty


async def _return_stock(session: AsyncSession, district_id: int, good: Good, qty: int) -> None:
    row = await session.get(MarketPrice, (district_id, good.id))
    if row is not None:
        row.supply += qty


async def _adjust_inventory(
    session: AsyncSession, character: Character, good_id: str, delta: int
) -> int:
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


def _roll_detection(
    district_row: DistrictState | None, current_tick: int, rng: random.Random
) -> bool:
    """Every black-market location is illicit by definition -- unlike
    `market.py`'s `_roll_illicit_detection`, there's no legal-location
    case to gate on, so this is just the bare probability roll (scaled
    up under an active crackdown)."""
    prob = crackdown_bad_odds(constants.MARKET_ILLICIT_DETECTION_PROB, district_row, current_tick)
    return rng.random() < prob


def _apply_caught_consequence(character: Character, district_row: DistrictState | None) -> None:
    character.money = max(0, character.money - constants.MARKET_ILLICIT_FINE)
    commit_to_jail(character, constants.MARKET_ILLICIT_JAIL_TICKS)
    character.reputation -= constants.REP_ILLICIT_CAUGHT_PENALTY

    if district_row is not None:
        district_row.peacekeeper_pressure = min(
            1.0, district_row.peacekeeper_pressure + BLACKMARKET_PRESSURE_DELTA
        )


async def buy(
    session: AsyncSession,
    *,
    character: Character,
    district: District,
    goods: dict[str, Good],
    npcs: dict[str, NpcContent],
    good_id: str,
    qty: int,
    tick: int,
    rng: random.Random,
) -> BlackMarketTradeResult:
    resolve_black_market_location(character, district)
    fence = resolve_fence(district.id, npcs)
    await check_can_trade(session, character, fence)
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

    district_row = await session.get(DistrictState, district.id)
    caught = _roll_detection(district_row, tick, rng)
    if caught:
        _apply_caught_consequence(character, district_row)
    return BlackMarketTradeResult(qty=qty, unit_price=price, total=total, caught=caught)


async def sell(
    session: AsyncSession,
    *,
    character: Character,
    district: District,
    goods: dict[str, Good],
    npcs: dict[str, NpcContent],
    good_id: str,
    qty: int,
    tick: int,
    rng: random.Random,
) -> BlackMarketTradeResult:
    resolve_black_market_location(character, district)
    fence = resolve_fence(district.id, npcs)
    await check_can_trade(session, character, fence)
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

    district_row = await session.get(DistrictState, district.id)
    caught = _roll_detection(district_row, tick, rng)
    if caught:
        _apply_caught_consequence(character, district_row)
    return BlackMarketTradeResult(qty=qty, unit_price=price, total=total, caught=caught)
