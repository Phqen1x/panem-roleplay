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
MISSES_TO_MASTERY_PENALTY = 3
"""Missing this many shifts in a row (or more) costs the character mastery
progress -- see `SHIFT_MASTERY_MISS_PENALTY` -- rather than the job itself;
jobs are no longer taken away for missed work (`panem_sim.systems.jobs` used
to fire a character at `MISSES_TO_FIRE`, a constant retired along with that
behavior)."""
SHIFT_MASTERY_MISS_PENALTY = 1
"""`Character.shifts_completed` lost (floored at 0) for every missed shift
once a character's `consecutive_missed` streak reaches
`MISSES_TO_MASTERY_PENALTY` -- so a habitual no-show's job-level progress
erodes for as long as the streak continues, the same way working a shift
builds it up one at a time."""
NEW_HIRE_GRACE_DAYS = 10
PLAYER_OUTPUT_WEIGHT = 0.35

WORK_GAME_WIN_WAGE_MULT = 1.5
WORK_GAME_LOSE_WAGE_MULT = 0.4
"""`/work`'s minigame (Minesweeper, `panem_api`'s Activity frontend):
winning pays `job.wage * WIN`, losing pays `job.wage * LOSE` -- replacing
the option-multiplier axis a player's free choice used to control (see
`panem_shared.shifts.resolve_shift_game`)."""
WORK_GAME_GRACE_TICKS = 1
"""How long past a shift's `tick_due` a *started* (`Shift.started_at_tick`
set) minigame stays open rather than being marked missed -- long enough
to actually go finish a Minesweeper board without racing the clock, short
enough that an abandoned game doesn't block a new shift from ever opening
or let `/work` be called well outside the character's own shift period.
This used to be a full `TICKS_PER_DAY` (24 ticks -- a whole extra day),
which let a player who'd merely launched the Activity once keep working
that same shift on later ticks long after it was due, drifting through
phases that had nothing to do with their assigned `shift_phase` and
blocking the next day's shift from ever opening (it stayed "still open"
right through the next occurrence of that phase). A short grace still
covers finishing the board in progress; it no longer doubles as an
open-ended extra work window."""

NO_ACTIVITY_WORK_WIN_PROBABILITY = 0.6
"""`/work` without `ACTIVITY_PUBLIC_URL` configured (no Minesweeper board to
actually win or lose) resolves immediately with a coin-flip at this
probability instead -- the classic 3-option choose-your-risk menu was
retired along with the `jobs.yaml` catalog it was authored against, so
win/lose is now the only outcome axis `/work` has, configured or not."""

DISTRICT_WEALTH_WAGE_MULT_MAX = 1.5
DISTRICT_WEALTH_WAGE_MULT_MIN = 0.5
"""The two ends of the per-district wage scale `panem_shared.shifts.
district_wealth_multiplier` interpolates between, keyed purely on
`District.id` (0..12, canon's own wealth ordering -- the Capitol is
richest, District Twelve poorest, with the career/luxury districts ahead
of the outlying ones in between). No content authoring needed: this is a
straight line from `MAX` at id 0 to `MIN` at id 12, not a per-district
value someone has to hand-tune and keep in sync as new districts change."""

PLAYER_JOB_BASE_WAGE = 20.0
"""Every player job pays from this same flat base now that a job is a
free-typed title (`Character.job_title`) rather than a catalog entry with
its own authored wage -- `panem_shared.job_levels`' level multiplier,
`district_wealth_multiplier`'s per-district scale, and
`WORK_GAME_WIN_WAGE_MULT`/`LOSE` stack on top of it, and
`panem_sim.systems.economy`'s market price on top of that again.
`panem_shared.shifts.resolve_shift_game` divides the final result by
`SHIFT_DURATION_TICKS`: this is meant as one shift's total pay, but a
shift can now be worked once per tick across its whole `SHIFT_DURATION_
TICKS`-tick window (see "Notes on working a shift multiple times per
tick" in the README) rather than once total, so paying the full base
wage on every one of those works would pay up to `SHIFT_DURATION_TICKS`x
too much for the same shift."""

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

JOB_LEVEL_BONUS_GOOD_CHANCE: dict[str, float] = {
    "apprentice": 0.05,
    "novice": 0.15,
    "journeyman": 0.30,
    "master": 0.45,
    "expert": 0.60,
}
"""Chance of producing a *second* unit of the district's quota good on a
won shift, scaled by `JobLevel` the same way `JOB_LEVEL_WAGE_MULTIPLIERS`
is -- a Master at their trade is more likely to turn a good shift into
extra output than an Apprentice is, on top of already earning a bigger
wage for it. Only ever rolled on a win (`panem_shared.shifts.
resolve_shift_game`): a skipped/neutral shift always produces exactly one
unit regardless of level, and a real loss produces none."""

