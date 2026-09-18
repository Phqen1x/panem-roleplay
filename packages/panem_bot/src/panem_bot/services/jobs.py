"""Catalog job lookups (`data/jobs.yaml`) -- NPCs only.

Moved to `panem_shared.jobs` (same reasoning as every other `panem_shared`
move this session); re-exported here for every existing call site.
"""

from __future__ import annotations

from panem_shared.jobs import get_all_jobs as get_all_jobs
from panem_shared.jobs import get_job as get_job
from panem_shared.jobs import jobs_for_district as jobs_for_district
