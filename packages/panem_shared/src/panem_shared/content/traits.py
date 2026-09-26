"""A generic personality-trait pool for synthetic (unauthored) NPCs
(Phase 3, Spec §6).

Real, hand-authored NPC personality (`data/npcs/*.yaml`) doesn't exist
yet -- see `panem_shared.content.names`'s module docstring for why. This
gives Phase 1/2's synthetic population (`panem_sim.world.seed_npcs`)
enough of a personality for `social.py`'s interactions and `/resident
profile` to read as more than a name and a job: a couple of traits and a
speech tone derived from them, deterministic per NPC. Not a substitute
for real authored dialogue/backstory -- just identity, the same
deliberate scope `names.py` describes.
"""

from __future__ import annotations

import random

TRAITS = (
    "stoic",
    "gossipy",
    "hot-tempered",
    "kind",
    "suspicious",
    "cheerful",
    "bitter",
    "brave",
    "cowardly",
    "proud",
    "humble",
    "sharp-tongued",
    "gentle",
    "ambitious",
    "lazy",
    "loyal",
    "paranoid",
    "reckless",
    "cautious",
    "witty",
    "shy",
    "boastful",
    "patient",
    "vengeful",
)

TRAITS_PER_NPC = 2

_WARM_TRAITS = {"kind", "cheerful", "gentle", "loyal", "patient"}
_BLUNT_TRAITS = {"hot-tempered", "sharp-tongued", "boastful", "reckless", "vengeful"}
_RESERVED_TRAITS = {"stoic", "shy", "cautious", "suspicious", "paranoid"}


def sample_traits(rng: random.Random) -> list[str]:
    """`TRAITS_PER_NPC` unique traits, deterministic for a given `rng`.
    Unlike `sample_names`, these are drawn from a small, shared pool --
    two NPCs having the same trait is expected, not a bug; only a given
    NPC's own list is guaranteed trait-unique."""
    return sorted(rng.sample(TRAITS, TRAITS_PER_NPC))


def speech_tone(traits: list[str]) -> str:
    """A single coarse speech-tone bucket derived from `traits`, first
    matching category wins (`_WARM_TRAITS` > `_BLUNT_TRAITS` >
    `_RESERVED_TRAITS` > "plain"). This is deliberately thin -- real
    `speech_style` content (word choice, cadence, catchphrases) is
    Phase 6 (LLM dialogue) scope; this only gives that later system, and
    `/resident profile` today, a starting hint rather than an empty
    dict."""
    trait_set = set(traits)
    if trait_set & _WARM_TRAITS:
        return "warm"
    if trait_set & _BLUNT_TRAITS:
        return "blunt"
    if trait_set & _RESERVED_TRAITS:
        return "reserved"
    return "plain"