PRICE_EXPONENT = 0.5
PRICE_CLAMP_MIN = 0.4
PRICE_CLAMP_MAX = 4.0
PRICE_EMA_ALPHA = 0.2
SELL_DISCOUNT = 0.85
ILLICIT_PRICE_MULT = 0.6

CAPITOL_CUT_FRACTION = 0.10
"""The Capitol's off-the-top skim of every good's *national* daily
production (`panem_sim.systems.economy`'s redistribution pass), before
what's left is divided back up among the districts that trade it. Applies
uniformly to every good -- there's no per-good tax rate to author or keep
in sync as goods change."""

MARKET_BASELINE_ALLOCATION_FRACTION = 0.10
"""Of what's left after `CAPITOL_CUT_FRACTION`, this share of a good's
national pool is split *equally* across every district that trades it
(produces or imports it) regardless of how much any of them actually
produced -- a floor so even a district that made none of a good itself
still gets some. The remaining `1 - MARKET_BASELINE_ALLOCATION_FRACTION`
is split proportional to each trading district's share of *total*
national production value that day (`panem_sim.systems.economy.
_district_production_value`) -- a district that produces a lot of
everything gets more of everything, including goods it doesn't make
itself, the same way a real wealthy region can outbid a poor one for
imports."""

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

LLM_REPLY_MAX_TOKENS = 200
LLM_REPLY_TEMPERATURE = 0.8
LLM_REPLY_FREQUENCY_PENALTY = 0.6
"""Penalizes tokens by how often they've already appeared in this request
(OpenAI-compatible field, honored by Lemonade's llama.cpp-backed server) --
the small local models this feature targets (`lemonade/README.md`'s
Profiles) fall into repeating the same phrase or the same memory almost
verbatim far more readily than a large hosted model, even with the
conversation's own prior turns as `history` right there in context."""
LLM_REPLY_PRESENCE_PENALTY = 0.4
"""Penalizes any token that has appeared at all, on top of `LLM_REPLY_
FREQUENCY_PENALTY` -- together they push the model off a topic (e.g. the
same memory) it has already spent a turn on, not just off exact repeated
wording."""
NPC_BACKGROUND_PROMPT_MAX_LEN = 240
"""An authored `NpcContent.backstory` can run up to 1500 characters (fine
for the `/resident profile` embed it's normally shown in), but every
dialogue reply resends the whole request header -- so the LLM's `[NPC]
... background` line gets only this many characters of it (truncated with
an ellipsis), enough to color a reply without dominating the prompt."""

MIN_WORDS_REPLY = 6
"""The floor `dialogue.generate_reply`'s length-matching clamps to -- a
one-word message ("Hey") shouldn't force the NPC down to a one-word reply,
just a short one."""
REPLY_LENGTH_RATIO = 2.0
"""How many words of reply per word of the message being replied to,
before clamping to `[MIN_WORDS_REPLY, MAX_WORDS_REPLY]` -- an NPC's line
should track how much the speaker just said, not sit at a flat cap
regardless of whether they were greeted with "hey" or given three
sentences of news."""

RELATIONSHIP_SUMMARY_MAX_WORDS = 120
"""The target length `dialogue.summarize_engagement`'s prompt asks for,
and the hard word-count this module truncates the LLM's answer to
defensively (mirroring `NPC_BACKGROUND_PROMPT_MAX_LEN`'s truncate-with-
ellipsis pattern) -- `RelationshipRow.summary` rides in every future
dialogue request's `[SPEAKER]` block for this pair, so it has to stay
short enough not to dominate the prompt the way an ever-growing transcript
would."""

TRANSPORT_GOOD_ID = "transport"
"""The one good every cross-district trip spends units of -- District
Six's own quota good (`data/districts/d6.yaml`) -- replacing the old
flat per-destination cash ticket (`train_ticket_d{N}`, one synthetic good
per district that never participated in real supply/demand at all).
Buying it at a district market like any other good, then spending it to
travel, means a district's access to travel is subject to the same
national redistribution (`CAPITOL_CUT_FRACTION`/`MARKET_BASELINE_
ALLOCATION_FRACTION`) as everything else -- a poor district can end up
with genuinely scarce, pricier tickets, not just a fixed toll."""
TRANSPORT_UNITS_PER_TRIP = 2
"""How many units of `TRANSPORT_GOOD_ID` one `/travel district:<id>` costs
-- covers the whole round trip (there and back) in one purchase, per "you
need to purchase 2 units of transportation to go anywhere and then back
home." A free route (`panem_bot.services.travel.is_free_route`/
`is_free_victor_route`) skips this entirely, same as it used to skip the
cash price."""
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

