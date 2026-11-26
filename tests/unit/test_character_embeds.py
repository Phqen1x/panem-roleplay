"""`panem_bot.cogs.characters`'s embed-building helpers -- `_job_value`
(shared formatting for "what this character does for work") and
`_build_profile_embed` (`/profile`'s embed: everything submitted at
creation, as opposed to `_build_character_embed`'s staff/announcement
framing or `/character status`'s live gameplay stats).
"""

from __future__ import annotations

from panem_bot.cogs.characters import _build_profile_embed, _job_value
from panem_shared.db.models import Character


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Katniss",
        age=16,
        gender="female",
        appearance="Braided hair, olive skin.",
        backstory="Hunts in the woods to feed her family.",
        rp_mode="simulation",
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = 1
    return character


class TestJobValue:
    def test_no_job_title_as_desired_reads_as_an_application(self):
        character = make_character(job_title=None)
        assert _job_value(character, desired=True) == "-- (Story mode)"

    def test_no_job_title_as_occupation_reads_as_already_set(self):
        character = make_character(job_title=None)
        assert _job_value(character, desired=False) == "None (Story mode)"

    def test_job_title_includes_shift_and_omits_illicit_suffix(self):
        character = make_character(job_title="Baker", shift_phase="morning", job_is_illicit=False)
        assert _job_value(character, desired=False) == "Baker (Morning shift)"

    def test_illicit_job_gets_the_illicit_suffix(self):
        character = make_character(job_title="Smuggler", shift_phase="night", job_is_illicit=True)
        assert _job_value(character, desired=False) == "Smuggler (Night shift) [illicit]"


class TestBuildProfileEmbed:
    def test_includes_every_submitted_field(self):
        character = make_character(
            name="Katniss",
            age=16,
            gender="female",
            appearance="Braided hair, olive skin.",
            backstory="Hunts in the woods to feed her family.",
            job_title="Baker",
            shift_phase="morning",
            avatar_url="https://example.com/katniss.png",
        )

        embed = _build_profile_embed(character, district_name="District 12")

        assert embed.title == "Katniss's Profile"
        field_values = {field.name: field.value for field in embed.fields}
        assert field_values["District"] == "District 12"
        assert field_values["Age"] == "16"
        assert field_values["Gender"] == "Female"
        assert field_values["RP Mode"] == "Simulation"
        assert field_values["Occupation"] == "Baker (Morning shift)"
        assert field_values["Appearance"] == "Braided hair, olive skin."
        assert field_values["Backstory"] == "Hunts in the woods to feed her family."
        assert embed.image.url == "https://example.com/katniss.png"

    def test_no_avatar_url_leaves_the_embed_imageless(self):
        character = make_character(avatar_url=None)
        embed = _build_profile_embed(character, district_name="District 12")
        assert embed.image.url is None

    def test_unset_gender_reads_as_a_dash(self):
        character = make_character(gender=None)
        embed = _build_profile_embed(character, district_name="District 12")
        field_values = {field.name: field.value for field in embed.fields}
        assert field_values["Gender"] == "-"

    def test_story_mode_occupation_reads_as_none(self):
        character = make_character(job_title=None, rp_mode="story")
        embed = _build_profile_embed(character, district_name="District 12")
        field_values = {field.name: field.value for field in embed.fields}
        assert field_values["Occupation"] == "None (Story mode)"
