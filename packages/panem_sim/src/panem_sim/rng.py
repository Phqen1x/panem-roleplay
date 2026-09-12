"""Deterministic per-tick randomness (Spec FR-TCK-4).

Every system draws randomness from the tick's own `random.Random` instance,
never the global `random` module -- replaying a tick from identical
persisted state must produce identical events, including across process
restarts.

The Plan/Spec pseudocode for this is `random.Random(hash((WORLD_SEED,
tick)))`, but Python's built-in `hash()` on `str` is randomized per
process (`PYTHONHASHSEED`) unless explicitly disabled, so that literal
reading would *not* reproduce across a restart -- defeating the point.
`hashlib.sha256` is used instead to get a seed that's stable everywhere.
"""

from __future__ import annotations

import hashlib
import random


def tick_rng(world_seed: str, tick: int) -> random.Random:
    return _seeded(f"{world_seed}:tick:{tick}")


def seed_rng(world_seed: str, label: str) -> random.Random:
    """For one-time, non-tick-scoped seeding (e.g. the initial synthetic
    NPC population) that still needs to be reproducible."""
    return _seeded(f"{world_seed}:seed:{label}")


def _seeded(key: str) -> random.Random:
    digest = hashlib.sha256(key.encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))
