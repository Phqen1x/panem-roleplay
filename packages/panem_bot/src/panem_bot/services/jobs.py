"""Staff-editable job overrides layered on top of `jobs.yaml`.

Staff manage these via `/staff job set|remove|list|show` (no code change or
redeploy needed to add, edit, or remove a job for a district). A
`JobOverride` row with the same id as a YAML job replaces it; a new id adds
a job YAML never defined; `disabled=True` removes it from the merged view
either way.
"""

from __future__ import annotations

import json

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot.errors import ValidationFailed
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import Job
from panem_shared.db.models import JobOverride


async def get_all_jobs(session: AsyncSession, content: ContentBundle) -> dict[str, Job]:
    """YAML jobs with DB overrides layered on top; `disabled` rows are removed."""
    merged = dict(content.jobs)
    overrides = (await session.execute(select(JobOverride))).scalars().all()
    for row in overrides:
        if row.disabled:
            merged.pop(row.id, None)
        else:
            # `payload` excludes id/district (stored as real columns on this
            # row); add them back before revalidating into a full Job.
            merged[row.id] = Job.model_validate(
                {**row.payload, "id": row.id, "district": row.district_id}
            )
    return merged


async def jobs_for_district(
    session: AsyncSession, content: ContentBundle, district_id: int
) -> list[Job]:
    all_jobs = await get_all_jobs(session, content)
    return [job for job in all_jobs.values() if job.district == district_id]


async def get_job(session: AsyncSession, content: ContentBundle, job_id: str) -> Job | None:
    return (await get_all_jobs(session, content)).get(job_id)


def _cross_validate(job: Job, content: ContentBundle) -> None:
    district = content.districts.get(job.district)
    if district is None:
        raise ValidationFailed("job_bad_district", detail=f"district {job.district} does not exist")
    location_ids = {loc.id for loc in district.locations}
    if job.workplace not in location_ids:
        raise ValidationFailed(
            "job_bad_workplace",
            detail=f"workplace {job.workplace!r} is not a location in district {job.district}",
        )


async def set_job(
    session: AsyncSession,
    *,
    content: ContentBundle,
    job_id: str,
    district_id: int,
    raw_json: str,
    staff_discord_id: int,
) -> Job:
    """Validate `raw_json` (the job's fields, minus `id`/`district`) against
    the same schema `jobs.yaml` is validated with, cross-check its
    `workplace` against real district content, then upsert."""
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValidationFailed("job_bad_json", detail=str(exc)) from exc
    if not isinstance(payload, dict):
        raise ValidationFailed("job_bad_json", detail="must be a JSON object")

    payload = {**payload, "id": job_id, "district": district_id}
    try:
        job = Job.model_validate(payload)
    except ValidationError as exc:
        raise ValidationFailed("job_schema_invalid", detail=str(exc)) from exc

    _cross_validate(job, content)

    row = await session.get(JobOverride, job_id)
    stored_payload = job.model_dump(mode="json", by_alias=True, exclude={"id", "district"})
    if row is None:
        row = JobOverride(
            id=job_id,
            district_id=district_id,
            payload=stored_payload,
            disabled=False,
            updated_by_discord_id=staff_discord_id,
        )
        session.add(row)
    else:
        row.district_id = district_id
        row.payload = stored_payload
        row.disabled = False
        row.updated_by_discord_id = staff_discord_id
    await session.flush()
    return job


async def remove_job(session: AsyncSession, *, job_id: str, staff_discord_id: int) -> None:
    """Marks the job removed regardless of whether it came from YAML or a
    prior override; never hard-deletes (a YAML job re-appearing after a
    hard delete would silently undo the removal on the next lookup)."""
    row = await session.get(JobOverride, job_id)
    if row is None:
        row = JobOverride(
            id=job_id,
            district_id=0,
            payload={},
            disabled=True,
            updated_by_discord_id=staff_discord_id,
        )
        session.add(row)
    else:
        row.disabled = True
        row.updated_by_discord_id = staff_discord_id
    await session.flush()
