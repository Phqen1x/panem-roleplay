"""Injuries, afflictions, and death (`AfflictionType`/`CharacterAffliction`,
`panem_shared.db.models`).

Two very different ways an affliction reaches a character, per the
feature's own scoping:

- **Manual** (Life mode only): the player runs `/character afflict`/
  `/character die` themselves, behind a confirmation, and types the cause.
  `apply_manual_affliction`/`mark_dead` below.
- **Automatic** (Simulation mode only): the nightly needs system
  (`panem_sim.systems.needs`) applies/clears these on its own once a
  stat crosses a staff-defined threshold, no player action involved.
  `apply_auto_afflictions`/`apply_auto_death` below.

Story-mode characters are excluded from this system entirely -- neither
path ever touches one (callers are expected to have already refused the
command/skipped the character before reaching this module; nothing here
re-checks for Story specifically beyond the RpMode.LIFE/SIMULATION checks
each path already needs).

`check_and_cure` is the one function that applies to *any* mode: a
Life-mode character's manually-applied affliction must still be curable
even though Life has no passive meter decay of its own -- see the
feature's plan for the reasoning. It's called both from the nightly sim
system and from `/eat`/`/drink`/`/entertain`/`/sleep` so a cure resolves
the moment its condition is met, not just once a (Simulation-only) night
passes.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from panem_shared import constants
from panem_shared.db.models import AfflictionType, Character, CharacterAffliction
from panem_shared.enums import AfflictionSource, CharacterStatus, RpMode
from panem_shared.errors import NotAllowed, ValidationFailed

AUTO_DEATH_MESSAGE = "Succumbed to their injuries."


async def _active_afflictions(
    session: AsyncSession, character_id: int
) -> list[CharacterAffliction]:
    result = await session.execute(
        select(CharacterAffliction)
        .where(
            CharacterAffliction.character_id == character_id,
            CharacterAffliction.cured_at.is_(None),
        )
        .options(selectinload(CharacterAffliction.affliction_type))
    )
    return list(result.scalars())


async def apply_manual_affliction(
    session: AsyncSession, *, character: Character, affliction_type: AfflictionType, cause: str
) -> CharacterAffliction:
    """`/character afflict` -- Life mode only. `cause` is the player's own
    explanation of what happened and how; required (unlike the auto-applied
    path, which has none)."""
    if character.rp_mode != RpMode.LIFE.value:
        raise NotAllowed("affliction_wrong_mode")
    cause = cause.strip()
    if not cause:
        raise ValidationFailed("affliction_cause_required")
    row = CharacterAffliction(
        character_id=character.id,
        affliction_type_id=affliction_type.id,
        cause=cause,
        source=AfflictionSource.MANUAL.value,
    )
    session.add(row)
    return row


def mark_dead(character: Character, cause: str | None) -> None:
    """`/character die` -- Life mode only. Simulation-mode death goes
    through `apply_auto_death` instead, which supplies its own fixed
    message rather than taking a player-entered one."""
    if character.rp_mode != RpMode.LIFE.value:
        raise NotAllowed("death_wrong_mode")
    if character.status == CharacterStatus.DEAD.value:
        raise NotAllowed("character_already_dead")
    character.status = CharacterStatus.DEAD.value
    character.death_cause = cause.strip() if cause and cause.strip() else None


async def check_and_cure(session: AsyncSession, character: Character) -> list[CharacterAffliction]:
    """Cures (stamps `cured_at`) every active, non-permanent affliction on
    `character` whose `cure_stat` has risen above its `cure_threshold`.
    Returns the newly-cured rows. Mode-agnostic -- see the module
    docstring."""
    now = dt.datetime.now(dt.UTC)
    cured: list[CharacterAffliction] = []
    for row in await _active_afflictions(session, character.id):
        affliction_type = row.affliction_type
        if affliction_type.is_permanent or affliction_type.cure_stat is None:
            continue
        stat_value = getattr(character, affliction_type.cure_stat)
        if stat_value >= affliction_type.cure_threshold:
            row.cured_at = now
            cured.append(row)
    return cured


async def apply_auto_afflictions(
    session: AsyncSession, *, character: Character, catalog: list[AfflictionType]
) -> list[CharacterAffliction]:
    """Simulation mode only -- applies any `AfflictionType` in `catalog`
    whose `auto_apply_stat` has fallen beneath its `auto_apply_threshold`
    and isn't already active on `character`. Returns the newly-applied
    rows. Never applied to Life/Story characters -- "automatically added
    ... ONLY [to] sim characters"."""
    if character.rp_mode != RpMode.SIMULATION.value:
        return []
    active = await _active_afflictions(session, character.id)
    already_active_type_ids = {row.affliction_type_id for row in active}
    applied: list[CharacterAffliction] = []
    for affliction_type in catalog:
        if affliction_type.auto_apply_stat is None or affliction_type.id in already_active_type_ids:
            continue
        stat_value = getattr(character, affliction_type.auto_apply_stat)
        if stat_value <= affliction_type.auto_apply_threshold:
            row = CharacterAffliction(
                character_id=character.id,
                affliction_type_id=affliction_type.id,
                cause=None,
                source=AfflictionSource.AUTO.value,
            )
            session.add(row)
            applied.append(row)
    return applied


def apply_auto_death(character: Character) -> bool:
    """Simulation mode only -- marks `character` dead once `health` bottoms
    out at `constants.HEALTH_MIN`. Returns whether it happened (so the
    caller can decide whether to narrate it). A no-op for a character
    that's already dead or not in Simulation mode."""
    if character.rp_mode != RpMode.SIMULATION.value:
        return False
    if character.status == CharacterStatus.DEAD.value:
        return False
    if character.health > constants.HEALTH_MIN:
        return False
    character.status = CharacterStatus.DEAD.value
    character.death_cause = AUTO_DEATH_MESSAGE
    return True
