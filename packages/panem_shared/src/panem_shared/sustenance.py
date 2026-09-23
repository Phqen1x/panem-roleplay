"""`/eat`/`/drink`/`/entertain` -- the player-initiated, proactive half of
Simulation mode's hunger/thirst/sanity meters. Purely a convenience on top
of the passive nightly resolution `panem_sim.systems.needs` already runs
(hunger's own money-gated nightly mechanic, and thirst/sanity's own daily
decay) -- these commands let a player relieve a meter on demand instead of
waiting for the night to resolve it, same as `/sleep` already does for
fatigue.

Pure logic, no DB session -- mirrors `panem_shared.housing`'s `check_can_
sleep`/`apply_fatigue_restoration` shape: takes the ORM `Character`, a
`current_tick`, raises `NotAllowed`/`ValidationFailed` on refusal, leaves
session handling to the caller.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared.db.models import Character
from panem_shared.enums import CharacterStatus, RpMode
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


def _already_done_today(last_tick: int | None, current_tick: int) -> bool:
    return last_tick is not None and last_tick // constants.TICKS_PER_DAY == (
        current_tick // constants.TICKS_PER_DAY
    )


def check_can_eat(character: Character, current_tick: int) -> None:
    _check_alive_approved_sim(character)
    if _already_done_today(character.last_ate_tick, current_tick):
        raise NotAllowed("already_ate_today", name=character.name)
    if character.money < constants.EAT_COST:
        raise NotAllowed(
            "sustenance_insufficient_funds", name=character.name, cost=constants.EAT_COST
        )


def eat(character: Character, current_tick: int) -> float:
    """Returns the character's new `hunger` value."""
    check_can_eat(character, current_tick)
    character.money -= constants.EAT_COST
    character.hunger = max(constants.HUNGER_MIN, character.hunger - constants.EAT_RELIEF)
    character.last_ate_tick = current_tick
    return character.hunger


def check_can_drink(character: Character, current_tick: int) -> None:
    _check_alive_approved_sim(character)
    if _already_done_today(character.last_drank_tick, current_tick):
        raise NotAllowed("already_drank_today", name=character.name)
    if character.money < constants.DRINK_COST:
        raise NotAllowed(
            "sustenance_insufficient_funds", name=character.name, cost=constants.DRINK_COST
        )


def drink(character: Character, current_tick: int) -> float:
    """Returns the character's new `thirst` value."""
    check_can_drink(character, current_tick)
    character.money -= constants.DRINK_COST
    character.thirst = max(
        constants.THIRST_MIN, character.thirst - constants.THIRST_RELIEF_PER_DRINK
    )
    character.last_drank_tick = current_tick
    return character.thirst


def check_can_entertain(character: Character, current_tick: int) -> None:
    _check_alive_approved_sim(character)
    if _already_done_today(character.last_entertained_tick, current_tick):
        raise NotAllowed("already_entertained_today", name=character.name)
    if character.money < constants.ENTERTAIN_COST:
        raise NotAllowed(
            "sustenance_insufficient_funds", name=character.name, cost=constants.ENTERTAIN_COST
        )


def entertain(character: Character, current_tick: int) -> float:
    """Returns the character's new `sanity` value."""
    check_can_entertain(character, current_tick)
    character.money -= constants.ENTERTAIN_COST
    character.sanity = min(
        constants.SANITY_MAX, character.sanity + constants.SANITY_RELIEF_PER_ENTERTAIN
    )
    character.last_entertained_tick = current_tick
    return character.sanity
