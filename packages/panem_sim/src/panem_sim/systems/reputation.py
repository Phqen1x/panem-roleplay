"""Periodic reputation nudge from NPC relationships.

Everything else that moves `Character.reputation` (the `/work` minigame's
win/lose/streak result in `panem_shared.shifts.resolve_shift_game`, a
missed shift in `panem_sim.systems.jobs`, an illicit-market catch in
`panem_bot.services.market`) reacts to one event. This is the one
*ambient* reputation source: "good relationships with many NPCs" raises
it, "bad relationships" lowers it, checked weekly rather than every tick
so a single interaction can't spike or crater it -- `social.py` already
does the actual per-tick affinity/stance bookkeeping this reads from.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared.enums import OwnerKind
from panem_shared.events import AnyWorldEvent
from panem_sim.state import TickContext, WorldState

_CHECK_INTERVAL_TICKS = constants.REP_RELATIONSHIP_CHECK_INTERVAL_DAYS * constants.TICKS_PER_DAY


def _relationship_delta(state: WorldState, character_id: int) -> float:
    """Reuses `STANCE_THRESHOLDS`' likes/dislikes cutoffs (the same ones
    `social.py._stance_for` classifies a `Stance` with) rather than a
    second affinity scale -- a relationship at or above the likes cutoff
    counts as good, at or below the dislikes cutoff counts as bad,
    anything in between is too lukewarm to move reputation."""
    _lo_extreme, lo, hi, _hi_extreme = constants.STANCE_THRESHOLDS
    subject_str = str(character_id)
    good = 0
    bad = 0
    for row in state.relationships.values():
        is_subject = row.subject_kind == OwnerKind.CHARACTER.value and row.subject_id == subject_str
        is_object = row.object_kind == OwnerKind.CHARACTER.value and row.object_id == subject_str
        if not is_subject and not is_object:
            continue
        if row.affinity >= hi:
            good += 1
        elif row.affinity <= lo:
            bad += 1
    return good * constants.REP_RELATIONSHIP_DELTA - bad * constants.REP_RELATIONSHIP_DELTA


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    if ctx.tick % _CHECK_INTERVAL_TICKS != 0:
        return []
    for character in state.characters.values():
        character.reputation += _relationship_delta(state, character.id)
    return []
