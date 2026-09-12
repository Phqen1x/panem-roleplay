"""A generic name pool for synthetic (unauthored) NPCs.

Phase 3 (`data/npcs/*.yaml`, Spec §5.4) gives real personality-rich NPCs
hand-authored names, traits, and speech; until then, Phase 1/2's synthetic
population (`panem_sim.world.seed_npcs`) still needs *something* better
than `"Resident 1"` for narration and the `/resident` command to read
naturally. This is deliberately plain and setting-neutral -- not drawn
from the books -- since it's filler for unauthored residents, not canon
content.
"""

from __future__ import annotations

import itertools
import random

FIRST_NAMES = (
    "Ada",
    "Arlo",
    "Beatrix",
    "Cass",
    "Dara",
    "Elin",
    "Fenn",
    "Greta",
    "Hollis",
    "Ines",
    "Jory",
    "Kestrel",
    "Lior",
    "Marisol",
    "Nash",
    "Odalys",
    "Petra",
    "Quill",
    "Rosalind",
    "Sable",
    "Tobias",
    "Ursa",
    "Vesper",
    "Wren",
    "Xiomara",
    "Yusuf",
    "Zinnia",
    "Briar",
    "Corvin",
    "Delphine",
    "Emrys",
    "Farrah",
    "Gideon",
    "Halcyon",
    "Ivo",
    "Junia",
    "Kellan",
    "Lyra",
    "Merrick",
    "Noor",
)

LAST_NAMES = (
    "Ashford",
    "Briarwood",
    "Coalfield",
    "Dunmore",
    "Elmhurst",
    "Fenwick",
    "Graystone",
    "Hollow",
    "Ironside",
    "Juniper",
    "Kestner",
    "Larke",
    "Mossgrove",
    "Norwich",
    "Oakes",
    "Pemberton",
    "Quarrie",
    "Ridgely",
    "Slate",
    "Thistledown",
    "Underhill",
    "Vane",
    "Wilder",
    "Yarrow",
    "Ashgrove",
    "Blackwell",
    "Cinder",
    "Drummond",
    "Everhart",
    "Flint",
    "Grimshaw",
    "Hargrove",
    "Ivorine",
    "Loamfield",
    "Marrow",
    "Nettle",
    "Osgood",
    "Pryce",
    "Rowntree",
    "Sedgwick",
)


def sample_names(rng: random.Random, count: int) -> list[str]:
    """`count` unique `"First Last"` names, deterministic for a given
    `rng`. `FIRST_NAMES x LAST_NAMES` gives 1600 combinations, comfortably
    more than any one district's synthetic population needs."""
    combos = list(itertools.product(FIRST_NAMES, LAST_NAMES))
    chosen = rng.sample(combos, min(count, len(combos)))
    return [f"{first} {last}" for first, last in chosen]
