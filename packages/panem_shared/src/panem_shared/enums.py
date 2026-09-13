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
    ENGAGEMENT = "engagement"


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


class Position(enum.StrEnum):
    """Special standing a character can hold, held in `Character.positions`
    (a list, not a single value -- nothing stops a character from being
    both a Victor and a Governor). Unlike a `Job`, a Position isn't
    content-authored or applied for: staff grant and revoke these directly
    (`/staff give position`), and holding any of them grants the same
    location-access privilege Victors always had (`proxy.has_location_access`),
    generalized from what used to be a single `Character.is_victor` bool."""

    VICTOR = "victor"
    GAMEMAKER = "gamemaker"
    GOVERNOR = "governor"


class PropertyKind(enum.StrEnum):
    """A `Property` row's kind (housing system). Ownership reuses
    `OwnerKind`; a house's tier reuses `JobLevel` -- apartments and inns
    store a placeholder tier (they aren't gated by job level, see
    `panem_bot.services.housing`)."""

    HOUSE = "house"
    APARTMENT = "apartment"
    INN = "inn"


class JobLevel(enum.StrEnum):
    """A player character's skill level at their free-typed job
    (`Character.job_title`), driven purely by `Character.shifts_completed`
    -- replaces the old per-job `ladder_next` progression now that jobs
    aren't a fixed catalog a ladder could be authored against. See
    `panem_shared.job_levels` for the shift-count thresholds and the
    wage multiplier each level grants."""

    APPRENTICE = "apprentice"
    NOVICE = "novice"
    JOURNEYMAN = "journeyman"
    MASTER = "master"
    EXPERT = "expert"
