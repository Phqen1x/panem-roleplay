"""Nightly living cost, per-phase hunger/thirst decay, health, and
(Simulation mode only) sanity (Spec FR-NDS-1/2/3, RP Modes feature; the
per-phase hunger/thirst cadence -- and decoupling it from living cost,
formerly hunger's only "did you eat" proxy -- is the Vitals tab feature).

Two cadences now, not one:
- `_apply_character_phase_needs` -- hunger and thirst, on
  `simtime.TICKS_PER_PHASE` (4 times a day, not once a night). Each
  climbs by a random amount (`constants.HUNGER_PHASE_DECAY_MIN`/`MAX`,
  `THIRST_PHASE_DECAY_MIN`/`MAX`) drawn from the tick's own seeded
  `ctx.rng` -- never Python's global `random`, which would break tick
  determinism (`T-1.1`/`T-1.2`). Each meter's own health-decay threshold
  check moved here too, alongside its decay -- checking health impact
  only once a night would be stale against a meter that now moves four
  times that often. Actual relief comes from `panem_shared.sustenance.
  eat`/`drink` (inventory-based, player-initiated) -- nothing in this
  module ever relieves hunger or thirst, only raises them.
- `_apply_character_nightly_needs` -- everything else, unchanged
  cadence: `NIGHTLY_LIVING_COST` (now a plain money deduction when
  affordable -- it no longer drives hunger, since real inventory-based
  eating does that job now), fatigue-exhaustion, sanity decay + its own
  health threshold, and the auto-affliction/auto-death checks.

Characters and NPCs are handled by separate small helpers rather than
one generic function, since `Character.money` is an int and `Npc.money`
is a float -- keeping them apart keeps both type-correct without an
artificial shared protocol. NPCs are untouched by the Vitals tab feature
-- they keep the original money-gated hunger mechanic, once a night,
exactly as before (no inventory-based eating for NPCs).

Thirst/sanity and the auto-affliction/auto-death checks are Simulation-
mode only -- Life/Story characters "do not need to sleep at night or eat
or drink or entertain themselves" at all, so both per-character
functions return immediately for them, leaving hunger/health/fatigue/
thirst/sanity exactly where this system already left them (dormant, not
reset) for a character that switches back into Simulation mode later.
"""

from __future__ import annotations

import datetime as dt
import random

from panem_shared import constants, simtime
from panem_shared.afflictions import (
    apply_auto_afflictions_sync,
    apply_auto_death,
    check_and_cure_sync,
)
from panem_shared.db.models import Character, Npc
from panem_shared.enums import RpMode
from panem_shared.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState


def _apply_character_phase_needs(character: Character, rng: random.Random) -> None:
    if character.rp_mode != RpMode.SIMULATION.value:
        return

    character.hunger = min(
        constants.HUNGER_MAX,
        character.hunger
        + rng.uniform(constants.HUNGER_PHASE_DECAY_MIN, constants.HUNGER_PHASE_DECAY_MAX),
    )
    if character.hunger >= constants.HEALTH_DECAY_HUNGER_THRESHOLD:
        character.health = max(
            constants.HEALTH_MIN, character.health - constants.HEALTH_DECAY_PER_NIGHT
        )
    else:
        character.health = min(
            constants.HEALTH_MAX, character.health + constants.HEALTH_RECOVERY_PER_NIGHT
        )

    character.thirst = min(
        constants.THIRST_MAX,
        character.thirst
        + rng.uniform(constants.THIRST_PHASE_DECAY_MIN, constants.THIRST_PHASE_DECAY_MAX),
    )
    if character.thirst >= constants.HEALTH_DECAY_THIRST_THRESHOLD:
        character.health = max(
            constants.HEALTH_MIN, character.health - constants.HEALTH_DECAY_PER_NIGHT_THIRST
        )


def _apply_character_nightly_needs(character: Character, state: WorldState, day_index: int) -> None:
    if character.rp_mode != RpMode.SIMULATION.value:
        return

    if character.money >= constants.NIGHTLY_LIVING_COST:
        character.money -= constants.NIGHTLY_LIVING_COST

    if character.fatigue <= constants.FATIGUE_EXHAUSTION_THRESHOLD:
        # Never made it to a real sleep (`/sleep`) before the night ended
        # too exhausted -- an independent need with its own threshold and
        # penalty.
        character.health = max(
            constants.HEALTH_MIN, character.health - constants.FATIGUE_EXHAUSTION_HEALTH_PENALTY
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
    if ctx.tick % simtime.TICKS_PER_PHASE == 0:
        for character in state.characters.values():
            _apply_character_phase_needs(character, ctx.rng)

    if ctx.tick % constants.TICKS_PER_DAY == 0:
        day_index = (ctx.tick - 1) // constants.TICKS_PER_DAY
        for character in state.characters.values():
            _apply_character_nightly_needs(character, state, day_index)
        for npc in state.npcs.values():
            _apply_npc_needs(npc)

    return []
