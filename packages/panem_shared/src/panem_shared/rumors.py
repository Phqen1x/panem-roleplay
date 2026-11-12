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

from sqlalchemy import func, select
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
) -> Rumor:
    if text is None:
        text, origin = random_flavor(rng, from_paper=origin == "paper")
    reliability = rng.randint(*constants.RUMOR_BASE_RELIABILITY_RANGE)
    rumor = Rumor(
        district_id=district_id,
        text=text,
        reliability_pct=reliability,
        related_kind=related_kind,
        related_id=related_id,
        origin=origin,
        spawned_tick=current_tick,
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
) -> RumorInvestigation:
    """Raises `NotAllowed` once `constants.RUMOR_MAX_INVESTIGATIONS` logged
    investigations already exist for this rumor -- past that it's
    considered settled. Otherwise rolls a signed shift within
    `constants.RUMOR_INVESTIGATE_SHIFT_RANGE`, clamps `reliability_pct` to
    0-100, and logs the exact delta applied alongside a flavor finding."""
    count = await investigation_count(session, rumor.id)
    if count >= constants.RUMOR_MAX_INVESTIGATIONS:
        raise NotAllowed("rumor_investigate_capped")

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