# Housing system: houses/apartments/inns, procedurally seeded per district
# (no hand-authored YAML content -- `panem_sim.world.seed_properties`).
# No spec document defines any of this; same placeholder caveat as the
# other non-spec-sourced blocks above.
HOUSE_BASE_PRICE_BY_TIER: dict[str, float] = {
    "apprentice": 500.0,
    "novice": 1_200.0,
    "journeyman": 2_500.0,
    "master": 5_000.0,
    "expert": 10_000.0,
}
"""A house's base purchase price before district/mastery/reputation
modifiers (`panem_bot.services.housing.quoted_price`), keyed by the
`JobLevel` tier it's gated to."""
APARTMENT_UNIT_BASE_PRICE = 800.0
"""Buying out a single vacant apartment unit -- not tier-gated."""
APARTMENT_UNIT_BASE_RENT = 40.0
"""Listed rent for a vacant apartment unit before any landlord repricing."""
INN_BASE_NIGHTLY_PRICE = 15.0
"""What a night at an NPC-run inn costs before any owner repricing --
covers lodging (fatigue restoration) and food (hunger reduction) both."""

HOUSES_PER_TIER_PER_DISTRICT = 3
APARTMENT_COMPLEXES_PER_DISTRICT = 2
APARTMENT_UNITS_PER_COMPLEX = 6
INNS_PER_DISTRICT = 1

HOUSING_MASTERY_PRICE_STEP = 0.05
"""A listing's price shifts by this fraction per job-level difference
between buyer and seller -- a buyer two levels above the seller pays
`1 - 2*0.05 = 0.9`x, one two levels below pays `1.1`x. Applies to houses,
apartment-complex buyouts, and inns alike (`panem_bot.services.housing.
quoted_price`) -- an NPC seller's level defaults to a house's own tier
(parity at a matched buy), or the buyer's own level for untiered
apartments/inns (parity always, until a real player-to-player resale
introduces a genuine gap)."""
HOUSING_REPUTATION_PRICE_STEP = 0.002
"""Houses only, on top of the mastery step: `1 - reputation *
HOUSING_REPUTATION_PRICE_STEP`, clamped to `HOUSING_PRICE_MULT_*` --
"better price on houses with better reputation in your district.\""""
HOUSING_PRICE_MULT_MIN = 0.5
HOUSING_PRICE_MULT_MAX = 2.0
"""Clamp on the combined mastery/reputation/district price multiplier --
keeps a very high reputation or a very lopsided mastery gap from making a
listing free or absurdly expensive."""
HOUSING_DISTRICT_UNREST_PRICE_WEIGHT = 0.3
HOUSING_DISTRICT_FAVOR_PRICE_WEIGHT = 0.02
"""How much a district's `unrest` (depresses prices) and `capitol_favor`
(raises them) shift `Property.suggested_price` in the daily refresh
(`panem_sim.systems.housing`) -- "the sim suggests prices that balance
with the economy," reusing `DistrictState` fields already tracked for
other systems rather than a new housing-specific economic indicator."""

FATIGUE_MAX = 100.0
FATIGUE_MIN = 0.0
FATIGUE_COST_PER_WORK = 8.0
"""Docked per `/work` resolution (`panem_shared.shifts.
apply_shift_outcome`) -- working a shift multiple times in one day (the
once-per-tick multi-work feature) drains fatigue proportionally more."""
FATIGUE_COST_PER_INTERACTION = 3.0
"""Docked per qualifying proxied RP message (`panem_bot.cogs.proxy`,
gated on the same length check as RP-credit shift completion so a
one-word message doesn't drain it)."""
FATIGUE_RESTORE_PER_TICK = 15.0
"""Base fatigue restored per tick slept (`/sleep`), before the location
multiplier below."""
FATIGUE_GROUND_SLEEP_MULT = 0.5
"""Sleeping with no bed at all -- no owned house, no leased apartment, no
inn stay -- restores fatigue at half the rate of a real bed."""
FATIGUE_EXHAUSTION_THRESHOLD = 20.0
FATIGUE_EXHAUSTION_HEALTH_PENALTY = 2.0
"""If fatigue is still at or below the threshold at the nightly needs
check, health takes this extra hit -- mirrors `HEALTH_DECAY_PER_NIGHT`'s
hunger-driven decay in `panem_sim.systems.needs`, same nightly cadence."""

