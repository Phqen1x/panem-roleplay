"""Detecting a hostile/harmful roleplay action directed at an NPC from a
player's freeform message text -- spitting on, hitting, attacking, or
otherwise harming/annoying them -- so `RelationshipRow` reacts to what
actually happens in roleplay, not just `panem_sim.systems.social`'s
passive "shared a location this tick" proximity nudge.

Plain case-insensitive substring matching, no LLM call and no real NLP,
mirroring `panem_shared.lore.match_history_entries`'s exact tradeoff (a
curated keyword list standing in for a "real" detector Spec's context
never gave a concrete algorithm for -- the same posture every other
detection mechanic in this codebase already takes, e.g. `market.py`'s
illicit-catch roll). It will both miss real hostility phrased unusually
and occasionally fire on an unrelated use of a word ("attack" in "a
panic attack") -- an accepted false-positive/negative rate for a simple
heuristic, not a claim of real language understanding.
"""

from __future__ import annotations

HOSTILE_KEYWORDS = (
    "spit",
    "spat",
    "hit",
    "punch",
    "slap",
    "kick",
    "strike",
    "struck",
    "attack",
    "assault",
    "stab",
    "shoot",
    "shot ",
    "strangl",
    "chok",
    "shove",
    "shov",
    "push",
    "grab",
    "beat",
    "whip",
    "claw",
    "bite",
    "bit her",
    "bit him",
    "kill",
    "murder",
    "torture",
    "harm",
    "hurt",
    "injur",
    "curse at",
    "swear at",
    "insult",
    "mock",
    "humiliat",
    "belittl",
    "threaten",
    "scream at",
    "yell at",
    "annoy",
    "harass",
    "torment",
    "bully",
    "taunt",
    "provoke",
)
"""Root forms, matched as plain substrings (`bit ` and `bite` both match
"bites"; `chok`/`strangl`/`humiliat`/`belittl`/`injur` cover several
conjugations off one stem the same way). Deliberately broad rather than
an exhaustive phrase list -- narrower phrasing would miss far more real
roleplay than a few loose substrings falsely catch."""


def is_hostile_action(message: str) -> bool:
    """Whether `message` describes a hostile/harmful action at all --
    callers decide *who* it's directed at (e.g. `engagements_svc.
    npcs_that_should_reply`'s same one-on-one/name-mention targeting the
    reply itself already uses, so a group scene only dings the NPC
    actually named rather than every NPC present)."""
    message_lower = message.lower()
    return any(keyword in message_lower for keyword in HOSTILE_KEYWORDS)
