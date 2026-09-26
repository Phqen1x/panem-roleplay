"""Memory retrieval (Spec §6, `RETRIEVAL_K`).

Lives in `panem_shared` (not `panem_sim.systems.memory`, which owns
formation/pruning) because `panem_bot`'s dialogue service needs to pull an
NPC's memories into an LLM prompt without depending on `panem_sim`.
"""

from __future__ import annotations

from collections.abc import Iterable

from panem_shared import constants
from panem_shared.db.models import Memory


def retrieve(
    memories: Iterable[Memory], owner_kind: str, owner_id: str, k: int = constants.RETRIEVAL_K
) -> list[Memory]:
    """The `k` most relevant memories for one owner -- highest importance
    first, most recent as the tiebreak."""
    candidates = [m for m in memories if m.owner_kind == owner_kind and m.owner_id == owner_id]
    candidates.sort(key=lambda m: (-m.importance, -m.tick))
    return candidates[:k]