# Housing: mortgages (financed purchases + refinancing against equity),
# auctions, and foreclosure. Same "no spec, reasonable placeholder" caveat.
MORTGAGE_DOWN_PAYMENT_PCT = 0.2
MORTGAGE_INTEREST_RATE = 0.1
"""A flat surcharge on the financed amount at origination, not compounding
interest -- the simplest thing still recognizably a mortgage."""
MORTGAGE_TERM_TICKS_DEFAULT = TICKS_PER_DAY * 90
"""90 sim-days of installments."""
MORTGAGE_PAYMENT_INTERVAL_TICKS = TICKS_PER_DAY
"""One installment/maintenance charge per sim-day."""
MORTGAGE_MAX_LTV = 0.8
"""Loan-to-value cap for `/housing refinance` -- total `mortgage_principal`
can never exceed this fraction of the property's current listed value."""
MORTGAGE_MISSES_TO_FORECLOSE = 3
"""Consecutive missed installments (a real mortgage, or an inn's daily
maintenance -- same field, same loop, see `Property`'s docstring) before
`panem_sim.systems.housing` repossesses the property and auctions it off."""
RENT_MISSES_TO_EVICT = 3
INN_DAILY_MAINTENANCE_COST = 5.0
"""Set on `Property.mortgage_payment` when a player buys an inn --
`mortgage_principal` stays `0` for an inn (there's nothing to pay off,
maintenance recurs forever), only `mortgage_payment`/`mortgage_next_due_
tick`/`mortgage_missed_payments` are meaningful."""
AUCTION_DURATION_TICKS_DEFAULT = TICKS_PER_DAY * 3
"""How long a `PropertyAuction` (voluntary or foreclosure) stays open for
bids before `panem_sim.systems.housing` resolves it."""

# NPC engagements: group RP threads with one or more NPCs, plus NPCs
# occasionally chatting with each other unprompted.
ENGAGEMENT_DEFAULT_IDLE_TIMEOUT_MINUTES = 30
"""Seed value for the `EngagementSettings` singleton row on first migrate
-- staff can change the live value afterward with `/staff engagement
set-timeout`; this constant is never read again once that row exists."""
ENGAGEMENT_IDLE_CHECK_INTERVAL_MINUTES = 5
"""How often `EngagementCog`'s background task scans open engagements for
`last_message_at` past the current timeout, mirroring `SceneCog.
archive_idle_scenes`'s own `tasks.loop` cadence pattern."""
ENGAGEMENT_MAX_PARTICIPANTS = 5
"""`/engage start` has no true variadic argument (Discord slash commands
don't support one), so participants are `ENGAGEMENT_MAX_PARTICIPANTS`
individually autocompleted, optional slots (`participant_1` required, the
rest optional) rather than one free-text field."""
ENGAGEMENT_HISTORY_HARD_CAP = 200
"""A defensive outer ceiling on how many prior `SceneMessage` rows (player
lines and NPC replies alike) `proxy.py` will ever fetch as conversation
history for an engagement reply -- short-term memory is meant to cover the
*whole* current engagement (every message since it opened), not a rolling
window, since engagements already auto-close on their own idle timeout
(`EngagementSettings.idle_timeout_minutes`) long before a real
conversation could approach this many turns. This only exists so a
pathological engagement that somehow never closes can't grow the LLM
request without bound; it should never be hit in normal play."""
NPC_NAME_MATCH_MIN_LEN = 3
"""In a multi-participant engagement, an NPC only replies to a message
naming them -- but matching a first/last name shorter than this many
characters (e.g. "Al") risks firing on an unrelated word that happens to
contain it, so a name below this length is skipped as a match candidate
(the NPC just won't be addressable by that name alone in a crowd; a
longer name-part still works)."""
NPC_CHATTER_CHANCE_PER_TICK = 0.01
"""Per (district, location) with 2+ co-located, unengaged NPCs, the
per-tick odds `panem_sim.systems.npc_chatter` rolls for a short,
unprompted NPC-to-NPC conversation -- "not happen particularly often"."""
NPC_CHATTER_MIN_LINES = 2
NPC_CHATTER_MAX_LINES = 4
"""A random NPC-NPC exchange runs this many lines total, alternating
speakers -- "should only last a few messages"."""
