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

WORK_GAME_WIN_WAGE_MULT = 1.5
WORK_GAME_LOSE_WAGE_MULT = 0.4
"""`/work`'s minigame (Minesweeper, `panem_api`'s Activity frontend):
winning pays `job.wage * WIN`, losing pays `job.wage * LOSE` -- replacing
the option-multiplier axis a player's free choice used to control (see
`panem_shared.shifts.resolve_shift_game`)."""
WORK_GAME_GRACE_TICKS = TICKS_PER_DAY
"""How long past a shift's `tick_due` a *started* (`Shift.started_at_tick`
set) minigame stays open rather than being marked missed -- long enough
to actually go finish a Minesweeper board without racing the clock, short
enough that an abandoned game doesn't block a new shift from ever
opening."""

NO_ACTIVITY_WORK_WIN_PROBABILITY = 0.6
"""`/work` without `ACTIVITY_PUBLIC_URL` configured (no Minesweeper board to
actually win or lose) resolves immediately with a coin-flip at this
probability instead -- the classic 3-option choose-your-risk menu was
retired along with the `jobs.yaml` catalog it was authored against, so
win/lose is now the only outcome axis `/work` has, configured or not."""

PLAYER_JOB_BASE_WAGE = 20.0
"""Every player job pays from this same flat base now that a job is a
free-typed title (`Character.job_title`) rather than a catalog entry with
its own authored wage -- `panem_shared.job_levels`' level multiplier and
`WORK_GAME_WIN_WAGE_MULT`/`LOSE` stack on top of it, and
`panem_sim.systems.economy`'s market price on top of that again."""

PLAYER_SHIFT_OUTPUT_QTY = 1.0
"""FR-ECO rework: every completed player shift (win or lose -- they still
did the work) produces exactly one unit of their home district's quota
good (`District.quota.good`, e.g. District 12's coal), feeding
`panem_sim.systems.economy`'s supply the same way `Job.produces` used to."""

JOB_LEVEL_SHIFT_THRESHOLDS: dict[str, int] = {
    "apprentice": 0,
    "novice": 28,
    "journeyman": 84,  # 28 + 56
    "master": 168,  # 84 + 84
    "expert": 280,  # 168 + 112
}
"""Cumulative `Character.shifts_completed` needed to reach each `JobLevel`
(`panem_shared.job_levels.job_level_for_shifts`)."""

JOB_LEVEL_WAGE_MULTIPLIERS: dict[str, float] = {
    "apprentice": 1.0,
    "novice": 1.5,
    "journeyman": 2.0,
    "master": 2.5,
    "expert": 3.0,
}
"""Wage multiplier for each `JobLevel`, +0.5x per level -- stacks with
(multiplies on top of) the win/lose outcome multiplier, per
`panem_shared.shifts.resolve_shift_game`."""

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
AWAY_GRACE_DAYS = 18
"""In sim-days, not real ones -- at the default `TICK_INTERVAL_SECONDS`
(600s) and `TICKS_PER_DAY` (24), one sim-day is 4 real hours, so 18
sim-days is ~3 real days of grace before a missed shift while traveling
starts counting again. The original value of 3 sim-days (~12 real
hours) was too tight for asynchronous Discord play -- a player who logs
back in the next day would already have lost their job. Re-tune if your
`TICK_INTERVAL_SECONDS` differs from the default."""

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
JOB_TITLE_MAX_LEN = 80
"""Free-typed at character creation (`Character.job_title`), matches the
DB column width."""
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

# Phase 2 Milestone D (markets/quotas/exports, FR-ECO-1/2/5/6/8/9) additions.
# Same caveat as the Milestone C tunables above: `panem-long-year-spec.md`
# §10 wasn't available in the session that built this, so these are
# reasonable placeholders, not the spec's real numbers -- revalidate before
# relying on them. There's also no real per-good consumption model (nothing
# tracks a character/NPC eating bread or burning coal), so demand is a flat
# per-capita rate against `District.population_base`, not derived from
# actual need -- a much cruder stand-in for FR-ECO-1's demand side than the
# supply side (real completed-shift/NPC-job output) gets.
MARKET_DEMAND_PER_CAPITA = 0.01
"""Daily demand for each good a district produces or imports, per person
of `population_base` -- e.g. 8000 population -> 80 units/day baseline."""
MARKET_SUPPLY_FLOOR = 0.01
"""Supply is clamped to at least this before dividing by it in the price
formula, so a district producing literally nothing today doesn't divide
by zero -- reads as "effectively empty shelves", not an error."""

