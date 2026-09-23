"""RP-mode switching and the Life-mode crime opt-out toggle.

Moved to `panem_shared.rp_modes` (same reasoning as every other
`panem_shared` move this session) because the web dashboard's mode-switch
panel needs this too and `panem_api` cannot import `panem_bot`. Re-exported
here for every existing call site.
"""

from __future__ import annotations

from panem_shared.rp_modes import check_can_switch_mode as check_can_switch_mode
from panem_shared.rp_modes import check_can_toggle_crime as check_can_toggle_crime
from panem_shared.rp_modes import next_eligible_crime_toggle_at as next_eligible_crime_toggle_at
from panem_shared.rp_modes import next_eligible_switch_at as next_eligible_switch_at
from panem_shared.rp_modes import switch_mode as switch_mode
from panem_shared.rp_modes import toggle_crime as toggle_crime
