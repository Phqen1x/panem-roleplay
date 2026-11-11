"""The museum -- every collectible a character has ever found
(`CollectibleInstance`) can be donated (`donate`, one-way: Spec "you
cannot get it back") into a `MuseumSlot`, then freely reorganized by hand
(`move`) or via one of `constants.MUSEUM_PRESETS` (`apply_preset`).
Viewing/sharing another character's museum needs no special plumbing --
`character_collection` takes any `character_id`, so `/museum view
@someone` is just the read path with a different id, same as `panem_bot.
cogs.residents`'s `/resident profile` already works for any character."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import Collectible
from panem_shared.db.models import Character, CollectibleInstance, MuseumSlot
from panem_shared.errors import NotAllowed, NotFound


async def undonated_instances(
    session: AsyncSession, character_id: int
) -> list[CollectibleInstance]:
    result = await session.execute(
        select(CollectibleInstance).where(
            CollectibleInstance.owner_character_id == character_id,
            CollectibleInstance.donated_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def museum_slots(session: AsyncSession, character_id: int) -> list[MuseumSlot]:
    result = await session.execute(
        select(MuseumSlot)
        .where(MuseumSlot.owner_character_id == character_id)
        .order_by(MuseumSlot.wing, MuseumSlot.position)
    )
    return list(result.scalars().all())


async def donate(
    session: AsyncSession,
    *,
    character: Character,
    instance_id: int,
    content: ContentBundle,
    current_tick: int,
) -> tuple[Collectible, int]:
    """Raises `NotFound` if `instance_id` isn't one of this character's
    own, still-undonated instances. Returns `(collectible, reward_money)`
    -- the reward is granted here (not left for the caller) since it's
    always exactly `Collectible.donation_reward`, never situational."""
    instance = await session.get(CollectibleInstance, instance_id)
    if (
        instance is None
        or instance.owner_character_id != character.id
        or instance.donated_at is not None
    ):
        raise NotFound("museum_item_not_found")
    collectible = content.collectibles.get(instance.collectible_id)
    if collectible is None:
        raise NotFound("museum_item_not_found")

    instance.donated_at = dt.datetime.now(dt.UTC)
    existing_wings = await museum_slots(session, character.id)
    position = sum(1 for slot in existing_wings if slot.wing == collectible.wing)
    session.add(
        MuseumSlot(
            owner_character_id=character.id,
            collectible_instance_id=instance.id,
            wing=collectible.wing,
            position=position,
            donated_tick=current_tick,
        )
    )
    character.money += collectible.donation_reward
    return collectible, collectible.donation_reward


async def move(
    session: AsyncSession, *, character: Character, slot_id: int, wing: str, position: int
) -> MuseumSlot:
    slot = await session.get(MuseumSlot, slot_id)
    if slot is None or slot.owner_character_id != character.id:
        raise NotFound("museum_slot_not_found")
    slot.wing = wing
    slot.position = position
    return slot


async def apply_preset(
    session: AsyncSession, *, character_id: int, preset: str, content: ContentBundle
) -> list[MuseumSlot]:
    """Rewrites `wing`/`position` for every one of a character's slots at
    once. Raises `NotAllowed` for an unknown preset name."""
    if preset not in constants.MUSEUM_PRESETS:
        raise NotAllowed("museum_unknown_preset")
    slots = await museum_slots(session, character_id)

    instances_by_id: dict[int, CollectibleInstance] = {}
    if slots:
        result = await session.execute(
            select(CollectibleInstance).where(
                CollectibleInstance.id.in_([s.collectible_instance_id for s in slots])
            )
        )
        instances_by_id = {row.id: row for row in result.scalars().all()}

    def sort_key(slot: MuseumSlot) -> tuple[object, ...]:
        instance = instances_by_id.get(slot.collectible_instance_id)
        collectible = content.collectibles.get(instance.collectible_id) if instance else None
        if preset == "by_wing":
            return (collectible.wing if collectible else "", slot.id)
        if preset == "by_rarity":
            order = {"legendary": 0, "rare": 1, "uncommon": 2, "common": 3}
            return (order.get(collectible.rarity.value if collectible else "common", 9), slot.id)
        if preset == "by_hobby":
            return (collectible.hobby_id or "" if collectible else "", slot.id)
        if preset == "alphabetical":
            return (collectible.name if collectible else "", )
        if preset == "by_date":
            return (slot.donated_tick,)
        return (slot.id,)

    ordered = sorted(slots, key=sort_key)
    for index, slot in enumerate(ordered):
        instance = instances_by_id.get(slot.collectible_instance_id)
        collectible = content.collectibles.get(instance.collectible_id) if instance else None
        if preset == "by_wing" and collectible is not None:
            slot.wing = collectible.wing
        elif preset == "by_hobby" and collectible is not None:
            slot.wing = (collectible.hobby_id or "misc").title()
        slot.position = index
    return ordered