ACTIVE_PLAYER_WINDOW_SIM_DAYS = 42
""""Interacted within the past real-life week" for the active-player
demand model below -- at the default `TICK_INTERVAL_SECONDS` (600s) and
`TICKS_PER_DAY` (24), one sim-day is 4 real hours, so 42 sim-days is 7
real days. Re-tune if your `TICK_INTERVAL_SECONDS` differs from the
default (same caveat as `AWAY_GRACE_DAYS`)."""
ACTIVE_PLAYER_DEMAND_PER_CAPITA = 1.0
"""Daily demand for a good, per active player (one who has worked a shift
or sent a proxied message within `ACTIVE_PLAYER_WINDOW_SIM_DAYS`) in a
district that needs it -- replaces `MARKET_DEMAND_PER_CAPITA`'s flat
`population_base` rate once a district has any active-player signal at
all (falls back to the old population-based rate for an all-NPC district
with nobody active yet, so its market doesn't go to zero)."""
DISTRICT_IMPORT_DEMAND_WEIGHT = 2.0
"""A district demands a good it *imports* more than one it produces
itself (Capitol wants a lot of luxury goods, D12 wants a lot of grain,
neither produces much of what it's short on) -- multiplies the per-capita
demand rate above for a district's `imports`; `produces` goods use a
1.0 baseline weight. A crude stand-in for real per-good/per-district
consumption modeling (see README's "Notes on the job system rework")."""
QUOTA_MET_FAVOR_DELTA = 2.0
QUOTA_MISSED_FAVOR_DELTA = 3.0
"""`capitol_favor` change at month-end (FR-ECO-5); missing costs more
favor than meeting it gains, matching the Capitol's asymmetric leverage
over districts -- the exact numbers are still a guess pending the spec."""
SHOPKEEPER_FLOAT_TARGET = 200.0
"""Seeded onto a shopkeeper NPC's `Npc.float_target` at world-seed time
(Milestone D's shopkeeper jobs, e.g. `hob_trader`) -- otherwise every NPC
defaults to a 0 float and `economy.py`'s nightly top-up would never have
anything to actually top up."""

# Phase 2 Milestone D (illicit markets, FR-ECO-4) addition. `Location.illicit`
# already existed in the content schema (e.g. District 12's "hob") but was
# unused until this milestone wires it up. Full escalation (feeding a
# district-wide peacekeeper crackdown) needs the crisis system, which is
# still a no-op stub -- this only applies a per-transaction consequence to
# whoever got caught, not to the district at large.
MARKET_ILLICIT_DETECTION_PROB = 0.1
MARKET_ILLICIT_FINE = 30
MARKET_ILLICIT_JAIL_TICKS = 12

# Reputation system extension: reputation used to move only from `/work`'s
# win/lose result (`+1`/`0`). These add a miss penalty, win/loss-streak
# bonuses/penalties, a periodic relationship-based nudge, and an
# illicit-market-catch penalty. No spec document defines these numbers --
# they're reasonable placeholders, same caveat as the Milestone C/D blocks
# above.
REP_MISS_PENALTY = 2
"""Reputation lost when a shift is marked MISSED (never worked, not
excused) -- `panem_sim.systems.jobs._resolve_missed_shifts`. EXCUSED and
COMPLETED shifts don't touch reputation at all."""
REP_STREAK_LEN = 3
"""How many `/work` wins (or losses) in a row before the streak
bonus/penalty below kicks in, and again every further multiple of this
many -- an occasional loss stays reputation-neutral (matching the
request), but a real losing streak starts costing reputation, and a real
winning streak starts paying extra on top of the flat +1/win."""
REP_STREAK_BONUS = 1
REP_STREAK_PENALTY = 1
REP_RELATIONSHIP_CHECK_INTERVAL_DAYS = 7
"""How often (in sim-days) `panem_sim.systems.reputation` re-scores a
character's reputation from their NPC relationships -- weekly, not every
tick, so a single good/bad interaction doesn't spike reputation."""
REP_RELATIONSHIP_DELTA = 1.0
"""Reputation change per relationship classified "good" (affinity at or
above `STANCE_THRESHOLDS`' likes cutoff) or "bad" (at or below the
dislikes cutoff) at each periodic check -- reuses the same thresholds
`social.py` already classifies `Stance` with, rather than inventing a
second affinity scale."""
REP_ILLICIT_CAUGHT_PENALTY = 15
"""Reputation lost on top of the existing fine/jail/peacekeeper-pressure
consequence when an illicit-market trade gets caught
(`panem_bot.services.market._apply_illicit_consequence`)."""
