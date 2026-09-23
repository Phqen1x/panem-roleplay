"""`/pay` -- an instant, one-way money transfer between two player
characters (no NPCs). Unlike `trades.py`'s two-party offer, this needs no
recipient confirmation, the same trust level as the existing housing
rent/purchase payments that already move money character-to-character
with no extra step.
"""

from __future__ import annotations

from panem_shared.db.models import Character
from panem_shared.enums import CharacterStatus, RpMode
from panem_shared.errors import NotAllowed, ValidationFailed


def check_can_pay(sender: Character, recipient: Character, amount: int) -> None:
    """Neither party may be Story mode ("life and sim players CANNOT ...
    pay ... story mode characters" -- Story characters have no balance to
    receive into anyway); both must be alive and approved; `sender` must
    have enough money."""
    for character in (sender, recipient):
        if character.rp_mode == RpMode.STORY.value:
            raise NotAllowed("pay_mode_forbidden", name=character.name)
        if character.status == CharacterStatus.DEAD.value:
            raise NotAllowed("character_dead", name=character.name)
        if character.status != CharacterStatus.APPROVED.value:
            raise NotAllowed("character_not_approved", name=character.name)
    if sender.id == recipient.id:
        raise ValidationFailed("pay_cannot_self")
    if amount <= 0:
        raise ValidationFailed("pay_invalid_amount")
    if amount > sender.money:
        raise NotAllowed("pay_insufficient_money", name=sender.name)


def pay(sender: Character, recipient: Character, amount: int) -> None:
    check_can_pay(sender, recipient, amount)
    sender.money -= amount
    recipient.money += amount
