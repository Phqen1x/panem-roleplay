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
`/work`, free-typed jobs (see "Notes on the job system rework" below --
`/job list|apply|quit` are retired), district-level supply/demand pricing,
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
  the missed-shift mastery penalty by never going home. This is the one place
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
- **Reducing repetitive LLM replies.** Players reported an NPC circling back to the same
  memory or the same line ("keep your coat buttoned tight") almost every turn, even
  inside a single engagement with the conversation's own prior turns right there as
  `history`. The small local models this feature targets (`lemonade/README.md`'s
  Profiles -- a 4B model on a modest machine) fall into that loop far more readily than a
  large hosted one. `generate_llm_reply`'s request body now sets `frequency_penalty`
  (`LLM_REPLY_FREQUENCY_PENALTY`) and `presence_penalty` (`LLM_REPLY_PRESENCE_PENALTY`),
  standard OpenAI-compatible fields Lemonade's llama.cpp-backed server honors, to push
  sampling away from tokens/topics already used earlier in the same request.
  `lemonade/system_prompt.md`'s "Voicing an NPC" section also now tells the model to use
  at most one memory per reply only when it actually fits (never the same one twice in a
  conversation) and to answer what was just said rather than returning to a favorite
  topic -- prompt-level guidance alongside the sampling-level fix, since either alone is
  weaker against a small model's tendency to fixate. `frequency_penalty`/`presence_penalty`
  only penalize tokens *within the completion currently being generated* (standard
  OpenAI-compatible behavior) -- they can't reach across separate LLM calls, so they don't
  stop an NPC from regenerating a near-copy of a line it spoke several turns ago even
  though that line is sitting right there in `history`. A follow-up report showed exactly
  this: an NPC's very last reply in a several-turn engagement was a near-verbatim repeat of
  its *opening* line. Since sampling parameters can't fix a cross-request repeat, the
  prompt now says so explicitly -- "check your own earlier lines in this conversation
  before you answer, and never reuse one... including your own opening line or greeting
  action."
