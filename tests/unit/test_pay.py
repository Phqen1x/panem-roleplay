from __future__ import annotations

import pytest

from panem_shared import pay
from panem_shared.db.models import Character
from panem_shared.enums import CharacterStatus, RpMode
from panem_shared.errors import NotAllowed, ValidationFailed


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


class TestCheckCanPay:
    def test_refuses_a_story_mode_sender(self):
        sender = make_character(rp_mode=RpMode.STORY.value, id=1)
        recipient = make_character(id=2)
        with pytest.raises(NotAllowed) as exc_info:
            pay.check_can_pay(sender, recipient, 10)
        assert exc_info.value.reason_key == "pay_mode_forbidden"

    def test_refuses_a_story_mode_recipient(self):
        sender = make_character(id=1)
        recipient = make_character(rp_mode=RpMode.STORY.value, id=2)
        with pytest.raises(NotAllowed) as exc_info:
            pay.check_can_pay(sender, recipient, 10)
        assert exc_info.value.reason_key == "pay_mode_forbidden"

    def test_refuses_paying_yourself(self):
        character = make_character(id=1)
        with pytest.raises(ValidationFailed) as exc_info:
            pay.check_can_pay(character, character, 10)
        assert exc_info.value.reason_key == "pay_cannot_self"

    def test_refuses_a_non_positive_amount(self):
        sender = make_character(id=1)
        recipient = make_character(id=2)
        with pytest.raises(ValidationFailed) as exc_info:
            pay.check_can_pay(sender, recipient, 0)
        assert exc_info.value.reason_key == "pay_invalid_amount"

    def test_refuses_insufficient_funds(self):
        sender = make_character(id=1, money=5)
        recipient = make_character(id=2)
        with pytest.raises(NotAllowed) as exc_info:
            pay.check_can_pay(sender, recipient, 10)
        assert exc_info.value.reason_key == "pay_insufficient_money"


class TestPay:
    def test_moves_money_from_sender_to_recipient(self):
        sender = make_character(id=1, money=100)
        recipient = make_character(id=2, money=10)
        pay.pay(sender, recipient, 40)
        assert sender.money == 60
        assert recipient.money == 50
