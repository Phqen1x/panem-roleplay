"""Staff-editable job overrides layered on top of `jobs.yaml`.

Staff manage these via `/staff job set|option|remove|list|show` (no code
change or redeploy needed to add, edit, or remove a job for a district). A
`JobOverride` row with the same id as a YAML job replaces it; a new id adds
a job YAML never defined; `disabled=True` removes it from the merged view
either way.

`/staff job set` edits a job's base fields; `/staff job option` edits one
of its 3 options. Both use patch semantics: any argument left unset keeps
whatever the job already has, so staff only need to pass the fields
they're actually changing. Creating a brand-new job via `job set` seeds it
with 3 placeholder options that `job option` then fills in -- `Job`
requires exactly 3 (FR-JOB-3), so a job can't be saved with fewer.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot.errors import ValidationFailed
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import Job
from panem_shared.db.models import JobOverride


class _Unset:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<unset>"


#: Sentinel distinguishing "argument not passed" (keep existing value) from
#: "argument passed as an explicit clear" (set field back to None).
_UNSET = _Unset()

#: Typed strings staff pass to explicitly blank an optional text/dict field.
_CLEAR_TEXT = "none"

#: Out-of-range sentinel for optional numeric fields that are otherwise
#: constrained to be non-negative (min_reputation, peacekeeper_attention);
#: Discord has no "null" a numeric option can carry, so -1 means "clear".
_CLEAR_NUMBER = -1


def _patch_text(raw: str | None) -> str | _Unset | None:
    if raw is None:
        return _UNSET
    return None if raw.strip().lower() == _CLEAR_TEXT else raw


def _patch_number(raw: float | None) -> float | _Unset | None:
    if raw is None:
        return _UNSET
    return None if raw == _CLEAR_NUMBER else raw


def _patch_json_object(raw: str | None, *, field: str) -> dict[str, Any] | _Unset | None:
    if raw is None:
        return _UNSET
    if raw.strip().lower() == _CLEAR_TEXT:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationFailed("job_bad_json", detail=f"{field}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValidationFailed("job_bad_json", detail=f"{field} must be a JSON object")
    return parsed


def _placeholder_option(n: int) -> dict[str, Any]:
    return {
        "label": f"Option {n}",
        "output_mult": 1.0,
        "risk": 0.0,
        "rep_delta": 0,
        "wage_mult": 1.0,
    }


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


async def _upsert(
    session: AsyncSession,
    content: ContentBundle,
    job_id: str,
    district_id: int,
    payload: dict[str, Any],
    staff_discord_id: int,
) -> Job:
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


async def set_job_fields(
    session: AsyncSession,
    *,
    content: ContentBundle,
    job_id: str,
    district_id: int,
    staff_discord_id: int,
    title: str | None = None,
    workplace: str | None = None,
    wage: float | None = None,
    shift_phase: str | None = None,
    slots: int | None = None,
    legal: bool | None = None,
    min_reputation: float | None = None,
    ladder_next: str | None = None,
    ladder_requirement_json: str | None = None,
    peacekeeper_attention: float | None = None,
    foreman_npc_id: str | None = None,
    produces_json: str | None = None,
) -> Job:
    """Patch a job's base fields (everything but its 3 options, edited
    separately via `set_job_option`). Any argument left as None keeps the
    job's current value when editing; a brand-new `job_id` requires
    title/workplace/wage/shift_phase/slots up front and starts with 3
    placeholder options."""
    baseline = await get_job(session, content, job_id)

    if baseline is None:
        required = {
            "title": title,
            "workplace": workplace,
            "wage": wage,
            "shift_phase": shift_phase,
            "slots": slots,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValidationFailed(
                "job_missing_fields",
                detail=f"new job {job_id!r} needs: {', '.join(missing)}",
            )
        payload: dict[str, Any] = {
            "id": job_id,
            "district": district_id,
            "title": title,
            "workplace": workplace,
            "wage": wage,
            "shift_phase": shift_phase,
            "slots": slots,
            "legal": True if legal is None else legal,
            "options": [_placeholder_option(n) for n in (1, 2, 3)],
        }
    else:
        payload = baseline.model_dump(mode="json", by_alias=True)
        payload["district"] = district_id
        if title is not None:
            payload["title"] = title
        if workplace is not None:
            payload["workplace"] = workplace
        if wage is not None:
            payload["wage"] = wage
        if shift_phase is not None:
            payload["shift_phase"] = shift_phase
        if slots is not None:
            payload["slots"] = slots
        if legal is not None:
            payload["legal"] = legal

    patched_min_rep = _patch_number(min_reputation)
    if patched_min_rep is not _UNSET:
        payload["min_reputation"] = patched_min_rep
    patched_ladder_next = _patch_text(ladder_next)
    if patched_ladder_next is not _UNSET:
        payload["ladder_next"] = patched_ladder_next
    patched_ladder_requirement = _patch_json_object(
        ladder_requirement_json, field="ladder_requirement"
    )
    if patched_ladder_requirement is not _UNSET:
        payload["ladder_requirement"] = patched_ladder_requirement
    patched_pk_attention = _patch_number(peacekeeper_attention)
    if patched_pk_attention is not _UNSET:
        payload["peacekeeper_attention"] = patched_pk_attention
    patched_foreman = _patch_text(foreman_npc_id)
    if patched_foreman is not _UNSET:
        payload["foreman_npc_id"] = patched_foreman
    patched_produces = _patch_json_object(produces_json, field="produces")
    if patched_produces is not _UNSET:
        payload["produces"] = {} if patched_produces is None else patched_produces

    return await _upsert(session, content, job_id, district_id, payload, staff_discord_id)


async def set_job_option(
    session: AsyncSession,
    *,
    content: ContentBundle,
    job_id: str,
    slot: Literal[1, 2, 3],
    staff_discord_id: int,
    label: str | None = None,
    output_mult: float | None = None,
    risk: float | None = None,
    risk_effect_json: str | None = None,
    rep_delta: int | None = None,
    wage_mult: float | None = None,
) -> Job:
    """Patch one of an existing job's 3 options (1-indexed). The job must
    already exist -- create it with `set_job_fields` first, which seeds
    placeholder options this can then fill in."""
    baseline = await get_job(session, content, job_id)
    if baseline is None:
        raise ValidationFailed("job_not_found")

    payload = baseline.model_dump(mode="json", by_alias=True)
    option = dict(payload["options"][slot - 1])
    if label is not None:
        option["label"] = label
    if output_mult is not None:
        option["output_mult"] = output_mult
    if risk is not None:
        option["risk"] = risk
    if rep_delta is not None:
        option["rep_delta"] = rep_delta
    if wage_mult is not None:
        option["wage_mult"] = wage_mult
    patched_risk_effect = _patch_json_object(risk_effect_json, field="risk_effect")
    if patched_risk_effect is not _UNSET:
        option["risk_effect"] = patched_risk_effect
    payload["options"][slot - 1] = option

    return await _upsert(session, content, job_id, baseline.district, payload, staff_discord_id)


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
