from __future__ import annotations

import json

import pytest

from panem_bot.errors import ValidationFailed
from panem_bot.services import jobs as jobs_svc
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    Job,
    JobOption,
    Location,
)


def make_content() -> ContentBundle:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Station", kind="station"),
        Location(id="mine", name="Mine", kind="workplace"),
    ]
    coords = {loc.id: (0, 0) for loc in locations}
    district = District(
        id=12,
        name="District Twelve",
        industry="coal",
        locations=locations,
        culture=DistrictCulture(),
        population_base=100,
        map=DistrictMap(image="x.png", width=10, height=10, location_coords=coords),
    )
    option = JobOption(label="a", output_mult=1.0, risk=0.0, rep_delta=0, wage_mult=1.0)
    miner = Job(
        id="miner",
        district=12,
        title="Miner",
        workplace="mine",
        wage=14,
        shift_phase="morning",
        slots=40,
        legal=True,
        options=[option, option, option],
    )
    return ContentBundle(districts={12: district}, goods={}, jobs={"miner": miner}, routes=[])


VALID_JOB_JSON = json.dumps(
    {
        "title": "Baker",
        "workplace": "square",
        "wage": 13,
        "shift_phase": "morning",
        "slots": 6,
        "legal": True,
        "options": [
            {"label": "a", "output_mult": 1.0, "risk": 0.0, "rep_delta": 0, "wage_mult": 1.0},
            {"label": "b", "output_mult": 1.0, "risk": 0.0, "rep_delta": 0, "wage_mult": 1.0},
            {"label": "c", "output_mult": 1.0, "risk": 0.0, "rep_delta": 0, "wage_mult": 1.0},
        ],
    }
)


class TestSetJob:
    async def test_adds_new_job(self, db_session):
        content = make_content()
        job = await jobs_svc.set_job(
            db_session,
            content=content,
            job_id="baker",
            district_id=12,
            raw_json=VALID_JOB_JSON,
            staff_discord_id=1,
        )
        assert job.id == "baker"
        assert job.district == 12
        assert job.title == "Baker"

    async def test_overrides_existing_yaml_job(self, db_session):
        content = make_content()
        job = await jobs_svc.set_job(
            db_session,
            content=content,
            job_id="miner",
            district_id=12,
            raw_json=VALID_JOB_JSON,
            staff_discord_id=1,
        )
        assert job.title == "Baker"  # miner's title replaced

        merged = await jobs_svc.get_all_jobs(db_session, content)
        assert merged["miner"].title == "Baker"

    async def test_rejects_bad_json(self, db_session):
        content = make_content()
        with pytest.raises(ValidationFailed):
            await jobs_svc.set_job(
                db_session,
                content=content,
                job_id="baker",
                district_id=12,
                raw_json="not json",
                staff_discord_id=1,
            )

    async def test_rejects_wrong_option_count(self, db_session):
        content = make_content()
        bad = json.dumps(
            {
                "title": "Baker",
                "workplace": "square",
                "wage": 13,
                "shift_phase": "morning",
                "slots": 6,
                "options": [{"label": "only one"}],
            }
        )
        with pytest.raises(ValidationFailed):
            await jobs_svc.set_job(
                db_session,
                content=content,
                job_id="baker",
                district_id=12,
                raw_json=bad,
                staff_discord_id=1,
            )

    async def test_rejects_unknown_district(self, db_session):
        content = make_content()
        with pytest.raises(ValidationFailed):
            await jobs_svc.set_job(
                db_session,
                content=content,
                job_id="baker",
                district_id=99,
                raw_json=VALID_JOB_JSON,
                staff_discord_id=1,
            )

    async def test_rejects_workplace_not_in_district(self, db_session):
        content = make_content()
        bad_workplace = json.dumps({**json.loads(VALID_JOB_JSON), "workplace": "nonexistent"})
        with pytest.raises(ValidationFailed):
            await jobs_svc.set_job(
                db_session,
                content=content,
                job_id="baker",
                district_id=12,
                raw_json=bad_workplace,
                staff_discord_id=1,
            )


class TestRemoveJob:
    async def test_removes_yaml_job_from_merged_view(self, db_session):
        content = make_content()
        assert "miner" in (await jobs_svc.get_all_jobs(db_session, content))
        await jobs_svc.remove_job(db_session, job_id="miner", staff_discord_id=1)
        assert "miner" not in (await jobs_svc.get_all_jobs(db_session, content))

    async def test_removes_staff_added_job(self, db_session):
        content = make_content()
        await jobs_svc.set_job(
            db_session,
            content=content,
            job_id="baker",
            district_id=12,
            raw_json=VALID_JOB_JSON,
            staff_discord_id=1,
        )
        await jobs_svc.remove_job(db_session, job_id="baker", staff_discord_id=1)
        assert "baker" not in (await jobs_svc.get_all_jobs(db_session, content))

    async def test_re_adding_after_removal_works(self, db_session):
        content = make_content()
        await jobs_svc.remove_job(db_session, job_id="miner", staff_discord_id=1)
        await jobs_svc.set_job(
            db_session,
            content=content,
            job_id="miner",
            district_id=12,
            raw_json=VALID_JOB_JSON,
            staff_discord_id=1,
        )
        merged = await jobs_svc.get_all_jobs(db_session, content)
        assert merged["miner"].title == "Baker"


class TestJobsForDistrict:
    async def test_filters_by_district(self, db_session):
        content = make_content()
        await jobs_svc.set_job(
            db_session,
            content=content,
            job_id="baker",
            district_id=12,
            raw_json=VALID_JOB_JSON,
            staff_discord_id=1,
        )
        jobs = await jobs_svc.jobs_for_district(db_session, content, 12)
        assert {j.id for j in jobs} == {"miner", "baker"}
        assert await jobs_svc.jobs_for_district(db_session, content, 3) == []


class TestGetJob:
    async def test_returns_none_when_missing(self, db_session):
        content = make_content()
        assert await jobs_svc.get_job(db_session, content, "nonexistent") is None

    async def test_returns_yaml_job(self, db_session):
        content = make_content()
        job = await jobs_svc.get_job(db_session, content, "miner")
        assert job is not None
        assert job.title == "Miner"
