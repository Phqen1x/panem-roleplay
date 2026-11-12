"""Reaction events (Spec "Reaction Events") -- a randomly triggered pop-up
with minimal detail and up to 3 choices (`ReactionChoice`: `GOOD`/
`NEUTRAL`/`EVIL`), answered within `constants.REACTION_EVENT_CHOICE_
SECONDS` real seconds or not at all. `SCENARIOS` is code-authored, not
content-YAML, the same choice `panem_shared.personal_events`'s lost-item
flavor makes -- a prompt plus three short button labels is easier to
read and review as Python than as a new catalog file+schema+loader.

`apply_reaction_choice` is the one place `Character.archetype_score`
moves. Per Spec: a consistent run of one side builds toward being seen as
that archetype; picking the opposite once doesn't erase the streak but
does cost real ground (the ordinary, full delta -- no separate "reset"
mechanic); picking neutral pulls the score toward 0 by a *fraction*
rather than a fixed amount, so it softens an extreme stance less harshly
than flipping to the opposite would, without ever being free.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.db.models import Character, ReactionEventLog
from panem_shared.enums import ReactionChoice
from panem_shared.errors import NotAllowed


@dataclass(frozen=True, slots=True)
class ReactionOption:
    choice: ReactionChoice
    label: str


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    prompt: str
    options: tuple[ReactionOption, ...]


SCENARIOS: dict[str, Scenario] = {
    s.id: s
    for s in (
        Scenario(
            id="falling_crate",
            prompt=(
                "A market stall's crate tips and a stranger's groceries scatter across the "
                "street. You're the only one close enough to react before the crowd closes in."
            ),
            options=(
                ReactionOption(ReactionChoice.GOOD, "Help them gather it all up"),
                ReactionOption(ReactionChoice.NEUTRAL, "Step around and keep walking"),
                ReactionOption(ReactionChoice.EVIL, "Grab what you can before anyone notices"),
            ),
        ),
        Scenario(
            id="overheard_confession",
            prompt=(
                "Through a thin wall, you catch someone admitting to something they clearly "
                "don't want known. They haven't noticed you yet."
            ),
            options=(
                ReactionOption(ReactionChoice.GOOD, "Let them know gently, so they can come clean"),
                ReactionOption(ReactionChoice.NEUTRAL, "Pretend you heard nothing"),
                ReactionOption(ReactionChoice.EVIL, "File it away to use against them later"),
            ),
        ),
        Scenario(
            id="lost_child",
            prompt=(
                "A small child is crying alone near the square, clearly separated from whoever "
                "they came with. People are starting to stare instead of stopping."
            ),
            options=(
                ReactionOption(ReactionChoice.GOOD, "Stay with them until you find their people"),
                ReactionOption(
                    ReactionChoice.NEUTRAL, "Point someone else toward them and move on"
                ),
                ReactionOption(ReactionChoice.EVIL, "Ignore it -- not your problem"),
            ),
        ),
        Scenario(
            id="unattended_stall",
            prompt=(
                "A vendor steps away from their stall for just a moment, leaving the till "
                "unwatched and in full view."
            ),
            options=(
                ReactionOption(ReactionChoice.GOOD, "Keep an eye on it until they're back"),
                ReactionOption(ReactionChoice.NEUTRAL, "Not your stall, not your business"),
                ReactionOption(ReactionChoice.EVIL, "Take the chance while it's there"),
            ),
        ),
        Scenario(
            id="rival_stumbles",
            prompt=(
                "Someone who's been openly unkind to you trips hard in front of a crowd, "
                "dropping everything they were carrying."
            ),
            options=(
                ReactionOption(ReactionChoice.GOOD, "Help them up anyway"),
                ReactionOption(ReactionChoice.NEUTRAL, "Let them sort themselves out"),
                ReactionOption(ReactionChoice.EVIL, "Laugh along with everyone else watching"),
            ),
        ),
    )
}


def pick_scenario(rng: random.Random) -> Scenario:
    return rng.choice(list(SCENARIOS.values()))


def archetype_label(score: float) -> str:
    if score >= constants.ARCHETYPE_GOOD_THRESHOLD:
        return "Good"
    if score <= constants.ARCHETYPE_EVIL_THRESHOLD:
        return "Evil"
    return "Neutral"


def apply_reaction_choice(character: Character, choice: ReactionChoice | None) -> float:
    """Mutates and returns `character.archetype_score`. `choice=None`
    (the pop-up timed out unanswered) leaves it untouched entirely."""
    if choice is None:
        return character.archetype_score
    if choice == ReactionChoice.GOOD:
        character.archetype_score += constants.ARCHETYPE_GOOD_DELTA
    elif choice == ReactionChoice.EVIL:
        character.archetype_score += constants.ARCHETYPE_EVIL_DELTA
    else:
        character.archetype_score -= character.archetype_score * constants.ARCHETYPE_NEUTRAL_PULL
    character.archetype_score = max(
        constants.ARCHETYPE_SCORE_MIN, min(constants.ARCHETYPE_SCORE_MAX, character.archetype_score)
    )
    return character.archetype_score


async def trigger(
    session: AsyncSession, *, character: Character, current_tick: int, rng: random.Random
) -> ReactionEventLog:
    """Logs a fresh, unanswered pop-up -- `panem_bot.cogs.reactions`'s
    trigger loop calls this right before posting the 3-button view/DM;
    `respond` (or the view timing out) is what fills in `choice` later."""
    scenario = pick_scenario(rng)
    log = ReactionEventLog(
        character_id=character.id,
        scenario_id=scenario.id,
        choice=None,
        tick=current_tick,
        archetype_score_after=character.archetype_score,
    )
    session.add(log)
    return log


async def latest_pending(
    session: AsyncSession, *, character_id: int, now: dt.datetime
) -> ReactionEventLog | None:
    """The character's own most recent still-answerable pop-up, if any --
    `created_at` (real wall-clock time, not sim ticks: the answer window
    is `constants.REACTION_EVENT_CHOICE_SECONDS` real seconds) must still
    be within that window. Backs `/reaction respond`, a command fallback
    for answering one without relying on the DM'd view's buttons still
    being clickable (lost in a muted DM, a stale message, ...)."""
    cutoff = now - dt.timedelta(seconds=constants.REACTION_EVENT_CHOICE_SECONDS)
    result = await session.execute(
        select(ReactionEventLog)
        .where(
            ReactionEventLog.character_id == character_id,
            ReactionEventLog.choice.is_(None),
            ReactionEventLog.created_at >= cutoff,
        )
        .order_by(ReactionEventLog.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def respond(
    session: AsyncSession, *, character: Character, log: ReactionEventLog, choice: ReactionChoice
) -> float:
    """Applies `choice` to `character.archetype_score` and stamps the log
    row. Raises `NotAllowed` if something else (the view's own button
    callback, most likely) already answered it first. Returns the new
    score."""
    if log.choice is not None:
        raise NotAllowed("reaction_already_answered")
    new_score = apply_reaction_choice(character, choice)
    log.choice = choice.value
    log.archetype_score_after = new_score
    return new_score
