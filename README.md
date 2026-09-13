# Panem: The Long Year

A persistent, NPC-driven Discord roleplay world that runs between Hunger
Games sessions. Built from `panem-long-year-build-plan-python.md` (the
Plan) and `panem-long-year-spec.md` (the Spec, which wins on any
disagreement with the Plan).

This repository implements **Phase 0 — Foundation** (character creation and
staff approval, Discord Forum-based scenes, and character proxying),
**Phase 1 — World Simulation** (Plan §4: a deterministic tick loop, NPC
movement, ambient narration, intra-district `/travel`/`/where`), and
**Phase 2 — Economy** (Plan §5: nightly hunger/health, job shifts,
`/work`, `/job list|apply|quit`, district-level supply/demand pricing,
exports, quotas, shopkeeper restocking, `/market prices|buy|sell`/
`/inventory`, and now cross-district travel by train -- `/travel
district:<id>`, tickets, transit ticks, and visitor roles -- plus a real
`scripts/calibrate.py`). Phase 2 is complete.

**Phase 3 — NPC Minds** (Plan §6) is underway: `data/npcs/*.yaml` gives
every one of the 299 seeded residents a real name, age, job, traits, and
a backstory/appearance (generated deterministically by
`scripts/npc_generate.py` from each district's own industry/culture --
see the Milestone I notes below for exactly what that does and doesn't
mean), `panem_sim/systems/social.py` builds real `RelationshipRow`s from
shared-location proximity, `memory.py` forms and prunes `Memory` rows
from notable events, and `/resident profile` surfaces a resident's
personality, backstory, and their stance toward a character.

**Phase 4 — Crises** (Plan §7) is underway too:
`panem_sim/systems/crisis.py` tracks each district's unrest (fed by
chronic hunger and missed quotas) and peacekeeper pressure (fed by
illicit-market catches), escalating/recovering through `CRISIS_THRESHOLDS`
and announcing a level change as a `Bulletin`; `/staff district <id>`
shows the raw numbers. See the Milestone G notes below for what's a
placeholder pending the real Spec §7 text.

