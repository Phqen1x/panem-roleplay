from __future__ import annotations

from sqlalchemy import select

from panem_shared.crime_log import list_crime_log, record_crime_log
from panem_shared.db.models import CrimeLog


class TestRecordCrimeLog:
    async def test_writes_a_row_with_the_given_fields(self, db_session):
        await record_crime_log(
            db_session,
            character_id=1,
            kind="steal",
            tick=42,
            success=True,
            caught=False,
            target_name="Mark",
            amount=15,
        )
        row = (await db_session.execute(select(CrimeLog))).scalar_one()
        assert row.character_id == 1
        assert row.kind == "steal"
        assert row.tick == 42
        assert row.success is True
        assert row.caught is False
        assert row.target_name == "Mark"
        assert row.good_name is None
        assert row.amount == 15

    async def test_defaults_target_and_good_name_to_none_and_amount_to_zero(self, db_session):
        await record_crime_log(
            db_session, character_id=1, kind="poach", tick=0, success=False, caught=True
        )
        row = (await db_session.execute(select(CrimeLog))).scalar_one()
        assert row.target_name is None
        assert row.good_name is None
        assert row.amount == 0


class TestListCrimeLog:
    async def test_returns_only_that_character_s_entries_most_recent_first(self, db_session):
        await record_crime_log(
            db_session, character_id=1, kind="steal", tick=1, success=True, caught=False
        )
        await record_crime_log(
            db_session, character_id=2, kind="poach", tick=2, success=True, caught=False
        )
        await record_crime_log(
            db_session, character_id=1, kind="burgle", tick=3, success=False, caught=True
        )

        rows = await list_crime_log(db_session, 1)

        assert [row.kind for row in rows] == ["burgle", "steal"]

    async def test_respects_the_limit(self, db_session):
        for tick in range(5):
            await record_crime_log(
                db_session, character_id=1, kind="steal", tick=tick, success=True, caught=False
            )
        rows = await list_crime_log(db_session, 1, limit=2)
        assert len(rows) == 2
        assert [row.tick for row in rows] == [4, 3]

    async def test_empty_for_a_character_with_no_history(self, db_session):
        assert await list_crime_log(db_session, 999) == []
