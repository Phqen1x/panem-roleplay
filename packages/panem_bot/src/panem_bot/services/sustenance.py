"""`/eat`/`/drink`/`/entertain` (Simulation mode's proactive meter relief).

Moved to `panem_shared.sustenance` (same reasoning as every other
`panem_shared` move this session) since a future dashboard needs analog
would need it too and `panem_api` cannot import `panem_bot`. Re-exported
here for `panem_bot.cogs.needs`.
"""

from __future__ import annotations

from panem_shared.sustenance import check_can_drink as check_can_drink
from panem_shared.sustenance import check_can_eat as check_can_eat
from panem_shared.sustenance import check_can_entertain as check_can_entertain
from panem_shared.sustenance import drink as drink
from panem_shared.sustenance import eat as eat
from panem_shared.sustenance import entertain as entertain