- **Actions vs. speech, and no unprompted scene-setting.** The same report showed an NPC
  writing plain, unwrapped narration ("The bell rings, signaling the start of a long day.
  I straighten my collar...") instead of the asterisk-wrapped-action/plain-speech split
  the prompt already asked for -- evidently too abstract an instruction for a small model
  to reliably follow. "Voicing an NPC" now gives a concrete correct/wrong example pair
  (`*Nash straightens his collar.* It's not often we get to sit together like this.` vs.
  the bell/collar line above) and calls out its three separate mistakes: an unwrapped
  sentence, first-person "I" inside an action (actions are third person, using the NPC's
  own name or he/she/they), and describing something happening *around* the NPC rather
  than the NPC's own words or action. A new, separate bullet forbids narrating the
  surroundings (weather, time of day, a bell, who's nearby) on the NPC's own initiative at
  all -- that's `[MODE: narrate]`'s job -- except when the NPC is actually remarking on it
  out loud to whoever they're talking to.
- **Economic non-sequiturs.** A District One jeweler explained wanting money for trinkets
  as needing "to keep the workshop lights warm during the winter nights" -- buying jewelry
  doesn't heat anything, a nonsensical cause-and-effect the model reached for instead of
  just stating the plain reason (a gift for family). The existing produce-vs-consume rule
  (coal isn't a meal) already covered a good's category not matching what it's used for,
  but not this: a worker doesn't get free or discounted use of what their own district
  produces either -- a jeweler buys jewelry same as anyone else, since the workshop's
  output belongs to the Capitol's order, not to them. Both are now spelled out in "Voicing
  an NPC": a new sentence extends the produce-vs-consume rule to "making a good doesn't
  mean you keep it," and a separate new bullet says outright not to invent a
  cause-and-effect link between two things that don't actually connect, especially when
  explaining why an NPC wants money or a good -- if the reason doesn't hold up stated
  plainly, drop it rather than reach for a poetic one that doesn't make sense.
- **Grounding a reply in more than just stance.** The system prompt has always documented
  `[NPC] ... job or role; ... personality`, `[SCENE] ... crisis level if any`, and
  `[SPEAKER] ... district, job, reputation` header lines, but `build_request_context` never
  actually populated them -- an NPC's opinion of the speaker (`stance`) was the only thing
  actually shaping a reply. It now also sends the NPC's age, job title (looked up from
  `content.jobs` by `Npc.job_id`), traits (personality) and an authored `NpcContent.
  backstory` excerpt (`NPC_BACKGROUND_PROMPT_MAX_LEN` characters -- a full 1500-character
  backstory would dominate every request), the district's live `DistrictState` (crisis
  level/kind, morale, unrest, gathered once per message in `ProxyCog.post_engagement_
  replies` since it doesn't vary between the NPCs replying to one line), and the speaking
  character's own job title, home district and `reputation`. All of it is optional and
  additive -- a caller that doesn't have some piece (npc-to-npc chatter, in particular)
  just omits that header line rather than sending a placeholder.
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
  `review_character`, `staff`) and the vision/transcription/embeddings roles a profile
  bundles alongside the planner LLM. `/talk` only ever sends text and only ever reads
  text back. (Neither profile bundles a `tts` component at all as of this session --
  see `lemonade/README.md`'s Profiles section for why.)

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

## Notes on the job system rework (free-typed jobs, leveling, district economy)

A feature request, not a Plan phase -- reworks how player characters get and grow in a
job, and extends the district economy to react to it.

- **Jobs are free-typed now, not picked from `data/jobs.yaml`.** At character creation,
  the player types a job title (`Character.job_title`, up to 80 chars) and picks a shift
  (`Character.shift_phase`, a `DayPhase`) instead of choosing from a list of catalog
  jobs with open slots. Staff review both as part of the normal approve/request-changes/
  reject flow (the approval embed shows them) to judge whether the job makes sense for
  the district and is allowable -- there's no automated check for this, staff judgment is
  the gate, same as it already was for names/backstories. Staff can also change a job any
  time after approval with `/staff give job <character> <job_title> <shift_phase>`, which
  now takes free text + a shift choice instead of a catalog job id.
- **`/staff give mastery <character> [shifts_completed] [level]`** lets staff directly
  correct or grant a character's job progress -- either an exact `Character.shifts_completed`
  count, or a `JobLevel` choice (Apprentice-Expert) that jumps straight to that level's shift
  threshold (`constants.JOB_LEVEL_SHIFT_THRESHOLDS`); `shifts_completed` wins if both are
  given. Added after the initial rework shipped, since the only way to change level was to
  actually complete that many shifts.
- **`/job apply|list|quit` are retired**, along with `/staff job set|option|remove|show`
  (the `JobOverride` DB table they edited is dropped by the same migration) -- there's no
  more catalog for a player to browse or apply to, and no per-job options to edit. A
  character with no job (never assigned yet) can only be given one by staff; there's no
  player self-service anymore, and missing shifts no longer takes an assigned job away
  (see "Notes on shift timing, wages, and missed-shift consequences" below). `/staff job
  list` survives
  unchanged -- it's read-only and still useful for seeing what catalog jobs *NPCs* hold
  (NPCs are entirely untouched by this rework: `Npc.job_id`/`data/jobs.yaml`/
  `JobOption`/`Job.wage`/`Job.produces` all still work exactly as before for them).
- **Apprentice -> Expert leveling** (`panem_shared.job_levels`, `JobLevel` enum) replaces
  the old per-job `ladder_next`/`ladder_requirement` promotion system, which had nowhere
  to be authored against once jobs aren't a catalog. Purely driven by
  `Character.shifts_completed` (incremented on every resolved shift, win or lose):
  Apprentice (0 shifts) -> Novice (28) -> Journeyman (+56 = 84) -> Master (+84 = 168) ->
  Expert (+112 = 280), each level a further +0.5x wage multiplier (1.0x/1.5x/2.0x/2.5x/
  3.0x). `/character status` shows the current level and shifts left to the next one.
- **Wage formula**: `PLAYER_JOB_BASE_WAGE` (20, flat -- there's no more per-job authored
  wage) x the level multiplier above x `district_wealth_multiplier(district.id)` (see
  "Notes on shift timing, wages, and missed-shift consequences" below) x the minigame's
  win/lose multiplier (`WORK_GAME_WIN_WAGE_MULT`/`LOSE`, unchanged from the Minesweeper
  build) x a market multiplier (below), divided by `SHIFT_DURATION_TICKS` (6) -- see the
  same notes section for why. The old catalog-authored 3-option "work hard / play safe / cover a
  crewmate" menu is gone with the catalog it was defined in -- `/work` without
  `ACTIVITY_PUBLIC_URL` configured now resolves immediately via a coin-flip
  (`NO_ACTIVITY_WORK_WIN_PROBABILITY`, 0.6) using the same win/lose math the minigame
  uses, rather than showing that menu. RP-credit (a long proxied message completing an
  open shift, FR-PRX-7) always counts as a win, and no longer needs the message's scene
  tagged for a job's `workplace` -- there isn't one to tag against anymore, so any
  proxied RP while a shift is open counts, everywhere (previously only Gamemakers got
  that "anywhere" privilege).
- **Every completed player shift produces one unit of the character's home district's
  own quota good** (`District.quota.good` -- already exactly the "key good per district"
  concept, e.g. District 12's coal, District 1's luxury goods; the Capitol has no quota
  and so player jobs there produce nothing), feeding `panem_sim.systems.economy`'s
  existing supply-side pricing the same way `Job.produces` used to for NPCs. Local
  discount for a district's own good falls out of the existing per-district pricing model
  for free -- a district producing its own key good already prices it lower there than a
  district that has to import it, with no new code needed for that part.
- **Demand is active-player-driven now**, not purely `population_base`: a district's
  demand for a good comes from how many of its characters have done something active
  (`/work`ed or proxied a message -- `Character.last_active_tick`) in the last
  `ACTIVE_PLAYER_WINDOW_SIM_DAYS` (42 sim-days = 7 real days at the default tick rate),
  weighted per-good by whether the district produces it (baseline) or imports it
  (`DISTRICT_IMPORT_DEMAND_WEIGHT`, 2x -- a district wants more of what it doesn't make
  itself, e.g. the Capitol wanting luxury goods more than coal). Falls back to the old
  flat `population_base * MARKET_DEMAND_PER_CAPITA` rate for a district with no
  active-player signal yet (a fresh world, or one nobody's playing in), so its market
  doesn't collapse to zero. This is what makes "produce too little for what's wanted, or
  more than anyone's buying" actually move prices per FR-ECO-2's existing scarcity/glut
  formula -- no new pricing math needed there, just a better demand input.
- **Wage feeds back from price**: a district whose own quota good is currently trading
  above its `base_price` (scarce) pays a wage boost on top of everything above; one
  whose good is undersupplied-relative-to-nothing or oversupplied (cheap) pays a debuff
  (`panem_shared.shifts.market_wage_multiplier`, `price / base_price`, already bounded by
  the sim's own `PRICE_CLAMP_MIN`/`MAX` so no separate clamp is needed). This is the
  "if goods are worth more, the producing district's wages go up" loop -- reached by
  `/work` and the minigame result endpoint looking up the character's home district's
  current `MarketPrice` for its own quota good before paying out.
- **What this pass does not build** (flagged explicitly rather than silently skipped):
  hand-authored per-good-per-district demand weights (every good mattering a specific,
  different amount to every district, e.g. the Capitol barely needing coal but needing
  grain a lot) -- this uses the cruder produces/imports-based heuristic above instead of
  200+ authored weights; and a real per-good consumption model (characters/NPCs actually
  needing to buy and consume specific goods daily -- food to not go hungry, coal to heat
  a home once housing exists) -- `needs.py`'s nightly living cost is still a flat money
  cost, not tied to owning any particular good. Both are real follow-up scope, deferred
  the same way housing itself was in the original request.

## Notes on the /character create crash fix and the multi-game /work minigame

**The `/character create` crash fix.** After the job system rework above, creating any
character crashed Discord-side: `JobTitleModal` (the modal that asks for a free-typed job
title) was opened from inside `CharacterDetailsModal.on_submit` -- i.e. one modal opening
another directly in response to that first modal's own `MODAL_SUBMIT` interaction -- and
Discord returned a 400 ("In type: Value must be one of {4, 5, 6, 7, 10, 12}"). An initial
attempt tried satisfying that with discord.py's newer `discord.ui.Label`-wrapped field
schema instead of the legacy Action-Row-wrapped `TextInput`, but that didn't hold up under
live testing -- Discord still rejected the chained modal either way. The actual fix drops
modal-chaining entirely: `_prompt_job_title` (`panem_bot/cogs/characters.py`) now responds
to `CharacterDetailsModal`'s submission with a message and a single button
(`JobTitlePromptView`, `panem_bot/views.py`), and only that button's own click -- a plain
component interaction, not a modal submission -- opens `JobTitleModal`. This is the same
proven pattern already used everywhere else a modal opens in this codebase (the district
select opening `CharacterDetailsModal` itself; `ApprovalView`'s buttons opening
`ChangesNoteModal`/`RejectReasonModal`), so `JobTitleModal` reverted to the plain
`TextInput` schema -- the Label workaround was only ever needed for the chained case this
no longer does.

**The multi-game `/work` minigame.** `/work`'s Activity-hosted minigame was previously
always the same Minesweeper board (`work.js`). It's now a random pick, per shift, from six
small games living in `panem_api/static/games/*.js`: Minesweeper, Snake (eat 8 to clear the
shift, arrow keys/WASD), Connect 4 against a robot foreman (takes an immediate win, else
blocks the player's, else plays center-weighted), Coin Flip (call heads or tails, 50/50),
Pick Your Poison (3 identical bottles, 1 poisoned, 2/3 odds), and a simplified
click-to-select-click-to-place Klondike Solitaire (only a pile's top card is ever movable;
a "Give Up" button covers an unwinnable deal since detecting that automatically is out of
scope here). Every game module exports the same `mount(boardEl, { onFinish, setStatus })`
contract and calls `onFinish(won, options?)` exactly once -- `work.js` (the coordinator)
doesn't care how a game reaches its outcome, only what it reports, matching the existing
trust model documented in `panem_api/app.py` (the work-result endpoint trusts whatever
`won`/`neutral` the client sends, same as it already did for the single Minesweeper board's
`won`). `options.neutral` (`resolve_shift_game`'s new `neutral` param) skips the lose-wage
penalty -- only Solitaire's "Give Up" button sends it, since some Klondike deals are
unwinnable from the very first deal and that loss isn't a misplay the way every other
game's loss is; reputation still doesn't move either way. `work.js` picks uniformly
at random from the six modules each time a shift's board loads. The game selection and
UI are pure frontend; `resolve_shift_game`'s `neutral` param (and its `WorkResultRequest`
plumbing) is the one bit of this feature with real Python and pytest coverage -- the rest
was verified with a headless Chromium (Playwright) smoke test per game plus a full
mocked round-trip through the coordinator, not by the pytest suite (there's no JS test
runner wired into this repo).

**Opting out of the minigame entirely.** `/work`'s minigame-launch message (both the
Discord-Activity and plain-browser-link variants) now carries a second button, "Skip
(neutral wage)", alongside the launch button -- a non-link button whose click resolves the
shift immediately for the same flat, unmodified wage Solitaire's "Give Up" pays
(`resolve_shift_game(won=False, neutral=True, ...)`), no win buff or lose debuff either
way. `JobsCog._finish_shift` gained a `neutral` keyword shared by this button and the
no-Activity coin-flip path, with its own outcome text ("skips the shift's minigame").
Implemented as `_SkipButton`, a small `discord.ui.Button` subclass (an injected coroutine,
matching `views.py`'s `ShiftPhaseSelect`/`ChangesNoteModal` pattern) rather than assigning
to `Button.callback` directly, which mypy's `strict` mode rejects (`method-assign`) since
`callback` is a real method on the base class, not a plain instance attribute.

**Minigame difficulty now scales with job mastery.** `/activity/work/{shift_id}`
(`WorkShiftStatus`) gained a `level` field (`job_level_for_shifts`'s value), which `work.js`
maps to a `levelIndex` (0 = Apprentice .. 4 = Expert) and passes into every game's
`mount(boardEl, { ..., levelIndex })` and `instructions(levelIndex)` (every game's
`instructions` export changed from a plain string to a function, even the ones that don't
vary by level, so the contract stays uniform). Three concrete effects, matching what a
"harder game at a higher level" can actually mean per game:
- **Coin Flip and Pick Your Poison are phased out** past Apprentice (`levelIndex > 0`) --
  fixed-odds games have no real difficulty knob to turn, so rather than pretending a 50/50
  coin gets "harder," `gamesForLevel()` just drops both from the pool entirely once a
  character isn't brand new.
- **Snake's win score climbs**: `BASE_WIN_SCORE (8) + WIN_SCORE_GROWTH_PER_LEVEL (4) *
  levelIndex` -- 8/12/16/20/24 across the five levels. Same board, same speed, just more to
  survive for.
- **Minesweeper's grid grows by 2 squares a level**: `BASE_GRID_SIZE (8) +
  GRID_GROWTH_PER_LEVEL (2) * levelIndex` -- 8x8 up to 16x16 at Expert. Mine count scales
  with the grid to hold mine density roughly constant (`BASE_MINE_DENSITY = 10/64`), so a
  bigger board isn't just bigger, it's proportionally as mine-dense as the original.

Connect 4 and Solitaire are unchanged by level -- their difficulty already comes from real
play (the robot opponent, the deal dealt), not a single tunable constant the way a grid size
or a win score is. Verified with headless-Chromium checks confirming the pool actually
excludes Coin Flip/Pick Your Poison past Apprentice, and that Minesweeper's cell count and
Snake's on-screen win target match the expected value at all five levels.

## Notes on working a shift multiple times per tick

Previously a `Shift` closed (`result = COMPLETED`) the instant it was worked once --
`/work` (or the Activity minigame's result endpoint) was a one-and-done action for the
whole shift. Now a shift stays open for its entire `tick_opened`..`tick_due` window (six
ticks, `SHIFT_DURATION_TICKS`) so a player can come back and work it again on a later
tick, capped at one resolution per tick (`Shift.last_worked_tick`,
`panem_shared.shifts.already_worked_this_tick`). `panem_sim.systems.jobs
._resolve_missed_shifts` is what finally closes a worked shift as COMPLETED once
`tick_due` passes -- the same way it already closed an unworked one as MISSED --
rather than `apply_shift_outcome` closing it immediately on the first work.

`Character.shifts_completed` (the counter driving job-level progression,
`panem_shared.job_levels`) only increments the *first* time a given shift is worked, not
once per `/work` call -- it counts shifts worked, not work actions. Wage, reputation, and
output are still applied on every resolution (`shift.output` now accumulates across every
work this shift gets, rather than being overwritten, so `panem_sim.systems.economy`'s
supply side still sees every unit produced), and `consecutive_missed` still resets on
every resolution, same as before.

The once-per-tick cap is enforced everywhere a shift can be resolved: `/work`'s command
handler refuses up front if the open shift was already worked this tick,
`JobsCog._finish_shift` (shared by the no-Activity coin-flip path and the minigame-launch
message's Skip button) checks it before resolving, and `panem_api`'s
`/activity/work/{shift_id}/result` returns a 409 if the client tries to resolve a shift
already worked this tick. `WorkShiftStatus` gained an `already_worked_this_tick` field so
`work.js` can refuse before mounting a whole game the result endpoint would reject anyway
(`already_resolved` alone can't answer this now that a shift stays open across many
ticks). New migration `c3f8a1d5e6b7` adds `shifts.last_worked_tick`.

This also changes the staff/Gamemaker `open_adhoc_shift_override` behavior: since a shift
no longer closes on its first work, staff working an ad-hoc shift are now subject to the
same once-per-tick cap as anyone else, and reworking it on a later tick continues the
*same* shift rather than synthesizing a fresh one each time (a fresh one is only
synthesized when there's truly no open shift for that character).

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

## Notes on reputation and housing

A feature request, built in five milestones: a richer reputation mechanic reacting to
job performance and NPC relationships, and a full housing economy (houses, apartments,
inns, mortgages, and auctions) tied to a new fatigue/sleep stat.

**Reputation** was previously touched in exactly one place -- a flat +1 for winning a
`/work` minigame, 0 for losing or skipping. It now also reacts to:
- **Streaks.** `Character.consecutive_wins`/`consecutive_losses` (new columns) track
  minigame results; every `REP_STREAK_LEN` (3) wins in a row adds a `REP_STREAK_BONUS`
  on top of the usual +1 ("going above and beyond"), and every `REP_STREAK_LEN` losses in
  a row subtracts `REP_STREAK_PENALTY` ("doing a poor job... over and over"). A single
  loss stays at 0, same as before -- only a real losing streak costs reputation.
  Skipping the minigame (`neutral=True`) is streak-blind and reputation-blind in both
  directions, matching "stay neutral by choosing not to do the game at all."
- **Missed shifts.** `panem_sim.systems.jobs._resolve_missed_shifts`'s MISSED branch
  (not EXCUSED, not COMPLETED) now docks `REP_MISS_PENALTY`.
- **NPC relationships.** A new weekly system, `panem_sim.systems.reputation`, scans every
  character's relationships (`panem_sim.systems.social`'s existing `affinity` rows) and
  applies `+REP_RELATIONSHIP_DELTA`/`-REP_RELATIONSHIP_DELTA` per good/bad one, reusing
  `social.py`'s own `STANCE_THRESHOLDS` rather than inventing a second affinity scale.
- **Getting caught doing something illicit.** `panem_bot.services.market
  ._apply_illicit_consequence` (the existing fine+jail+peacekeeper-pressure hook) now
  also subtracts `REP_ILLICIT_CAUGHT_PENALTY` -- by far the largest single hit, per
  "reputation plummets."

**Housing** is fully greenfield: three new tables (`Property`, `ApartmentLease`,
`PropertyAuction`) plus `Character.fatigue`/`housing_property_id`. Properties are
**procedurally seeded** per district (`panem_sim.world.seed_properties`) rather than
hand-authored in YAML -- houses at all five job-level tiers, a few apartment complexes
of rentable units, and an NPC-run inn, the same idempotent pattern as `seed_npcs`. Key
mechanics:
- **Buying a house** is gated to the buyer's own district and to a job level *at or
  below* the house's tier (a Journeyman may buy Novice/Apprentice/Journeyman housing, not
  Master/Expert -- clarified with the user rather than requiring an exact match).
  Apartments and inns aren't tier- or district-gated. `/housing buy ... financed:true`
  finances the purchase instead: a down payment now, the rest amortized into daily
  installments with a flat origination surcharge (`MORTGAGE_INTEREST_RATE`) rather than
  compounding interest -- the simplest thing still recognizably a mortgage.
- **Pricing** (`panem_bot.services.housing.quoted_price`) starts from a listing's
  `asking_price` override (seller or staff choice) or the sim's daily-refreshed
  `suggested_price`, then applies a mastery-differential multiplier for every kind (a
  buyer above the seller's job level pays less, below pays more) and, houses only, a
  reputation multiplier -- "a better price on houses with better reputation." Both
  combine and clamp to `[HOUSING_PRICE_MULT_MIN, HOUSING_PRICE_MULT_MAX]` so neither
  factor alone can zero out or blow up a price. An NPC seller's mastery defaults to the
  house's own tier (so buying at your matching tier costs the sticker price) or the
  buyer's own level for tier-less apartments/inns. The daily refresh
  (`panem_sim.systems.housing`) only ever touches NPC-owned listings, driven by district
  unrest/capitol favor -- a player's own listing or a staff override always sticks.
- **Renting** an apartment unit signs an `ApartmentLease`; owning every unit sharing one
  `complex_id` (buyable outright via `/housing buy-complex` from a fully NPC-owned
  building) makes a player that building's landlord, collecting rent from tenants.
- **Fatigue** (`Character.fatigue`, 100 = rested) drains from working
  (`FATIGUE_COST_PER_WORK`, docked in `apply_shift_outcome`) and from qualifying proxied
  RP interactions (`FATIGUE_COST_PER_INTERACTION`, same length gate as RP-credit shift
  completion) -- "based on how many times they work and interact." `/sleep`, allowed only
  during the night phase (between the evening and morning shifts), restores it instantly
  for however many ticks are asked (capped to what's left of the night), at full rate
  with a real bed (an owned house, a leased apartment, or a paid inn stay) or half on the
  ground, per the request. Running low overnight costs extra health, mirroring the
  existing hunger-to-health pattern.
- **Mortgages, rent, and inn maintenance** share one collection loop
  (`panem_sim.systems.housing`, once per sim-day): a due payment is deducted if
  affordable, or counts a miss otherwise. An inn's daily maintenance cost reuses the same
  `mortgage_payment`/`mortgage_next_due_tick` fields as a real mortgage installment,
  documented inline, rather than adding a second parallel mechanism for "can't afford the
  payment." Past `MORTGAGE_MISSES_TO_FORECLOSE` a house is repossessed and automatically
  listed for **auction** (`PropertyAuction(seller_kind="bank", ...)`) rather than
  silently reverting to NPC stock; past `RENT_MISSES_TO_EVICT` an apartment lease is
  simply deleted. A player can also voluntarily start an auction on a property they own
  (`/housing auction-start`) or refinance one for a cash loan against its equity, capped
  at `MORTGAGE_MAX_LTV` of its current listed value. Auctions resolve at their end tick to
  the highest still-affording bidder, or relist as NPC stock at the minimum bid if there
  was no bid or the winner can no longer pay.
- **Staff** can override any property's listed price directly (`/staff housing
  set-price`), per "staff should be able to override this in the market if need be."

**Interpretation calls** (flagged for easy review rather than buried in code): the
tier gate and reputation price modifier apply to houses only, since the request's own
wording names "houses" specifically for both; apartments/inns price by district and
mastery differential alone. "Mortgage" covers both financing a purchase and refinancing
an owned one; "auction" covers both a voluntary sale and the automatic foreclosure
fallback. Sleep restores fatigue instantly for the ticks requested rather than literally
pausing the bot for real time, mirroring how `/travel district` already abstracts
transit time.

## Notes on NPC engagements

A feature request: let players roleplay with NPCs the same persistent, multi-party way
they already RP with each other, rather than through `/talk`'s old one-shot ephemeral
reply. Built in five milestones on top of scaffolding that turned out to already be
sitting in the schema unused -- `Scene.participants` (a `JSONB` column with no reader or
writer anywhere in the codebase), `SceneMessage`, and `DialogueLog` were all migrated in
the original Phase 0 baseline and never touched again, as if provisioned for exactly
this.

**An engagement is a `Scene`** (`SceneKind.ENGAGEMENT`), not a new parallel table.
`Scene.participants` gets a documented shape: `{"characters": [...], "pending_characters":
[...], "npcs": [...]}` -- joined players, players invited but not yet accepted, and
joined NPCs. Because `panem_bot.cogs.proxy`'s `on_message` already looks up `Scene`
generically by `thread_id` without special-casing `SceneKind.PLAYER`, an engagement
thread gets RP-credit, location pinning, and webhook proxying for free the moment it's
just another `Scene` row.

- **`/engage start location:<id> participant_1:<name> [participant_2 ... participant_5]`**
  resolves each named `participant_N` against NPCs in the character's district first,
  then against any other approved character eligible to RP there
  (`proxy_svc.can_rp_in_district` -- their home district, or wherever `/travel
  district:<id>` last took them; they don't need to already be standing at the location).
  Discord slash commands have no true variadic argument, so "1 or more" participants are
  `ENGAGEMENT_MAX_PARTICIPANTS` (5) individually autocompleted slots (`participant_1`
  required, the rest optional) rather than one free-text field, excluding names already
  sitting in a different slot. A named NPC not currently working their shift or asleep
  (`panem_bot.services.engagements.npc_is_busy`, reusing `panem_sim.systems.schedule`'s
  own arrival predicate rather than a second copy of it) is relocated there immediately
  (`Npc.location_id`/`x`/`y` overwritten, the same instant-arrival model `schedule.py`
  already uses every tick) and gets a new `Npc.engagement_id` set, which `schedule.py`
  checks each tick to skip movement for them entirely -- "NPCs won't leave until the
  engagement ends." A busy NPC is left out of the thread, and the starter is told
  ephemerally where to find them instead -- *unless* the engagement's own location is
  that NPC's workplace (`npc_is_busy`'s `at_location_id` parameter): walking up to a
  shopkeeper or clerk at their own counter while they're on shift is exactly how you'd
  talk to them, not something that needs them to step away, so working a shift is only
  "busy" toward starting an engagement somewhere else. A named *character* belonging to another player
  is added to `participants.pending_characters` and must accept an invite (two buttons on
  a message only that player can press) before joining for real, per the user's own
  answer -- physical presence at the location is never enough on its own for someone
  else's character. On accept, their character is relocated to the engagement's location
  the same free, instant way `/travel location:<id>` already moves someone within a
  district (never across districts -- that still costs a ticket and takes real transit
  time, so an invite never bypasses that economy) -- and, since an invitee is eligible by
  home district alone (`can_rp_in_district`), accepting also pins `current_district_id` to
  the engagement's district, since they may not have been considered "in" it at all until
  now. The Accept/Decline buttons' handler originally had two paths (the scene already
  gone, the invite already answered by a double-click or a stale message) that returned
  without ever calling `interaction.response...` at all -- Discord surfaces an
  unacknowledged interaction as "the application did not respond," not a normal error
  message, so this looked like the bot hanging rather than a handled case. Both paths now
  edit the message with an explanation instead of silently returning.
- **Run `/engage start` or `/talk` from inside a thread that already has a scene
  registered** (an ambient thread, an open `/scene`, or a prior engagement) and the named
  NPCs/characters are pulled into *that* conversation instead of a new thread being
  created -- the player is already there, so that's where the NPC should start talking.
  Only outside of such a thread does either command fall back to reusing (or creating) a
  dedicated engagement thread at a location. "Engaged" is decided by a scene's
  `participants.npcs` being non-empty, not by its `kind` being `ENGAGEMENT` -- an ambient
  thread or an ordinary `/scene` can have NPCs attached this way without ever becoming a
  dedicated engagement itself, and stays open (rather than being archived) once released
  by `/engage end` or the idle timeout.
- **An NPC only ever converses in its own assigned district, and never in a location
  its profession doesn't grant it access to.** The current-thread-attach path above
  means a player could otherwise summon a home-district NPC into a thread that belongs
  to a different district or location entirely -- so both `/talk` and `/engage start`
  now resolve the named resident's district from *the thread being attached to*
  (`here_scene.district_id`/`location_id`) rather than blindly from the character's own
  `current_district_id`, checking `proxy_svc.can_rp_in_district` on the character first.
  Separately, `proxy_svc.npc_has_location_access` reuses the same `Location.restricted`/
  `access_jobs` gate `has_location_access` already applies to players -- but matched
  against `Npc.job_id` directly, since (unlike a player's free-typed `Character.
  job_title`) an NPC's `job_id` is always a real `jobs.yaml` catalog id. A Peacekeeper
  NPC can be pulled into the Justice Building; a shopkeeper NPC can't.
- **Who replies in a group.** In a strict one-on-one engagement (one character, one NPC)
  every qualifying message gets a reply, same as `/talk` always worked. With more than
  one participant, an NPC only replies to a message that contains their first or last
  name (`engagements_svc.name_mentioned`, a whole-word match so a short name doesn't
  fire inside an unrelated word) -- per the user's explicit answer, not every line in a
  crowded thread is addressed to every NPC in it.
- **`/talk` is folded into this system** rather than kept as a second NPC-dialogue path:
  it now finds-or-opens a one-NPC engagement between exactly this character and this NPC
  at their shared location, posts the player's line into it via webhook, and triggers the
  NPC's reply through the same `ProxyCog.post_engagement_replies` method the group flow
  uses -- non-ephemeral, in a real visible thread, indistinguishable from typing the same
  message there directly. The only ephemeral reply left is housekeeping ("thread
  created", "that NPC's at work, try the Hob instead").
- **Multi-turn history** now actually flows into the LLM call. `omni.build_messages`
  already accepted a full list of prior turns; `dialogue_svc.generate_reply` just never
  passed more than the newest message before this feature. NPC replies build `history`
  from the engagement's own `SceneMessage` rows (capped at `MAX_ENGAGEMENT_HISTORY_TURNS`)
  and a `present` list of everyone else in the scene, both new parameters threaded
  through `build_request_context`/`generate_llm_reply`. Every reply is also logged to
  `DialogueLog` -- the first real use of that table.
- **`Scene.last_message_at` keeps meaning "last *player* message"** (already true before
  this feature -- only a player's own proxied line ever touched it). NPC replies
  deliberately never update it, or an engagement full of NPCs replying to each other
  would never go idle.
- **Inactivity auto-close** is a `tasks.loop` background task
  (`EngagementCog.close_idle_engagements`, the same pattern `SceneCog.archive_idle_scenes`
  already established) that archives any `ENGAGEMENT` scene whose `last_message_at` is
  older than a **staff-configurable** timeout, per the user's own answer -- a one-row
  `EngagementSettings` table (mirroring `WorldClock`'s singleton shape) rather than a
  code constant, tunable live via `/staff engagement set-timeout` with no redeploy.
  Closing clears `engagement_id` on every participant NPC, letting their normal weighted
  schedule resume the next tick.
- **NPC-NPC ambient chatter** happens without any player involved, per "NPCs should also
  be able to randomly start short engagements with other NPCs, but not players." This is
  deliberately *not* a `Scene`/thread of its own -- a new low-probability
  `panem_sim.systems.npc_chatter` system (registered in `FIXED_ORDER` right after
  `social`) looks each tick for two or more co-located, unengaged NPCs, rolls the odds,
  and on a hit emits an `NpcChatter` event naming the district, location, and the two
  NPCs picked. Since `panem_sim` never calls the LLM anywhere in this codebase, the actual
  lines are generated bot-side: `narrator.py`'s new `_handle_npc_chatter` finds the
  location's existing pinned `AMBIENT` thread (the same lookup `_handle_narration` already
  does) and posts a short back-and-forth (`NPC_CHATTER_MIN_LINES`-`NPC_CHATTER_MAX_LINES`,
  "should only last a few messages... not happen particularly often") through the
  district forum's webhook **as each NPC** (their own name and avatar), per the user's
  explicit answer -- the same visual treatment a player's proxied line gets, not posted
  as "The Narrator." A new `dialogue_svc.generate_npc_to_npc_reply` variant builds the
  speaker block from the other NPC rather than a player `Character`; the very first line
  of an exchange uses an OOC stage direction (`"(( You notice each other nearby... ))"`,
  the same `(( ... ))` convention `lemonade/system_prompt.md` already documents for
  out-of-character instructions) rather than anything literally said, since nothing
  prompted the conversation but proximity.

**Interpretation calls**: an engagement's NPC "travel" is an instant relocation, not a
simulated multi-tick walk -- every other arrival in this codebase (schedule, ambient
narration) is already instantaneous, and there's no existing intra-district
travel-over-time model to build on. `/engage start`'s participants are a fixed number of
autocompleted, optional slots rather than a single free-text field, since Discord slash
commands have no true variadic argument; five is comfortably more than any normal
engagement needs while still fitting Discord's per-command option limit.

**A `Scene.thread_id` race, and its fix.** `forum.create_thread()` dispatches a gateway
`on_thread_create` event the moment the thread exists on Discord's side; `SceneCog.
on_thread_create` (which auto-registers any new, tagged forum thread as an ordinary
`SceneKind.PLAYER` scene, for players who start a thread by hand rather than via
`/scene start`) can win that race and insert a `Scene` row for the same thread first.
`/scene start` already handles this with an upsert (`insert ... on conflict do update`)
so its own data always wins regardless of ordering; `/engage start` and `/talk`
originally didn't, and a plain insert crashed on the listener's row with a duplicate-key
error when it lost the race -- surfaced to players as a generic "Something went wrong"
error, and (once patched with a fix that merely adopted the existing row without
correcting its `kind`) as `/engage end`/`/engage join` claiming the thread wasn't a
registered engagement. Both commands now upsert the same way `/scene start` does.

## Notes on staff NPC management (`/staff npc ...`)

A feature request: let staff retroactively rename an NPC or edit their background,
appearance, traits and speech, and add brand new NPCs outright, all without touching code
or a data file. `NpcContent.backstory`/`.appearance` (`data/npcs/*.yaml`) were designed as
display-only, never-simulated flavor text with no DB column of their own -- fine for an
authored resident nobody edits, but that leaves staff no way to correct one after the fact,
and a wholly new NPC created by a command has no YAML entry to read from in the first place.

- **`Npc.backstory_override`/`.appearance_override`** (new nullable columns, migration
  `b2d4f7a9c1e6`) hold a staff edit, mirroring `provider_override`'s own
  override-a-default shape. Every read site -- `/resident profile`'s embed and
  `ProxyCog.post_engagement_replies`'s `npc_background` for the LLM prompt -- now prefers
  the override when set and falls back to the authored `NpcContent` otherwise. `name`,
  `traits` and `speech_style` already lived on the `Npc` row itself (no content-vs-DB split
  to work around), so renaming and re-tagging traits/tone just update those columns directly.
- **`/staff npc rename`, `set-background`, `set-appearance`, `set-traits`, `set-speech`**
  each look the NPC up (`autocomplete.any_npc`, a new global-not-district-scoped
  autocomplete since staff need to reach any resident, not just ones near their own
  character) and update exactly one thing, mirroring `/staff give money`/`housing
  set-price`'s single-purpose shape rather than one big edit-everything command. Lookup is
  by `Npc.id`, not name: the synthetic name pool is sampled independently per district, so
  the same name showing up in two different districts is expected, and an early version of
  this that matched on name crashed (`MultipleResultsFound`) the first time it hit a real
  duplicate. `any_npc`'s suggestions carry the id as the choice's `value` (the name plus
  district is only the display label), so picking a suggestion always resolves to exactly
  the one NPC shown. `_find_npc` still falls back to a plain-name lookup for a staff member
  who ignores the suggestions and types a name directly (the common case, since most names
  are in fact unique) -- it only comes up empty, with a message pointing at the
  suggestions, when that typed name is genuinely ambiguous.
- **`/staff npc add`** creates a genuinely new `Npc` row from scratch -- name, district,
  age, home location (validated against that district's real locations), comma-separated
  traits (speech tone auto-derived from them via the same `speech_tone` helper synthetic
  seeding already uses), and optional job/backstory/appearance. The id is a random
  `staff_<district>_<hex8>` (never collides with seeded `d<district>_npc_<n>` or
  content-authored ids). Deliberately out of scope: no `NpcSchedule` rows are created, so
  a staff-added NPC has no weighted movement yet (`panem_sim.systems.schedule.run` simply
  skips any NPC with no schedule weights for the current phase, the same safe no-op it
  already does for an NPC that's `engagement_id`-locked) -- they stay exactly where placed
  until staff moves them or a future NPC-schedule command exists.

## Notes on stale NPC job/bio drift

A player reported an NPC whose authored bio described one profession while the LLM's
`[NPC] job: ...` header (built from the live `Npc.job_id`) named a different one entirely,
confusing the NPC's own dialogue about their day. `seed_npcs`/`world.py`'s own docstring
already documents why: seeding is per-district and one-time -- "a district already holding
`district_state`/`npcs` rows is left untouched." That's the right call for anything a
player or the sim has since changed about an NPC, but it also means `data/npcs/*.yaml`
being hand-edited or regenerated *after* a district was first seeded silently strands the
already-seeded rows on whatever `job_id` they started with, forever, even once the content
file (and that NPC's own `backstory`, which names a profession) has moved on.

`world.sync_authored_npc_jobs`, called from `seed_world` right after `seed_npcs` on every
boot (not just the first), closes that gap: for every authored `NpcContent` entry whose id
already exists as an `Npc` row, if the row's `job_id` doesn't match the content's, it's
corrected. Deliberately narrow in scope -- unlike `job_id`, `traits`/`speech_style`/`name`
are all things `/staff npc set-traits`/`set-speech`/`rename` can now deliberately change,
and this sync has no way to tell a deliberate staff edit apart from stale content, so it
leaves them alone entirely. `job_id` has no such staff command (an NPC's job comes only
from content or from being freshly generated), so it's the one field guaranteed to only
ever drift by accident.

## Notes on `/help` embeds cutting commands off

A player reported some `/help` embeds getting cut off. `_build_embed` already puts each
subgroup (`/staff give ...`, `/staff npc ...`, ...) in its own embed field specifically to
avoid Discord's 1024-character-per-field limit truncating the whole category's
description text (an earlier version of this file did exactly that) -- but a subgroup that
had since grown past that same limit on its own (`/staff give ...` at 5 commands,
`/staff npc ...` at 6) hit the identical problem one level down: the field's own value got
hard-truncated with an ellipsis, silently dropping whichever commands landed past the
1024-character cutoff.

`_chunk_lines` fixes this the same way the field-per-subgroup split already did at the
category level: instead of truncating an oversized section's text, it greedily packs the
section's lines into as many `FIELD_VALUE_LIMIT`-sized embed fields as it actually needs,
labeling every field after the first "(cont.)" -- so a long subgroup spans two or more
fields rather than losing its tail end. Only a single line that's somehow longer than the
limit all on its own (not something any current command description does) still falls
back to truncation, since there's no line boundary left to split it across.

## Notes on shift timing, wages, and missed-shift consequences

A player reported being able to `/work` well outside their assigned `shift_phase`, and
asked for two related changes: stop taking a character's job away for missing shifts
(dock mastery progress instead, once misses start piling up), and pay less per `/work`
call now that a shift can be worked more than once.

**The out-of-shift bug** was `WORK_GAME_GRACE_TICKS`. It's meant to let a player who
launched `/work`'s minigame (Minesweeper, etc.) before the shift's `tick_due` finish the
board afterwards rather than losing the shift the instant the clock rolls over --
`panem_sim.systems.jobs._is_within_work_game_grace` keeps a *started*
(`Shift.started_at_tick` set) shift open for this many extra ticks past `tick_due`. It
was set to `TICKS_PER_DAY` (24 ticks -- a full extra day), which meant a shift merely
*started* before its deadline stayed callable via `/work` for a whole day afterwards,
drifting through every other `shift_phase` in the meantime (a "morning" shift's grace
window ran straight through afternoon, evening, and the next night) and, since
`SHIFT_DURATION_TICKS` (6) evenly divides `TICKS_PER_DAY` (24), landing almost exactly on
the next day's own shift-open boundary -- blocking the next day's shift from ever opening
for as long as the stale one sat un-resolved (`_open_shifts_for_due_characters` skips a
character who already has an open shift). `WORK_GAME_GRACE_TICKS` is now `1` tick (10
real minutes at the default `TICK_INTERVAL_SECONDS`) -- long enough to actually finish an
in-progress board, far too short to spill into a different `shift_phase` or collide with
the next day's shift.

**Firing is retired.** `panem_sim.systems.jobs._fire` used to clear
`Character.job_title`/`shift_phase` once `consecutive_missed` reached `MISSES_TO_FIRE`
(5) and record a `JobHistory` row -- a character kept missing work, they lost the job
outright and needed staff to hand them a new one. That's gone: `_apply_mastery_penalty`
replaces it, triggered at the lower `MISSES_TO_MASTERY_PENALTY` (3, the old unused
`MISSES_TO_WARN` threshold, renamed now that it actually does something) and re-applied
on *every* further miss in the streak rather than firing once and resetting -- there's no
more firing event to reset toward, so a character who simply never comes back keeps
losing `SHIFT_MASTERY_MISS_PENALTY` (1) off `Character.shifts_completed` (floored at 0,
same counter `panem_shared.job_levels` reads for job-level progression) for as long as
the streak runs, the same one-at-a-time rate that counter builds up by actually working.
No `JobHistory` row is written (the job never ends, so there's nothing to close out), but
a `mastery_slip` `NotableEvent` still records each occurrence for `/resident profile`-style
memory. `job_title`/`shift_phase` are untouched by any of this now -- the sim never clears
a job; only `/staff give job` does.

**Wages are now divided by `SHIFT_DURATION_TICKS`.** Since "Notes on working a shift
multiple times per tick" above, a shift can be resolved once per tick across its whole
`tick_opened`..`tick_due` window (6 ticks) rather than once total, but
`resolve_shift_game` still paid the full `PLAYER_JOB_BASE_WAGE`-derived wage on *every*
one of those resolutions -- up to 6x the intended pay for one shift if a player kept
coming back each tick. `resolve_shift_game` now divides its final wage by
`SHIFT_DURATION_TICKS`, so working every tick of a shift totals to roughly the same pay
the old single-resolution design intended, while still rewarding checking in more often
(more resolutions still means more reputation streak progress and one more unit of
output each time -- only the wage itself is time-sliced). Output quantity and reputation
deltas are untouched; the user asked specifically about wages.

**Different districts now pay different base wages**, poorer districts less, following a
later request in the same conversation: "the higher the district number the poorer they
are." `panem_shared.shifts.district_wealth_multiplier(district_id)` is a straight line
from `DISTRICT_WEALTH_WAGE_MULT_MAX` (1.5) at the Capitol (`district_id == 0`) down to
`DISTRICT_WEALTH_WAGE_MULT_MIN` (0.5) at District Twelve (`district_id == 12`), and
`resolve_shift_game` multiplies it in alongside the job-level and market multipliers.
This is a pure function of `District.id` rather than a value authored per district in
`data/*.yaml`: canon's wealth ordering is already exactly `id` itself (Capitol richest,
career districts next, District Twelve poorest), so there's nothing to hand-tune and no
new content field, migration, or per-district authoring pass needed -- and a formula
can't drift out of sync with the district list the way 13 separately-authored numbers
eventually would. It only touches the player wage formula; NPC wages
(`Job.wage` in `data/jobs.yaml`, resolved by `panem_sim.systems.jobs._apply_npc_job_completion`)
are a separate, already-per-job-authored system the request didn't ask to change.

## Notes on two minigame bugs: flagged Minesweeper squares, Solitaire stack moves

Both `panem_api/static/games/minesweeper.js` and `.../solitaire.js` are plain static
modules with no build step and no test harness in this repo (there's no `package.json`
or JS test runner at all) -- these two fixes were verified by tracing the logic by hand
and, for Solitaire's run-matching rule, running the pure `isValidRun` logic standalone
under Node against the exact example reported, rather than through the Python
`pytest`/`mypy` loop that covers everything else in this repository.

**Minesweeper: flagging didn't actually protect a square.** `onCellClick` checked
`cell.mine` before it checked `cell.flagged` -- so left-clicking a flagged square that
happened to be a mine still ended the game immediately, even though flagging exists
specifically to mark a square as "don't click this." (A flagged *non*-mine already
silently no-opped, since `reveal()` itself skips flagged cells -- only the mine case was
actually broken.) Fixed by returning immediately on `cell.flagged` before the mine check
runs at all, so a flagged square can no longer be resolved by a left click either way;
it still has to be unflagged (right-click) first.

**Solitaire could only ever move one card at a time.** The tableau only let a player pick
up `pile[pile.length - 1]` (the single exposed card) and drop it elsewhere -- a core
Klondike rule was missing: an already-validly-stacked run of cards (each one rank lower
and the opposite color of the card above it, e.g. a red 9 on a black 10, with black 8 and
red 7 already sitting on that 9) should move together as a unit onto any pile whose
exposed card fits the *run's own top card* (the highest-rank one, e.g. that 9), not just
the frontmost single card. Fixed with `isValidRun(pile, cardIndex)`, which checks that
`pile[cardIndex..]` is entirely face-up and correctly alternating/descending; clicking any
card in a tableau column that starts a valid run (not only the exposed top card) now
selects that whole run -- highlighted together in `render()` -- and `tryMove` moves it as
one `splice`/`push` unit when dropped on a compatible pile, preserving the run's internal
order. A run can only ever be dropped on another tableau pile, never a foundation --
foundations still take exactly one card at a time (`fromIndex !== pile.length - 1` refuses
the drop), which is also how real Klondike works.
