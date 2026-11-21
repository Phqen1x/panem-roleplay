"""Rumors -- overheard gossip, scraps of paper, whatever (Spec "Rumors")
that may or may not be true. `Rumor.reliability_pct` starts wherever
`spawn_rumor` rolls it and only ever moves via a logged `investigate` call
-- nothing else reads or writes it, and a low reliability never means a
rumor is actually false, only that *investigating it so far* points that
way (Spec is explicit about this distinction).

`_FLAVOR` is code-authored, not content-YAML, the same "a handful of short
strings is easier to read as Python than as a new catalog" choice
`panem_shared.personal_events`/`panem_shared.reactions` already make. A
rumor optionally ties to a real upcoming `related_kind`/`related_id` (a
`WeatherEvent` still `FORECAST`, a `GlobalEvent` still `SCHEDULED`) so a
reliable one can plausibly give advance warning (Spec: "prior warning to
collect materials"/"advance notice to practice") -- `panem_bot.cogs.
weather`'s poll and `/staff event create` are what actually pass one in;
a rumor spawned with neither is free-floating flavor, allowed to be
fabricated start to finish.
"""

from __future__ import annotations

import random

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.db.models import Rumor, RumorInvestigation
from panem_shared.errors import NotAllowed, NotFound

_OVERHEARD_FLAVOR = (
    "a vague warning about something coming this way",
    "talk of strange goings-on near the edge of town",
    "a secondhand account that doesn't quite add up",
    "a story that's been getting more dramatic with each retelling",
    "a tip nobody will admit to actually having seen themselves",
)
_FOUND_PAPER_FLAVOR = (
    "a damp, half-legible scrap of paper on the ground",
    "a torn notice pinned up and then mostly torn back down",
    "a note someone clearly dropped in a hurry",
)
_INVESTIGATE_SUPPORTING = (
    "{name} nods along -- that tracks with what they've heard too.",
    "{name} swears it's true, and says who told them.",
    "A second source says almost the same thing, unprompted.",
)
_INVESTIGATE_DOUBTING = (
    "{name} laughs it off -- sounds made up to them.",
    "{name} hasn't heard anything like that, and seems sure they would have.",
    "Someone else tells a completely different, incompatible version.",
)
_INVESTIGATE_EMPTY_HANDED = (
    "You ask around, but come up empty -- nobody's talking today.",
    "No one you find has anything useful to say about it.",
    "You lose the thread before finding anyone worth asking.",
)


def random_flavor(rng: random.Random, *, from_paper: bool) -> tuple[str, str]:
    """Returns `(text, origin)` for a freshly spawned, free-floating
    rumor."""
    if from_paper:
        return rng.choice(_FOUND_PAPER_FLAVOR), "a scrap of paper on the ground"
    return rng.choice(_OVERHEARD_FLAVOR), "overheard"


async def spawn_rumor(
    session: AsyncSession,
    *,
    district_id: int,
    current_tick: int,
    rng: random.Random,
    text: str | None = None,
    origin: str = "overheard",
    related_kind: str | None = None,
    related_id: int | None = None,
    expires_tick: int | None = None,
) -> Rumor:
    if text is None:
        text, origin = random_flavor(rng, from_paper=origin == "paper")
    reliability = rng.randint(*constants.RUMOR_BASE_RELIABILITY_RANGE)
    if expires_tick is None:
        # Default: 1 simulation month. An explicit `expires_tick` lets a
        # caller tie a rumor's lifespan to whatever it's actually about
        # instead (e.g. not outliving the `WeatherEvent` it's rumoring).
        expires_tick = current_tick + constants.DAYS_PER_MONTH * constants.TICKS_PER_DAY
    rumor = Rumor(
        district_id=district_id,
        text=text,
        reliability_pct=reliability,
        related_kind=related_kind,
        related_id=related_id,
        origin=origin,
        spawned_tick=current_tick,
        expires_tick=expires_tick,
    )
    session.add(rumor)
    return rumor


async def district_rumors(session: AsyncSession, district_id: int) -> list[Rumor]:
    result = await session.execute(
        select(Rumor).where(Rumor.district_id == district_id).order_by(Rumor.spawned_tick.desc())
    )
    return list(result.scalars().all())


