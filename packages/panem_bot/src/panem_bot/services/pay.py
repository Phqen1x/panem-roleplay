"""`/pay` -- instant player-to-player money transfer.

Moved to `panem_shared.pay` (same reasoning as every other `panem_shared`
move this session) since a future dashboard equivalent needs it too and
`panem_api` cannot import `panem_bot`. Re-exported here for `panem_bot.
cogs.trades`.
"""

from __future__ import annotations

from panem_shared.pay import check_can_pay as check_can_pay
from panem_shared.pay import pay as pay
