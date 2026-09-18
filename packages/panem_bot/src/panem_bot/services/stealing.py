"""`/steal`/`/burgle` -- pickpocketing and burglary (contraband system).

The initial skill check -- did the lift/break-in itself go undetected --
is either a roll here (`STEAL_FROM_*_BASE_SUCCESS`/`BURGLE_BASE_SUCCESS`,
used when no `ACTIVITY_PUBLIC_URL` is configured or the player hits
Skip) or the pickpocket/lockpick Activity minigame's own result
(`panem_api`'s crime-attempt result endpoint calling
`panem_shared.stealing.apply_steal_outcome`/`apply_burgle_outcome`
directly). Either way, a failure has two further rolls, mirroring the
spec's "successfully steal without alerting them, or with alerting
them... escape or get caught": `STEAL_ALERT_PROB` decides whether the
mark notices at all (a clean miss otherwise), and `STEAL_ESCAPE_BASE_
PROB` decides whether an alerted character gets away before
peacekeepers catch up. Only a real catch carries any consequence.
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot.services import jail as jail_svc
from panem_shared import constants
from panem_shared.db.models import Character, DistrictState, Npc, Property
from panem_shared.stealing import (
    STEAL_PRESSURE_DELTA as STEAL_PRESSURE_DELTA,
)
from panem_shared.stealing import (
    StealResult,
    StealVictim,
    apply_burgle_outcome,
    apply_steal_outcome,
)
from panem_shared.stealing import (
    check_can_burgle as check_can_burgle,
)
from panem_shared.stealing import (
    check_can_steal as check_can_steal,
)


async def resolve_steal(
    session: AsyncSession,
    *,
    character: Character,
    victim: StealVictim,
    district_id: int,
    current_tick: int,
    rng: random.Random,
) -> StealResult:
    check_can_steal(character, victim, current_tick)
    character.last_steal_tick = current_tick
    district_row = await session.get(DistrictState, district_id)
    return await roll_and_apply_steal(
        session,
        character=character,
        victim=victim,
        district_row=district_row,
        current_tick=current_tick,
        rng=rng,
    )


async def roll_and_apply_steal(
    session: AsyncSession,
    *,
    character: Character,
    victim: StealVictim,
    district_row: DistrictState | None,
    current_tick: int,
    rng: random.Random,
) -> StealResult:
    """The RNG-fallback skill check (no `ACTIVITY_PUBLIC_URL` configured,
    or the player hits Skip) -- split out from `resolve_steal` so the
    `/steal` cog can call this directly for an already-validated,
    already-cooldown-set attempt (an Activity launch validates and sets
    `last_steal_tick` once, up front; re-running `check_can_steal` at
    Skip time would wrongly reject its own just-set cooldown)."""
    is_npc = isinstance(victim, Npc)
    base_success = (
        constants.STEAL_FROM_NPC_BASE_SUCCESS
        if is_npc
        else constants.STEAL_FROM_PLAYER_BASE_SUCCESS
    )
    base_success = jail_svc.crackdown_good_odds(base_success, district_row, current_tick)
    success = rng.random() < base_success
    return await apply_steal_outcome(
        session,
        character=character,
        victim=victim,
        district_row=district_row,
        current_tick=current_tick,
        success=success,
        rng=rng,
    )


async def resolve_burgle(
    session: AsyncSession,
    *,
    character: Character,
    house: Property,
    current_tick: int,
    rng: random.Random,
    owner: Character | None = None,
) -> StealResult:
    """The house-burglary counterpart to `resolve_steal` -- same alert/
    escape/caught shape, `BURGLE_BASE_SUCCESS` odds (flatly harder, no
    owner to reuse `/steal`'s per-target tiers off of), and a payout
    from the house's own value instead of a person's wallet
    (`BURGLE_YIELD_FRACTION` of `suggested_price`, capped at `BURGLE_
    YIELD_CAP`) rather than debiting anyone."""
    check_can_burgle(character, house, current_tick, owner=owner)
    character.last_steal_tick = current_tick
    district_row = await session.get(DistrictState, house.district_id)
    return await roll_and_apply_burgle(
        session,
        character=character,
        house_value=house.suggested_price,
        district_row=district_row,
        current_tick=current_tick,
        rng=rng,
    )


async def roll_and_apply_burgle(
    session: AsyncSession,
    *,
    character: Character,
    house_value: float,
    district_row: DistrictState | None,
    current_tick: int,
    rng: random.Random,
) -> StealResult:
    """`roll_and_apply_steal`'s burglary counterpart -- see its docstring
    for why this is split out from `resolve_burgle`."""
    success_prob = jail_svc.crackdown_good_odds(
        constants.BURGLE_BASE_SUCCESS, district_row, current_tick
    )
    success = rng.random() < success_prob
    return await apply_burgle_outcome(
        session,
        character=character,
        house_value=house_value,
        district_row=district_row,
        current_tick=current_tick,
        success=success,
        rng=rng,
    )
