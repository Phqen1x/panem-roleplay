"""Assigns each NPC a default 3D body on the live map: one of the 18
vendored Blocky Characters meshes (`outfit`) wearing one of the 18
vendored recolor textures (`palette`) -- `static/assets3d/characters/
character-{outfit}.glb` + `.../Textures/texture-{palette}.png`.

Honesty about what this actually does: the Blocky Characters pack ships
18 unlabeled outfit meshes and 18 unlabeled recolor textures -- there is
no staff-curated mapping from "Peacekeeper" or "Stylist Assistant" to a
specific letter, and no visual difference between the letters beyond
whatever an artist would see in the pack's own preview images. So this
can't actually dress an NPC as their job the way a real costume designer
would; what it *can* do, and all it claims to do, is give every NPC a
*visually distinct, stable* body (the same NPC always renders the same
way, and different NPCs mostly render differently) with a small, explicit,
documented gender lean baked in via `_GENDER_POOLS` below -- "lay the
groundwork" (the feature request's own words) for a future pass where
someone who's actually looked at the 18 previews curates a real mapping
per job/trait, which can replace `_GENDER_POOLS`/`assign_block_model`
without touching anything that calls them.

Deterministic by design (seeded by the NPC's own id, not `random`): two
requests for the same NPC must return the same outfit/palette, or every
map3d client would render a different body for the same NPC and disagree
with each other.
"""

from __future__ import annotations

import hashlib

OUTFIT_LETTERS = "abcdefghijklmnopqr"
PALETTE_LETTERS = "abcdefghijklmnopqr"

_GENDER_POOLS: dict[str, str] = {
    "male": "acegikmoq",
    "female": "bdfhjlnpr",
}
"""Which half of `OUTFIT_LETTERS` a gendered NPC's outfit is drawn from --
an arbitrary, even split with no claim that either half "looks like"
either gender (see this module's docstring); an NPC with no gender on
file (or any value other than `"male"`/`"female"`) draws from the full
18-letter range instead."""


def _stable_index(seed: str, modulus: int) -> int:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return digest[0] % modulus


def assign_block_model(npc_id: str, *, gender: str | None) -> tuple[str, str]:
    """Returns `(outfit, palette)`, each a single letter `a`-`r`."""
    pool = _GENDER_POOLS.get(gender or "", OUTFIT_LETTERS)
    outfit = pool[_stable_index(npc_id, len(pool))]
    palette = PALETTE_LETTERS[_stable_index(f"{npc_id}|palette", len(PALETTE_LETTERS))]
    return outfit, palette


def is_valid_letter(value: str) -> bool:
    """Used by `map3d_routes`'s appearance-update endpoint to validate a
    player's chosen `block_outfit`/`block_palette` before it's persisted
    -- both columns share the same `a`-`r` domain as the NPC assignment
    above, so one check covers both."""
    return len(value) == 1 and value in OUTFIT_LETTERS
