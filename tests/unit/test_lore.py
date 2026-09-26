from __future__ import annotations

import pytest

from panem_shared import constants, lore
from panem_shared.db.models import PanemHistoryEntry
from panem_shared.errors import NotFound, ValidationFailed


def make_history_entry(id_: int, **overrides: object) -> PanemHistoryEntry:
    defaults: dict[str, object] = dict(
        keywords=["dark days"],
        text="The Dark Days ended with the Treaty of Treason.",
        created_by_staff_discord_id=1,
    )
    defaults.update(overrides)
    row = PanemHistoryEntry(**defaults)  # type: ignore[arg-type]
    row.id = id_
    return row


class TestMatchHistoryEntries:
    def test_matches_a_keyword_case_insensitively(self):
        rows = [make_history_entry(1, keywords=["Dark Days"])]
        result = lore.match_history_entries(rows, "Tell me about the dark days.")
        assert result == [rows[0].text]

    def test_no_match_when_no_keyword_appears(self):
        rows = [make_history_entry(1, keywords=["dark days"])]
        result = lore.match_history_entries(rows, "How's the weather in district four?")
        assert result == []

    def test_entry_with_no_keywords_never_matches(self):
        rows = [make_history_entry(1, keywords=[])]
        result = lore.match_history_entries(rows, "anything at all")
        assert result == []

    def test_any_matching_keyword_is_enough(self):
        rows = [make_history_entry(1, keywords=["dark days", "treaty of treason"])]
        result = lore.match_history_entries(rows, "What's the Treaty of Treason?")
        assert result == [rows[0].text]

    def test_caps_at_max_history_entries_per_reply(self):
        rows = [
            make_history_entry(i, keywords=["dark days"], text=f"fact {i}")
            for i in range(constants.MAX_HISTORY_ENTRIES_PER_REPLY + 3)
        ]
        result = lore.match_history_entries(rows, "the dark days")
        assert len(result) == constants.MAX_HISTORY_ENTRIES_PER_REPLY

    def test_preserves_input_order_among_matches(self):
        rows = [
            make_history_entry(1, keywords=["dark days"], text="first"),
            make_history_entry(2, keywords=["dark days"], text="second"),
        ]
        result = lore.match_history_entries(rows, "the dark days")
        assert result == ["first", "second"]


class TestAddHistoryEntry:
    async def test_creates_a_row_with_cleaned_keywords(self, db_session):
        entry = await lore.add_history_entry(
            db_session,
            keywords=[" dark days ", "", "  ", "treaty of treason"],
            text="  The Dark Days ended with the Treaty of Treason.  ",
            created_by_staff_discord_id=42,
        )
        assert entry.id is not None
        assert entry.keywords == ["dark days", "treaty of treason"]
        assert entry.text == "The Dark Days ended with the Treaty of Treason."
        assert entry.created_by_staff_discord_id == 42

    async def test_rejects_no_real_keywords(self, db_session):
        with pytest.raises(ValidationFailed) as exc_info:
            await lore.add_history_entry(
                db_session, keywords=["  ", ""], text="Some fact.", created_by_staff_discord_id=1
            )
        assert exc_info.value.reason_key == "panem_history_needs_a_keyword"

    async def test_rejects_blank_text(self, db_session):
        with pytest.raises(ValidationFailed) as exc_info:
            await lore.add_history_entry(
                db_session, keywords=["dark days"], text="   ", created_by_staff_discord_id=1
            )
        assert exc_info.value.reason_key == "panem_history_text_required"


class TestListAndDeleteHistoryEntries:
    async def test_lists_in_id_order(self, db_session):
        first = await lore.add_history_entry(
            db_session, keywords=["a"], text="first", created_by_staff_discord_id=1
        )
        second = await lore.add_history_entry(
            db_session, keywords=["b"], text="second", created_by_staff_discord_id=1
        )
        rows = await lore.list_history_entries(db_session)
        assert [r.id for r in rows] == [first.id, second.id]

    async def test_deletes_an_existing_entry(self, db_session):
        entry = await lore.add_history_entry(
            db_session, keywords=["a"], text="fact", created_by_staff_discord_id=1
        )
        deleted = await lore.delete_history_entry(db_session, entry.id)
        assert deleted.id == entry.id
        assert await lore.list_history_entries(db_session) == []

    async def test_deleting_an_unknown_entry_raises_not_found(self, db_session):
        with pytest.raises(NotFound) as exc_info:
            await lore.delete_history_entry(db_session, 999999)
        assert exc_info.value.reason_key == "panem_history_entry_not_found"


class TestWorldLore:
    async def test_get_returns_none_before_anything_is_set(self, db_session):
        assert await lore.get_world_lore(db_session) is None

    async def test_set_creates_the_singleton_row(self, db_session):
        row = await lore.set_world_lore(db_session, alternate_universe_notes="  Some AU note.  ")
        assert row.id == 1
        assert row.alternate_universe_notes == "Some AU note."
        assert (await lore.get_world_lore(db_session)).alternate_universe_notes == "Some AU note."

    async def test_set_updates_the_row_in_place(self, db_session):
        await lore.set_world_lore(db_session, alternate_universe_notes="first")
        await lore.set_world_lore(db_session, alternate_universe_notes="second")
        row = await lore.get_world_lore(db_session)
        assert row.alternate_universe_notes == "second"

    async def test_set_allows_clearing_the_notes_to_empty(self, db_session):
        await lore.set_world_lore(db_session, alternate_universe_notes="something")
        row = await lore.set_world_lore(db_session, alternate_universe_notes="   ")
        assert row.alternate_universe_notes == ""
