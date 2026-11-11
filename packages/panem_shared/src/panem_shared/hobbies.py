"""Hobbies -- pick up to `constants.MAX_HOBBIES_PER_CHARACTER`, then play a
timed skill-check "session" (`panem_bot.cogs.hobbies`'s `HobbyTimingView`,
a cast-and-reel/till-and-water/mix-and-pull button prompt shared by all
three starter hobbies, differing only in flavor text and tool good) for a
chance at a `data/collectibles.yaml` drop, occasionally a personal event
chain instead (Spec's "fishing up a toy" example) rather than a plain
collectible.

Deliberately has no Activity/canvas minigame of its own yet -- every
hobby works end-to-end through Discord alone, the same "always playable
without `ACTIVITY_PUBLIC_URL`" shape `panem_shared.poaching`'s RNG-
fallback already established; a richer per-hobby canvas game (reusing
`static/games/bake.js`'s existing mechanic for Baking, or a new fishing-
bar reskin of `lockpick.js` for Fishing) is natural follow-up work, not a
redesign, since `success` here is already just a bool handed in from
wherever the skill check happened.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants, hobby_levels
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import Collectible, Hobby
from panem_shared.db.models import Character, CharacterHobby, CollectibleInstance, Inventory
from panem_shared.enums import JobLevel, OwnerKind
from panem_shared.errors import LimitReached, NotAllowed, NotFound, ValidationFailed


async def _owned_hobbies(session: AsyncSession, character_id: int) -> list[CharacterHobby]:
    result = await session.execute(
        select(CharacterHobby).where(CharacterHobby.character_id == character_id)
    )
    return list(result.scalars().all())


async def selected_hobbies(session: AsyncSession, character_id: int) -> list[CharacterHobby]:
    return await _owned_hobbies(session, character_id)


async def select_hobby(
    session: AsyncSession, character: Character, hobby_id: str, content: ContentBundle
) -> CharacterHobby:
    """Raises `NotFound` (unknown hobby), `LimitReached` (already at
    `MAX_HOBBIES_PER_CHARACTER`), or `ValidationFailed` (already picked).
    Grants one free unit of the hobby's `tool_good_id`, if it doesn't
    already own one -- Spec doesn't gate hobbies behind a purchase, so
    this is a one-time starter kit, not a refillable freebie."""
    hobby = content.hobbies.get(hobby_id)
    if hobby is None:
        raise NotFound("hobby_not_found")
    owned = await _owned_hobbies(session, character.id)
    if any(row.hobby_id == hobby_id for row in owned):
        raise ValidationFailed("hobby_already_selected", name=character.name, hobby=hobby.name)
    if len(owned) >= constants.MAX_HOBBIES_PER_CHARACTER:
        raise LimitReached(
            "hobby_limit_reached", limit=constants.MAX_HOBBIES_PER_CHARACTER
        )
    row = CharacterHobby(character_id=character.id, hobby_id=hobby_id)
    session.add(row)
    if hobby.tool_good_id is not None:
        owner_id = str(character.id)
        inv = await session.get(
            Inventory, (OwnerKind.CHARACTER.value, owner_id, hobby.tool_good_id)
        )
        if inv is None:
            session.add(
                Inventory(
                    owner_kind=OwnerKind.CHARACTER.value,
                    owner_id=owner_id,
                    good_id=hobby.tool_good_id,
                    qty=1,
                )
            )
    return row


async def forget_hobby(session: AsyncSession, character: Character, hobby_id: str) -> None:
    """Frees a slot -- the tool stays in inventory (it was a real item
    grant, not on loan), and `sessions_played` is lost if the player
    picks the same hobby again later (a fresh `CharacterHobby` row), the
    same "no history preserved" tradeoff `panem_shared.rp_modes` accepts
    for other reversible choices."""
    owned = await _owned_hobbies(session, character.id)
    row = next((r for r in owned if r.hobby_id == hobby_id), None)
    if row is None:
        raise NotFound("hobby_not_selected")
    await session.delete(row)


def check_can_play(
    character_hobby: CharacterHobby, current_tick: int
) -> None:
    """Raises `NotAllowed` on cooldown."""
    if (
        character_hobby.last_played_tick is not None
        and current_tick - character_hobby.last_played_tick < constants.HOBBY_SESSION_COOLDOWN_TICKS
    ):
        raise NotAllowed("hobby_on_cooldown")


def has_tool(hobby: Hobby, inventory_rows: dict[str, int]) -> bool:
    return hobby.tool_good_id is not None and inventory_rows.get(hobby.tool_good_id, 0) > 0


def session_success_probability(hobby: Hobby, sessions_played: int, owns_tool: bool) -> float:
    level = hobby_levels.hobby_level_for_sessions(sessions_played)
    probability = constants.HOBBY_BASE_SUCCESS + hobby_levels.success_bonus_for_level(level)
    if owns_tool:
        probability += constants.HOBBY_TOOL_SUCCESS_BONUS
    return min(0.95, probability)


def roll_drop(
    hobby: Hobby, collectibles: dict[str, Collectible], sessions_played: int, rng: random.Random
) -> Collectible | None:
    """Weighted pick among `hobby.drops` whose `min_level` the character's
    current mastery meets; `None` if somehow nothing qualifies (shouldn't
    happen with shipped content -- every hobby's cheapest drop requires
    Apprentice, the floor every character starts at)."""
    eligible = [
        drop for drop in hobby.drops if hobby_levels.meets_min_level(sessions_played, drop.min_level)
    ]
    if not eligible:
        return None
    total = sum(drop.weight for drop in eligible)
    roll = rng.random() * total
    upto = 0.0
    for drop in eligible:
        upto += drop.weight
        if roll <= upto:
            return collectibles.get(drop.collectible_id)
    return collectibles.get(eligible[-1].collectible_id)


@dataclass(frozen=True, slots=True)
class HobbySessionResult:
    success: bool
    collectible: Collectible | None
    """`None` on a miss, or on a hit that started a chain instead (see
    `chain_started`)."""
    chain_started: bool
    leveled_up_to: JobLevel | None
    """Set only on the exact session that crosses into a new level."""


async def resolve_session(
    session: AsyncSession,
    *,
    character: Character,
    character_hobby: CharacterHobby,
    hobby: Hobby,
    content: ContentBundle,
    current_tick: int,
    success: bool,
    rng: random.Random,
) -> HobbySessionResult:
    """Applies a session's outcome -- call after the Discord timing
    challenge (or any future Activity minigame) has already decided
    `success`. Always stamps the cooldown and, on a hit, `sessions_played`
    regardless of what was actually found (a chain-starting catch still
    counts as a successful session for levelling purposes)."""
    character_hobby.last_played_tick = current_tick
    if not success:
        return HobbySessionResult(False, None, False, None)

    before_level = hobby_levels.hobby_level_for_sessions(character_hobby.sessions_played)
    character_hobby.sessions_played += 1
    after_level = hobby_levels.hobby_level_for_sessions(character_hobby.sessions_played)
    leveled_up_to = after_level if after_level != before_level else None

    if hobby.rare_find_chain is not None and rng.random() < constants.HOBBY_RARE_CHAIN_CHANCE:
        from panem_shared import personal_events

        if not await personal_events.has_active_chain(
            session, character.id, hobby.rare_find_chain
        ):
            await personal_events.start_chain(
                session,
                character=character,
                chain_kind=hobby.rare_find_chain,
                current_tick=current_tick,
                flavor_hobby=hobby,
            )
            return HobbySessionResult(True, None, True, leveled_up_to)

    collectible = roll_drop(hobby, content.collectibles, character_hobby.sessions_played, rng)
    if collectible is not None:
        session.add(
            CollectibleInstance(
                owner_character_id=character.id,
                collectible_id=collectible.id,
                source=hobby.id,
                found_tick=current_tick,
                found_district_id=character.current_district_id,
            )
        )
    return HobbySessionResult(True, collectible, False, leveled_up_to)