**Phase 5 — The Activity** (Plan §8) has a working backend and a real,
testable frontend: `panem_api` is a FastAPI REST/WebSocket bridge
serving each district's live NPC/character positions (`GET /districts`,
`GET /districts/{id}/positions`, `WS /ws/districts/{id}/positions`), fed
by `panem_sim` writing a JSON snapshot to Redis every tick, plus a
static single-page app (`packages/panem_api/src/panem_api/static/`) it
serves at its own root: a schematic live map (no real map art exists
yet -- see the Milestone J notes) with a district picker, driven by the
same position feed. It does the Discord embedded-app-sdk OAuth handshake
(`GET /activity/config`, `POST /activity/token`) when actually running
inside a Discord Activity iframe, and falls back to an unauthenticated
"preview mode" when opened directly in a browser, which is how it's been
tested end-to-end in this session (no live Discord Activity install was
available to verify the real OAuth path against -- see the Milestone J
notes for exactly what is and isn't verified). The data endpoints
(`/districts*`) still enforce no auth of their own.

**Phase 6 — LLM Dialogue** (Plan §9) has a first real slice working: `/talk
character:<name> resident:<name> message:<text>` lets a character speak to an NPC at
their location and get a reply, via `panem_bot/services/dialogue.py` calling a local
Lemonade OmniModel server (`lemonade/`, ported from
[PR #4](https://github.com/Phqen1x/panem-roleplay/pull/4)) when `DIALOGUE_PROVIDER` (or a
specific NPC's override) says to, with a free, no-server template fallback otherwise and
on any LLM failure. See the Milestone K notes below for exactly what's wired up, what's
still just a documented contract (narration/broadcast/speech modes), and what's verified
against a mocked HTTP transport vs. a real Lemonade server (not available in this
sandbox).

## Layout

```
packages/
  panem_shared/   data model (SQLAlchemy), content YAML schemas/loaders, settings, enums, world event types
  panem_bot/      the discord.py process: commands, proxying, scenes, staff tools, narration, travel, jobs
  panem_sim/      world tick loop, NPC movement (Phase 1), needs/jobs/economy/travel (Phase 2)
  panem_api/      FastAPI REST/WebSocket bridge for the Activity's live map (Phase 5)
data/             districts, goods, jobs, routes (content YAML, validated at boot)
migrations/       Alembic migrations
scripts/          setup_guild.py (Phase 0), calibrate.py (Phase 2), plus stubs for later-phase scripts
deploy/           docker-compose, Dockerfile, systemd unit
tests/            pytest (service-layer unit tests; no live Discord needed)
```

## Setup

Requires [uv](https://docs.astral.sh/uv/) (it installs the pinned Python
itself — no system Python version dependency).

```bash
uv sync --all-packages --dev
cp .env.example .env   # fill in DISCORD_TOKEN, DISCORD_GUILD_ID, DATABASE_URL, REDIS_URL, ...
```

Start Postgres and Redis (via `deploy/docker-compose.yml`, or locally), then:

```bash
uv run alembic upgrade head
```

Register the bot's slash commands and Discord-side channels/roles/webhooks
for every district (idempotent — safe to re-run):

```bash
uv run python scripts/setup_guild.py
```

Note the `APPROVAL_CHANNEL_ID` / `LOG_CHANNEL_ID` it prints and add them to
`.env`. Then run the bot:

```bash
uv run python -m panem_bot.main
```

Run the world simulation alongside it (same `.env`, `WORLD_SEED` sets the
deterministic RNG seed and `TICK_INTERVAL_SECONDS` the tick length — see
`.env.example`):

```bash
uv run python -m panem_sim.main
```

On first run it seeds a `district_state` row and a synthetic NPC
population per district (see "Notes on this Phase 1 build" below), then
ticks on `TICK_INTERVAL_SECONDS`. With the bot running too, NPC movement
shows up as ambient narration in each location's pinned thread, and
`/travel`/`/where` let a player move their own character around the
district.

## Development

```bash
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy packages/panem_shared/src packages/panem_sim/src   # strict type check (NFR-11)
TEST_DATABASE_URL=postgresql+asyncpg://panem:panem@localhost:5432/panem_test \
  uv run pytest --cov=panem_sim --cov=panem_shared --cov=panem_bot
```

Tests need a real Postgres (content uses `JSONB`/`ARRAY` columns, which
SQLite can't represent) — point `TEST_DATABASE_URL` at a throwaway
database; the schema is created fresh each test session and every test
runs inside a rolled-back savepoint.

## Notes on this Phase 0 build

- **Untestable without a live guild**: the `discord.py` gateway glue in
  `panem_bot/cogs/*` and `panem_bot/bot.py` can't be exercised by this
  test suite (no Discord test harness was available while building this).
  All decision logic that *can* be unit-tested without Discord — character
  validation/approval, proxy resolution and access checks, scene tag
  resolution and idle-archive selection, the outbound rate-limited queue,
  and content YAML validation — lives in `panem_bot/services/*` and
  `panem_bot/outbound.py`, is fully covered by `tests/`, and is what the
  cogs call into. Before relying on this in production, run
  `scripts/setup_guild.py` against a real test guild and walk through
  `/character create` → approve → `/rp` → proxying → `/scene start`/`close`
  by hand once.
- **Approval/log channels** are read from `.env` (`APPROVAL_CHANNEL_ID`,
  `LOG_CHANNEL_ID`), matching the Plan's `.env` schema; `setup_guild.py`
  creates them and prints the ids to copy in.
- **District roles** are looked up by exact name match (the district's
  `name` field in its YAML) rather than a stored role id, unless a
  `CAPITOL_ROLE_ID` / `DISTRICT_N_ROLE_ID` override is set in `.env` (see
  `.env.example`), in which case `setup_guild.py` uses that existing role
  instead of finding/creating one by name.
- **A member's district role picks their character's district, not a
  dropdown.** Members are expected to already hold exactly one district
  role (via Discord's own onboarding flow) before running `/character
  create`; the bot skips straight to the creation modal for that district.
  Someone with no district role, or with more than one, is refused with a
  message telling them why. The bot no longer grants or removes a
  district role itself on approval/retirement — onboarding is the only
  source of truth for who's in which district now. Add the district roles
  `setup_guild.py` creates (or your own, if using the `_ROLE_ID`
  overrides) as choosable options in Discord's own Server Settings →
  Onboarding, ideally in a single-select group so members land in exactly
  one.
- **One active character per user by default.** `MAX_CHARACTERS_PER_USER`
  in `.env` sets the guild-wide default (1); staff can raise or lower it
  for one specific person with `/staff character_limit <user> [limit]`
  (omit `limit` to reset them back to the default).
- Item-gated `restricted` locations (`access_items`) always deny access
  for now, since inventories don't exist until Phase 2 — only
  `access_jobs` and holding any staff-granted `Position` (Victor,
  Gamemaker, Governor — see the Notes on Positions below) grant access
  in Phase 0.
- **Character age**: reaping-eligible districts (1-12) are capped at age 18;
  only The Capitol may create adult characters, up to 80.
- **Jobs are editable in Discord**, not just in `data/jobs.yaml`: `/staff
  job set <job_id> <district> [fields...]` adds or patches a job's base
  fields (title, workplace, wage, shift_phase, slots, legal, and the rarer
  optional fields), and `/staff job option <job_id> <slot> [fields...]`
  patches one of its 3 options at a time -- both take named arguments
  instead of a JSON blob, validated the same way `jobs.yaml` is (including
  that `workplace` is a real location in that district). Either command
  uses patch semantics: any argument left unset keeps the job's current
  value, so staff only pass what's actually changing; a brand-new job id
  requires title/workplace/wage/shift_phase/slots up front and starts with
  3 placeholder options for `job option` to fill in (`Job` always needs
  exactly 3). A few fields that are inherently open-ended maps (`produces`,
  `ladder_requirement`, an option's `risk_effect`) still take a small JSON
  object rather than one argument per possible key; pass `none` (or `-1`
  for the two numeric fields, `min_reputation`/`peacekeeper_attention`) to
  clear an optional field back out. `/staff job remove <job_id>` takes a
  job out entirely (whether it came from YAML or a prior override), and
  `/staff job list <district>` / `/staff job show <job_id>` inspect the
  current merged view. Changes take effect immediately, no restart needed
  — every job lookup in the bot goes through `panem_bot.services.jobs`,
  which layers `job_overrides` (Postgres) on top of `jobs.yaml` at read
  time.
- **Autocomplete** replaces free typing everywhere a command takes a
  character name, job id, or district: `/character edit|retire|status|
  avatar|tag`, `/staff kill|note|job set|job option|job remove|job show|
  job list`, and `/scene start|move|invite` all suggest matching options as
  you type, scoped to what's relevant (e.g. `/character edit` only offers
  your own pending submissions). See `panem_bot/autocomplete.py` for the
  shared callbacks; district/scene-scoped ones live next to their commands
  in `cogs/scenes.py`.
- `/staff delete_pending <character> [reason]` removes a pending
  application outright (optionally DMing the applicant why), for
  submissions staff want gone rather than rejected-and-kept.
- **Character names must be unique** (case-insensitively), enforced both
  in the bot and by a DB-level unique index — see "Upgrading past
  duplicate character names" below if you're updating an existing guild.
- **Rejected applications aren't kept.** Clicking Reject posts the full
  application (name, district, age, appearance, backstory, who rejected
  it, and why) to `#panem-log`, DMs the applicant the reason, then deletes
  the character row entirely — a rejected application never became a real
  character, so nothing about it stays in `characters` (and its name is
  immediately reusable).
- **Avatar is now part of `/character create` and `/character edit`**, as
  a 5th (optional) field on `CharacterDetailsModal` -- Discord's modal
  input cap, so this is the most it can hold without a second step. A URL
  set there goes to staff with the rest of the application (shown as the
  approval embed's thumbnail) instead of bypassing review the way setting
  it via `/character avatar` after approval still does; only a URL is
  accepted here (a modal can't take a file upload) -- uploading an image
  still requires `/character avatar` post-approval.

## Notes on this Phase 1 build

- **Milestones A + B only** (Plan's own breakdown): the tick loop skeleton,
  real NPC movement, ambient narration, and intra-district travel are
  built and Phase 1's acceptance criteria (Plan §4.5) are met. Milestones
  C/D — needs, jobs/shifts, markets, quotas, cross-district travel (Phase
  2, Plan §5) — have their schema and `data/jobs.yaml`/`goods.yaml`/
  `routes.yaml` content already in place but no runtime systems yet;
  `panem_sim/systems/needs.py`, `jobs.py`, `economy.py`, `social.py`,
  `memory.py`, `crisis.py`, and `games.py` are no-op stubs in their final
  spec order (FR-TCK-2), ready to be filled in one at a time without
  reordering the tick loop.
- **No real NPC content yet.** `data/npcs/*.yaml` (names, traits, speech
  style, relationships — Phase 3) doesn't exist. `panem_sim/world.py`
  seeds `SYNTHETIC_NPCS_PER_DISTRICT` (23) minimal NPCs per district
  directly into `npcs`/`npc_schedule` on first run instead — no traits or
  dialogue, just enough of a body (home location, a generic
  home/public/market schedule) for movement and, later, jobs/shopkeeper
  mechanics to act on. Phase 3 enriches these same rows in place; nothing
  here blocks it.
- **The tick loop is one DB transaction per tick** (FR-TCK-3): each tick
  loads `WorldState` from `world_clock`/`district_state`/`npcs`/
  `npc_schedule`, runs every system in `panem_sim.systems.FIXED_ORDER`,
  and commits together. A tick that raises is retried once; a second
  failure pauses the loop and publishes to the `sim:alerts` Redis channel
  rather than crash-looping or silently skipping ahead.
- **Per-tick randomness is seeded, not global** (FR-TCK-4): every system
  draws from `panem_sim.rng.tick_rng(world_seed, tick)`, a `random.Random`
  reseeded from a `sha256` of `(world_seed, tick)` — deterministic across
  restarts, unlike Python's per-process-randomized `hash()`.
- **Events are durable before they're announced** (NFR-7): each tick's
  events are written to `world_events` (`announced=false`) in the same
  transaction as the state change that produced them, then published to
  Redis `world:events` and marked `announced=true` only after that commit
  succeeds. `panem_sim.main` calls `recover_pending_events` on startup to
  re-publish anything a crash left unannounced, so a process restart never
  loses or silently drops an event.
- **Ambient narration posts through the same `OutboundQueue`** player
  proxy and NPC messages already use (`panem_bot/narrator.py`,
  `SendPriority.NARRATOR` — already the lowest priority, so it never
  starves a player mid-scene), via the same forum webhook credentials
  `/rp` proxying uses. `Bulletin` events (district-wide notices) route to
  a pinned `Board` thread inside that district's own forum, tracked by
  `ChannelKind.BOARD` — `scripts/setup_guild.py` creates it per district
  (see the Milestone D notes below); this path sat wired-but-inert until
  the economy system started actually emitting `Bulletin`s.
- **`/travel` and `/where` take a character name** (`own_approved`
  autocomplete), matching every other character-scoped command in the
  bot, rather than resolving an "active" character from `/rp`'s
  thread-session mechanism — that exists specifically for "who is
  speaking in this thread," a different concern from "where is my
  character." Both were intra-district only at the time Phase 1 shipped;
  `/travel district:<id>` (cross-district, tickets/transit/visitor roles)
  is a later addition to the same command -- see the Phase 2 notes below.

## Notes on this Phase 2 build

- **Phase 2 is complete**: needs, jobs/shifts, the economy (markets/
  quotas/exports/shopkeepers), cross-district travel, and
  `scripts/calibrate.py` are all built. `panem_sim/systems/crisis.py`,
  `social.py`, and `memory.py` remain no-op stubs (Phase 3/4 scope) — see
  the Milestone D notes below for the economy, and the Milestone E notes
  further down for travel and calibration.
- **The hunger/health tunables in `constants.py` are placeholders, not
  the spec's real numbers.** `panem-long-year-spec.md` §10 wasn't
  available in the session that built this (only the Plan's
  higher-level description) — `NIGHTLY_LIVING_COST`, `HUNGER_*`, and
  `HEALTH_*` were chosen to be reasonable, not authoritative. Re-check
  them against the real spec before relying on the numbers, though the
  mechanism (pay or hunger rises; high hunger erodes health) is right.
- **`JobOption.risk_effect` only recognizes the keys `data/jobs.yaml`
  actually uses** (`health`, a delta like `-20`) plus two speculative
  extras (`reputation`, `jailed_ticks`) no current job exercises.
- **NPCs never get a `Shift` row or miss-tracking** — `shifts.character_id`
  is a FK to `characters`, not `npcs`, and there's no player to notify
  either way. An NPC with a matching job instead just probabilistically
  "completes" it in place each phase boundary (`NPC_JOB_COMPLETION_PROB`,
  also a placeholder), feeding `Npc.money`. `economy.py`'s supply side
  doesn't replay that same roll for district production, though -- see
  the Milestone D notes below.
- **A missed shift or firing is a pure DB-state change, not a push
  notification.** There's no DM when a character is warned or fired;
  they find out via `/job list`/`/work` failing next time. A proper
  notification would need a new DM-capable event kind alongside the
  existing district-scoped `NarrationLine`/`Bulletin`, which is more
  than this milestone's scope.
- **RP credit (`FR-PRX-7`)** completes an open shift automatically
  (as if `/work` picked option 0) when a proxied message is
  `RP_CREDIT_MIN_CHARS` (120) characters or longer *and* posted in a
  scene whose location matches the job's `workplace` — checked in
  `panem_bot/cogs/proxy.py`'s `on_message`, right where a scene's
  location already syncs onto the character.
- **Promotion (`FR-JOB-9`) only checks `ladder_requirement`'s
  `min_reputation` key** — its exact schema wasn't available either;
  a job with no `ladder_next` is never promotion-eligible, and one with
  no `min_reputation` in its requirement is always eligible once it has
  `ladder_next` set. Shown as a note after `/work`, not a separate
  accept/decline flow.
- **Tesserae (FR-ECO-7) is not implemented.** This RPG's rules don't use
  it, so `/tesserae claim`, `Character.tesserae_count`, and the Redis
  claim-tracking key were removed rather than built out further.
- **`/time`** shows an actual 12-hour clock (e.g. `12:00 PM`) and phase,
  plus a real-seconds countdown to the next tick, instead of raw tick
  numbers — `panem_shared.simtime.clock_string` scales against
  `TICKS_PER_DAY` (not a hardcoded hour-per-tick), and the countdown
  reads `WorldClock.updated_at` (new column, migration `2b838e376cd7`)
  against `tick_interval_seconds`. `/character status` also now shows
  whether a shift is open, and its due tick, in a new **Shift** field.
- **Synthetic NPCs get a name and (usually) a job at seed time**
  (`panem_sim/world.py`), not just a name pool: each is weight-assigned
  one of its district's jobs (weighted by `slots`), and its schedule
  pulls it toward that job's workplace during the job's `shift_phase`
  instead of the generic public/market spread. Still well short of
  Phase 3 (no traits/speech/personality) — see `world.py`'s module
  docstring.
- **Ambient narration only announces two kinds of NPC arrival**: showing
  up at a job's workplace during its own shift phase, or arriving home
  at night (`panem_sim/systems/schedule.py::_arrival_reason`). An NPC's
  schedule sending them to the market or square mid-afternoon still
  moves them (and updates `/resident where`'s answer) but posts nothing
  — with ~23 NPCs/district re-rolling a weighted choice every tick, the
  unfiltered version made a district's ambient thread unreadable.
- **`/resident list`/`/resident where`** (new `cogs/residents.py`) is how
  a player finds a district's residents (name + job) and looks up a
  specific one's current location, since narration no longer covers most
  of their movement.

## Notes on this Milestone D (markets/quotas/exports/shopkeepers) build

- **Supply is real for players, expected-value for NPCs.**
  `panem_sim/systems/economy.py` sums actual completed `Shift.output`
  for player production, but for NPCs it computes
  `job.produces * NPC_JOB_COMPLETION_PROB` per NPC holding that job
  rather than replaying `jobs.py`'s own per-NPC stochastic roll --
  the two systems don't share bookkeeping on purpose (see the module
  docstring): `jobs.py` still pays each NPC individually and randomly for
  flavor, while pricing only needs an aggregate, deterministic number.
- **Demand has no real consumption model behind it.** Nothing tracks a
  character or NPC actually eating bread or burning coal, so demand is a
  flat `MARKET_DEMAND_PER_CAPITA` rate against `District.population_base`
  for every good the district produces or imports -- a much cruder
  placeholder than the supply side gets, and one of the numbers most
  worth revisiting against the real spec.
- **Exports are capacity/supply-capped, not "scaled by D6/D5 ratios."**
  The Plan mentions that phrase for FR-ECO-6; its formula wasn't
  available in this session's context, so `_run_exports` just ships
  `min(route.capacity, remaining supply)` along each `routes.yaml` route,
  in file order, with several routes sharing one `(from, good)` remaining
  pool when a district has multiple export destinations for the same
  good (District 12's coal, five ways). One real consequence worth
  knowing: several districts' route capacity for their specialty good
  comfortably exceeds their daily production, so nearly everything gets
  exported and the *local* price for that good sits near
  `PRICE_CLAMP_MAX` most of the time -- a direct, correct result of the
  model as built, not a bug, but worth knowing before treating local
  specialty-good prices as meaningful in play.
- **Quota progress is specifically exports to the Capitol of a district's
  own quota good** (matching every district's `routes.yaml` entry, which
  ships exactly that good to district 0) -- not total production.
  Evaluated at the first tick of a new month: `capitol_favor` moves by
  `QUOTA_MET_FAVOR_DELTA`/`-QUOTA_MISSED_FAVOR_DELTA` (both placeholders)
  and `quota_progress` resets.
- **A "shopkeeper" is any NPC whose job's `workplace` resolves to a
  `LocationKind.MARKET` location** (`economy.is_shopkeeper_job`) --
  already true of real content (District 12's `hob_trader`). World-seed
  time now gives such an NPC a non-zero `float_target`
  (`SHOPKEEPER_FLOAT_TARGET`); without that fix -- found by an actual
  seed-and-simulate smoke test, not just unit tests -- every NPC's
  `float_target` defaulted to 0 and the nightly treasury top-up in
  `_restock_shopkeepers` never had anything to do.
- **The daily `Bulletin` (FR-ECO-8) needed somewhere real to post, which
  `scripts/setup_guild.py` never created** -- found live, after the
  economy system started actually emitting `Bulletin`s: every day
  boundary, `panem_bot/narrator.py` looked up each district's
  `ChannelKind.BOARD` row and either found none (a guild set up before
  this milestone) or, if one existed anyway, a channel id Discord no
  longer recognized, and logged an `Unknown Channel` failure per district
  instead of posting. `setup_guild.py` now gives each district's forum a
  pinned, never-archived `Board` thread (tagged `Board`, alongside the
  per-location ambient ones) and posts `Bulletin`s there through that
  same forum webhook, rather than a separate top-level channel --
  reconciled by name/id on every run the same way the forum/ambient
  threads already were, so re-running it against a guild that hit this
  fixes it, no manual DB cleanup needed.
- **Illicit markets (`Location.illicit`, e.g. District 12's "hob") apply
  a per-transaction consequence, not district-wide escalation.** A
  `/market buy`/`sell` at an illicit location rolls
  `MARKET_ILLICIT_DETECTION_PROB`; on detection the character is fined
  `MARKET_ILLICIT_FINE` and jailed `MARKET_ILLICIT_JAIL_TICKS`. Feeding a
  district-wide peacekeeper crackdown (raising `peacekeeper_pressure`, a
  crisis event) needs the still-stubbed `crisis.py` and is out of scope
  here.
- **`/market buy|sell` require the character to be physically at a
  market-kind location** in their current district (`Character.location_id`,
  set by `/travel`) -- trading isn't available district-wide from
  anywhere, since which specific market you're at is what determines
  whether `Location.illicit` even applies.
## Notes on this Milestone E (cross-district travel + calibration) build

Completes Phase 2 (Plan §5.5/5.6, FR-LOC-7/8/9, T-2.1).

- **`/travel district:<id>` is the same `/travel` command**, not a new
  one -- pass `location` for intra-district movement (unchanged) or
  `district` for a cross-district trip, never both. A character must be
  standing at their current district's one `kind: station` location
  (`travel_svc.resolve_station`; every district's content is already
  validated to have exactly one) and not already jailed or in transit.
  The ticket is `train_ticket_dN` from `data/goods.yaml` at its flat
  `base_price` -- these are Milestone D's `kind: ticket` goods, already
  authored but never actually purchasable until now; there's no dynamic
  ticket pricing (no supply/demand model for a service, unlike physical
  goods).
- **Travel is two-phase, not instant**: `/travel district:<id>` deducts
  the fare and sets `Character.in_transit_until_tick`/
  `transit_destination_id`, but doesn't move the character yet.
  `panem_sim/systems/time.py::run` resolves the actual arrival once the
  sim tick reaches that tick -- `time.py` otherwise only advances the
  clock, but "is it time yet" for a character's journey has no more
  natural home among the fixed systems, and this avoids adding a new
  system to `FIXED_ORDER` for one `Character`-mutating step. On arrival
  the character lands at the destination's station, a `CharacterArrived`
  event (new `WorldEvent` kind) tells the bot to swap the district
  "visitor" role, and a `NarrationLine` announces them there the same as
  any other arrival.
- **The bot never grants or revokes a character's home district role**,
  only a temporary one for wherever they're currently visiting
  (`narrator.py::_handle_character_arrived`) -- consistent with Phase 1's
  choice to leave home-role assignment to Discord onboarding entirely.
  Multi-hop travel (home → A → B) drops A's visitor role and grants B's;
  the home role, whichever district that is, is never touched.
  Role-swap failures (missing roles, permissions) are caught and logged,
  not raised -- a guild not yet run through `setup_guild.py`'s role setup
  shouldn't break the tick loop over it.
- **A shift missed while traveling is excused, not missed** (a new
  `Character.away_since_tick` column, migration `f3a1c9d7b4e2`): the
  existing `TRANSIT_TICKS`/`AWAY_GRACE_DAYS` constants were already in
  `constants.py`, unused, clearly waiting for exactly this. Set the
  moment a character first leaves their home district, cleared on
  return; `panem_sim/systems/jobs.py::_resolve_missed_shifts` excuses a
  shift due while still en route, or within `AWAY_GRACE_DAYS` of that
  departure -- long enough for a short trip, not a permanent way to dodge
  `MISSES_TO_FIRE` by never going home. This is the one place
  `jailed_until_tick` gates anything at all in the whole codebase today
  (checked in `check_can_travel_district`) -- otherwise-unenforced
  elsewhere, but leaving a jailed character free to hop a train away
  from the consequence felt like a real gap worth closing here.
- **`scripts/calibrate.py` is a real 12-simulated-month headless run**
  now (zero player characters, per T-2.1), reporting each district's
  final quota/price/treasury numbers and flagging a price pinned at
  `PRICE_CLAMP_MIN`/`MAX` or a negative treasury. T-2.1's actual target
  bands weren't available in this session's context (no spec/plan file
  to read them from), so it reports and flags structurally suspicious
  numbers rather than asserting specific ranges against unknown targets
  -- a sanity check to read, not a pass/fail gate. It refuses to run
  against the configured `DATABASE_URL` (only `--database-url`/
  `CALIBRATE_DATABASE_URL`, a scratch database), since a 12-month
  fast-forward is not something to risk running against a live game by
  a typo.

## Notes on this Milestone F (Phase 3: NPC minds) build

Plan §6. No `panem-long-year-spec.md` §6 text was available in this
session's context, so every numeric interaction/decay/memory-lifetime
constant below is a placeholder, not a spec-sourced value -- the
mechanism (proximity builds familiarity, extremes need history, memories
fade unless important) is the part meant to be right.

- **Traits/speech tone are procedural, not authored.** `data/npcs/*.yaml`
  (Spec §5.4) still doesn't exist -- see the Phase 1 notes above for why.
  `panem_shared/content/traits.py` gives each synthetic NPC 2 unique
  traits from a ~24-word pool at seed time (`panem_sim/world.py`), plus a
  `speech_style.tone` derived from them (`warm`/`blunt`/`reserved`/
  `plain`). Real per-trait dialogue/backstory is Phase 6 (LLM dialogue)
  scope; this only gives that later system, and `/resident profile`
  today, something to read instead of an empty list/dict.
- **Relationships form from shared location, nothing else.** Any NPC or
  character sharing a `(district, location)` on a tick nudges affinity/
  trust with everyone else there, at most once per pair per tick
  (`panem_sim/systems/social.py`) -- no notion of *why* they're near each
  other, no RP content read, no NPC-initiated conversation. A pair needs
  at least one NPC in it; character-character dynamics are for players to
  roleplay themselves; the sim doesn't score them.
- **Found live, not by unit tests: relationships and stance changes form
  fast.** A 30-tick smoke run produced 3289 relationship rows and just
  over 3000 stance-change memories -- most districts' NPCs spend a lot of
  generic-schedule time in one shared public location (`world.py`'s
  `_generic_schedule`), so the same small population re-encounters itself
  constantly. `AFFINITY_STEP`/the `STANCE_THRESHOLDS` gap (20 to cross
  from neutral) is small enough that ~10 shared-location ticks is enough
  to start liking someone. Not a bug -- `MEMORY_CAP_PER_NPC` bounds it
  long-term -- but a concrete number worth re-tuning against the real
  spec rather than trusting the placeholder magnitude.
- **`RelationshipRow`'s subject/object is canonicalized, not meaningful
  direction.** `panem_shared/relationships.py::relationship_key` sorts an
  unordered `(kind, id)` pair the same way regardless of caller, so
  `panem_sim` (which writes the row) and `panem_bot`'s `/resident
  profile` (which reads it, and has no dependency on `panem_sim` to
  reuse its logic) always agree on one row per pair rather than two
  mirror-image ones. `"character" < "npc"` lexicographically, so a
  character is always the subject of its own NPC relationships.
  `interaction_count` (new column, migration `a7c2e8f19d3b`) gates the
  extreme stances (`STANCE_MIN_INTERACTIONS_EXTREME`) -- enough
  encounters to dislike someone is not enough to hate them.
- **Memory formation is event-driven, not a log of everything.** Only a
  handful of things create a `Memory` row today: a character being fired
  (`jobs.py`) and a relationship crossing into a new stance
  (`social.py`), both via a shared `WorldState.notable_events` list
  (kept as plain data, not full `Memory` rows, so an early system like
  `jobs.py` doesn't need a DB session or `Memory`'s full column set just
  to flag "remember this"). `memory.py` turns those into rows, expires
  ones past `expires_tick` (`importance >= 4` never expires), and trims
  each owner back to `MEMORY_CAP_PER_NPC` by dropping the least
  important/oldest first -- every tick, over already-loaded
  `WorldState.memories`, not just for owners who got something new.
- **Retrieval lives in `panem_shared.memory.retrieve()`, not here.** It's
  the pure top-`RETRIEVAL_K` query a Phase 6 dialogue prompt calls; it's
  in `panem_shared` (not `panem_sim.systems.memory`, which only owns
  formation/pruning) so `panem_bot` can call it without depending on
  `panem_sim`. `/resident profile` still shows current stance directly
  rather than a memory summary.

## Notes on this Milestone G (Phase 4: crises) build

Plan §7. `panem-long-year-spec.md` §7 wasn't available in this session's
context either, so -- same as Milestone F -- every numeric weight below
is a placeholder; `CRISIS_THRESHOLDS`/`CRISIS_RECOVERY_DAYS` are the only
Phase 4 constants that already existed before this milestone.

- **`DistrictState.unrest` (0.0-1.0) has exactly two inputs today**: a
  chronic-hunger fraction computed fresh each day in `crisis.py` itself
  (the same `HEALTH_DECAY_HUNGER_THRESHOLD` cutoff `needs.py` already
  uses), and a flat bump from `economy.py::_evaluate_quotas` on a missed
  quota -- added directly to the same `DistrictState` row `economy.py`
  already had open, rather than inventing a new event-passing path for
  one number. Absent new pushes, unrest relaxes by `1/CRISIS_RECOVERY_DAYS`
  of its current value every day; `crisis_level` (0-4) is just how many
  of `CRISIS_THRESHOLDS`'s four cut points it's cleared, and `crisis_kind`
  is a single placeholder label (`"unrest"`) rather than a real
  taxonomy -- Spec §7's actual crisis categories weren't available
  either. A `Bulletin` fires only when a district's level actually
  changes, not every day it's evaluated.
- **`DistrictState.peacekeeper_pressure` is bumped bot-side, not by the
  tick loop.** An illicit-market catch (`panem_bot/services/market.py`)
  is a live trade action, not something that should wait for the next
  tick to have a consequence, so `_apply_illicit_consequence` now also
  nudges that district's `peacekeeper_pressure` directly in the same
  transaction as the fine/jail -- closing the exact gap the Milestone D
  notes flagged ("needs the still-stubbed crisis.py... out of scope
  here"). `crisis.py` relaxes it back toward its own column default
  (0.3) the same way it relaxes unrest, once a day.
- **`/staff district <id>`** (new, staff-only) shows a district's raw
  numbers -- crisis level/kind, unrest, peacekeeper pressure, morale,
  capitol favor, quota progress, treasury -- for GMing/debugging. Players
  get the in-fiction version instead: the `Bulletin` narration on a level
  change, same as any other district-wide notice.
- **Found live, not by unit tests: a 2-day starve-one-district smoke run
  moved unrest exactly as the formula predicts** (0.0 → 0.05 → 0.0833,
  matching `HUNGER_UNREST_WEIGHT`/`CRISIS_RECOVERY_DAYS` by hand), with
  the unaffected district staying at 0.0 -- confirms per-district
  isolation and the decay math both work end-to-end, though 2 days
  wasn't enough starvation to actually cross `CRISIS_THRESHOLDS[0]` and
  fire a `Bulletin`; that path is covered by the unit tests instead,
  which force `unrest` directly rather than starving a population for
  dozens of simulated days.

## Notes on this Milestone H (Phase 5: the Activity backend) build

Plan §8. This is the API bridge only -- the actual Discord Activity
(the embedded iframe app a player would open inside Discord, using
Discord's Embedded App SDK) is a separate frontend project this repo
doesn't contain; nothing here should be read as "the Activity is done."

- **`panem_sim` writes `pos:{district_id}` to Redis every tick**
  (`tick.py::_publish_positions`, a plain JSON string, not a durable
  `WorldEvent`) -- `Npc.x/y`/`Character.x/y` were already being computed
  for exactly this (the module docstrings said so since Phase 1), just
  never actually published anywhere until now. Best-effort: a Redis
  failure here is caught and logged, not raised -- it doesn't share
  `FR-TCK-3`'s retry/alert path with the DB transaction, since a stale
  map is a much smaller problem than a stuck tick loop.
- **`panem_api` only reads that key back** -- `GET /districts`, `GET
  /districts/{id}/positions`, and `WS /ws/districts/{id}/positions`
  (a plain poll every `POSITIONS_POLL_INTERVAL_SECONDS`, not push-on-
  change, since there's no pubsub notification on the key changing,
  only an overwrite). A fresh world with no ticks yet, or a
  Redis hiccup, reads back as empty positions, not an error.
- **No auth is enforced anywhere in `panem_api`.** A real Discord
  Activity authenticates through Discord's own OAuth handshake, which
  needs live Activity credentials to build and verify against -- this
  session had neither, so rather than write unverifiable placeholder
  auth code, this is left as an explicit, documented gap. Don't expose
  `panem_api` on a public port without addressing this first.
- **`docker-compose.yml`'s `api` service publishes :8000 directly**;
  the `caddy` reverse-proxy the original comment mentioned (Plan §8,
  presumably for TLS/routing in a real deployment) isn't implemented
  either.
- **Verified live**: seeded a world, ran one tick, and hit the running
  `panem_api` process's REST and WebSocket endpoints directly against
  real Postgres + Redis -- both returned the same position data
  `tick.py` had just written, confirming the write path (`panem_sim`)
  and read path (`panem_api`) actually agree on the JSON shape end to
  end, not just in unit tests against fakes.

## Notes on this Milestone I (Phase 3: authored NPC content) build

Plan §6.1/§11. Replaces every district's fully-synthetic NPC population
with real, permanent content files, in response to a direct ask for
"real NPC content" as part of "finish the project."

- **`scripts/npc_generate.py` is templated procedural prose, not
  hand-written literary backstory.** It combines a small bank of
  district-flavored sentence templates (keyed off `District.industry`/
  `culture.tone`) and a hand-written one-liner per trait (`_TRAIT_FLAVOR`,
  covering all 24 words in `content/traits.py`) into 2-3 sentences per
  NPC. It's meant as a real, permanent, hand-editable starting point
  (`data/npcs/*.yaml` is just YAML -- a writer can open one and rewrite
  any line), not a substitute for genuine authored content, and not the
  same thing as Phase 6's planned LLM dialogue generation.
- **Deterministic and idempotent per district**, same pattern as every
  other seeded-content generator in this repo (`panem_sim.rng.seed_rng`):
  re-running with the same `--seed` (default matches `Settings
  .world_seed`'s own default) reproduces byte-identical output, verified
  by actually diffing a regenerated file against the original during this
  build.
- **`panem_sim.world.seed_npcs` prefers authored content per district,
  synthetic as the fallback.** A district with a `data/npcs/d<id>.yaml`
  file uses exactly those NPCs (id/name/age/job/traits as authored); a
  district with none still gets the original fully-synthetic population
  Phase 1/2 shipped with. This means partial authoring (regenerate one
  district, hand-edit another, leave a third untouched) always works,
  and a fresh checkout with an empty `data/npcs/` still boots a complete,
  playable world.
- **All 299 residents (13 districts x 23) are authored now** -- this
  build actually ran the generator for every district and committed the
  output, not just the capability to do so. `/resident profile` shows
  the new Backstory/Appearance fields when present.
- **What this doesn't do**: no LLM was used anywhere in this generation
  (Phase 6 scope, still stubbed); no NPC content was hand-written by a
  person; `Npc.speech_style`'s "tone" bucket (warm/blunt/reserved/plain,
  from Milestone F) is unchanged by this -- backstory and speech tone are
  derived from the same traits independently and can read slightly
  differently voiced from each other.

## Notes on this Milestone J (Phase 5: the Activity frontend) build

Plan §8. Builds the piece the Milestone H notes above explicitly called
out as missing: an actual page for Discord's Activity iframe to load,
plus a real OAuth exchange for it.

- **No build step, deliberately.** This repo has no Node/npm tooling
  anywhere else, so the frontend (`packages/panem_api/src/panem_api/
  static/{index.html,app.js,style.css}`) is plain HTML/CSS/vanilla JS,
  served directly by `panem_api` (`StaticFiles(html=True)` mounted at
  `"/"`, registered *after* the API routes so `/health`, `/districts`,
  etc. keep taking priority over it -- verified by a test that hits
  `/health` through the exact same running app that also serves `/`).
  `@discord/embedded-app-sdk` loads from `cdn.jsdelivr.net` via a
  **dynamic** `import()` inside the same try/catch as the rest of the
  Discord handshake -- a static top-level `import` of that URL was tried
  first and found to be a real bug: when the CDN fetch fails (this
  sandbox's own network egress proxy blocked it outright during testing;
  a restrictive local network or an ad/tracker blocker would too), a
  static import throws before any of the module's own code runs,
  permanently stuck on "Connecting…" with no fallback at all. The
  dynamic import fixes that: a failed fetch there is just one more path
  into preview mode.
- **Two run modes, both real, only one actually verified against
  Discord.** Inside a real Activity iframe it does the documented
  embedded-app-sdk flow: `ready()` → `commands.authorize()` → this
  repo's own `POST /activity/token` (needs `DISCORD_CLIENT_SECRET`,
  which only `panem_api` reads) → `commands.authenticate()`. Opened
  directly in a browser (or when `DISCORD_CLIENT_ID` isn't configured,
  or the handshake throws for any reason) it falls back to an
  unauthenticated "preview mode" and shows the map anyway. **Only
  preview mode was actually exercised this session** -- verified with a
  real headless-Chromium screenshot of the running page (district picker
  populated from `/districts`, a live character/NPC dot rendered from a
  hand-seeded Redis position, correct preview-mode messaging). The
  Discord-authenticated path was written to match Discord's documented
  flow but never run against a live Activity install -- no credentials
  or real Discord client this session could test against, same category
  of gap as Milestone H's "no auth verified" note, not a new one.
- **No real map art.** `District.map.image` in `data/*.yaml` has always
  been a placeholder path (no file at that path exists anywhere in this
  repo -- see the Milestone A-era notes). Rather than pretend otherwise,
  the frontend draws a schematic layout instead: each location as a
  labeled circle at its `map.location_coords` position, to scale against
  `map_width`/`map_height` (now returned by `GET /districts` alongside
  each district's locations, extended for exactly this), with NPC dots
  (small, gray) and character dots (larger, labeled, gold) layered on
  top from the existing positions feed.
- **The data endpoints (`/districts*`) still have no auth of their
  own** -- unchanged from Milestone H. `/activity/config` returns the
  client id (not a secret; Discord Activities put it in the iframe URL
  already) so the static frontend never has to hardcode it.

## Notes on Positions, staff-grantable jobs, and the /help fix

Four separate asks landed together: a real `/help` bug, a `/help` UX
redesign, a new Positions concept (Victor/Gamemaker/Governor), and a way
for staff to hand out special jobs (Mentor) outside the normal apply flow.

- **`/help` was silently dropping most of the game.** It paired a live
  command-tree walk with a hardcoded category whitelist
  (`roleplay`/`character`/`scene`/`staff`) -- any top-level command or
  group whose name wasn't in that list was matched to `None` and
  skipped entirely. In practice that meant `/job`, `/market`,
  `/resident`, `/travel`, `/where`, `/time`, `/work`, and `/inventory`
  never appeared in `/help` at all, even though every one of them was a
  real, working command. Rewritten to build categories from whatever's
  actually registered -- every top-level group becomes its own category
  automatically, every ungrouped command falls into a shared "General"
  bucket -- so a future command with a name nobody thought to add to a
  list can't go missing the same way again.
- **`/help`'s first page is now just General** (the ungrouped commands:
  `/rp`, `/ooc`, `/where`, `/time`, `/work`, `/inventory`, `/travel`),
  with a dropdown to switch to any other category. "Staff" only appears
  in that dropdown for staff members -- everyone else never sees it as
  an option at all, not just a hidden/disabled one.
- **Character creation never asked which district to create in** --
  this was already true before this change, not something added now.
  `/character create` derives the district entirely from the caller's
  Discord role (see the Onboarding note above); there is no district
  picker anywhere in the creation or editing flow, and it stayed that
  way.
- **`Character.is_victor` (a single bool) became `Character.positions`
  (a list)**, generalizing to three staff-grantable titles --
  `Position.VICTOR` / `GAMEMAKER` / `GOVERNOR` (`panem_shared.enums`).
  Holding any position grants the same restricted-location access a
  Victor always had (`proxy.has_location_access`); Gamemaker and
  Governor don't have any *other* mechanical effect yet -- deliberately
  scoped to reuse the one real mechanic this codebase already had for
  "someone with special standing," rather than inventing new unrelated
  ones with no spec behind them. `/staff give position character:<name>
  position:<Victor|Gamemaker|Governor> grant:<true|false>` grants or
  revokes one; `/character status` shows a character's Positions field
  when they hold any. The migration backfills existing `is_victor=true`
  rows into `positions=["victor"]` rather than dropping that data.
- **Jobs gained a `staff_only` flag** (`data/jobs.yaml`): a job with
  `staff_only: true` is refused by `/job apply` (`job_staff_only`) and
  filtered out of the character-creation job picker, but is otherwise a
  completely normal job -- still shown in `/job list`, still has wages/
  shifts/options like any other. `/staff give job character:<name>
  job_id:<id>` assigns any job directly, staff-only or not, bypassing
  every `/job apply` check (already-employed, reputation, staff-only) --
  the same "staff already knows what they're doing" trust model
  `/staff give money|item` already used.
- **A real Mentor job per district** (`mentor_d1`..`mentor_d12`,
  `staff_only: true`, workplace at each district's residential quarter --
  `seam` for Twelve, `residential` everywhere else) ships as actual
  content, not just the capability to add one later, so `/staff give
  job` has something concrete to grant. No Capitol mentor exists --
  the Capitol doesn't send tributes.

## Notes on vendoring the Activity's embedded-app-sdk

Not a milestone -- a reported bug fix.

- **The Activity frontend used to dynamic-import
  `@discord/embedded-app-sdk` from `cdn.jsdelivr.net`** (`+esm`, a
  jsdelivr-bundled build). On a deployment whose network can't reach that
  CDN (a restrictive Docker network, a corporate firewall, an offline
  dev box), the import itself fails with "Failed to fetch dynamically
  imported module" -- a real, reported failure, not a hypothetical one --
  and the page falls back to preview mode citing that fetch error rather
  than anything about Discord auth.
- **Fixed by vendoring the SDK as a static file**
  (`packages/panem_api/src/panem_api/static/vendor/
  discord-embedded-app-sdk.js`) instead of fetching it at runtime: the
  real npm package (`@discord/embedded-app-sdk@1.9.0`, MIT --
  `discord-embedded-app-sdk.LICENSE.md` sits alongside it) bundled with
  `esbuild --bundle --format=esm --platform=browser --target=es2020
  --minify` against its own `output/index.mjs`, the same thing jsdelivr's
  `/+esm` endpoint was doing on the fly. `app.js`'s `DISCORD_SDK_URL` now
  points at this same-origin path; no CDN, no external network
  dependency, same `DiscordSDK` export either way.
- Verified in a real headless-Chromium browser against a live `panem_api`
  instance: the vendored bundle loads (`import()` resolves `DiscordSDK` as
  a real constructor) and the page falls through to preview mode for the
  *expected* reason outside a real Activity iframe (`DiscordSDK`'s own
  constructor rejecting a missing `frame_id` query param, which a real
  Discord Activity launch supplies) -- not the CDN-fetch failure this was
  meant to fix. `tests/unit/test_api_app.py` also asserts `/app.js`
  imports the vendored path and that path is actually served.
- To pick up a newer SDK version later: `npm pack
  @discord/embedded-app-sdk@<version>`, extract it, then re-run the same
  `esbuild` command above against its `output/index.mjs` and overwrite
  the vendored file (keep the header comment, update the version it
  names).

## Notes on the log channel and the Activity's OAuth handshake

Two support fixes, not a milestone.

- **`#panem-log` (`LOG_CHANNEL_ID`) used to log exactly one thing: a
  rejected character application.** Nothing else ever posted there --
  every staff moderation action (`/staff ban|kill|note|delete_pending|
  give ...`) was written to the `staff_actions` DB table and nowhere
  else, and a `panem_sim` tick failing twice in a row (`FR-TCK-3`,
  published to Redis's `SIM_ALERTS_CHANNEL`) had no listener on the bot
  side at all -- the single most operationally important failure mode in
  the whole system was completely invisible in Discord. Both are fixed:
  `log_staff_action` now also posts a one-line summary to the log
  channel when given a `bot` (every call site in `staff.py`/`proxy.py`
  passes one), and `narrator.run` now also subscribes to
  `SIM_ALERTS_CHANNEL` and forwards whatever `panem_sim` publishes there
  straight to the log channel.
- **The Activity's OAuth handshake failing silently was traced to
  `prompt: "none"`** in the `commands.authorize()` call -- that tells
  Discord to skip the consent screen entirely, which fails outright
  (rather than prompting) for anyone who hasn't already granted this
  application the `identify` scope. Removed. The frontend also now
  tracks which exact step failed (`ready()` / `authorize()` / the token
  exchange / `authenticate()`) with an 8s timeout per step so a hang
  reads as a clear error instead of an indefinite silence, and reports
  failures to a new `POST /activity/debug` (logged server-side as
  `activity_client_error`) since a real Discord Activity's devtools can
  be genuinely hard to reach to read the browser console directly.

## Notes on this Milestone K (Phase 6: LLM-driven NPC dialogue) build

Ports and builds on the groundwork from
[PR #4](https://github.com/Phqen1x/panem-roleplay/pull/4), which added the Lemonade
OmniModel plumbing (`panem_shared/lemonade/omni.py`, `lemonade/system_prompt.md`,
`lemonade/components.json`, `scripts/lemonade_omni.py`) but stopped short of any code
actually calling it -- that PR predates Phases 1-5 and its `docker-compose.yml`/
`Dockerfile`/`README.md` diffs assumed `panem_sim`/`panem_api` didn't exist yet, so
rather than merge it as-is, its self-contained modules were ported file-by-file onto the
current, much-evolved tree (the Lemonade collection JSON files were rebuilt fresh against
today's `data/` rather than copied) and its deploy-file diffs re-adapted by hand. Its
`snap/` packaging was left out as out of scope for this pass.

- **`panem_bot/services/dialogue.py` + `/talk` (`cogs/dialogue.py`) are the new, real
  functionality** -- the first code that actually calls the OmniModel contract PR #4 only
  documented. `/talk character:<name> resident:<name> message:<text>` requires the
  character be at the same location as the NPC (`talk_not_here` otherwise), spends one
  unit of a per-NPC, per-in-world-hour "talk stamina" (`TALK_STAMINA_PER_HOUR`, counted in
  Redis rather than a DB column since it's disposable state that naturally expires with
  the tick it was spent in), and replies with an embed.
- **`Settings.dialogue_provider` (`"template"` by default) picks the reply path;
  `Npc.provider_override` lets one NPC override it** -- e.g. a named Mentor forced onto
  the LLM while the rest of a district's residents stay on the free, no-server-required
  template path. Any LLM failure (timeout, connection error, malformed response) silently
  falls back to the template reply rather than erroring the command out -- an
  immersion-breaking canned line beats a visible stack trace for a roleplay bot.
- **The template path is genuinely new too**, not a pre-existing fallback -- nothing
  called `dialogue_provider` before this milestone. It's a small, deterministic (seeded by
  the NPC + message text) set of canned lines varied by `speech_tone`
  (`panem_shared.content.traits.speech_tone`, itself a Phase 3 "starting hint" this
  milestone is the first to actually consume) and the character's `RelationshipRow`
  stance with that NPC.
- **`panem_shared.memory.retrieve()` moved out of `panem_sim.systems.memory`** into
  `panem_shared` (new, not part of PR #4) so `panem_bot`'s dialogue service can pull an
  NPC's top memories into the request context without depending on `panem_sim` --
  matching the same writer/reader split every other cross-process shared piece in this
  codebase already follows. `/talk` only *reads* `Memory`/`RelationshipRow` rows; it
  writes neither -- those stay sim-owned, formed only when `panem_sim`'s own systems
  decide a moment was notable, the same as every other NPC-facing interaction.
- **What's verified vs. not.** `uv run pytest` covers the full template-reply path, the
  stamina counter, provider resolution, request-context construction, and
  `generate_llm_reply`'s request/response handling against a mocked HTTP transport
  (`httpx.MockTransport`) -- all passing. What is *not* verified in this session: an actual
  Lemonade server serving a real OmniModel collection, since no such server was reachable
  from this sandbox (same caveat `lemonade/README.md` already carries for `serve`/
  `bundle`). Treat the LLM path as code-complete and unit-tested against its own contract,
  not as end-to-end proven.
- **Still just a documented contract, not wired up:** every `RequestMode` besides
  `dialogue` (`narrate`, `broadcast`, `speak`, `describe_image`, `npc_generate`,
  `review_character`, `staff`) and the vision/transcription/speech/embeddings roles a
  profile bundles alongside the planner LLM. `/talk` only ever sends text and only ever
  reads text back.

## Notes on RP-location enforcement (district, location, and free travel)

Not a milestone -- tightens an existing rule (which used to only really be
enforced at scene creation, and only by home district) and adds two
Gamemaker/Victor conveniences on top.

- **A character may now only RP where they actually are: their assigned
  `Character.district_id`, or wherever `/travel district:<id>` has
  physically taken them (`current_district_id`) -- never a third district
  they've never been near** (`proxy_svc.can_rp_in_district`). Previously
  this was only checked by `/scene start`'s `_caller_character` (and
  mirrored, inconsistently, in its and `/rp`'s character autocompletes),
  using home district alone; `/rp` and proxied messages (`on_message`)
  had no district check at all. Both now call the same
  `check_can_proxy`, which also fixes a latent bug where `on_message`
  resolved location-restriction checks against the character's *home*
  district's locations instead of the scene's actual district -- harmless
  while RP was implicitly always in your home district, wrong the moment
  it wasn't.
- **Within a district they're allowed in, a character also needs to have
  actually traveled to a scene's specific location**
  (`proxy_svc.can_rp_at_location`, comparing `Character.location_id` --
  set by `/travel location:<id>` -- against the scene's location) --
  posting in a scene no longer silently teleports a character there for
  free; `/scene start` and every proxied message require it. A staff
  scene that doesn't pin a location (`Scene.pins_location`, e.g. a roaming
  Capitol broadcast thread) is exempt from this and the district check
  both, via `proxy_svc.scene_location_id`, since nobody could ever have
  "traveled" to a scene with no fixed place.
- **A Gamemaker's characters are exempt from both of the above** -- they
  may be played in any district, at any location, without ever traveling
  there. Gamemakers are Capitol staff overseeing every Games regardless of
  where it's held, so requiring them to physically visit a district (or
  even a specific room in it) first didn't fit.
- **Train tickets are round-trip**: the leg back to a character's own
  assigned district is always free (`travel_svc.is_free_route`), no
  matter which district they're returning from -- it's already covered by
  whatever ticket got them away from home. A Victor's home<->Capitol route
  is additionally free in *both* directions outright
  (`travel_svc.is_free_victor_route`), which only actually matters for the
  outbound (home -> Capitol) leg, since the return leg is already covered
  by the general round-trip rule. Every other route still costs the usual
  fare, and a paid trip is otherwise unchanged (still two-phase, still
  takes `TRANSIT_TICKS`) -- once a Victor's free trip to the Capitol
  actually lands them there, `current_district_id` updates the same as
  anyone else's, which is what then lets them RP there at all; there's no
  separate RP-location exception for Victors anymore; it falls out of the
  district rule above once they've actually traveled.
- The Gamemaker exceptions read `Character.positions`
  (`Position.GAMEMAKER`), the same staff-granted list `/staff give
  position` already manages -- no new column or command.
- **The same Gamemaker exception extends to `/work`**: a Gamemaker with a
  job can work it at any time, not just when `panem_sim` has already
  opened a shift for its `shift_phase` -- `/work` synthesizes a fresh
  `Shift` on the spot instead of refusing with "no open shift"
  (`shifts_svc.open_adhoc_shift_override`) -- and the RP-credit
  shortcut (a long-enough proxied message auto-completing an open shift,
  FR-PRX-7) no longer requires them to be physically at the job's
  `workplace` scene either (`shifts_svc.can_earn_rp_credit_anywhere`).
  Still requires an actual job (`/staff give job`/`/job apply`) --
  a Gamemaker with no job has nothing to synthesize a shift for. No
  cooldown or cap on the ad-hoc path beyond what `/work`'s minigame grace
  window already implies; this trusts whoever holds the Gamemaker
  position the same way its other privileges already do.
- **Real (Discord-role) staff get the same "no open shift needed" override
  on their own characters**, regardless of the character's in-fiction
  `Position` -- `/work` checks `bot.is_staff(interaction.user)` (the same
  role check `/staff ...` commands use) and passes it as
  `shifts_svc.open_adhoc_shift_override(..., is_staff=True)`. This only
  affects whether `/work` needs a pre-opened shift to run at all; it
  doesn't extend the RP-credit-anywhere shortcut above, which stays
  Gamemaker-only. Still requires an actual job -- staff with no job has
  nothing to synthesize a shift for either.

## Notes on this Milestone L (`/work`'s Minesweeper minigame) build

Not a phase from the Plan -- a feature request. `/work` no longer always shows the classic
3-option select menu (still there as a fallback); when `ACTIVITY_PUBLIC_URL` is set, it
instead marks the shift's minigame started and links to a small Minesweeper board served
by `panem_api`'s Activity frontend (`work.html`/`work.js`, no build step, matching
`index.html`/`app.js`'s existing pattern), themed to whichever job/character it's for.

- **Winning pays more, losing pays less** (`panem_shared.shifts.resolve_shift_game`):
  `job.wage * WORK_GAME_WIN_WAGE_MULT` (1.5x) on a cleared board,
  `* WORK_GAME_LOSE_WAGE_MULT` (0.4x) on hitting a mine -- replacing the option-multiplier
  axis a player's free choice (`resolve_shift`, still used by the fallback flow and by
  RP-credit) used to control. No risk roll for a game-resolved shift: the game itself is
  now the "did something go wrong" axis, so a `JobOption.risk_effect` would double-dip.
- **"Paid if you started before the shift ends"** is real, not just a UI promise: `/work`
  sets a new `Shift.started_at_tick` column (migration `b1c4e7a92f05`), and
  `panem_sim.systems.jobs._resolve_missed_shifts` won't mark a started-but-unresolved
  shift missed until `WORK_GAME_GRACE_TICKS` (a day) past its `tick_due` -- long enough to
  actually go finish a board, short enough that an abandoned game doesn't block a new
  shift from ever opening for that character.
- **`ShiftOutcome`/`resolve_shift`/`apply_shift_outcome` moved to `panem_shared.shifts`**
  (re-exported from `panem_bot.services.shifts` unchanged, so every existing call site
  keeps working) since `panem_api`'s new result endpoint needs them too and can't depend
  on `panem_bot` -- the same writer/reader split this codebase already uses repeatedly
  (`redis_keys`, `panem_shared.memory`, `panem_shared.lemonade`).
- **`panem_api` gained its first DB write.** Every other endpoint in that process is
  read-only (see its module docstring); `GET /activity/work/{shift_id}` (shift/job/
  character info for the page) and `POST /activity/work/{shift_id}/result` (resolves the
  shift once) are the one exception, gated entirely by whether `session_factory` is
  configured (`main.py` always wires one up against `DATABASE_URL`, same as `panem_bot`/
  `panem_sim`). No auth here either, same as the rest of Phase 5 -- the result endpoint
  trusts the client's reported `won` outright, consistent with this phase's existing,
  documented no-auth posture.
- **`/work` now sends a real in-Discord Activity launch when it can, not just a plain
  link.** If the player is in a voice channel when they run `/work`, the bot creates an
  `embedded_application` invite on that channel
  (`discord.VoiceChannel.create_invite(target_type=..., target_application_id=...)`) --
  Discord's client renders that as a "Launch Activity" join, opening `work.html` inside
  the voice channel's embedded iframe rather than an external browser tab. This requires
  the bot's Discord application to have Activities enabled and an Activity URL Mapping
  configured in the Developer Portal (pointing at `ACTIVITY_PUBLIC_URL`), which this
  session has no way to configure or verify against a real Discord client. If the player
  isn't in a voice channel, or the invite fails (missing `CREATE_INSTANT_INVITE`
  permission, Activities not enabled for the app), `/work` falls back to the plain
  `work.html?shift_id=...` browser link as before.
- **A real Activity launch can't carry a custom `?shift_id=`** -- Discord always loads
  the app's one configured root URL for every launch, only ever appending its own
  `channel_id`/`guild_id`/`instance_id` (confirmed from this session's own earlier
  server logs of a real launch). So `/work` instead stashes the shift it just opened in
  Redis, keyed by the voice channel the invite was made for
  (`panem_shared.redis_keys.work_pending_key`, `WORK_PENDING_TTL_S` = 10 minutes); once
  `work.html` loads, it reads Discord's own `channel_id` query param and asks
  `GET /activity/work/for-channel/{channel_id}` which shift that resolves to, falling
  back to a direct `?shift_id=` when present (the plain-link path still works exactly as
  before).
- **What's verified**: `resolve_shift_game`'s wage math, the grace-period logic (unit
  tests), the `?channel_id=`-only launch path (a real headless-Chromium browser loading
  `work.html` with no `shift_id` at all, only `channel_id`, correctly resolving the shift
  via `GET /activity/work/for-channel/{channel_id}` against a live `panem_api` and
  Postgres), and the full `work.html` round trip -- played to a loss, and confirmed the
  shift resolved with the correct wage in the DB via the actual HTTP endpoints (not
  mocked). Winning was exercised directly against the API
  (`POST .../result` with `won: true`) rather than forced through the browser, since a
  fair board's outcome isn't fully controllable from outside; the code path is identical
  either way (`finish(won)`), so this is not a meaningfully weaker check.
- Snake/Solitaire/other games, and randomizing which minigame `/work` picks, are follow-up
  scope, not built in this pass.

## Upgrading past duplicate character names

The migration that adds the name-uniqueness index (`7116c3213f6e`) will
fail to apply if your database already has two non-rejected characters
sharing a name (case-insensitively) — this can only happen from before
this change existed. `scripts/resolve_duplicate_names.py` finds and
resolves them using your existing `DATABASE_URL`, no `psql`/direct SQL
access required — run it *before* `alembic upgrade head`:

```bash
uv run python scripts/resolve_duplicate_names.py   # lists every collision
```

For each group it prints, keep one and resolve the rest:

```bash
uv run python scripts/resolve_duplicate_names.py --delete 42
uv run python scripts/resolve_duplicate_names.py --rename 43 "New Name"
```

`--delete` only works on a pending character (matches `/staff
delete_pending`'s safety rule — deleting an approved/retired/dead one
would lose real history); `--rename` works on any status and reuses the
bot's own validation, so it can't create a new collision. Re-run with no
arguments until it reports none left, then run `uv run alembic upgrade
head` as usual. Going forward the bot refuses same-name submissions
itself, so this is a one-time cleanup.
