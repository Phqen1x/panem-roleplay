"""`/eat`/`/drink`/`/heal`/`/entertain` -- the player-initiated, proactive half of
Simulation mode's hunger/thirst/health/sanity meters (Vitals tab feature).

Full rewrite from this module's original once-per-sim-day/flat-cost shape:
eating/drinking/healing now consume a specific owned `Good` from inventory (each
good carries its own `hunger_value`/`thirst_value`/`heal_value`, content-authored --
`panem_shared.content.schemas.Good`), and there's no daily cooldown on
either -- the passive per-phase hunger/thirst decay
(`panem_sim.systems.needs`, `constants.HUNGER_PHASE_DECAY_MIN`/`MAX`) is
what keeps this balanced now, not a cooldown. `eat`'s `bonus` flag applies
`constants.COOK_BONUS_MULTIPLIER` when the cook/bake minigame's timing
landed correctly (`static/games/cook.js`/`bake.js`) -- the caller (the
dashboard's Vitals router) is responsible for only ever passing
`bonus=True` when that minigame actually reported a win, the same trust
model `panem_shared.shifts.resolve_shift_game`'s client-reported `won`
already uses for every other Activity minigame in this codebase.

Entertainment stays fully decoupled from inventory -- picking one of the
six `/work` leisure minigames (`constants.ENTERTAINMENT_SANITY_VALUES`)
and completing it (win or lose) credits `sanity` by that game's flat
value, uncapped repeats, bounded only by `SANITY_MAX`.

Needs a `session` (unlike the old pure-function shape) since eating/
drinking now mutates `Inventory` via `panem_shared.market.adjust_inventory`
-- `check_can_*` stays pure/sync for the "can this happen at all" gate,
mirroring `panem_shared.housing`'s `check_can_sleep`/`apply_fatigue_
restoration` split.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants, market
from panem_shared.content.schemas import Good
from panem_shared.db.models import Character, Inventory
from panem_shared.enums import CharacterStatus, OwnerKind, RpMode
from panem_shared.errors import NotAllowed


def _check_alive_approved_sim(character: Character) -> None:
    if character.status == CharacterStatus.DEAD.value:
        raise NotAllowed("character_dead")
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")
    if character.rp_mode != RpMode.SIMULATION.value:
        # Life/Story characters "do not need to ... eat or drink or
        # entertain themselves" at all -- these commands don't exist for
        # them, mirroring housing's own Simulation-only gate.
        raise NotAllowed("sustenance_mode_forbidden", name=character.name)


async def _owned_qty(session: AsyncSession, character: Character, good_id: str) -> int:
    row = await session.get(Inventory, (OwnerKind.CHARACTER.value, str(character.id), good_id))
    return row.qty if row is not None else 0


async def owned_consumables(
    session: AsyncSession, character: Character, goods: dict[str, Good]
) -> list[tuple[Inventory, Good]]:
    """Every inventory row the character owns that's actually edible,
    drinkable, or usable for healing (`hunger_value > 0`, `thirst_value > 0`,
    or `heal_value > 0`), paired with its `Good` -- the Vitals tab status
    endpoint's eat/drink/heal panel data."""
    rows = (
        await session.execute(
            select(Inventory).where(
                Inventory.owner_kind == OwnerKind.CHARACTER.value,
                Inventory.owner_id == str(character.id),
                Inventory.qty > 0,
            )
        )
    ).scalars()
    pairs: list[tuple[Inventory, Good]] = []
    for row in rows:
        good = goods.get(row.good_id)
        if good is not None and (
            good.hunger_value > 0 or good.thirst_value > 0 or good.heal_value > 0
        ):
            pairs.append((row, good))
    return pairs


def check_can_eat(character: Character, good: Good, qty_owned: int) -> None:
    _check_alive_approved_sim(character)
    if good.hunger_value <= 0:
        raise NotAllowed("good_not_edible", name=character.name, good=good.name)
    if qty_owned < 1:
        raise NotAllowed("sustenance_no_inventory", name=character.name, good=good.name)


