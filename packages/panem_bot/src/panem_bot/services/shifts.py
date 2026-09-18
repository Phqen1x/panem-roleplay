"""Shift resolution and job eligibility (Spec FR-JOB-3/4/9, FR-PRX-7).

`panem_sim.systems.jobs` opens and misses/fires shifts inside the tick
loop; everything here resolves a shift a player actually did something
about -- played `/work`'s minigame (or its no-Activity coin-flip
fallback), or earned RP credit by proxying in the right scene -- which
happens outside the tick loop, on the bot's own DB session.

Everything below except `resolve_illicit_heat`/`can_earn_rp_credit_
anywhere`/`meets_rp_credit` lives in `panem_shared.shifts` now, not here,
re-exported so every existing call site (`shifts_svc.resolve_shift_game
(...)`) keeps working unchanged -- `panem_api`'s dashboard Work tab and
`/work` minigame result endpoint need the same job-eligibility/wage
logic `panem_bot`'s own `/work` does and can't depend on `panem_bot` to
get it. The old per-option `resolve_shift`, and the `/job apply|list|
quit`/`check_can_apply`/`check_promotion_eligible` functions that went
with the `jobs.yaml` catalog it read, are retired along with that
catalog -- a player's job is now a free-typed `Character.job_title` +
`shift_phase`, set at character creation and changed only by staff
(`/staff give job`), not applied for or quit by the player.
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.db.models import Character, DistrictState
from panem_shared.enums import Position
from panem_shared.jail import (
    resolve_illicit_heat as _resolve_illicit_heat,
)
from panem_shared.shifts import (
    ShiftOutcome as ShiftOutcome,
)
from panem_shared.shifts import (
    already_worked_this_tick as already_worked_this_tick,
)
from panem_shared.shifts import (
    apply_shift_outcome as apply_shift_outcome,
)
from panem_shared.shifts import (
    has_job as has_job,
)
from panem_shared.shifts import (
    illicit_shift_output as illicit_shift_output,
)
from panem_shared.shifts import (
    market_multiplier_for_district as market_multiplier_for_district,
)
from panem_shared.shifts import (
    market_wage_multiplier as market_wage_multiplier,
)
from panem_shared.shifts import (
    open_adhoc_shift_override as open_adhoc_shift_override,
)
from panem_shared.shifts import (
    resolve_shift_game as resolve_shift_game,
)
from panem_shared.shifts import (
    start_shift_game as start_shift_game,
)


def can_earn_rp_credit_anywhere(character: Character) -> bool:
    """A Gamemaker's RP-credit shift completion (FR-PRX-7) isn't tied to
    being physically in a job's workplace scene -- the same "any place"
    privilege `open_adhoc_shift_override` gives `/work` itself."""
    return Position.GAMEMAKER.value in character.positions


def meets_rp_credit(content: str) -> bool:
    """FR-PRX-7: whether a proxied message is long enough to count as
    working a shift. Whether the *scene* is the right one is the caller's
    job -- this only checks length."""
    return len(content) >= constants.RP_CREDIT_MIN_CHARS


async def resolve_illicit_heat(
    session: AsyncSession,
    *,
    character: Character,
    district_id: int,
    current_tick: int,
    lost: bool,
    rng: random.Random | None = None,
) -> bool:
    """Thin session-fetching wrapper around `panem_shared.jail.
    resolve_illicit_heat` (the actual heat/arrest logic, shared with
    `panem_api`'s `/work` minigame result endpoint) -- looks up
    `district_id`'s `DistrictState` row for the arrest-consequence
    pressure bump and crackdown check, then delegates. Returns whether
    an arrest happened, for `/work`'s reply text."""
    district_row = await session.get(DistrictState, district_id)
    return _resolve_illicit_heat(
        character, district_row, lost=lost, current_tick=current_tick, rng=rng
    )
