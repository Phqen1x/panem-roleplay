"""Canonical `RelationshipRow` pair ordering (Spec §6).

`RelationshipRow.subject_kind/subject_id` vs `object_kind/object_id` is
otherwise-meaningless direction for `panem_sim.systems.social`'s
symmetric proximity model -- one canonical ordering per unordered pair,
rather than two mirror-image rows, so both the writer (`panem_sim`,
which never depends on `panem_bot`) and a reader (`panem_bot`'s
`/resident profile`, which never depends on `panem_sim`) can compute the
same primary key independently.
"""

from __future__ import annotations

Occupant = tuple[str, str]
"""`(owner_kind, owner_id)`, matching `RelationshipRow`'s own columns."""


def relationship_key(a: Occupant, b: Occupant) -> tuple[str, str, str, str]:
    """The `(subject_kind, subject_id, object_kind, object_id)` primary
    key for the unordered pair `{a, b}`."""
    subject, obj = (a, b) if a <= b else (b, a)
    return (*subject, *obj)
