"""Catalog job lookups (`data/jobs.yaml`) -- NPCs only.

Player characters no longer pick a job from this catalog (free-typed
`Character.job_title` + `shift_phase` instead, see `panem_bot.services.
shifts` and `panem_bot.cogs.characters`); NPCs still do, so these thin
lookups stay for `residents.py`'s NPC display and `/staff job list`'s
NPC-job visibility. `session` is unused now that there's no more DB-backed
`JobOverride` layered on top -- kept in the signature so existing call
sites don't need to change.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import Job


async def get_all_jobs(session: AsyncSession, content: ContentBundle) -> dict[str, Job]:
    return dict(content.jobs)


async def jobs_for_district(
    session: AsyncSession, content: ContentBundle, district_id: int
) -> list[Job]:
    return [job for job in content.jobs.values() if job.district == district_id]


async def get_job(session: AsyncSession, content: ContentBundle, job_id: str) -> Job | None:
    return content.jobs.get(job_id)
