"""Default tunables (Spec §10). Overridable at runtime by `data/tuning.yaml`
via `panem_shared.content.loader.load_tuning`, which is why every name here
is also a plain module-level constant: callers needing a live-tunable value
should go through the loaded tuning dict, and these remain the shipped
defaults and the fallback when a key is absent from `tuning.yaml`.
"""

from __future__ import annotations

TICKS_PER_DAY = 24
DAYS_PER_MONTH = 30

STARTING_MONEY = 40

SHIFT_DURATION_TICKS = 6
MISSES_TO_WARN = 3
MISSES_TO_FIRE = 5
NEW_HIRE_GRACE_DAYS = 10
PLAYER_OUTPUT_WEIGHT = 0.35

PRICE_EXPONENT = 0.5
PRICE_CLAMP_MIN = 0.4
PRICE_CLAMP_MAX = 4.0
PRICE_EMA_ALPHA = 0.2
SELL_DISCOUNT = 0.85
ILLICIT_PRICE_MULT = 0.6

CRISIS_THRESHOLDS = [0.15, 0.35, 0.55, 0.75]
CRISIS_RECOVERY_DAYS = 3

AFFINITY_DECAY_FLOOR = 20
MEMORY_CAP_PER_NPC = 200
RETRIEVAL_K = 6

TALK_STAMINA_PER_HOUR = 12
NPC_REPLY_BASE_DELAY_S = 3
SOFTMAX_TEMPERATURE = 0.3
APPROACH_COOLDOWN_TICKS = 2
LOCATION_RADIUS_PX = 60
MAX_WORDS_REPLY = 90

TICKET_BASE = 20
TRANSIT_TICKS = 4
AWAY_GRACE_DAYS = 3

TREASURY_BASE = 4000

STANCE_THRESHOLDS = [-60, -20, 20, 60]
STANCE_MIN_INTERACTIONS_EXTREME = 5
STANCE_PRICE_MOD = {
    "loves": 0.9,
    "likes": 0.95,
    "neutral": 1.0,
    "dislikes": 1.1,
    # "hates" is a refusal, not a multiplier (FR-ECO-3).
}

# Phase 0 additions not in the tunables table but referenced by name-length /
# validation rules spelled out in the spec (FR-CHR-2/7).
CHARACTER_NAME_MAX_LEN = 32
CHARACTER_APPEARANCE_MAX_LEN = 400
CHARACTER_BACKSTORY_MAX_LEN = 1500
CHARACTER_AGE_MIN = 12
CHARACTER_AGE_MAX = 80
# Reaping-eligible districts (all but the Capitol) may only create
# characters in the reaping age range; adult characters are Capitol-only.
NON_CAPITOL_AGE_MAX = 18
CAPITOL_DISTRICT_ID = 0
PROXY_TAG_MIN_LEN = 1
PROXY_TAG_MAX_LEN = 12
AVATAR_URL_MAX_LEN = 512
AVATAR_URL_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

PROXY_MESSAGE_MAX_LEN = 2000
SCENE_IDLE_ARCHIVE_HOURS = 24
NPC_APPROACH_INVITE_TAG_MATCH_REQUIRED = True

# Phase 1 addition: real, personality-rich NPCs (`data/npcs/*.yaml`) are
# Phase 3 content (Plan §11); Phase 1/2 seed this many minimal synthetic
# NPCs per district instead, so movement/needs/jobs/shopkeeper mechanics
# have bodies to act on. Falls within Plan §6.1's own "20-30 NPCs per
# district" range, so Phase 3 mostly enriches these rows rather than
# replacing them.
SYNTHETIC_NPCS_PER_DISTRICT = 23

# Phase 2 Milestone C (needs, FR-NDS-1/2/3) additions. Unlike the tunables
# above, these are *not* sourced from Spec §10 -- the spec document isn't
# available in this session, only the Plan's high-level description
# ("nightly living cost (bread/coal), hunger/health effects"). These are
# reasonable placeholder values, not the spec's actual numbers; revalidate
# against `panem-long-year-spec.md` §10 before relying on them.
NIGHTLY_LIVING_COST = 5
"""Money deducted once per day (Spec's abstraction for buying grain/coal);
NPCs pay the float equivalent, `NIGHTLY_LIVING_COST_NPC`."""
NIGHTLY_LIVING_COST_NPC = 5.0
HUNGER_MAX = 100.0
HUNGER_MIN = 0.0
HUNGER_INCREASE_UNMET = 15.0
"""Hunger gained on a night the living cost can't be paid."""
HUNGER_DECREASE_MET = 10.0
"""Hunger lost on a night the living cost is paid."""
HEALTH_MAX = 100.0
HEALTH_MIN = 0.0
HEALTH_DECAY_HUNGER_THRESHOLD = 70.0
"""Above this hunger level, health starts to erode each night."""
HEALTH_DECAY_PER_NIGHT = 2.0
HEALTH_RECOVERY_PER_NIGHT = 3.0
"""Health regained on a night hunger stays below the decay threshold."""

# Phase 2 Milestone C (jobs/shifts, FR-JOB-10) addition, same caveat as
# above: not sourced from Spec §10, a placeholder pending the real spec.
NPC_JOB_COMPLETION_PROB = 0.85
RP_CREDIT_MIN_CHARS = 120
"""FR-PRX-7: a proxied message at least this long, in a scene tagged for
the job's workplace, completes an open shift as if `/work` picked option 0."""
