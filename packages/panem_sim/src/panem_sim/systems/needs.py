"""Nightly living cost, hunger, health, and (Simulation mode only) thirst
and sanity (Spec FR-NDS-1/2/3, RP-modes feature).

Once per day (Spec §1.1: 30-day months, so "once per day" is the natural
cadence here, not once per tick), every character and NPC pays a living
cost. Paying it lowers hunger and lets health recover; failing to pay it
(not enough money) raises hunger and, once hunger crosses a threshold,
erodes health. Characters and NPCs are handled by separate small helpers
rather than one generic function, since `Character.money` is an int and
`Npc.money` is a float -- keeping them apart keeps both type-correct
without an artificial shared protocol.

Thirst/sanity and the auto-affliction/auto-death checks are Simulation-
mode only -- Life/Story characters "do not need to sleep at night or eat
or drink or entertain themselves" at all, so `_apply_character_needs`
returns immediately for them, leaving hunger/health/fatigue/thirst/sanity
exactly where this system already left them (dormant, not reset) for a
character that switches back into Simulation mode later.
"""

from __future__ import annotations

import datetime as dt

from panem_shared import constants
from panem_shared.afflictions import (
    apply_auto_afflictions_sync,
    apply_auto_death,
    check_and_cure_sync,
)
from panem_shared.db.models import Character, Npc
from panem_shared.enums import RpMode
from panem_shared.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState


def _apply_character_needs(character: Character, state: WorldState, day_index: int) -> None:
    if character.rp_mode != RpMode.SIMULATION.value:
        return

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

    if character.fatigue <= constants.FATIGUE_EXHAUSTION_THRESHOLD:
        # Never made it to a real sleep (`/sleep`) before the night ended
        # too exhausted -- same shape as the hunger check above, just a
        # second, independent need with its own threshold and penalty.
        character.health = max(
            constants.HEALTH_MIN, character.health - constants.FATIGUE_EXHAUSTION_HEALTH_PENALTY
        )

    if (
        character.last_drank_tick is None
        or character.last_drank_tick // constants.TICKS_PER_DAY != day_index
    ):
        character.thirst = min(
            constants.THIRST_MAX, character.thirst + constants.THIRST_INCREASE_PER_DAY
        )
    if character.thirst >= constants.HEALTH_DECAY_THIRST_THRESHOLD:
        character.health = max(
            constants.HEALTH_MIN, character.health - constants.HEALTH_DECAY_PER_NIGHT_THIRST
        )

    if (
        character.last_entertained_tick is None
        or character.last_entertained_tick // constants.TICKS_PER_DAY != day_index
    ):
        character.sanity = max(
            constants.SANITY_MIN, character.sanity - constants.SANITY_DECREASE_PER_DAY
        )
    if character.sanity <= constants.HEALTH_DECAY_SANITY_THRESHOLD:
        character.health = max(
            constants.HEALTH_MIN, character.health - constants.HEALTH_DECAY_PER_NIGHT_SANITY
        )

    active = state.active_afflictions.get(character.id, [])
    check_and_cure_sync(character, active, dt.datetime.now(dt.UTC))
    state.new_character_afflictions.extend(
        apply_auto_afflictions_sync(character, state.affliction_types, active)
    )
    apply_auto_death(character)


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

    day_index = (ctx.tick - 1) // constants.TICKS_PER_DAY
    for character in state.characters.values():
        _apply_character_needs(character, state, day_index)
    for npc in state.npcs.values():
        _apply_npc_needs(npc)

    return []
