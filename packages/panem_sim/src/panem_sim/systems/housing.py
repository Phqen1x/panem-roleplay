"""Daily housing-market maintenance.

This milestone only refreshes each NPC-listed `Property.suggested_price`
from its district's economic health ("the sim should suggest prices that
balance with the economy") -- a player's own listing (`owner_kind ==
"character"`) is never touched here, so a seller's or staff's price
override always sticks until they change it themselves. Mortgage/rent/
maintenance collection, foreclosure, and auction resolution are a later
addition to this same module.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared.enums import OwnerKind, PropertyKind
from panem_shared.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState


def _base_price(property_kind: str, tier: str) -> float:
    if property_kind == PropertyKind.HOUSE.value:
        return constants.HOUSE_BASE_PRICE_BY_TIER[tier]
    if property_kind == PropertyKind.APARTMENT.value:
        return constants.APARTMENT_UNIT_BASE_RENT
    return constants.INN_BASE_NIGHTLY_PRICE


def _district_modifier(state: WorldState, district_id: int) -> float:
    district_row = state.districts.get(district_id)
    if district_row is None:
        return 1.0
    raw = 1.0 + (
        constants.HOUSING_DISTRICT_FAVOR_PRICE_WEIGHT * district_row.capitol_favor
        - constants.HOUSING_DISTRICT_UNREST_PRICE_WEIGHT * district_row.unrest
    )
    return max(0.5, raw)


def _refresh_prices(state: WorldState) -> None:
    for property_ in state.properties.values():
        if property_.owner_kind != OwnerKind.NPC.value:
            continue
        modifier = _district_modifier(state, property_.district_id)
        property_.suggested_price = _base_price(property_.kind, property_.tier) * modifier


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    if ctx.tick % constants.TICKS_PER_DAY != 0:
        return []
    _refresh_prices(state)
    return []
