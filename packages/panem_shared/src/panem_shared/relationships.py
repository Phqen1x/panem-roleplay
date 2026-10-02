"""Canonical `RelationshipRow` pair ordering (Spec §6), plus the shared
affinity/stance mutation helpers `panem_sim.systems.social`,
`panem_shared.stealing`, and `panem_shared.hostility`'s callers all need.

`RelationshipRow.subject_kind/subject_id` vs `object_kind/object_id` is
otherwise-meaningless direction for `panem_sim.systems.social`'s
symmetric proximity model -- one canonical ordering per unordered pair,
rather than two mirror-image rows, so both the writer (`panem_sim`,
which never depends on `panem_bot`) and a reader (`panem_bot`'s
`/resident profile`, which never depends on `panem_sim`) can compute the
same primary key independently.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.db.models import RelationshipRow
from panem_shared.enums import Stance

Occupant = tuple[str, str]
"""`(owner_kind, owner_id)`, matching `RelationshipRow`'s own columns."""


def relationship_key(a: Occupant, b: Occupant) -> tuple[str, str, str, str]:
    """The `(subject_kind, subject_id, object_kind, object_id)` primary
    key for the unordered pair `{a, b}`."""
    subject, obj = (a, b) if a <= b else (b, a)
    return (*subject, *obj)


def stance_for_affinity(affinity: int, interaction_count: int) -> str:
    """Pure classification shared by `panem_sim.systems.social` (its own
    `_stance_for` re-exports this) and every other affinity mutator below
    -- `interaction_count == 0` always means `stranger` regardless of
    affinity (never interacted, nothing to have an opinion from yet);
    the two extreme buckets (`hates`/`loves`) additionally need
    `STANCE_MIN_INTERACTIONS_EXTREME` interactions on record before a raw
    affinity swing alone can produce them."""
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


async def get_or_create_relationship(
    session: AsyncSession, subject: Occupant, obj: Occupant
) -> RelationshipRow:
    """The get-or-create half of the pattern `panem_shared.stealing` and
    `panem_shared.hostility`'s bot-side callers both need -- column
    defaults (`affinity=0`, `interaction_count=0`, ...) only apply at
    flush, not on a plain transient object, so a freshly created row is
    safe to mutate (`+=`, `apply_affinity_delta`) immediately either way
    since every field it needs is set explicitly here."""
    key = relationship_key(subject, obj)
    row = await session.get(RelationshipRow, key)
    if row is None:
        row = RelationshipRow(
            subject_kind=key[0],
            subject_id=key[1],
            object_kind=key[2],
            object_id=key[3],
            affinity=0,
            trust=0.0,
            interaction_count=0,
            stance=Stance.STRANGER.value,
        )
        session.add(row)
    return row


def apply_affinity_delta(row: RelationshipRow, delta: int, *, current_tick: int | None) -> None:
    """A one-off affinity nudge from something real happening between the
    two (not `panem_sim.systems.social`'s ambient proximity tick) --
    counts as an interaction, and recomputes `stance` immediately via
    `stance_for_affinity` so a reader (`/resident profile`, the dashboard
    Social tab) reflects it right away rather than waiting for the two to
    next share a location on some future sim tick. `current_tick` is
    `None` from a caller with no tick to hand (e.g. a bot-side command
    that doesn't otherwise need `WorldClock`) -- `stance_updated_tick`
    then just stays whatever it was."""
    row.affinity += delta
    row.interaction_count += 1
    row.stance = stance_for_affinity(row.affinity, row.interaction_count)
    if current_tick is not None:
        row.stance_updated_tick = current_tick


def crash_to_hated(row: RelationshipRow, *, current_tick: int | None) -> None:
    """Immediately drives a relationship to the worst possible stance --
    used where the act itself (stealing from an NPC) is severe enough
    that no ordinary affinity nudge would represent the reaction, per the
    request that friendship "immediately" hit "the lowest possible" for
    that specific act, not just decrement further from wherever it
    already was."""
    row.affinity = constants.AFFINITY_FLOOR
    row.interaction_count = max(
        row.interaction_count + 1, constants.STANCE_MIN_INTERACTIONS_EXTREME
    )
    row.stance = Stance.HATES.value
    if current_tick is not None:
        row.stance_updated_tick = current_tick
