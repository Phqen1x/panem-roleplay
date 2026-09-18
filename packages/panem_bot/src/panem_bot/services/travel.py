"""Character travel, both within a district (Spec FR-LOC-2/3, CMD-14/15)
and across districts by train (FR-LOC-7/8/9, CMD-14b).

Moved to `panem_shared.travel` (same reasoning as every other `panem_shared`
move this session) because the web dashboard's Travel tab needs this too and
`panem_api` cannot import `panem_bot`. Nothing here has ever depended on
discord.py; this was a package-boundary accident, not a real coupling.
Re-exported here for every existing call site.
"""

from __future__ import annotations

from panem_shared.travel import check_can_travel as check_can_travel
from panem_shared.travel import check_can_travel_district as check_can_travel_district
from panem_shared.travel import is_free_route as is_free_route
from panem_shared.travel import is_free_victor_route as is_free_victor_route
from panem_shared.travel import place as place
from panem_shared.travel import resolve_location as resolve_location
from panem_shared.travel import resolve_station as resolve_station
from panem_shared.travel import spend_transport as spend_transport
