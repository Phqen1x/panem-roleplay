from __future__ import annotations

from panem_shared import constants, lore
from panem_shared.db.models import PanemHistoryEntry


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
