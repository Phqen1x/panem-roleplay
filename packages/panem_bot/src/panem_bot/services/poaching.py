"""Illegal hunting/gathering: re-exports `panem_shared.poaching` so every
existing `poaching_svc.X(...)` call site in `panem_bot` keeps working
unchanged -- the logic itself lives in `panem_shared` because
`panem_api`'s dashboard Crime tab needs it too and can't depend on
`panem_bot` to get it (same reasoning as every other move this
session)."""

from __future__ import annotations

from panem_shared.poaching import (
    PEACEKEEPER_PRESSURE_DELTA as PEACEKEEPER_PRESSURE_DELTA,
)
from panem_shared.poaching import (
    PoachResult as PoachResult,
)
from panem_shared.poaching import (
    check_can_poach as check_can_poach,
)
from panem_shared.poaching import (
    resolve_outskirts as resolve_outskirts,
)
from panem_shared.poaching import (
    resolve_poach as resolve_poach,
)
