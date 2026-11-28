"""Staged profile edits, awaiting staff approval.

Lives in `panem_shared.profile_edits` (same reasoning as every other
`panem_shared` move this session) because the web dashboard will need
this too and `panem_api` cannot import `panem_bot`. Re-exported here for
every existing call site.
"""

from __future__ import annotations

from panem_shared.profile_edits import apply_staged_profile_edit as apply_staged_profile_edit
from panem_shared.profile_edits import check_can_stage_profile_edit as check_can_stage_profile_edit
from panem_shared.profile_edits import discard_staged_profile_edit as discard_staged_profile_edit
from panem_shared.profile_edits import profile_edit_diff as profile_edit_diff
from panem_shared.profile_edits import stage_profile_edit as stage_profile_edit
