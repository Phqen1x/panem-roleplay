"""Daily housing-market maintenance: price refresh, mortgage/rent/
maintenance collection, foreclosure, and auction resolution.

Everything here runs once per sim-day. Player-initiated actions (buying,
renting, refinancing, starting an auction, bidding) live in
`panem_bot.services.housing`/`panem_bot.cogs.housing` instead -- this
module is only what happens to a property/lease/auction with the mere
passage of time.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared.db.models import Property, PropertyAuction
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
    # A player's own listing (`owner_kind == "character"`) is never
    # touched here -- their (or staff's) price override always sticks
    # until they change it themselves.
    for property_ in state.properties.values():
        if property_.owner_kind != OwnerKind.NPC.value:
            continue
        modifier = _district_modifier(state, property_.district_id)
        property_.suggested_price = _base_price(property_.kind, property_.tier) * modifier


def _foreclose(state: WorldState, ctx: TickContext, property_: Property) -> None:
    """Repossesses `property_` (mortgage or, for an inn, maintenance
    arrears -- same fields, same loop) and auto-lists it for auction
    rather than silently reverting it to NPC stock, so the bank recovers
    at least the outstanding balance if a bidder steps up."""
    previous_owner_id = property_.owner_id
    remaining = max(property_.mortgage_principal, 1.0)

    property_.owner_kind = OwnerKind.NPC.value
    property_.owner_id = None
    property_.for_sale = True
    property_.asking_price = None
    property_.mortgage_principal = 0.0
    property_.mortgage_payment = 0.0
    property_.mortgage_next_due_tick = None
    property_.mortgage_missed_payments = 0

    if previous_owner_id is not None:
        owner = state.characters.get(previous_owner_id)
        if owner is not None and owner.housing_property_id == property_.id:
            owner.housing_property_id = None

    state.new_property_auctions.append(
        PropertyAuction(
            property_id=property_.id,
            seller_kind="bank",
            seller_id=None,
            minimum_bid=remaining,
            ends_at_tick=ctx.tick + constants.AUCTION_DURATION_TICKS_DEFAULT,
            status="open",
        )
    )


def _collect_payments(state: WorldState, ctx: TickContext) -> None:
    """A `Property` with a due mortgage installment or (an inn) daily
    maintenance charge: deduct and advance the due date if the owner can
    afford it, otherwise count a miss and foreclose past
    `MORTGAGE_MISSES_TO_FORECLOSE`."""
    for property_ in state.properties.values():
        if property_.mortgage_next_due_tick is None or property_.mortgage_next_due_tick > ctx.tick:
            continue
        owner = (
            state.characters.get(property_.owner_id)
            if property_.owner_kind == OwnerKind.CHARACTER.value and property_.owner_id is not None
            else None
        )
        if owner is not None and owner.money >= round(property_.mortgage_payment):
            owner.money -= round(property_.mortgage_payment)
            property_.mortgage_missed_payments = 0
            if property_.kind == PropertyKind.INN.value:
                property_.mortgage_next_due_tick = (
                    ctx.tick + constants.MORTGAGE_PAYMENT_INTERVAL_TICKS
                )
            else:
                property_.mortgage_principal = max(
                    0.0, property_.mortgage_principal - property_.mortgage_payment
                )
                if property_.mortgage_principal <= 0.0:
                    property_.mortgage_payment = 0.0
                    property_.mortgage_next_due_tick = None
                else:
                    property_.mortgage_next_due_tick = (
                        ctx.tick + constants.MORTGAGE_PAYMENT_INTERVAL_TICKS
                    )
            continue

        property_.mortgage_missed_payments += 1
        property_.mortgage_next_due_tick = ctx.tick + constants.MORTGAGE_PAYMENT_INTERVAL_TICKS
        if property_.mortgage_missed_payments >= constants.MORTGAGE_MISSES_TO_FORECLOSE:
            _foreclose(state, ctx, property_)


def _collect_rent(state: WorldState, ctx: TickContext) -> None:
    for lease in list(state.apartment_leases.values()):
        if lease.next_rent_due_tick > ctx.tick:
            continue
        tenant = state.characters.get(lease.tenant_character_id)
        property_ = state.properties.get(lease.property_id)
        landlord = (
            state.characters.get(property_.owner_id)
            if property_ is not None
            and property_.owner_kind == OwnerKind.CHARACTER.value
            and property_.owner_id is not None
            else None
        )

        if tenant is not None and tenant.money >= round(lease.rent_price):
            tenant.money -= round(lease.rent_price)
            if landlord is not None:
                landlord.money += round(lease.rent_price)
            lease.missed_payments = 0
            lease.next_rent_due_tick = ctx.tick + constants.TICKS_PER_DAY
            continue

        lease.missed_payments += 1
        lease.next_rent_due_tick = ctx.tick + constants.TICKS_PER_DAY
        if lease.missed_payments >= constants.RENT_MISSES_TO_EVICT:
            if tenant is not None and tenant.housing_property_id == lease.property_id:
                tenant.housing_property_id = None
            del state.apartment_leases[lease.id]
            state.deleted_apartment_lease_ids.append(lease.id)


def _resolve_auctions(state: WorldState, ctx: TickContext) -> None:
    for auction in state.property_auctions.values():
        if auction.status != "open" or auction.ends_at_tick > ctx.tick:
            continue
        property_ = state.properties.get(auction.property_id)
        if property_ is None:
            auction.status = "closed"
            continue

        bidder = (
            state.characters.get(auction.current_bidder_id)
            if auction.current_bidder_id is not None
            else None
        )
        if (
            bidder is not None
            and auction.current_bid is not None
            and bidder.money >= round(auction.current_bid)
        ):
            bidder.money -= round(auction.current_bid)
            if auction.seller_kind == OwnerKind.CHARACTER.value and auction.seller_id is not None:
                seller = state.characters.get(auction.seller_id)
                if seller is not None:
                    seller.money += round(auction.current_bid)
            property_.owner_kind = OwnerKind.CHARACTER.value
            property_.owner_id = bidder.id
            property_.for_sale = False
            property_.asking_price = None
            property_.mortgage_principal = 0.0
            property_.mortgage_payment = 0.0
            property_.mortgage_next_due_tick = None
            property_.mortgage_missed_payments = 0
            auction.status = "closed"
            continue

        # No bid, or the winning bidder can no longer afford it -- relist
        # rather than leaving the property in limbo.
        property_.owner_kind = OwnerKind.NPC.value
        property_.owner_id = None
        property_.for_sale = True
        property_.asking_price = auction.minimum_bid
        auction.status = "closed"


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    if ctx.tick % constants.TICKS_PER_DAY != 0:
        return []
    _refresh_prices(state)
    _collect_payments(state, ctx)
    _collect_rent(state, ctx)
    _resolve_auctions(state, ctx)
    return []
