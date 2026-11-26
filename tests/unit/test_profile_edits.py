"""`panem_shared.profile_edits` -- staged profile edits
(`Character.pending_profile_*`) awaiting staff approval, `/profile
edit`'s counterpart to `panem_shared.rp_modes`'s staged mode switch."""

from __future__ import annotations

import pytest

from panem_bot.errors import NotAllowed, ValidationFailed
from panem_bot.services import profile_edits as profile_edits_svc
from panem_shared.db.models import Character, User
from panem_shared.enums import CharacterStatus, RpMode


async def make_character(session, **overrides: object) -> Character:
    user = User(discord_id=overrides.pop("discord_id", 111))
    session.add(user)
    await session.flush()
    defaults: dict[str, object] = dict(
        user_id=user.id,
        district_id=1,
        current_district_id=1,
        name="Katniss",
        age=16,
        gender="female",
        appearance="Braided hair.",
        backstory="Hunts in the woods.",
        job_title="Baker",
        status=CharacterStatus.APPROVED.value,
        rp_mode=RpMode.SIMULATION.value,
        avatar_url="https://example.com/katniss.png",
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    session.add(character)
    await session.flush()
    return character


def _edit_kwargs(character: Character, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        name=character.name,
        age=character.age,
        gender=character.gender,
        appearance=character.appearance,
        backstory=character.backstory,
        job_title=character.job_title,
        avatar_url=character.avatar_url,
    )
    base.update(overrides)
    return base


class TestCheckCanStageProfileEdit:
    async def test_allows_when_nothing_pending(self, db_session):
        character = await make_character(db_session)
        profile_edits_svc.check_can_stage_profile_edit(character)  # no raise

    async def test_refuses_when_already_pending(self, db_session):
        character = await make_character(db_session, pending_profile_name="Someone Else")
        with pytest.raises(NotAllowed):
            profile_edits_svc.check_can_stage_profile_edit(character)


class TestStageProfileEdit:
    async def test_stages_a_real_change(self, db_session):
        character = await make_character(db_session)
        await profile_edits_svc.stage_profile_edit(
            db_session, character, **_edit_kwargs(character, backstory="A new story entirely.")
        )
        assert character.pending_profile_name == "Katniss"
        assert character.pending_profile_backstory == "A new story entirely."
        # The real fields are untouched until approval.
        assert character.backstory == "Hunts in the woods."

    async def test_refuses_when_nothing_actually_changed(self, db_session):
        character = await make_character(db_session)
        with pytest.raises(ValidationFailed):
            await profile_edits_svc.stage_profile_edit(
                db_session, character, **_edit_kwargs(character)
            )

    async def test_refuses_an_invalid_age(self, db_session):
        character = await make_character(db_session)
        with pytest.raises(ValidationFailed):
            await profile_edits_svc.stage_profile_edit(
                db_session, character, **_edit_kwargs(character, age=5)
            )

    async def test_refuses_a_name_already_taken_by_another_character(self, db_session):
        await make_character(db_session, name="Peeta", discord_id=222)
        character = await make_character(db_session, name="Katniss", discord_id=333)
        with pytest.raises(ValidationFailed):
            await profile_edits_svc.stage_profile_edit(
                db_session, character, **_edit_kwargs(character, name="Peeta")
            )

    async def test_allows_keeping_its_own_current_name(self, db_session):
        # Resubmitting the character's own unchanged name must not trip
        # the uniqueness check against itself.
        character = await make_character(db_session)
        await profile_edits_svc.stage_profile_edit(
            db_session, character, **_edit_kwargs(character, age=17)
        )
        assert character.pending_profile_name == "Katniss"

    async def test_refuses_an_invalid_avatar_url(self, db_session):
        character = await make_character(db_session)
        with pytest.raises(ValidationFailed):
            await profile_edits_svc.stage_profile_edit(
                db_session, character, **_edit_kwargs(character, avatar_url="not-a-url")
            )


class TestProfileEditDiff:
    async def test_lists_only_the_fields_that_changed(self, db_session):
        character = await make_character(db_session)
        await profile_edits_svc.stage_profile_edit(
            db_session,
            character,
            **_edit_kwargs(character, age=17, backstory="A new story entirely."),
        )
        diff = profile_edits_svc.profile_edit_diff(character)
        fields = {label: (before, after) for label, before, after in diff}
        assert fields["Age"] == ("16", "17")
        assert fields["Backstory"] == ("Hunts in the woods.", "A new story entirely.")
        assert "Name" not in fields
        assert "Appearance" not in fields

    async def test_blank_before_value_displays_as_a_dash(self, db_session):
        character = await make_character(db_session, appearance="", job_title=None, rp_mode="story")
        await profile_edits_svc.stage_profile_edit(
            db_session, character, **_edit_kwargs(character, appearance="Now has a description.")
        )
        diff = profile_edits_svc.profile_edit_diff(character)
        fields = {label: (before, after) for label, before, after in diff}
        assert fields["Appearance"] == ("-", "Now has a description.")


class TestApplyStagedProfileEdit:
    async def test_copies_every_staged_field_onto_the_real_ones(self, db_session):
        character = await make_character(db_session)
        await profile_edits_svc.stage_profile_edit(
            db_session,
            character,
            **_edit_kwargs(
                character, age=17, gender="nonbinary", backstory="A new story entirely."
            ),
        )
        profile_edits_svc.apply_staged_profile_edit(character)
        assert character.age == 17
        assert character.gender == "nonbinary"
        assert character.backstory == "A new story entirely."
        assert character.pending_profile_name is None
        assert character.pending_profile_age is None


class TestDiscardStagedProfileEdit:
    async def test_clears_staging_without_touching_the_real_fields(self, db_session):
        character = await make_character(db_session)
        await profile_edits_svc.stage_profile_edit(
            db_session, character, **_edit_kwargs(character, backstory="A new story entirely.")
        )
        profile_edits_svc.discard_staged_profile_edit(character)
        assert character.pending_profile_name is None
        assert character.backstory == "Hunts in the woods."
