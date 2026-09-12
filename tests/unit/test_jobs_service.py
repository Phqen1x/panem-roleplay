from __future__ import annotations

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


NEW_JOB_KWARGS = {
    "title": "Baker",
    "workplace": "square",
    "wage": 13,
    "shift_phase": "morning",
    "slots": 6,
}


class TestSetJobFields:
    async def test_adds_new_job(self, db_session):
        content = make_content()
        job = await jobs_svc.set_job_fields(
            db_session,
            content=content,
            job_id="baker",
            district_id=12,
            staff_discord_id=1,
            **NEW_JOB_KWARGS,
        )
        assert job.id == "baker"
        assert job.district == 12
        assert job.title == "Baker"
        assert job.legal is True
        assert len(job.options) == 3
        assert job.options[0].label == "Option 1"

    async def test_new_job_requires_core_fields(self, db_session):
        content = make_content()
        with pytest.raises(ValidationFailed):
            await jobs_svc.set_job_fields(
                db_session,
                content=content,
                job_id="baker",
                district_id=12,
                staff_discord_id=1,
                title="Baker",
            )

    async def test_edits_existing_job_keep_unset_fields(self, db_session):
        content = make_content()
        job = await jobs_svc.set_job_fields(
            db_session,
            content=content,
            job_id="miner",
            district_id=12,
            staff_discord_id=1,
            title="Head Miner",
        )
        assert job.title == "Head Miner"
        assert job.wage == 14  # unset field kept from the YAML baseline
        assert job.workplace == "mine"
        assert job.options[0].label == "a"  # options untouched by job set

        merged = await jobs_svc.get_all_jobs(db_session, content)
        assert merged["miner"].title == "Head Miner"

    async def test_clears_optional_text_field(self, db_session):
        content = make_content()
        await jobs_svc.set_job_fields(
            db_session,
            content=content,
            job_id="miner",
            district_id=12,
            staff_discord_id=1,
            foreman_npc_id="foreman_bob",
        )
        job = await jobs_svc.set_job_fields(
            db_session,
            content=content,
            job_id="miner",
            district_id=12,
            staff_discord_id=1,
            foreman_npc_id="none",
        )
        assert job.foreman_npc_id is None

    async def test_clears_optional_numeric_field(self, db_session):
        content = make_content()
        await jobs_svc.set_job_fields(
            db_session,
            content=content,
            job_id="miner",
            district_id=12,
            staff_discord_id=1,
            min_reputation=10,
        )
        job = await jobs_svc.set_job_fields(
            db_session,
            content=content,
            job_id="miner",
            district_id=12,
            staff_discord_id=1,
            min_reputation=-1,
        )
        assert job.min_reputation is None

    async def test_produces_json_round_trips(self, db_session):
        content = make_content()
        job = await jobs_svc.set_job_fields(
            db_session,
            content=content,
            job_id="miner",
            district_id=12,
            staff_discord_id=1,
            produces_json='{"coal": 8}',
        )
        assert job.produces == {"coal": 8}

    async def test_rejects_bad_json(self, db_session):
        content = make_content()
        with pytest.raises(ValidationFailed):
            await jobs_svc.set_job_fields(
                db_session,
                content=content,
                job_id="miner",
                district_id=12,
                staff_discord_id=1,
                produces_json="not json",
            )

    async def test_rejects_unknown_district(self, db_session):
        content = make_content()
        with pytest.raises(ValidationFailed):
            await jobs_svc.set_job_fields(
                db_session,
                content=content,
                job_id="baker",
                district_id=99,
                staff_discord_id=1,
                **NEW_JOB_KWARGS,
            )

    async def test_rejects_workplace_not_in_district(self, db_session):
        content = make_content()
        with pytest.raises(ValidationFailed):
            await jobs_svc.set_job_fields(
                db_session,
                content=content,
                job_id="baker",
                district_id=12,
                staff_discord_id=1,
                **{**NEW_JOB_KWARGS, "workplace": "nonexistent"},
            )


class TestSetJobOption:
    async def test_rejects_unknown_job(self, db_session):
        content = make_content()
        with pytest.raises(ValidationFailed):
            await jobs_svc.set_job_option(
                db_session,
                content=content,
                job_id="nonexistent",
                slot=1,
                staff_discord_id=1,
                label="x",
            )

    async def test_patches_single_slot_leaving_others_untouched(self, db_session):
        content = make_content()
        job = await jobs_svc.set_job_option(
            db_session,
            content=content,
            job_id="miner",
            slot=2,
            staff_discord_id=1,
            label="Work carefully",
            risk=0.1,
        )
        assert job.options[1].label == "Work carefully"
        assert job.options[1].risk == 0.1
        assert job.options[0].label == "a"  # slot 1 untouched
        assert job.options[2].label == "a"  # slot 3 untouched

    async def test_fills_in_placeholder_option_on_new_job(self, db_session):
        content = make_content()
        await jobs_svc.set_job_fields(
            db_session,
            content=content,
            job_id="baker",
            district_id=12,
            staff_discord_id=1,
            **NEW_JOB_KWARGS,
        )
        job = await jobs_svc.set_job_option(
            db_session,
            content=content,
            job_id="baker",
            slot=1,
            staff_discord_id=1,
            label="Bake extra",
            output_mult=1.2,
            risk=0.05,
            rep_delta=1,
            wage_mult=1.0,
        )
        assert job.options[0].label == "Bake extra"
        assert job.options[1].label == "Option 2"

    async def test_clears_risk_effect(self, db_session):
        content = make_content()
        await jobs_svc.set_job_option(
            db_session,
            content=content,
            job_id="miner",
            slot=1,
            staff_discord_id=1,
            risk_effect_json='{"health": -5}',
        )
        job = await jobs_svc.set_job_option(
            db_session,
            content=content,
            job_id="miner",
            slot=1,
            staff_discord_id=1,
            risk_effect_json="none",
        )
        assert job.options[0].risk_effect is None


class TestRemoveJob:
    async def test_removes_yaml_job_from_merged_view(self, db_session):
        content = make_content()
        assert "miner" in (await jobs_svc.get_all_jobs(db_session, content))
        await jobs_svc.remove_job(db_session, job_id="miner", staff_discord_id=1)
        assert "miner" not in (await jobs_svc.get_all_jobs(db_session, content))

    async def test_removes_staff_added_job(self, db_session):
        content = make_content()
        await jobs_svc.set_job_fields(
            db_session,
            content=content,
            job_id="baker",
            district_id=12,
            staff_discord_id=1,
            **NEW_JOB_KWARGS,
        )
        await jobs_svc.remove_job(db_session, job_id="baker", staff_discord_id=1)
        assert "baker" not in (await jobs_svc.get_all_jobs(db_session, content))

    async def test_re_adding_after_removal_works(self, db_session):
        content = make_content()
        await jobs_svc.remove_job(db_session, job_id="miner", staff_discord_id=1)
        await jobs_svc.set_job_fields(
            db_session,
            content=content,
            job_id="miner",
            district_id=12,
            staff_discord_id=1,
            **NEW_JOB_KWARGS,
        )
        merged = await jobs_svc.get_all_jobs(db_session, content)
        assert merged["miner"].title == "Baker"


class TestJobsForDistrict:
    async def test_filters_by_district(self, db_session):
        content = make_content()
        await jobs_svc.set_job_fields(
            db_session,
            content=content,
            job_id="baker",
            district_id=12,
            staff_discord_id=1,
            **NEW_JOB_KWARGS,
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
