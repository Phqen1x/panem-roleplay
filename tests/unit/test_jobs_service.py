from __future__ import annotations

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


class TestGetAllJobs:
    async def test_returns_the_yaml_catalog(self, db_session):
        content = make_content()
        jobs = await jobs_svc.get_all_jobs(db_session, content)
        assert set(jobs) == {"miner"}
        assert jobs["miner"].title == "Miner"


class TestJobsForDistrict:
    async def test_filters_by_district(self, db_session):
        content = make_content()
        jobs = await jobs_svc.jobs_for_district(db_session, content, 12)
        assert {j.id for j in jobs} == {"miner"}
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
