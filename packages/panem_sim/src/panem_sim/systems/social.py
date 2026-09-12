"""NPC-NPC and NPC-character social interaction, affinity/stance changes
(Spec §6, `STANCE_THRESHOLDS`, `STANCE_MIN_INTERACTIONS_EXTREME`,
`AFFINITY_DECAY_FLOOR`).

Proximity model: anyone (NPC or character) sharing a `(district_id,
location_id)` this tick nudges affinity/trust with everyone else there,
at most once per pair per tick -- there's no model here of *why* two
people are near each other, no NPC-initiated conversation, no RP content
read; "familiarity grows from shared space" is a placeholder for
whatever Spec §6's real interaction model turns out to be (not available
in this session's context). A pair needs at least one NPC in it --
character-character dynamics are for players to roleplay themselves, not
for the sim to track and score.

`RelationshipRow`'s `subject_kind/subject_id` vs `object_kind/object_id`
is otherwise-meaningless direction for a symmetric proximity nudge, so
each pair is canonicalized (`panem_shared.relationships.relationship_key`)
to one row rather than two mirror-image ones -- shared with `panem_bot`
(e.g. `/resident profile`) so a reader can look up the same row without
depending on this package.
"""

from __future__ import annotations

from collections import defaultdict

from panem_shared import constants
from panem_shared.db.models import RelationshipRow
from panem_shared.enums import OwnerKind, Stance
from panem_shared.events import AnyWorldEvent
from panem_shared.relationships import Occupant, relationship_key
from panem_sim.state import NotableEvent, TickContext, WorldState

AFFINITY_STEP = 2
"""Placeholder magnitude for one shared-location interaction -- Spec
§6's real interaction weighting wasn't available in this session's
context."""
TRUST_STEP = 1.0
TRUST_MAX = 100.0
DECAY_STEP = 1
"""Per-day affinity decay toward zero for anything already past
`AFFINITY_DECAY_FLOOR` -- a relationship strong enough to clear that
floor doesn't erode past it just from time passing, but does drift back
from its peak without upkeep."""


def _occupants(state: WorldState) -> dict[tuple[int, str], list[Occupant]]:
    groups: dict[tuple[int, str], list[Occupant]] = defaultdict(list)
    for npc in state.npcs.values():
        if npc.location_id is not None:
            groups[(npc.district_id, npc.location_id)].append((OwnerKind.NPC.value, npc.id))
    for character in state.characters.values():
        if character.location_id is not None:
            groups[(character.current_district_id, character.location_id)].append(
                (OwnerKind.CHARACTER.value, str(character.id))
            )
    return groups


def _pair_key(a: Occupant, b: Occupant) -> tuple[Occupant, Occupant]:
    kind_a, id_a, kind_b, id_b = relationship_key(a, b)
    return (kind_a, id_a), (kind_b, id_b)


def _get_or_create(state: WorldState, subject: Occupant, obj: Occupant) -> RelationshipRow:
    key = relationship_key(subject, obj)
    row = state.relationships.get(key)
    if row is None:
        # Column defaults only apply at flush, not on a plain transient
        # object -- spelled out explicitly here since this row's fields
        # get mutated (e.g. `+=`) before that flush ever happens.
        row = RelationshipRow(
            subject_kind=subject[0],
            subject_id=subject[1],
            object_kind=obj[0],
            object_id=obj[1],
            affinity=0,
            trust=0.0,
            interaction_count=0,
            stance=Stance.NEUTRAL.value,
        )
        state.relationships[key] = row
        state.new_relationships.append(row)
    return row


def _stance_for(affinity: int, interaction_count: int) -> str:
    """Pure classification, reusable outside `_apply_interaction`'s
    always-at-least-one-interaction call path (e.g. a future `/resident
    profile` querying a pair with no `RelationshipRow` at all yet) --
    `interaction_count == 0` means exactly that: never interacted."""
    if interaction_count == 0:
        return Stance.STRANGER.value
    lo_extreme, lo, hi, hi_extreme = constants.STANCE_THRESHOLDS
    extreme_ok = interaction_count >= constants.STANCE_MIN_INTERACTIONS_EXTREME
    if affinity <= lo_extreme:
        return Stance.HATES.value if extreme_ok else Stance.DISLIKES.value
    if affinity <= lo:
        return Stance.DISLIKES.value
    if affinity < hi:
        return Stance.NEUTRAL.value
    if affinity < hi_extreme:
        return Stance.LIKES.value
    return Stance.LOVES.value if extreme_ok else Stance.LIKES.value


def _display_name(state: WorldState, kind: str, owner_id: str) -> str:
    if kind == OwnerKind.NPC.value:
        npc = state.npcs.get(owner_id)
        return npc.name if npc is not None else owner_id
    character = state.characters.get(int(owner_id))
    return character.name if character is not None else owner_id


def _stance_change_note(
    state: WorldState, subject: Occupant, obj: Occupant, new_stance: str
) -> NotableEvent:
    subject_name = _display_name(state, *subject)
    object_name = _display_name(state, *obj)
    return NotableEvent(
        owner_kind=subject[0],
        owner_id=subject[1],
        kind="stance_change",
        importance=2,
        text=f"{subject_name} now {new_stance} {object_name}.",
        subject_kind=obj[0],
        subject_id=obj[1],
        tags=["relationship", new_stance],
    )


def _apply_interaction(state: WorldState, ctx: TickContext, a: Occupant, b: Occupant) -> None:
    subject, obj = _pair_key(a, b)
    row = _get_or_create(state, subject, obj)
    row.affinity += AFFINITY_STEP
    row.trust = min(TRUST_MAX, row.trust + TRUST_STEP)
    row.interaction_count += 1
    row.last_interaction_tick = ctx.tick

    new_stance = _stance_for(row.affinity, row.interaction_count)
    if new_stance != row.stance:
        row.stance = new_stance
        row.stance_updated_tick = ctx.tick
        state.notable_events.append(_stance_change_note(state, subject, obj, new_stance))


def _apply_interactions(state: WorldState, ctx: TickContext) -> None:
    seen: set[tuple[Occupant, Occupant]] = set()
    for occupants in _occupants(state).values():
        if len(occupants) < 2:
            continue
        for i in range(len(occupants)):
            for j in range(i + 1, len(occupants)):
                a, b = occupants[i], occupants[j]
                if a[0] != OwnerKind.NPC.value and b[0] != OwnerKind.NPC.value:
                    continue
                pair = _pair_key(a, b)
                if pair in seen:
                    continue
                seen.add(pair)
                _apply_interaction(state, ctx, a, b)


def _decay_relationships(state: WorldState, ctx: TickContext) -> None:
    if ctx.tick % constants.TICKS_PER_DAY != 0:
        return
    for row in state.relationships.values():
        if abs(row.affinity) <= constants.AFFINITY_DECAY_FLOOR:
            continue
        row.affinity += -DECAY_STEP if row.affinity > 0 else DECAY_STEP


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    _apply_interactions(state, ctx)
    _decay_relationships(state, ctx)
    return []
