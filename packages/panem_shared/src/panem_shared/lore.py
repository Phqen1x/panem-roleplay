"""Panem-wide staff lore for NPC dialogue (`PanemHistoryEntry`/
`WorldLoreSettings`, `db/models.py`).

Lives in `panem_shared` (not `panem_bot`), same reasoning as `memory.py`:
the dialogue service pulls this into an LLM prompt without depending on
anything bot-specific, and the request/response shape here is plain data,
not ORM-session logic.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants
from panem_shared.db.models import PanemHistoryEntry, WorldLoreSettings
from panem_shared.errors import NotFound, ValidationFailed

TEXT_MAX_LEN = 4000
"""Matches `district_lore.NOTES_MAX_LEN` -- both feed the same kind of
free-form staff prose into a dialogue prompt (a capped excerpt of it, not
the whole thing), so there's no reason for one to allow more raw input
than the other."""


def _clean_keywords(keywords: Iterable[str]) -> list[str]:
    cleaned = [k.strip() for k in keywords if k and k.strip()]
    if not cleaned:
        raise ValidationFailed("panem_history_needs_a_keyword")
    return cleaned


def _clean_text(text: str, *, field: str, required: bool = True) -> str:
    cleaned = (text or "").strip()
    if required and not cleaned:
        raise ValidationFailed(f"panem_history_{field}_required")
    if len(cleaned) > TEXT_MAX_LEN:
        raise ValidationFailed(f"panem_history_{field}_too_long")
    return cleaned


async def list_history_entries(session: AsyncSession) -> list[PanemHistoryEntry]:
    """Oldest first, matching `match_history_entries`' own tie-break order
    and `/staff lore history-list`'s existing display order."""
    result = await session.execute(select(PanemHistoryEntry).order_by(PanemHistoryEntry.id))
    return list(result.scalars().all())


async def add_history_entry(
    session: AsyncSession,
    *,
    keywords: Iterable[str],
    text: str,
    created_by_staff_discord_id: int,
) -> PanemHistoryEntry:
    """Shared by `/staff lore history-add` and the Activity's History tab
    -- one keyword-cleaning/text-validation path for both, rather than each
    surface enforcing its own rules that could quietly drift apart."""
    entry = PanemHistoryEntry(
        keywords=_clean_keywords(keywords),
        text=_clean_text(text, field="text"),
        created_by_staff_discord_id=created_by_staff_discord_id,
    )
    session.add(entry)
    await session.flush()
    return entry


async def delete_history_entry(session: AsyncSession, entry_id: int) -> PanemHistoryEntry:
    entry = await session.get(PanemHistoryEntry, entry_id)
    if entry is None:
        raise NotFound("panem_history_entry_not_found")
    await session.delete(entry)
    return entry


async def get_world_lore(session: AsyncSession) -> WorldLoreSettings | None:
    return await session.get(WorldLoreSettings, 1)


async def set_world_lore(
    session: AsyncSession, *, alternate_universe_notes: str
) -> WorldLoreSettings:
    """Shared by `/staff lore au-set` and the Activity's History tab.
    Always the singleton row (id=1), created on first use -- same posture
    as `WorldClock`/`EngagementSettings`."""
    text = _clean_text(alternate_universe_notes, field="au_notes", required=False)
    row = await session.get(WorldLoreSettings, 1)
    if row is None:
        row = WorldLoreSettings(id=1, alternate_universe_notes=text)
        session.add(row)
    else:
        row.alternate_universe_notes = text
    await session.flush()
    return row


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
