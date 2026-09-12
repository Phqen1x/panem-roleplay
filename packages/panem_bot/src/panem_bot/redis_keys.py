"""Redis key builders (Plan §2, Spec §2.3). Centralized so every reader and
writer agrees on the exact key shape."""

from __future__ import annotations

SESSION_TTL_S = 6 * 60 * 60
PROXY_TTL_S = 7 * 24 * 60 * 60
PRESENCE_TTL_S = 30 * 60
TESSERAE_TTL_S = 24 * 60 * 60
"""One claim per real day, not per game day -- tesserae is a player action,
not tied to the sim's tick clock."""


def session_key(user_id: int, thread_id: int) -> str:
    return f"session:{user_id}:{thread_id}"


def proxy_key(webhook_message_id: int) -> str:
    return f"proxy:{webhook_message_id}"


def scene_presence_key(thread_id: int) -> str:
    return f"scene:presence:{thread_id}"


def talk_window_key(thread_id: int, npc_id: str) -> str:
    return f"talk:{thread_id}:{npc_id}"


def positions_key(district_id: int) -> str:
    return f"pos:{district_id}"


def tesserae_key(character_id: int) -> str:
    return f"tesserae:{character_id}"