async def eat(
    session: AsyncSession,
    character: Character,
    good: Good,
    current_tick: int,
    *,
    bonus: bool = False,
) -> float:
    """Consumes one unit of `good`, relieves `hunger` by its `hunger_value`
    -- doubled by `constants.COOK_BONUS_MULTIPLIER` when `bonus` is true
    and `good.cook_method` is set (a straight eat, or a cook/bake attempt
    that missed its timing window, never doubles). Returns the character's
    new `hunger`."""
    qty_owned = await _owned_qty(session, character, good.id)
    check_can_eat(character, good, qty_owned)
    await market.adjust_inventory(session, character, good.id, -1)
    multiplier = constants.COOK_BONUS_MULTIPLIER if bonus and good.cook_method else 1.0
    character.hunger = max(constants.HUNGER_MIN, character.hunger - good.hunger_value * multiplier)
    character.last_ate_tick = current_tick
    return character.hunger


def check_can_drink(character: Character, good: Good, qty_owned: int) -> None:
    _check_alive_approved_sim(character)
    if good.thirst_value <= 0:
        raise NotAllowed("good_not_drinkable", name=character.name, good=good.name)
    if qty_owned < 1:
        raise NotAllowed("sustenance_no_inventory", name=character.name, good=good.name)


async def drink(
    session: AsyncSession, character: Character, good: Good, current_tick: int
) -> float:
    """Consumes one unit of `good`, relieves `thirst` by its
    `thirst_value` -- no minigame/bonus for drinking, "just a straight
    drink button." Returns the character's new `thirst`."""
    qty_owned = await _owned_qty(session, character, good.id)
    check_can_drink(character, good, qty_owned)
    await market.adjust_inventory(session, character, good.id, -1)
    character.thirst = max(constants.THIRST_MIN, character.thirst - good.thirst_value)
    character.last_drank_tick = current_tick
    return character.thirst


def check_can_heal(character: Character, good: Good, qty_owned: int) -> None:
    _check_alive_approved_sim(character)
    if good.heal_value <= 0:
        raise NotAllowed("good_not_healing", name=character.name, good=good.name)
    if qty_owned < 1:
        raise NotAllowed("sustenance_no_inventory", name=character.name, good=good.name)


async def heal(
    session: AsyncSession, character: Character, good: Good, current_tick: int
) -> float:
    """Consumes one unit of `good`, restores `health` by its `heal_value`,
    clamped to `constants.HEALTH_MAX`. Returns the character's new `health`."""
    qty_owned = await _owned_qty(session, character, good.id)
    check_can_heal(character, good, qty_owned)
    await market.adjust_inventory(session, character, good.id, -1)
    character.health = min(constants.HEALTH_MAX, character.health + good.heal_value)
    return character.health


def check_can_entertain(character: Character) -> None:
    _check_alive_approved_sim(character)


def entertain(character: Character, game_id: str | None, current_tick: int) -> float:
    """Credits `sanity` -- `game_id` given (the Vitals tab's Entertainment
    panel, one of the six leisure minigames) looks up its flat value from
    `constants.ENTERTAINMENT_SANITY_VALUES` and raises `NotAllowed
    ("unknown_game")` if it's not a recognized id; `game_id=None` (the
    Discord `/entertain` command, which has no minigame to report) instead
    credits the flat `constants.SANITY_RELIEF_PER_ENTERTAIN`. Win or lose
    counts either way. No inventory cost, no cooldown (only `SANITY_MAX`
    bounds repeated play). Returns the character's new `sanity`."""
    check_can_entertain(character)
    sanity_value: float
    if game_id is None:
        sanity_value = constants.SANITY_RELIEF_PER_ENTERTAIN
    else:
        looked_up = constants.ENTERTAINMENT_SANITY_VALUES.get(game_id)
        if looked_up is None:
            raise NotAllowed("unknown_game", name=character.name, game=game_id)
        sanity_value = looked_up
    character.sanity = min(constants.SANITY_MAX, character.sanity + sanity_value)
    character.last_entertained_tick = current_tick
    return character.sanity
