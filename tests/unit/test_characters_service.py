from __future__ import annotations

import datetime as dt

import pytest

from panem_bot.errors import LimitReached, NotAllowed, ValidationFailed
from panem_bot.services import characters as characters_svc
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    DistrictQuota,
    Location,
)
from panem_shared.db.models import Character
from panem_shared.enums import CharacterStatus


def make_district(*, id_: int = 12, extra_locations: list[Location] | None = None) -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
        Location(id="mine", name="Mine Entrance", kind="workplace", job_ids=["miner"]),
    ] + (extra_locations or [])
    coords = {loc.id: (0, 0) for loc in locations}
    return District(
        id=id_,
        name=f"District {id_}",
        industry="coal",
        produces=["coal"],
        imports=[],
        quota=DistrictQuota(good="coal", amount=100),
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=100, height=100, location_coords=coords),
    )


async def make_user(session, discord_id: int = 111):
    return await characters_svc.get_or_create_user(session, discord_id)


class TestValidateCharacterFields:
    def test_valid(self):
        characters_svc.validate_character_fields(
            name="Katniss", age=16, appearance="Braid", backstory="Hunts."
        )

    def test_bad_name_numbers(self):
        with pytest.raises(ValidationFailed):
            characters_svc.validate_character_fields(
                name="1234", age=16, appearance="", backstory=""
            )

    def test_bad_name_too_long(self):
        with pytest.raises(ValidationFailed):
            characters_svc.validate_character_fields(
                name="A" * 33, age=16, appearance="", backstory=""
            )

    @pytest.mark.parametrize("age", [11, 81, 0, -1])
    def test_bad_age(self, age):
        with pytest.raises(ValidationFailed):
            characters_svc.validate_character_fields(
                name="Ok", age=age, appearance="", backstory=""
            )

    def test_appearance_too_long(self):
        with pytest.raises(ValidationFailed):
            characters_svc.validate_character_fields(
                name="Ok", age=16, appearance="x" * 401, backstory=""
            )

    def test_backstory_too_long(self):
        with pytest.raises(ValidationFailed):
            characters_svc.validate_character_fields(
                name="Ok", age=16, appearance="", backstory="x" * 1501
            )


class TestValidateAvatarUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/a.png",
            "https://example.com/a.jpg",
            "https://example.com/a.jpeg?x=1",
            "https://example.com/a.webp",
            "https://example.com/a.gif",
        ],
    )
    def test_valid(self, url):
        characters_svc.validate_avatar_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/a.png",  # not https
            "https://example.com/a.txt",  # bad extension
            "https://" + "a" * 512 + ".png",  # too long
        ],
    )
    def test_invalid(self, url):
        with pytest.raises(ValidationFailed):
            characters_svc.validate_avatar_url(url)


class TestValidateProxyTag:
    @pytest.mark.parametrize("tag", ["md", "k", "abcdefghijkl"])
    def test_valid(self, tag):
        characters_svc.validate_proxy_tag(tag)

    @pytest.mark.parametrize("tag", ["", "abcdefghijklm", "/md", "((md"])
    def test_invalid(self, tag):
        with pytest.raises(ValidationFailed):
            characters_svc.validate_proxy_tag(tag)


class TestCreateCharacter:
    async def test_happy_path(self, db_session):
        user = await make_user(db_session)
        character = await characters_svc.create_character(
            db_session,
            user=user,
            district_id=12,
            name="Katniss",
            age=16,
            appearance="",
            backstory="",
            desired_job_id="miner",
            max_characters=3,
        )
        assert character.status == CharacterStatus.PENDING.value
        assert character.job_id == "miner"

    async def test_banned_user_refused(self, db_session):
        user = await make_user(db_session)
        user.banned_at = dt.datetime.now(dt.UTC)
        with pytest.raises(NotAllowed):
            await characters_svc.create_character(
                db_session,
                user=user,
                district_id=12,
                name="Katniss",
                age=16,
                appearance="",
                backstory="",
                desired_job_id=None,
                max_characters=3,
            )

    async def test_limit_reached(self, db_session):
        user = await make_user(db_session)
        for name in ("Alpha", "Beta", "Gamma"):
            await characters_svc.create_character(
                db_session,
                user=user,
                district_id=12,
                name=name,
                age=16,
                appearance="",
                backstory="",
                desired_job_id=None,
                max_characters=3,
            )
        with pytest.raises(LimitReached):
            await characters_svc.create_character(
                db_session,
                user=user,
                district_id=12,
                name="OneTooMany",
                age=16,
                appearance="",
                backstory="",
                desired_job_id=None,
                max_characters=3,
            )

    async def test_invalid_fields_raise_before_insert(self, db_session):
        user = await make_user(db_session)
        with pytest.raises(ValidationFailed):
            await characters_svc.create_character(
                db_session,
                user=user,
                district_id=12,
                name="???",
                age=16,
                appearance="",
                backstory="",
                desired_job_id=None,
                max_characters=3,
            )


