"""Player-to-player trading (`Trade`, `panem_shared.db.models`) -- a
two-party accept/decline exchange of one item-or-money offer per side, not
a multi-item cart (kept to a single slash command with plain parameters
instead of a modal-built cart -- see the feature's plan for the scoping).

Pure logic plus the one DB-touching step every trade needs (moving
money/goods on accept) -- mirrors `panem_shared.market`'s shape, including
its own private `_adjust_inventory` (this module keeps its own copy rather
than importing market's, the same "small, per-module duplication" choice
`blackmarket.py` already made for the identical helper).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared.db.models import Character, Inventory, Trade
from panem_shared.enums import CharacterStatus, OwnerKind, RpMode, TradeStatus
from panem_shared.errors import NotAllowed, ValidationFailed


def check_can_trade(initiator: Character, recipient: Character) -> None:
    """Neither party may be Story mode ("life and sim players CANNOT ...
    trade ... with story mode characters" -- and Story characters have no
    inventory/balance to trade in the first place); both must be alive and
    approved."""
    for character in (initiator, recipient):
        if character.rp_mode == RpMode.STORY.value:
            raise NotAllowed("trade_mode_forbidden", name=character.name)
        if character.status == CharacterStatus.DEAD.value:
            raise NotAllowed("character_dead", name=character.name)
        if character.status != CharacterStatus.APPROVED.value:
            raise NotAllowed("character_not_approved", name=character.name)
    if initiator.id == recipient.id:
        raise ValidationFailed("trade_cannot_self")


def _validate_side(
    character: Character, *, money: int, good_id: str | None, qty: int | None
) -> None:
    if money < 0:
        raise ValidationFailed("trade_negative_money")
    if (good_id is None) != (qty is None):
        raise ValidationFailed("trade_good_qty_mismatch")
    if qty is not None and qty <= 0:
        raise ValidationFailed("trade_invalid_qty")
    if money == 0 and good_id is None:
        raise ValidationFailed("trade_empty_offer")


def validate_offer(
    *,
    initiator: Character,
    give_good_id: str | None,
    give_qty: int | None,
    give_money: int,
    want_good_id: str | None,
    want_qty: int | None,
    want_money: int,
) -> None:
    """Shape-validates a proposed offer -- both `_validate_side` calls --
    plus that the initiator currently has enough money to cover what they'd
    give (their inventory is checked here at propose time as a courtesy;
    it's re-checked again in `accept_trade` since it can go stale)."""
    _validate_side(initiator, money=give_money, good_id=give_good_id, qty=give_qty)
    _validate_side(initiator, money=want_money, good_id=want_good_id, qty=want_qty)
    if give_money > initiator.money:
        raise NotAllowed("trade_insufficient_money", name=initiator.name)


async def _adjust_inventory(
    session: AsyncSession, character: Character, good_id: str, delta: int
) -> int:
    owner_id = str(character.id)
    row = await session.get(Inventory, (OwnerKind.CHARACTER.value, owner_id, good_id))
    current = row.qty if row is not None else 0
    new_qty = current + delta
    if new_qty < 0:
        raise NotAllowed("trade_insufficient_inventory", name=character.name)
    if row is None:
        row = Inventory(
            owner_kind=OwnerKind.CHARACTER.value, owner_id=owner_id, good_id=good_id, qty=new_qty
        )
        session.add(row)
    else:
        row.qty = new_qty
    return new_qty


def check_can_respond(trade: Trade) -> None:
    if trade.status != TradeStatus.PENDING.value:
        raise NotAllowed("trade_not_pending")


async def accept_trade(
    session: AsyncSession,
    *,
    trade: Trade,
    initiator: Character,
    recipient: Character,
    now: dt.datetime,
) -> None:
    """Re-validates both sides currently hold what they offered -- an
    offer can go stale between propose and accept -- then moves
    money/goods atomically. Raises before mutating anything if either side
    can no longer cover their part."""
    check_can_respond(trade)
    check_can_trade(initiator, recipient)
    if trade.give_money > initiator.money:
        raise NotAllowed("trade_insufficient_money", name=initiator.name)
    if trade.want_money > recipient.money:
        raise NotAllowed("trade_insufficient_money", name=recipient.name)
    if trade.give_good_id is not None and trade.give_qty is not None:
        await _adjust_inventory(session, initiator, trade.give_good_id, -trade.give_qty)
        await _adjust_inventory(session, recipient, trade.give_good_id, trade.give_qty)
    if trade.want_good_id is not None and trade.want_qty is not None:
        await _adjust_inventory(session, recipient, trade.want_good_id, -trade.want_qty)
        await _adjust_inventory(session, initiator, trade.want_good_id, trade.want_qty)
    initiator.money -= trade.give_money
    recipient.money += trade.give_money
    recipient.money -= trade.want_money
    initiator.money += trade.want_money
    trade.status = TradeStatus.ACCEPTED.value
    trade.resolved_at = now


def decline_trade(trade: Trade, now: dt.datetime) -> None:
    check_can_respond(trade)
    trade.status = TradeStatus.DECLINED.value
    trade.resolved_at = now


def cancel_trade(trade: Trade, now: dt.datetime) -> None:
    check_can_respond(trade)
    trade.status = TradeStatus.CANCELLED.value
    trade.resolved_at = now


def expire_trade(trade: Trade, now: dt.datetime) -> None:
    check_can_respond(trade)
    trade.status = TradeStatus.EXPIRED.value
    trade.resolved_at = now
