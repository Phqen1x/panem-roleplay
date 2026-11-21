"""Enums for status/state-machine fields (Spec §7)."""

from __future__ import annotations

import enum


class CharacterStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RETIRED = "retired"
    DEAD = "dead"


class Gender(enum.StrEnum):
    """`Character.gender`/`Npc.gender` -- both nullable (an unset/older row
    reads as `None`, "their"/"they" pronouns everywhere gender feeds into
    NPC dialogue, `panem_bot.services.dialogue`)."""

    MALE = "male"
    FEMALE = "female"
    NONBINARY = "nonbinary"


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
    JAIL = "jail"


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
    PRESIDENT = "president"
    VICE_PRESIDENT = "vice_president"


class PropertyKind(enum.StrEnum):
    """A `Property` row's kind (housing system). Ownership reuses
    `OwnerKind`; a house's tier reuses `JobLevel` -- apartments and inns
    store a placeholder tier (they aren't gated by job level, see
    `panem_bot.services.housing`)."""

    HOUSE = "house"
    APARTMENT = "apartment"
    INN = "inn"


class RpMode(enum.StrEnum):
    """A character's chosen roleplay mode, set at creation and changeable
    afterward (subject to `rp_modes.check_can_switch_mode`'s real-day
    cooldown). Story characters are freeform-RP-only (no economy, crime,
    housing, work, or NPC interaction); Life characters get the full
    economy/crime/market/work/travel loop but not housing or the needs
    system; Simulation is today's full experience plus thirst/sanity."""

    STORY = "story"
    LIFE = "life"
    SIMULATION = "simulation"


class AfflictionStat(enum.StrEnum):
    """The five `Character` meters an `AfflictionType`'s cure/auto-apply
    condition can reference."""

    HEALTH = "health"
    HUNGER = "hunger"
    THIRST = "thirst"
    FATIGUE = "fatigue"
    SANITY = "sanity"


class TradeStatus(enum.StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class AfflictionSource(enum.StrEnum):
    MANUAL = "manual"
    AUTO = "auto"


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


class DistrictClassification(enum.StrEnum):
    """A district's `DistrictLore.classification` (History tab): the
    Capitol-favored "inner" districts (traditionally 1-2, sometimes 3-4)
    versus the poorer, more heavily policed "outlier" districts. Staff-set
    per district, not derived from anything -- some tables leave it unset."""

    INNER = "inner"
    OUTLIER = "outlier"


class CollectibleRarity(enum.StrEnum):
    """A `data/collectibles.yaml` entry's rarity -- weights how often
    `panem_shared.hobbies` rolls it on a successful session, and sorts a
    `MuseumSlot`'s default ("by_rarity") preset (`panem_shared.museum`)."""

    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    LEGENDARY = "legendary"


class Season(enum.StrEnum):
    """Derived purely from the in-world month (`panem_shared.simtime.
    season_for_month`) -- not its own stored field anywhere. Northern-
    hemisphere ordering: Dec-Feb winter, Mar-May spring, Jun-Aug summer,
    Sep-Nov autumn (Spec's "same time spans as real life")."""

    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"


class WeatherKind(enum.StrEnum):
    """`WeatherForecast.condition`'s two no-severity values plus every
    severity-rated (category 1-6) inclement event `panem_bot.cogs.
    weather`'s poll can roll. Which kinds are even eligible in a given season is
    content (`data/climate.yaml`), not hardcoded here -- a flash flood in
    midwinter is just never in that district's seasonal table."""

    CLEAR = "clear"
    RAIN = "rain"
    SNOW = "snow"
    THUNDERSTORM = "thunderstorm"
    HAILSTORM = "hailstorm"
    BLIZZARD = "blizzard"
    FLASH_FLOOD = "flash_flood"
    HEATWAVE = "heatwave"
    WINDSTORM = "windstorm"


class WeatherEventStatus(enum.StrEnum):
    """A `WeatherEvent` row's lifecycle. `FORECAST` -> `ACTIVE` -> `ENDED`
    is the normal path; `FIZZLED` is the Spec's explicit "or not at all"
    case -- decided once, at generation time (`panem_shared.weather.
    _decide_event`), for the event to simply never start."""

    FORECAST = "forecast"
    ACTIVE = "active"
    ENDED = "ended"
    FIZZLED = "fizzled"


class ReactionChoice(enum.StrEnum):
    """One of a `ReactionEvent`'s up to-3 buttons (`panem_shared.
    reactions`) -- never stored as an arbitrary string so archetype math
    has a fixed, orderable set to reason about."""

    GOOD = "good"
    NEUTRAL = "neutral"
    EVIL = "evil"


class EventChainStatus(enum.StrEnum):
    """A `PersonalEventChain` row's lifecycle (`panem_shared.
    personal_events`)."""

    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    EXPIRED = "expired"


class GlobalEventStatus(enum.StrEnum):
    """A `GlobalEvent` row's lifecycle (`panem_shared.global_events`) --
    `panem_bot.cogs.events`'s lifecycle loop flips `SCHEDULED` -> `ACTIVE`
    -> `FINISHED` purely off `starts_tick`/`ends_tick`, no staff action
    needed once `/staff event create` schedules one."""

    SCHEDULED = "scheduled"
    ACTIVE = "active"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class BulletinSourceKind(enum.StrEnum):
    """What produced a `BulletinEntry` -- purely descriptive (drives its
    "Admin hosted"/"Player hosted"/... tag and which cog can edit/retract
    it), never read by the reminder/sort logic itself."""

    ANNOUNCEMENT = "announcement"
    WEATHER_FORECAST = "weather_forecast"
    WEATHER_EVENT = "weather_event"
    GLOBAL_EVENT = "global_event"
    RUMOR = "rumor"
    PLAYER_POST = "player_post"
    NPC_POST = "npc_post"
