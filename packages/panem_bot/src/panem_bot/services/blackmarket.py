"""Black market trading: re-exports `panem_shared.blackmarket` so every
existing `blackmarket_svc.X(...)` call site in `panem_bot` keeps working
unchanged -- the logic itself lives in `panem_shared` because
`panem_api`'s dashboard Market tab needs it too and can't depend on
`panem_bot` to get it (same reasoning as every other move this
session)."""

from __future__ import annotations

from panem_shared.blackmarket import (
    BLACKMARKET_PRESSURE_DELTA as BLACKMARKET_PRESSURE_DELTA,
)
from panem_shared.blackmarket import (
    TRUSTED_STANCES as TRUSTED_STANCES,
)
from panem_shared.blackmarket import (
    BlackMarketTradeResult as BlackMarketTradeResult,
)
from panem_shared.blackmarket import (
    buy as buy,
)
from panem_shared.blackmarket import (
    check_can_trade as check_can_trade,
)
from panem_shared.blackmarket import (
    get_price as get_price,
)
from panem_shared.blackmarket import (
    resolve_black_market_location as resolve_black_market_location,
)
from panem_shared.blackmarket import (
    resolve_fence as resolve_fence,
)
from panem_shared.blackmarket import (
    resolve_good as resolve_good,
)
from panem_shared.blackmarket import (
    sell as sell,
)
