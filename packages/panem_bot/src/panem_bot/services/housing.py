"""Housing: buying houses/apartment units (or a whole complex), renting a
unit, sleeping/fatigue, inn stays, financed purchases/refinancing, and
auctions.

Pure logic, no DB session -- mirrors `panem_bot.services.travel`/`market`:
takes ORM/content objects as arguments, raises `NotAllowed`/`NotFound` on
refusal, leaves the actual row mutation and session handling to the cog.
Payment collection, foreclosure, and auction resolution over time are
`panem_sim.systems.housing`'s job, not this module's -- this only covers
the player-initiated actions (buy financed, refinance, list, bid).
"""

from __future__ import annotations

from dataclasses import dataclass

from panem_bot.errors import NotAllowed, NotFound
from panem_shared import constants, job_levels
from panem_shared.db.models import (
    ApartmentLease,
    Character,
    DistrictState,
    Property,
    PropertyAuction,
)
from panem_shared.enums import CharacterStatus, DayPhase, JobLevel, OwnerKind, PropertyKind


def _check_alive_and_approved(character: Character) -> None:
    if character.status == CharacterStatus.DEAD.value:
        raise NotAllowed("character_dead")
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")


def check_can_buy_property(*, character: Character, property_: Property) -> None:
    """A house is gated to a character's own home district and to a job
    level at or above the house's tier (clarified with the user: "at or
    below buyer's level" -- a Journeyman may buy Novice/Apprentice/
    Journeyman-tier housing, not Master/Expert). Apartments and inns
    aren't tier-gated or district-gated -- only houses are, per the
    request's own wording ("purchase houses in your own district")."""
    _check_alive_and_approved(character)
    if not property_.for_sale:
        raise NotAllowed("housing_not_for_sale")
    if property_.owner_kind == OwnerKind.CHARACTER.value:
        raise NotAllowed("housing_already_owned")
    if property_.kind == PropertyKind.HOUSE.value:
        if character.district_id != property_.district_id:
            raise NotAllowed("housing_wrong_district", name=character.name)
        buyer_index = job_levels.level_index(
            job_levels.job_level_for_shifts(character.shifts_completed)
        )
        tier_index = job_levels.level_index(JobLevel(property_.tier))
        if buyer_index < tier_index:
            raise NotAllowed(
                "housing_tier_too_low", name=character.name, tier=property_.tier.title()
            )


def check_can_rent(
    *, character: Character, property_: Property, existing_lease: ApartmentLease | None
) -> None:
    _check_alive_and_approved(character)
    if property_.kind != PropertyKind.APARTMENT.value:
        raise NotAllowed("housing_not_an_apartment")
    if existing_lease is not None:
        raise NotAllowed("housing_unit_already_leased")


def _seller_level_index(property_: Property, buyer_index: int, seller: Character | None) -> int:
    """A real player `seller`'s actual job level; otherwise NPC stock is
    priced at parity -- a house's own tier (so buying a house that
    matches your level costs the sticker price), or the buyer's own level
    for apartments/inns (which have no tier of their own to be at parity
    with)."""
    if seller is not None:
        return job_levels.level_index(job_levels.job_level_for_shifts(seller.shifts_completed))
    if property_.kind == PropertyKind.HOUSE.value:
        return job_levels.level_index(JobLevel(property_.tier))
    return buyer_index


def quoted_price(
    property_: Property,
    buyer: Character,
    *,
    seller: Character | None = None,
    district_state: DistrictState | None = None,
) -> float:
    """The price `buyer` would pay for `property_` (a house purchase, an
    apartment-complex buyout, an apartment unit's rent at lease signing,
    or an inn's nightly stay) right now.

    Starts from `Property.asking_price` (a seller/staff override) or
    `suggested_price` (the sim's daily-refreshed baseline,
    `panem_sim.systems.housing`), then applies:
    - A mastery-differential multiplier for every kind: a buyer above the
      seller's job level pays less, below pays more, by
      `HOUSING_MASTERY_PRICE_STEP` per level of gap.
    - Houses only, on top of that: a reputation multiplier
      (`HOUSING_REPUTATION_PRICE_STEP`) -- "a better price on houses with
      better reputation in your district."
    Both combine and clamp to `[HOUSING_PRICE_MULT_MIN,
    HOUSING_PRICE_MULT_MAX]` so neither factor alone can zero out or
    blow up the price."""
    base = (
        property_.asking_price if property_.asking_price is not None else property_.suggested_price
    )
    buyer_index = job_levels.level_index(job_levels.job_level_for_shifts(buyer.shifts_completed))
    seller_index = _seller_level_index(property_, buyer_index, seller)

    multiplier = 1.0 - (buyer_index - seller_index) * constants.HOUSING_MASTERY_PRICE_STEP
    if property_.kind == PropertyKind.HOUSE.value:
        multiplier *= 1.0 - buyer.reputation * constants.HOUSING_REPUTATION_PRICE_STEP
    multiplier = max(
        constants.HOUSING_PRICE_MULT_MIN, min(constants.HOUSING_PRICE_MULT_MAX, multiplier)
    )
    return base * multiplier


def complex_purchase_price(units: list[Property]) -> float:
    """Buying out an entire apartment complex prices from a flat
    per-unit base valuation (`APARTMENT_UNIT_BASE_PRICE`) -- there's no
    stored sale price per unit (a unit's `suggested_price`/`asking_price`
    is its rent, never a sale price), and since only a fully NPC-owned
    complex can be bought out at all (`check_can_buy_complex`), there's
    no real seller mastery to apply a differential against."""
    return constants.APARTMENT_UNIT_BASE_PRICE * len(units)


