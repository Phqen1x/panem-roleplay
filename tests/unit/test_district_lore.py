from __future__ import annotations

import pytest

from panem_shared import district_lore
from panem_shared.db.models import DistrictLore, DistrictLorePerson
from panem_shared.errors import NotFound, ValidationFailed


class TestUpsertLore:
    async def test_creates_a_new_row(self, db_session):
        row = await district_lore.upsert_lore(
            db_session,
            4,
            classification="outlier",
            adjectives=["Brash", "Flashy"],
            accent_notes="Drops final consonants.",
            urban_rural_notes="Mostly rural fishing villages.",
            academy_name=None,
            academy_notes="",
            games_history="Won twice in the last decade.",
            regime_notes="",
            opinions={"1": "Resents their wealth"},
            misc_notes="",
            updated_by=123,
        )
        assert row.district_id == 4
        assert row.classification == "outlier"
        assert row.adjectives == ["Brash", "Flashy"]
        assert row.opinions == {"1": "Resents their wealth"}
        assert row.updated_by == 123

    async def test_updates_an_existing_row_in_place(self, db_session):
        await district_lore.upsert_lore(
            db_session,
            2,
            classification="inner",
            adjectives=["Arrogant"],
            accent_notes="",
            urban_rural_notes="",
            academy_name="The Iron Academy",
            academy_notes="",
            games_history="",
            regime_notes="",
            opinions={},
            misc_notes="",
            updated_by=1,
        )
        row = await district_lore.upsert_lore(
            db_session,
            2,
            classification="inner",
            adjectives=["Arrogant", "Disciplined"],
            accent_notes="",
            urban_rural_notes="",
            academy_name="The Iron Academy",
            academy_notes="Trains from age six.",
            games_history="",
            regime_notes="",
            opinions={},
            misc_notes="",
            updated_by=2,
        )
        loaded = await db_session.get(DistrictLore, 2)
        assert loaded is row
        assert loaded.adjectives == ["Arrogant", "Disciplined"]
        assert loaded.academy_notes == "Trains from age six."
        assert loaded.updated_by == 2

    async def test_rejects_an_out_of_range_district_id(self, db_session):
        with pytest.raises(ValidationFailed) as exc_info:
            await district_lore.upsert_lore(
                db_session,
                13,
                classification=None,
                adjectives=[],
                accent_notes="",
                urban_rural_notes="",
                academy_name=None,
                academy_notes="",
                games_history="",
                regime_notes="",
                opinions={},
                misc_notes="",
                updated_by=1,
            )
        assert exc_info.value.reason_key == "invalid_district_id"

    async def test_rejects_an_invalid_classification(self, db_session):
        with pytest.raises(ValidationFailed) as exc_info:
            await district_lore.upsert_lore(
                db_session,
                1,
                classification="capitol",
                adjectives=[],
                accent_notes="",
                urban_rural_notes="",
                academy_name=None,
                academy_notes="",
                games_history="",
                regime_notes="",
                opinions={},
                misc_notes="",
                updated_by=1,
            )
        assert exc_info.value.reason_key == "district_lore_invalid_classification"

    async def test_rejects_too_many_adjectives(self, db_session):
        with pytest.raises(ValidationFailed) as exc_info:
            await district_lore.upsert_lore(
                db_session,
                1,
                classification=None,
                adjectives=[f"word{i}" for i in range(20)],
                accent_notes="",
                urban_rural_notes="",
                academy_name=None,
                academy_notes="",
                games_history="",
                regime_notes="",
                opinions={},
                misc_notes="",
                updated_by=1,
            )
        assert exc_info.value.reason_key == "district_lore_too_many_adjectives"

    async def test_rejects_an_out_of_range_opinion_key(self, db_session):
        with pytest.raises(ValidationFailed) as exc_info:
            await district_lore.upsert_lore(
                db_session,
                1,
                classification=None,
                adjectives=[],
                accent_notes="",
                urban_rural_notes="",
                academy_name=None,
                academy_notes="",
                games_history="",
                regime_notes="",
                opinions={"99": "nonsense"},
                misc_notes="",
                updated_by=1,
            )
        assert exc_info.value.reason_key == "district_lore_invalid_opinion_district"

    async def test_blank_opinion_values_are_dropped(self, db_session):
        row = await district_lore.upsert_lore(
            db_session,
            1,
            classification=None,
            adjectives=[],
            accent_notes="",
            urban_rural_notes="",
            academy_name=None,
            academy_notes="",
            games_history="",
            regime_notes="",
            opinions={"2": "  ", "3": "Trade partners"},
            misc_notes="",
            updated_by=1,
        )
        assert row.opinions == {"3": "Trade partners"}


class TestDistrictLorePeople:
    async def test_creates_and_lists_a_person(self, db_session):
        row = await district_lore.create_person(
            db_session,
            district_id=4,
            role="victor",
            name="Old Finch",
            character_id=None,
            is_active=False,
            notes="Retired after the 60th Games.",
        )
        assert row.id is not None
        people = await district_lore.list_people(db_session, 4)
        assert [p.name for p in people] == ["Old Finch"]

    async def test_rejects_an_invalid_role(self, db_session):
        with pytest.raises(ValidationFailed) as exc_info:
            await district_lore.create_person(
                db_session,
                district_id=1,
                role="president",
                name="Someone",
                character_id=None,
                is_active=True,
                notes="",
            )
        assert exc_info.value.reason_key == "district_lore_invalid_person_role"

    async def test_update_person_can_clear_character_link(self, db_session):
        row = await district_lore.create_person(
            db_session,
            district_id=1,
            role="mentor",
            name="Sabine",
            character_id=None,
            is_active=True,
            notes="",
        )
        updated = await district_lore.update_person(
            db_session,
            row.id,
            role=None,
            name=None,
            character_id=None,
            character_id_set=False,
            is_active=False,
            notes="Stepped down.",
        )
        assert updated.is_active is False
        assert updated.notes == "Stepped down."

    async def test_update_missing_person_raises_not_found(self, db_session):
        with pytest.raises(NotFound):
            await district_lore.update_person(
                db_session,
                999999,
                role=None,
                name=None,
                character_id=None,
                character_id_set=False,
                is_active=None,
                notes=None,
            )

    async def test_delete_person(self, db_session):
        row = await district_lore.create_person(
            db_session,
            district_id=1,
            role="victor",
            name="Temp",
            character_id=None,
            is_active=True,
            notes="",
        )
        await district_lore.delete_person(db_session, row.id)
        assert await db_session.get(DistrictLorePerson, row.id) is None


class TestPromptSummary:
    def test_none_lore_yields_none(self):
        assert district_lore.prompt_summary(None) is None

    def test_empty_lore_yields_none(self):
        row = DistrictLore(district_id=1)
        assert district_lore.prompt_summary(row) is None

    def test_combines_available_fields(self):
        row = DistrictLore(
            district_id=1,
            classification="inner",
            adjectives=["Arrogant", "Disciplined"],
            urban_rural_notes="Wealthy urban core.",
            academy_name="The Iron Academy",
        )
        summary = district_lore.prompt_summary(row)
        assert summary is not None
        assert "inner district" in summary
        assert "Arrogant" in summary
        assert "Iron Academy" in summary

    def test_truncates_long_summaries(self):
        from panem_shared import constants

        row = DistrictLore(
            district_id=1,
            classification="outlier",
            urban_rural_notes="x" * 500,
        )
        summary = district_lore.prompt_summary(row)
        assert summary is not None
        assert len(summary) <= constants.DISTRICT_LORE_PROMPT_MAX_LEN
        assert summary.endswith("…")