class TestApproveCharacter:
    async def test_assigns_job_when_slot_free(self, db_session):
        user = await make_user(db_session)
        district = make_district()
        character = await characters_svc.create_character(
            db_session,
            user=user,
            district_id=district.id,
            name="Katniss",
            age=16,
            appearance="",
            backstory="",
            desired_job_id="miner",
            max_characters=3,
        )
        await characters_svc.approve_character(
            db_session, character, district=district, job_slots={"miner": 1}
        )
        assert character.status == CharacterStatus.APPROVED.value
        assert character.job_id == "miner"
        assert character.money == 40
        assert character.location_id == "square"

    async def test_falls_back_to_unemployed_when_slot_full(self, db_session):
        user = await make_user(db_session)
        district = make_district()
        # fill the one slot with an already-approved character
        taken = await characters_svc.create_character(
            db_session,
            user=await make_user(db_session, discord_id=222),
            district_id=district.id,
            name="Gale",
            age=18,
            appearance="",
            backstory="",
            desired_job_id="miner",
            max_characters=3,
        )
        await characters_svc.approve_character(
            db_session, taken, district=district, job_slots={"miner": 1}
        )

        character = await characters_svc.create_character(
            db_session,
            user=user,
            district_id=district.id,
            name="Katniss",
            age=16,
            appearance="",
            backstory="",
            desired_job_id="miner",
            max_characters=3,
        )
        await characters_svc.approve_character(
            db_session, character, district=district, job_slots={"miner": 1}
        )
        assert character.job_id is None

    async def test_raises_if_not_pending(self, db_session):
        user = await make_user(db_session)
        district = make_district()
        character = await characters_svc.create_character(
            db_session,
            user=user,
            district_id=district.id,
            name="Katniss",
            age=16,
            appearance="",
            backstory="",
            desired_job_id=None,
            max_characters=3,
        )
        await characters_svc.approve_character(
            db_session, character, district=district, job_slots={}
        )
        with pytest.raises(NotAllowed):
            await characters_svc.approve_character(
                db_session, character, district=district, job_slots={}
            )


class TestRetireCharacter:
    async def test_retire_releases_job(self, db_session):
        user = await make_user(db_session)
        district = make_district()
        character = await characters_svc.create_character(
            db_session,
            user=user,
            district_id=district.id,
            name="Katniss",
            age=16,
            appearance="",
            backstory="",
            desired_job_id="miner",
            max_characters=3,
        )
        await characters_svc.approve_character(
            db_session, character, district=district, job_slots={"miner": 1}
        )
        await characters_svc.retire_character(db_session, character)
        assert character.status == CharacterStatus.RETIRED.value
        assert character.job_id is None

    async def test_retire_requires_approved(self, db_session):
        user = await make_user(db_session)
        character = await characters_svc.create_character(
            db_session,
            user=user,
            district_id=12,
            name="Katniss",
            age=16,
            appearance="",
            backstory="",
            desired_job_id=None,
            max_characters=3,
        )
        with pytest.raises(NotAllowed):
            await characters_svc.retire_character(db_session, character)


class TestRejectAndRequestChanges:
    async def test_reject_requires_pending(self, db_session):
        user = await make_user(db_session)
        district = make_district()
        character = await characters_svc.create_character(
            db_session,
            user=user,
            district_id=district.id,
            name="Katniss",
            age=16,
            appearance="",
            backstory="",
            desired_job_id=None,
            max_characters=3,
        )
        await characters_svc.approve_character(
            db_session, character, district=district, job_slots={}
        )
        with pytest.raises(NotAllowed):
            characters_svc.reject_character(character)

    def test_reject_pending(self):
        character = Character(status=CharacterStatus.PENDING.value)
        characters_svc.reject_character(character)
        assert character.status == CharacterStatus.REJECTED.value
