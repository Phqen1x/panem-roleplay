"""Enums for status/state-machine fields (Spec §7)."""

from __future__ import annotations

import enum


class CharacterStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RETIRED = "retired"
    DEAD = "dead"


class SceneKind(enum.StrEnum):
    AMBIENT = "ambient"
    PLAYER = "player"
    STAFF = "staff"


class SceneStatus(enum.StrEnum):
    OPEN = "open"
    ARCHIVED = "archived"
    LOCKED = "locked"
    DELETED = "deleted"


class ShiftResult(enum.StrEnum):
    COMPLETED = "completed"
    MISSED = "missed"
    EXCUSED = "excused"


class ChannelKind(enum.StrEnum):
    FORUM = "forum"
    OOC = "ooc"
    BOARD = "board"
    APPROVAL = "approval"
    LOG = "log"


class Stance(enum.StrEnum):
    STRANGER = "stranger"
    HATES = "hates"
    DISLIKES = "dislikes"
    NEUTRAL = "neutral"
    LIKES = "likes"
    LOVES = "loves"


class DayPhase(enum.StrEnum):
    NIGHT = "night"
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"


class DialogueProviderName(enum.StrEnum):
    TEMPLATE = "template"
    LLM = "llm"
    HYBRID = "hybrid"
    TEMPLATE_FALLBACK = "template_fallback"


class OwnerKind(enum.StrEnum):
    """Values for `Inventory.owner_kind`/`MarketOrder.owner_kind` (Milestone D,
    FR-ECO-3/4). Only characters hold player-facing inventory today; `npc` is
    here for shopkeeper-side bookkeeping symmetry, not currently written."""

    CHARACTER = "character"
    NPC = "npc"


class LocationKind(enum.StrEnum):
    PUBLIC = "public"
    MARKET = "market"
    WORKPLACE = "workplace"
    GOVERNMENT = "government"
    RESIDENTIAL = "residential"
    OUTSKIRTS = "outskirts"
    STATION = "station"
