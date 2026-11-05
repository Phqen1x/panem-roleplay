"""The block-character appearance system: every character (player or NPC)
on the 3D map is assembled from four independently-chosen Blocky
Characters body parts -- head, torso, arms, legs -- each sourced from any
of the 18 vendored outfit meshes (`static/assets3d/characters/character-
{letter}.glb`) and independently recolored from any of the 18 vendored
palette textures (`.../Textures/texture-{letter}.png`).

This is possible at all because every outfit file shares one rig: the
same six named nodes (`root` -> `leg-left`/`leg-right`/`torso`, `torso` ->
`arm-left`/`arm-right`/`head`), the same per-part vertex counts, and the
same local transforms at each node -- confirmed by inspecting several
outfit files directly before this was built. `static/map3d/character.js`
is the part that actually cuts a part out of one outfit file and grafts
it onto another at the shared joint; this module only decides *which*
letters to use, both for a player's own saved choice (`Character.
block_parts`) and for an NPC's assigned one.

Honesty about NPC assignment, same point the previous single-outfit
version of this module made: the 18 shapes/colors aren't labeled by job
or appearance, so there's still no way to make an NPC's body mean
"Peacekeeper" or "Stylist Assistant" -- what changes with four
independently-chosen parts is that two NPCs collide far less often (18^4
shape combinations before color even enters it, instead of 18), which is
what "visually distinct" actually buys here.
"""

from __future__ import annotations

import hashlib
from typing import Final

LETTERS = "abcdefghijklmnopqr"
BODY_PARTS: Final[tuple[str, ...]] = ("head", "torso", "arms", "legs")

_GENDER_POOLS: dict[str, str] = {
    "male": "acegikmoq",
    "female": "bdfhjlnpr",
}
"""Which half of `LETTERS` a gendered NPC's *shapes* are drawn from (colors
always draw from the full range) -- an arbitrary, even split with no claim
that either half "looks like" either gender; an NPC with no gender on file
(or any value other than `"male"`/`"female"`) draws shapes from the full
18-letter range too."""

DEFAULT_APPEARANCE: Final[dict[str, str]] = {
    "head": "a",
    "head_color": "a",
    "torso": "a",
    "torso_color": "a",
    "arms": "a",
    "arms_color": "a",
    "legs": "a",
    "legs_color": "a",
}
"""`Character.block_parts`' default for a character that's never
customized -- every part set to the plain `"a"` outfit/color, the same
baseline every character used to start from back when this was a single
outfit+palette pair."""


def _stable_index(seed: str, modulus: int) -> int:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return digest[0] % modulus


def is_valid_letter(value: object) -> bool:
    return isinstance(value, str) and len(value) == 1 and value in LETTERS


def is_valid_appearance(value: object) -> bool:
    """Used by `map3d_routes`'s appearance-update endpoint to validate a
    player's chosen `block_parts` before it's persisted: exactly the eight
    keys `DEFAULT_APPEARANCE` has, each a single `a`-`r` letter, nothing
    extra."""
    if not isinstance(value, dict):
        return False
    if set(value.keys()) != set(DEFAULT_APPEARANCE.keys()):
        return False
    return all(is_valid_letter(v) for v in value.values())


def assign_npc_appearance(npc_id: str, *, gender: str | None) -> dict[str, str]:
    """Returns a full `block_parts`-shaped dict for an NPC -- deterministic
    by design (seeded by the NPC's own id, not `random`): two requests for
    the same NPC must return the same body, or every map3d client would
    render a different one for the same NPC and disagree with each
    other."""
    shape_pool = _GENDER_POOLS.get(gender or "", LETTERS)
    appearance: dict[str, str] = {}
    for part in BODY_PARTS:
        appearance[part] = shape_pool[_stable_index(f"{npc_id}|{part}|shape", len(shape_pool))]
        appearance[f"{part}_color"] = LETTERS[_stable_index(f"{npc_id}|{part}|color", len(LETTERS))]
    return appearance