async def investigation_count(session: AsyncSession, rumor_id: int) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(RumorInvestigation)
        .where(RumorInvestigation.rumor_id == rumor_id)
    )
    return result.scalar_one()


async def investigate(
    session: AsyncSession,
    *,
    character_id: int,
    rumor: Rumor,
    current_tick: int,
    rng: random.Random,
    success: bool = True,
) -> RumorInvestigation:
    """Raises `NotAllowed` once `constants.RUMOR_MAX_INVESTIGATIONS` logged
    investigations already exist for this rumor -- past that it's
    considered settled. Otherwise rolls a signed shift within
    `constants.RUMOR_INVESTIGATE_SHIFT_RANGE`, clamps `reliability_pct` to
    0-100, and logs the exact delta applied alongside a flavor finding.

    `success` is the dashboard's own "Spot the Odd One Out" minigame
    result (`static/games/odd_one_out.js`, via `static/rumor_investigate.
    js`) -- losing it still consumes one of the capped investigation
    slots (asking around takes the attempt whether or not it pans out),
    but skips the reliability roll entirely and logs a flavor "came up
    empty" finding instead. Discord's `/rumor investigate` has no
    minigame of its own and always passes the default `True`, the same
    "always playable without one" shape every other dashboard-only
    minigame's Discord-side command keeps."""
    count = await investigation_count(session, rumor.id)
    if count >= constants.RUMOR_MAX_INVESTIGATIONS:
        raise NotAllowed("rumor_investigate_capped")

    if not success:
        finding = rng.choice(_INVESTIGATE_EMPTY_HANDED)
        log = RumorInvestigation(
            rumor_id=rumor.id,
            character_id=character_id,
            finding=finding,
            reliability_delta=0,
            tick=current_tick,
        )
        session.add(log)
        return log

    supports = rng.random() < (rumor.reliability_pct / 100)
    magnitude = rng.randint(*constants.RUMOR_INVESTIGATE_SHIFT_RANGE)
    delta = magnitude if supports else -magnitude
    before = rumor.reliability_pct
    rumor.reliability_pct = max(0, min(100, before + delta))
    actual_delta = rumor.reliability_pct - before

    template = rng.choice(_INVESTIGATE_SUPPORTING if supports else _INVESTIGATE_DOUBTING)
    finding = template.format(name="A local")

    log = RumorInvestigation(
        rumor_id=rumor.id,
        character_id=character_id,
        finding=finding,
        reliability_delta=actual_delta,
        tick=current_tick,
    )
    session.add(log)
    return log


async def get_rumor(session: AsyncSession, rumor_id: int) -> Rumor:
    rumor = await session.get(Rumor, rumor_id)
    if rumor is None:
        raise NotFound("rumor_not_found")
    return rumor


async def delete_rumor(session: AsyncSession, rumor_id: int) -> None:
    """A player's own "Delete" action on the Rumors tab -- unlike
    `panem_shared.bulletin`'s per-viewer hide, a rumor has no per-viewer
    anything (investigating one is district-wide, not character-owned),
    so this is a real delete of the one shared row, for everyone. Deletes
    its `RumorInvestigation` log rows first -- that FK has no `ondelete=
    CASCADE` in the migration that created it, so leaving them would
    violate the constraint."""
    await session.execute(delete(RumorInvestigation).where(RumorInvestigation.rumor_id == rumor_id))
    rumor = await session.get(Rumor, rumor_id)
    if rumor is not None:
        await session.delete(rumor)


async def expire_stale_rumors(session: AsyncSession, current_tick: int) -> list[int]:
    """`panem_bot.cogs.rumors`'s spawn loop sweep -- a rumor past its
    `expires_tick` (1 simulation month by default, see `spawn_rumor`)
    just quietly disappears rather than sitting stale on the board
    forever. Unlike `panem_shared.personal_events.expire_stale_chains`,
    there's no status column to flip to EXPIRED: a `Rumor` is either
    live or gone, so this really deletes the row (and its investigation
    log rows, same as `delete_rumor`). Returns the deleted ids."""
    result = await session.execute(
        select(Rumor.id).where(Rumor.expires_tick.is_not(None), Rumor.expires_tick <= current_tick)
    )
    expired_ids = [row[0] for row in result.all()]
    for rumor_id in expired_ids:
        await delete_rumor(session, rumor_id)
    return expired_ids