def check_can_buy_complex(*, character: Character, units: list[Property]) -> None:
    _check_alive_and_approved(character)
    if not units:
        raise NotFound("housing_complex_not_found")
    if any(unit.owner_kind == OwnerKind.CHARACTER.value for unit in units):
        raise NotAllowed("housing_complex_partially_owned")


def has_a_bed(character: Character) -> bool:
    """A real bed for `/sleep` purposes: an owned house or a leased
    apartment. An inn stay restores fatigue at the same full rate but is
    a one-off transaction, not tracked through `housing_property_id`."""
    return character.housing_property_id is not None


def fatigue_restored(ticks: int, *, has_bed: bool) -> float:
    """`ticks * FATIGUE_RESTORE_PER_TICK`, halved with no real bed and no
    inn stay -- "sleep on the ground... half fatigue restoration compared
    to sleeping in a bed." Callers pass `has_bed=True` for an inn stay
    too (a paid night's lodging is still a real bed for the night)."""
    rate = constants.FATIGUE_RESTORE_PER_TICK
    if not has_bed:
        rate *= constants.FATIGUE_GROUND_SLEEP_MULT
    return ticks * rate


def apply_fatigue_restoration(character: Character, ticks: int, *, has_bed: bool) -> float:
    """Restores fatigue in place, capped at `FATIGUE_MAX`; returns the
    amount actually restored (may be less than `fatigue_restored`'s raw
    value if already near the cap)."""
    restored = fatigue_restored(ticks, has_bed=has_bed)
    before = character.fatigue
    character.fatigue = min(constants.FATIGUE_MAX, before + restored)
    return character.fatigue - before


def dock_fatigue(character: Character, amount: float) -> None:
    character.fatigue = max(constants.FATIGUE_MIN, character.fatigue - amount)


def check_can_sleep(phase: DayPhase) -> None:
    """Sleep is only available during `DayPhase.NIGHT` -- "between the
    end of the evening shift and the beginning of the morning one," which
    per `panem_shared.simtime`'s phase order (night, morning, afternoon,
    evening) is exactly the night phase."""
    if phase != DayPhase.NIGHT:
        raise NotAllowed("sleep_wrong_phase")


# ------------------------------------------------------- mortgages/refinance


@dataclass(frozen=True, slots=True)
class FinancedPurchase:
    down_payment: float
    principal: float
    payment: float


def financed_purchase_terms(price: float) -> FinancedPurchase:
    """A down payment now (`MORTGAGE_DOWN_PAYMENT_PCT` of the price), the
    rest financed with a flat origination surcharge
    (`MORTGAGE_INTEREST_RATE`, not compounding) spread evenly across
    `MORTGAGE_TERM_TICKS_DEFAULT` / `MORTGAGE_PAYMENT_INTERVAL_TICKS`
    installments."""
    down_payment = price * constants.MORTGAGE_DOWN_PAYMENT_PCT
    principal = (price - down_payment) * (1.0 + constants.MORTGAGE_INTEREST_RATE)
    installments = max(
        1, constants.MORTGAGE_TERM_TICKS_DEFAULT // constants.MORTGAGE_PAYMENT_INTERVAL_TICKS
    )
    payment = principal / installments
    return FinancedPurchase(down_payment=down_payment, principal=principal, payment=payment)


def check_owns_property(*, character: Character, property_: Property) -> None:
    if property_.owner_kind != OwnerKind.CHARACTER.value or property_.owner_id != character.id:
        raise NotAllowed("housing_not_your_property", name=character.name)


def check_can_refinance(*, character: Character, property_: Property, amount: float) -> None:
    """`amount` on top of `Property.mortgage_principal` can't push the
    total past `MORTGAGE_MAX_LTV` of the property's current listed
    value."""
    _check_alive_and_approved(character)
    check_owns_property(character=character, property_=property_)
    if amount > max_refinance_amount(property_):
        raise NotAllowed("housing_refinance_too_much", name=character.name)


def property_value(property_: Property) -> float:
    return (
        property_.asking_price if property_.asking_price is not None else property_.suggested_price
    )


def max_refinance_amount(property_: Property) -> float:
    cap = property_value(property_) * constants.MORTGAGE_MAX_LTV
    return max(0.0, cap - property_.mortgage_principal)


def apply_refinance(property_: Property, amount: float, *, tick: int) -> float:
    """Adds `amount` to the property's mortgage principal and
    re-amortizes the *whole* new balance over a fresh
    `MORTGAGE_TERM_TICKS_DEFAULT` -- a real refinance resets the term the
    same way. Returns the new per-installment payment."""
    new_principal = property_.mortgage_principal + amount
    installments = max(
        1, constants.MORTGAGE_TERM_TICKS_DEFAULT // constants.MORTGAGE_PAYMENT_INTERVAL_TICKS
    )
    property_.mortgage_principal = new_principal
    property_.mortgage_payment = new_principal / installments
    if property_.mortgage_next_due_tick is None:
        property_.mortgage_next_due_tick = tick + constants.MORTGAGE_PAYMENT_INTERVAL_TICKS
    property_.mortgage_missed_payments = 0
    return property_.mortgage_payment


# ------------------------------------------------------------------ auctions


def check_can_start_auction(
    *, character: Character, property_: Property, existing_auction: PropertyAuction | None
) -> None:
    _check_alive_and_approved(character)
    check_owns_property(character=character, property_=property_)
    if existing_auction is not None and existing_auction.status == "open":
        raise NotAllowed("housing_auction_already_open")


def check_can_bid(*, character: Character, auction: PropertyAuction, amount: float) -> None:
    _check_alive_and_approved(character)
    floor = auction.current_bid if auction.current_bid is not None else auction.minimum_bid
    if amount <= floor:
        raise NotAllowed("housing_bid_too_low")
    if character.money < amount:
        raise NotAllowed("housing_insufficient_funds", name=character.name)
