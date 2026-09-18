"""Jailing (contraband system): re-exports `panem_shared.jail` so every
existing `jail_svc.X(...)` call site in `panem_bot` keeps working
unchanged -- the logic itself lives in `panem_shared` because `panem_api`
needs it too (the `/work` minigame result endpoint for `resolve_illicit_
heat`, and now the dashboard's Jail tab for everything else) and can't
depend on `panem_bot` to get it (same reasoning as `panem_shared.
stealing`/`panem_shared.characters`)."""

from __future__ import annotations

from panem_shared.jail import (
    apply_lockpick_attempt as apply_lockpick_attempt,
)
from panem_shared.jail import (
    attempt_lockpick as attempt_lockpick,
)
from panem_shared.jail import (
    bail_cost as bail_cost,
)
from panem_shared.jail import (
    check_can_attempt_lockpick as check_can_attempt_lockpick,
)
from panem_shared.jail import (
    check_is_jailed as check_is_jailed,
)
from panem_shared.jail import (
    commit_to_jail as commit_to_jail,
)
from panem_shared.jail import (
    crackdown_bad_odds as crackdown_bad_odds,
)
from panem_shared.jail import (
    crackdown_good_odds as crackdown_good_odds,
)
from panem_shared.jail import (
    is_crackdown_active as is_crackdown_active,
)
from panem_shared.jail import (
    lockpick_difficulty as lockpick_difficulty,
)
from panem_shared.jail import (
    pay_bail as pay_bail,
)
from panem_shared.jail import (
    release_from_jail as release_from_jail,
)
