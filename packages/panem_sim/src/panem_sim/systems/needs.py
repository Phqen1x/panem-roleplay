"""Nightly living cost, hunger, and health (Spec FR-NDS-1/2/3).

Once per day (Spec §1.1: 30-day months, so "once per day" is the natural
cadence here, not once per tick), every character and NPC pays a living
cost. Paying it lowers hunger and lets health recover; failing to pay it
(not enough money) raises hunger and, once hunger crosses a threshold,
erodes health. Characters and NPCs are handled by separate small helpers
rather than one generic function, since `Character.money` is an int and
`Npc.money` is a float -- keeping them apart keeps both type-correct
without an artificial shared protocol.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared.db.models import Character, Npc
from panem_shared.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState


def _apply_character_needs(character: Character) -> None:
    if character.money >= constants.NIGHTLY_LIVING_COST:
        character.money -= constants.NIGHTLY_LIVING_COST
        character.hunger = max(
            constants.HUNGER_MIN, character.hunger - constants.HUNGER_DECREASE_MET
        )
    else:
        character.hunger = min(
            constants.HUNGER_MAX, character.hunger + constants.HUNGER_INCREASE_UNMET
        )

    if character.hunger >= constants.HEALTH_DECAY_HUNGER_THRESHOLD:
        character.health = max(
            constants.HEALTH_MIN, character.health - constants.HEALTH_DECAY_PER_NIGHT
        )
    else:
        character.health = min(
            constants.HEALTH_MAX, character.health + constants.HEALTH_RECOVERY_PER_NIGHT
        )


def _apply_npc_needs(npc: Npc) -> None:
    if npc.money >= constants.NIGHTLY_LIVING_COST_NPC:
        npc.money -= constants.NIGHTLY_LIVING_COST_NPC
        npc.hunger = max(constants.HUNGER_MIN, npc.hunger - constants.HUNGER_DECREASE_MET)
    else:
        npc.hunger = min(constants.HUNGER_MAX, npc.hunger + constants.HUNGER_INCREASE_UNMET)

    if npc.hunger >= constants.HEALTH_DECAY_HUNGER_THRESHOLD:
        npc.health = max(constants.HEALTH_MIN, npc.health - constants.HEALTH_DECAY_PER_NIGHT)
    else:
        npc.health = min(constants.HEALTH_MAX, npc.health + constants.HEALTH_RECOVERY_PER_NIGHT)


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    if ctx.tick % constants.TICKS_PER_DAY != 0:
        return []

    for character in state.characters.values():
        _apply_character_needs(character)
    for npc in state.npcs.values():
        _apply_npc_needs(npc)

    return []
