"""Whether a character can be at a given (possibly restricted) location --
shared by `panem_bot.services.travel`/`proxy`/`dialogue`/`engagements` and,
now, `panem_api`'s dashboard Travel tab, none of which can depend on
`panem_bot` to get it. Re-exported from `panem_bot.services.proxy` for
every existing call site; this was the one Discord-independent function
in an otherwise Discord-heavy module, so only it moved, not the module.
"""

from __future__ import annotations

from panem_shared.content.schemas import Location


def has_location_access(*, job_title: str | None, has_position: bool, location: Location) -> bool:
    """FR-LOC-3. Item-based access (`access_items`) needs inventory, which
    doesn't exist before Phase 2, so it's treated as never satisfied here —
    a restricted item-gated location is inaccessible to everyone until then,
    which is the safe direction to fail in.

    `has_position` generalizes what used to be a single `is_victor` check:
    holding *any* staff-granted `Position` (Victor, Gamemaker, Governor)
    grants the same restricted-location access a Victor always had.

    `location.access_jobs` (a list of `jobs.yaml` catalog ids) can't
    reliably match a free-typed `Character.job_title` anymore since the
    job rework -- this check is kept for the rare case a player happened
    to type exactly one of those ids, but `has_position` (or staff simply
    moving the character somewhere via `/staff`) is the real access path
    now for a job-gated location."""
    if not location.restricted:
        return True
    if has_position:
        return True
    return job_title is not None and job_title in location.access_jobs
