from __future__ import annotations

import datetime as dt

import pytest

from panem_shared import trades
from panem_shared.db.models import Character, Inventory, Trade
from panem_shared.enums import CharacterStatus, OwnerKind, RpMode, TradeStatus
from panem_shared.errors import NotAllowed, ValidationFailed

NOW = dt.datetime(2026, 1, 10, tzinfo=dt.UTC)


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
        rp_mode=RpMode.SIMULATION.value,
        money=100,
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = overrides.get("id", 1)
    return character


def make_trade(**overrides: object) -> Trade:
    defaults: dict[str, object] = dict(
        initiator_character_id=1,
        recipient_character_id=2,
        give_good_id=None,
        give_qty=None,
        give_money=0,
        want_good_id=None,
        want_qty=None,
        want_money=0,
        status=TradeStatus.PENDING.value,
    )
    defaults.update(overrides)
    trade = Trade(**defaults)  # type: ignore[arg-type]
    trade.id = 1
    return trade


class TestCheckCanTrade:
    def test_refuses_a_story_mode_initiator(self):
        initiator = make_character(rp_mode=RpMode.STORY.value, id=1)
        recipient = make_character(id=2)
        with pytest.raises(NotAllowed) as exc_info:
            trades.check_can_trade(initiator, recipient)
        assert exc_info.value.reason_key == "trade_mode_forbidden"

    def test_refuses_a_story_mode_recipient(self):
        initiator = make_character(id=1)
        recipient = make_character(rp_mode=RpMode.STORY.value, id=2)
        with pytest.raises(NotAllowed) as exc_info:
            trades.check_can_trade(initiator, recipient)
        assert exc_info.value.reason_key == "trade_mode_forbidden"

    def test_refuses_trading_with_yourself(self):
        character = make_character(id=1)
        with pytest.raises(ValidationFailed) as exc_info:
            trades.check_can_trade(character, character)
        assert exc_info.value.reason_key == "trade_cannot_self"

    def test_allows_a_life_and_simulation_pair(self):
        initiator = make_character(rp_mode=RpMode.LIFE.value, id=1)
        recipient = make_character(rp_mode=RpMode.SIMULATION.value, id=2)
        trades.check_can_trade(initiator, recipient)


class TestValidateOffer:
    def test_refuses_a_money_and_good_both_missing(self):
        initiator = make_character(id=1)
        with pytest.raises(ValidationFailed) as exc_info:
            trades.validate_offer(
                initiator=initiator,
                give_good_id=None,
                give_qty=None,
                give_money=0,
                want_good_id="grain",
                want_qty=1,
                want_money=0,
            )
        assert exc_info.value.reason_key == "trade_empty_offer"

    def test_refuses_a_good_without_a_qty(self):
        initiator = make_character(id=1)
        with pytest.raises(ValidationFailed) as exc_info:
            trades.validate_offer(
                initiator=initiator,
                give_good_id="grain",
                give_qty=None,
                give_money=0,
                want_good_id=None,
                want_qty=None,
                want_money=5,
            )
        assert exc_info.value.reason_key == "trade_good_qty_mismatch"

    def test_refuses_more_money_than_the_initiator_has(self):
        initiator = make_character(id=1, money=10)
        with pytest.raises(NotAllowed) as exc_info:
            trades.validate_offer(
                initiator=initiator,
                give_good_id=None,
                give_qty=None,
                give_money=50,
                want_good_id=None,
                want_qty=None,
                want_money=5,
            )
        assert exc_info.value.reason_key == "trade_insufficient_money"

    def test_accepts_a_well_formed_money_for_goods_offer(self):
        initiator = make_character(id=1, money=100)
        trades.validate_offer(
            initiator=initiator,
            give_good_id=None,
            give_qty=None,
            give_money=20,
            want_good_id="grain",
            want_qty=3,
            want_money=0,
        )


class TestAcceptTrade:
    async def test_moves_money_and_goods_both_ways(self, db_session):
        initiator = make_character(id=1, money=100)
        recipient = make_character(id=2, money=50)
        db_session.add_all(
            [Inventory(owner_kind=OwnerKind.CHARACTER.value, owner_id="1", good_id="grain", qty=5)]
        )
        await db_session.flush()

        trade = make_trade(give_good_id="grain", give_qty=3, give_money=10, want_money=20)

        await trades.accept_trade(
            db_session, trade=trade, initiator=initiator, recipient=recipient, now=NOW
        )

        assert initiator.money == 100 - 10 + 20
        assert recipient.money == 50 + 10 - 20
        assert trade.status == TradeStatus.ACCEPTED.value
        assert trade.resolved_at == NOW

        initiator_grain = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "grain"))
        recipient_grain = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "2", "grain"))
        assert initiator_grain.qty == 2
        assert recipient_grain.qty == 3

    async def test_refuses_when_the_initiator_no_longer_has_the_goods(self, db_session):
        initiator = make_character(id=1, money=100)
        recipient = make_character(id=2, money=50)
        trade = make_trade(give_good_id="grain", give_qty=3, give_money=0)

        with pytest.raises(NotAllowed) as exc_info:
            await trades.accept_trade(
                db_session, trade=trade, initiator=initiator, recipient=recipient, now=NOW
            )
        assert exc_info.value.reason_key == "trade_insufficient_inventory"

    async def test_refuses_a_non_pending_trade(self, db_session):
        initiator = make_character(id=1)
        recipient = make_character(id=2)
        trade = make_trade(status=TradeStatus.DECLINED.value)
        with pytest.raises(NotAllowed) as exc_info:
            await trades.accept_trade(
                db_session, trade=trade, initiator=initiator, recipient=recipient, now=NOW
            )
        assert exc_info.value.reason_key == "trade_not_pending"


class TestDeclineCancelExpire:
    def test_decline_sets_status_and_timestamp(self):
        trade = make_trade()
        trades.decline_trade(trade, NOW)
        assert trade.status == TradeStatus.DECLINED.value
        assert trade.resolved_at == NOW

    def test_cancel_sets_status_and_timestamp(self):
        trade = make_trade()
        trades.cancel_trade(trade, NOW)
        assert trade.status == TradeStatus.CANCELLED.value

    def test_expire_sets_status_and_timestamp(self):
        trade = make_trade()
        trades.expire_trade(trade, NOW)
        assert trade.status == TradeStatus.EXPIRED.value

    def test_cannot_respond_twice(self):
        trade = make_trade()
        trades.decline_trade(trade, NOW)
        with pytest.raises(NotAllowed):
            trades.decline_trade(trade, NOW)
