"""Panem-wide staff lore for NPC dialogue (`PanemHistoryEntry`/
`WorldLoreSettings`, `db/models.py`).

Lives in `panem_shared` (not `panem_bot`), same reasoning as `memory.py`:
the dialogue service pulls this into an LLM prompt without depending on
anything bot-specific, and the request/response shape here is plain data,
not ORM-session logic.
"""

from __future__ import annotations

from collections.abc import Iterable

from panem_shared import constants
from panem_shared.db.models import PanemHistoryEntry


def match_history_entries(entries: Iterable[PanemHistoryEntry], message: str) -> list[str]:
    """Which staff-authored history facts are relevant to `message` -- a
    plain case-insensitive substring check per entry's `keywords` (an
    entry with no keywords never matches), capped at `constants.
    MAX_HISTORY_ENTRIES_PER_REPLY` so a busy table doesn't crowd out the
    rest of the prompt. Insertion order (oldest `PanemHistoryEntry` first)
    decides which entries survive the cap when more than that many match --
    no relevance ranking exists here, unlike `memory.retrieve`'s
    importance/recency sort, since there's no equivalent signal to rank on."""
    message_lower = message.lower()
    matched = [
        entry.text
        for entry in entries
        if any(keyword.lower() in message_lower for keyword in entry.keywords if keyword)
    ]
    return matched[: constants.MAX_HISTORY_ENTRIES_PER_REPLY]
