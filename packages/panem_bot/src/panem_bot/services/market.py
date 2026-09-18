"""Market trading: re-exports `panem_shared.market` so every existing
`market_svc.X(...)` call site in `panem_bot` keeps working unchanged --
the logic itself lives in `panem_shared` because `panem_api`'s dashboard
Market tab needs it too and can't depend on `panem_bot` to get it (same
reasoning as every other move this session)."""

from __future__ import annotations

from panem_shared.market import (
    ILLICIT_PRESSURE_DELTA as ILLICIT_PRESSURE_DELTA,
)
from panem_shared.market import (
    TradeResult as TradeResult,
)
from panem_shared.market import (
    buy as buy,
)
from panem_shared.market import (
    check_can_trade as check_can_trade,
)
from panem_shared.market import (
    get_price as get_price,
)
from panem_shared.market import (
    list_inventory as list_inventory,
)
from panem_shared.market import (
    resolve_good as resolve_good,
)
from panem_shared.market import (
    resolve_market_location as resolve_market_location,
)
from panem_shared.market import (
    sell as sell,
)
