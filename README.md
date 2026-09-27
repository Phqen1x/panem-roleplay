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
deploy/           docker-compose, Dockerfile, systemd unit, snap packaging + LXD provisioning (deploy/snap/README.md)
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
- **`commands.authorize()` failing with `OAuth2 Error: invalid_request:
  Missing "redirect_uri" in request"` is a Developer Portal setting, not
  a code bug.** The embedded-app-sdk's RPC-brokered `authorize()` never
  actually uses a redirect URI (the Discord client handles returning
  control to the Activity internally) -- but Discord's OAuth backend
  still refuses to issue an authorization code at all unless the
  Application has *at least one* Redirect URI registered under its
  **OAuth2** settings tab. Add any URL there (it's never visited; even
  `http://127.0.0.1` works) and save -- no redeploy needed, existing
  Activity sessions pick it up on the next `authorize()` call. (Also:
  the client-side status banner and `activity_client_error` log line
  used to stringify any non-`Error` SDK rejection -- exactly what an
  RPC OAuth error is -- as the useless `"[object Object]"`; `app.js`'s
  `describeError()` now surfaces the real `code`/`message` instead,
  which is what made this diagnosable.)

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

**The launch message now clears its own buttons once the shift is worked.** Previously
`/work`'s ephemeral minigame-launch message kept showing its Play/Skip buttons forever,
even after the shift was resolved -- clicking either one again after the fact just hit
`shift_no_longer_open`/`shift_already_worked_this_tick` instead of anything visibly
changing. Skip now edits that same message in place (`shift_worked_banner`: "This shift
has already been worked!", buttons removed) as its interaction response, with the actual
result delivered as a followup right after (Discord allows only one initial response per
interaction). Completing the real Activity minigame instead resolves through
`panem_api`, a separate process with no Discord gateway connection of its own -- so
`/work` now stashes the launching interaction's `application_id`/`token` in Redis, keyed
by shift id (`redis_keys.work_interaction_key`, `WORK_INTERACTION_TTL_S` = 14 minutes,
just under Discord's 15-minute interaction-token validity). `panem_api`'s
`/activity/work/{shift_id}/result` reads it back and edits the original message directly
via Discord's webhook-edit REST endpoint (`PATCH
/webhooks/{application_id}/{token}/messages/@original`) -- no bot token needed, since an
interaction's own token is sufficient to edit its own responses. Best-effort: a missing
or expired token (a shift resolved more than 15 minutes after `/work` was run) just
leaves the stale buttons behind rather than failing the actual work result.

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

## Notes on NPC long-term memory and reply pacing

A player asked for a third-party architecture sketch (persona injection, a vector-DB lore
library, dual short/long-term memory) to be checked against what this repo actually does.
Most of it turned out to already exist under different names from the Phase 6 dialogue work
and the NPC Engagements milestone: `dialogue.build_request_context` is the persona engine,
the world atlas baked into the collection's system prompt (`omni.render_world_atlas`) is the
lore library (no vector DB or embedding search actually runs anywhere, despite the `nomic-
embed`/`Qwen3-Embedding` components being bundled and described as powering memory recall --
`memory.retrieve()` just sorts by importance and recency), and `lemonade/system_prompt.md`
already told the model not to volunteer unrelated facts or narrate on its own initiative. Two
real gaps came out of the comparison, addressed here:

**Short-term memory only covered the last 12 turns of an engagement, not the whole thing.**
`proxy.py`'s `post_engagement_replies` capped its `SceneMessage` history query at
`MAX_ENGAGEMENT_HISTORY_TURNS`. Renamed to `ENGAGEMENT_HISTORY_HARD_CAP` and raised to 200 --
a defensive ceiling rather than a working limit, since an engagement already auto-closes on
its own idle timeout long before a real conversation could approach that many turns. In
practice this means every message since the engagement opened rides along as history now, not
just the last dozen.

**Nothing summarized a finished conversation into anything an NPC could recall later.** The
`Memory` table only holds event-derived fact bullets the sim forms from `notable_events`, and
`RelationshipRow` only tracked affinity/trust/stance numbers -- there was no mechanism for "the
NPC remembers what you two actually talked about" across separate engagements, days, or bot
restarts. Added `RelationshipRow.summary` (nullable `Text`, migration `d4e8f1a6c3b9`): a
compacted, running recap of every engagement a character and an NPC have had together. Both
places an engagement closes (`EngagementCog.end` and `close_idle_engagements`) now call
`dialogue.summarize_engagement`, a new `omni.RequestMode.SUMMARIZE` LLM request (documented as
its own mode in `system_prompt.md`, alongside the existing `staff`/`review_character` OOC
modes) that's handed both the closing engagement's transcript *and* whatever was already
stored, and asked to fold them into one updated recap rather than only describing what's new --
the standard running-summary pattern, so the stored text stays bounded
(`RELATIONSHIP_SUMMARY_MAX_WORDS`, defensively hard-truncated on top of the model's own
instruction) instead of growing forever. One summarization call per joined NPC per closed
engagement, not per character: the result is that NPC's own recap of the scene, written
identically onto every joined character's relationship row with them. The stored summary rides
in the next dialogue request's `[SPEAKER] ... known` field -- a header block the system prompt
had always documented ("what the NPC knows of them") but that nothing had ever populated
before this. A new prompt rule tells the model to treat it as quiet background for recognition
and continuity, the same "sparingly, never verbatim" restraint already applied to `[MEMORIES]`.

**Reply length was a flat 90-word cap regardless of what it was replying to.** Added
`_length_matched_max_words` (`dialogue.py`): `REPLY_LENGTH_RATIO` words of reply per word of
the incoming message, clamped to `[MIN_WORDS_REPLY, MAX_WORDS_REPLY]`. A one-word greeting now
gets a short answer instead of room to ramble up to the old flat cap, while a longer message
still gets real room to respond, up to the same overall ceiling as before. Applied to both
`/talk`/`/engage` replies and NPC-to-NPC ambient chatter.

## Notes on the economy rework (minigame-tied production, national redistribution, career districts, transport-as-a-good travel, poaching)

A detailed spec covering five things, delivered as five milestones/commits:

**1. Good production now tracks the minigame's outcome, not just whether a shift got
worked.** `resolve_shift_game` (`panem_shared/shifts.py`) used to produce exactly one unit of
the district's quota good on every completed shift, win or lose. Now: a win produces one unit
plus a job-level-scaled chance (`JOB_LEVEL_BONUS_GOOD_CHANCE`, 5% Apprentice up to 60% Expert)
at a second unit, on top of the existing win wage boost; a real loss produces nothing, on top
of the existing lose wage penalty; a skipped/neutral shift is unchanged -- exactly one unit at
the unmodified wage. `resolve_shift_game` takes an optional `rng: random.Random` for the bonus
roll (defaults to a fresh one per call, same shape `market.py`'s `buy`/`sell` already use).

**2. The market is now a genuinely finite, per-district, nationally-redistributed pool, not
just locally-priced local production.** `panem_sim.systems.economy.run()` used to feed a
district's own raw production straight into its own price update -- what District One made was
the only thing that mattered for District One's market. Added a redistribution pass
(`_district_production_value`, `_redistribute`) that runs between computing raw supply and
everything downstream of it: every good's national total (summed across every district that
makes it) has `CAPITOL_CUT_FRACTION` (10%) taken off the top, and what's left is split among
every district that trades that good (produces or imports it) as an equal
`MARKET_BASELINE_ALLOCATION_FRACTION` (10%) floor plus a bonus weighted by each trading
district's share of *total national production value that day* (summed across everything it
makes, not just this one good) -- a district that produces a lot of everything ends up with
more of everything, including things it doesn't make itself, and a poor one gets little even of
its own necessities. Exports and pricing (`_run_exports`, `_update_prices`) run against this
redistributed figure now, not raw local production -- several `TestExports` assertions moved
accordingly (the same capacity/supply-capping logic, just against smaller, post-cut numbers).
`MarketPrice.supply` also stopped being purely informational: it's now today's actual
remaining purchasable stock, checked and decremented by `/market buy` (a new
`market_insufficient_stock` refusal once a district's daily allocation of a good runs out) and
incremented by `/market sell`. This is what makes "a finite amount of goods each day" concrete
rather than just a price signal.

**3. District goods realigned to the canon list, plus a new Career Training resource.**
`data/goods.yaml` display names updated to match (Fish -> Seafood, Livestock -> Meats, Produce
-> Fruits/Drinks, Masonry -> Stone) without touching any `id`s, so `routes.yaml`/jobs/tests
keep working unchanged -- the underlying industries already matched the requested list
one-for-one otherwise. Added a `career_training` good, produced only by the four career
districts (1, 2, 4, 9) via a new academy job in each (`d1_academy_trainer`/`d2_academy_trainer`
already existed; District Four and Nine got their own new `academy` location + trainer job to
match). It's never added to any district's `imports`, so it only ever trades within its four
producing districts, but still counts in full toward their national production-value ranking
from part 2 above -- "converted to value" without a special-cased value path. Every district's
`imports` also gained `transport` (needed for part 4 below, and for its own sake: everyone
needs some baseline transport allocation even if they don't produce any).

**4. Travel now spends units of a real good instead of a flat cash ticket.** The old system
charged cash at one of 13 synthetic per-destination `train_ticket_d{N}` goods' flat
`base_price` -- goods that were never in any district's `produces`/`imports`, so they never
participated in real supply/demand at all, just a fixed toll. Retired all 13 (and the now-
pointless `Good.kind` "commodity"/"ticket" discriminator they existed for) in favor of the one
`transport` good District Six already produces: `/travel district:<id>` now spends
`TRANSPORT_UNITS_PER_TRIP` (2, covering the round trip) from the character's `Inventory`
(`travel_svc.spend_transport`), bought at a district market like any other good. A free route
(home return, a Victor's home<->Capitol route) still skips this entirely, same as it used to
skip the cash deduction. This plugs travel into the same national redistribution as everything
else -- a poor district's transport allocation can run genuinely short, not just cost a fixed
amount.

**5. `/poach`: illegal hunting/gathering when the market allocation isn't enough.** New command,
new `panem_bot/services/poaching.py` -- a character at their district's `kind: outskirts`
location (the Capitol has none, so poaching is unavailable there) can attempt to poach a unit
of the district's primary food good (whatever it produces, falling back to whatever food it
imports) instead of buying it. Reuses the exact detection/consequence shape
`market.py`'s illicit-market catch already established: a probability roll
(`POACH_DETECTION_PROB`), then on a catch a fine, jail time, a reputation hit, and a district
`peacekeeper_pressure` bump (`POACH_FINE`/`POACH_JAIL_TICKS`/`POACH_REP_PENALTY`); on success,
`POACH_YIELD_QTY` units land in `Inventory`, no roleplay judgment call needed. Deliberately
scoped to just this mechanic -- "stealing from NPCs" stays something a GM/player narrates,
since fairly automating theft from a specific NPC needs judgment a formula can't make well.

**What this pass does not build**: "NPCs also need to comment only briefly... unless it's
specifically relevant" and "don't volunteer unrelated information" were already covered by
existing `system_prompt.md` rules from the dialogue/memory work above and needed no change. No
new UI surfaces a district's daily production-value rank directly (it only drives the
redistribution math internally) -- a `/district rank` command or similar is real follow-up
scope if players want to see it.

## Notes on the contraband system (illicit work, black market, stealing, jail)

A second large pass on top of the economy rework above: illicit jobs any player can declare,
a per-district black market fed only by that work, `/steal`/`/burgle`, and the jail loop
(priors, bail, lock-picking) all of that funnels into, plus a moderator lever to crack down on
a district and make all of it harder. One scope call made up front and worth flagging plainly:
the spec's skill checks ("a line circling a small target zone") are implemented as single
weighted probability rolls, not a real-time client minigame -- every existing detection
mechanic in this codebase (`market.py`'s illicit-market catch, `/poach`'s detection roll) is
already this exact shape, and a literal timing UI would need a whole new generic Activity
subsystem (unlike every existing Activity game, a skill check here isn't tied to a `Shift`)
that's also fundamentally unreliable over Discord's interaction API, whose round-trip latency
can't judge a sub-second window fairly. Every check below is a roll whose odds move with
difficulty/priors/district pressure, narrated in the reply text.

**1. Illicit jobs are self-declared, not catalog-driven.** `Job.legal`/`peacekeeper_attention`
already existed in `data/jobs.yaml` (three jobs even used them: `hob_trader`, `d6_hustler`/
`d6_black_marketeer`, `d8_smuggler`) but were dead fields -- nothing in `panem_sim`/`panem_bot`
read them, since the player job system was reworked to free-typed `Character.job_title` long
before this session, with no catalog left to check `legal` against. Rather than resurrecting a
catalog, character creation gained one more step after the existing job-title/shift-phase
prompts (`IllicitDeclareView`, a two-button choice mirroring `ShiftPhaseSelectView`): "is this
job illicit?", stored as `Character.job_is_illicit` and editable after the fact via `/staff
give job`'s new `illicit` parameter. `data/jobs.yaml`'s `legal`/`peacekeeper_attention`/
`options.risk` fields stay exactly as vestigial as before -- NPC-flavor-only, untouched.

**2. Illicit goods and each district's black market.** Four new contraband goods, deliberately
no alcohol or drugs (a children's server): `contraband_weapons`, `forbidden_literature`,
`smuggled_luxuries`, `counterfeit_papers`. Every district 1-12 (the Capitol trades in none of
this) got a new `illicit_produces: [<good>]` field (`District` schema) assigning it one, and an
`illicit: true` market location if it didn't already have one from the four districts
(D6/D8/D11/D12) that already had one authored -- 8 new dedicated locations, one flavor name
each ("The Black Armory", "The Scrap Circuit", ...). `panem_sim/systems/economy.py` carries
illicit production forward with none of the legal pipeline's Capitol cut or national
redistribution (module docstring point 8): a district's black-market stock is exactly what its
own illicit workers made that day, priced flat at `base_price`, and a quiet day really does
mean zero stock, unlike legal goods' `MARKET_SUPPLY_FLOOR` floor -- "the only way more goods
appear... is if people with illicit jobs work" holds literally. `/work`ing an illicit job
(`cogs/jobs.py::_finish_shift`) produces that good instead of the legal quota good via a new
`panem_shared.shifts.illicit_shift_output` (same won/neutral/loss shape as legal production,
kept separate rather than a branch inside `resolve_shift_game` since it reads a different good
list off `district`), and builds per-character `illicit_heat` (`ILLICIT_HEAT_PER_SHIFT`, more
on a real loss) that triggers an immediate arrest-evasion roll once it clears a threshold
(`panem_shared/jail.py::resolve_illicit_heat`, shared with `panem_api`'s `/work` endpoint for
the same reason `panem_shared.shifts` is -- that endpoint can't depend on `panem_bot`).

Access to the black market itself (`/blackmarket prices|buy|sell`, `panem_bot/services/
blackmarket.py`) is gated behind "good relations with certain NPCs", taken literally: each
district's `data/npcs/*.yaml` got one already-authored NPC flagged `black_market_contact:
true` (no new NPCs needed), and trading requires a `RelationshipRow` stance of Likes or Loves
with *that specific NPC* -- a stranger, or someone merely tolerated, is refused before any
trade math runs. Otherwise mirrors `market.py`'s buy/sell shape closely, including its illicit-
detection/consequence roll on every trade.

**3. `/steal` and `/burgle`: pickpocketing and burglary.** `panem_bot/services/stealing.py`,
once per day-phase (`Character.last_steal_tick`, compared via `tick // simtime.TICKS_PER_
PHASE` -- no new helper needed). A success is silent either way, no reputation cost. A failure
rolls twice more, matching the spec's "steal without alerting them, or with alerting them...
escape or get caught": `STEAL_ALERT_PROB` decides if the mark notices at all (a clean miss
otherwise), then `STEAL_ESCAPE_BASE_PROB` decides whether an alerted character gets away.
Only an actual catch carries consequence: jail, a fine, a general reputation hit, and -- for an
NPC victim specifically -- an additional `RelationshipRow.affinity` hit with them (a player
victim has no equivalent row to dock; Spec §6's relationship model only covers NPC standing).
Targets are resolved by free-typed name at the thief's own location, matching `/talk`'s NPC-
name resolution -- with a `target` autocomplete (`StealingCog.steal_target_autocomplete`, reading
the already-typed `character` param via `interaction.namespace`, same pattern `/talk`'s own
resident autocomplete uses) listing exactly who a thief could actually hit right now: approved
characters and NPCs sharing both district and exact location, each suffixed `(player)`/`(NPC)` to
disambiguate a name collision between the two. Typing a target by hand instead of picking a
suggestion also accepts a leading `@` (`@Commodus`), stripped before matching either way, for
players used to @-mentioning a name elsewhere in the server. `/burgle` reuses the exact same alert/escape/caught machinery against another
character's house (`Property.kind == HOUSE`) instead of a person -- `Property` carries no
`location_id` the way a person does, so a flat harder success rate (`BURGLE_BASE_SUCCESS`)
stands in for the "same location as you" precision a house can't offer, refuses your own house
and the wrong district, shares `/steal`'s cooldown, and pays out a capped fraction of the
house's `suggested_price` rather than debiting anyone.

**4. Jail: priors, bail, lock-picking.** Every jailing call site (`market.py`, `poaching.py`,
now also the illicit-work/steal/burgle paths above) used to duplicate the same `base_tick =
jailed_until_tick or 0; jailed_until_tick = base_tick + X` arithmetic inline. Consolidated into
`panem_shared.jail.commit_to_jail` (moved to `panem_shared`, not `panem_bot`, for the same
"panem_api needs it too" reason as `resolve_illicit_heat`): scales the sentence by
`Character.jail_count` priors, sets `jail_sentence_ticks` (the sentence's fixed original
length) and resets `jail_lockpick_tries_used`. `/bail` pays `BAIL_BASE_COST` plus a per-
remaining-tick charge to walk free immediately; `/lockpick` offers up to `LOCKPICK_MAX_TRIES`
(3) probability-roll attempts, odds fixed from `jail_sentence_ticks` (not the counting-down
`jailed_until_tick`) so a long sentence stays hard to pick for the whole stay rather than
easing up near release.

**5. Moderator crackdowns.** `/staff district crackdown` sets a new `DistrictState.crackdown_
until_tick` and immediately spikes `peacekeeper_pressure` (which `crisis.py` already relaxes
back to baseline over `CRISIS_RECOVERY_DAYS` once the window passes -- no new decay mechanism
needed). `panem_shared/jail.py::crackdown_bad_odds`/`crackdown_good_odds` read that window and
scale every illicit-activity probability this whole feature rolls -- market/black-market
detection, illicit-work arrest evasion, `/steal`'s and `/burgle`'s success/alert/escape odds --
harder in one place rather than duplicating the check at each call site. `dialogue.py`'s NPC
reply context gained a `district_on_edge` field (the caller pre-resolves `is_crackdown_active`
and passes a plain bool in, keeping `dialogue.py` itself tick-unaware like every other field it
renders) that surfaces as visible NPC nervousness the same way `crisis_level` already does.

**What this pass does not build**: the literal real-time circular skill-check UI the spec
describes (see the scope note at the top) -- every check here is a probability roll instead,
including the jail lock-picking minigame's "3 tries" (implemented as 3 separate `/lockpick`
command invocations, not a single interactive session). Stealing an inventory *good* (not just
money) from a player or NPC was considered and dropped: NPCs don't carry real `Inventory` rows
today (`Npc`'s `shop_goods` is flavor-only), so there'd be nothing to actually take from most
targets -- `/steal`/`/burgle` move money only. No `/character status` line surfaces `illicit_
heat`/`jail_count` directly; a player finds out about heat when the arrest roll actually fires.
(A later pass, "Notes on the contraband skill-check Activity minigames" below, replaces the
probability-roll-only posture above with a real playable minigame for `/lockpick`, `/steal`,
and `/burgle` -- read that section for the current behavior; the RNG rolls described here still
run as the fallback when no Activity is configured or the player hits Skip.)

## Notes on the contraband skill-check Activity minigames (lockpick/pickpocket)

A follow-up feature request: give `/lockpick`, `/steal`, and `/burgle` a real, playable skill
check -- the same in-Discord Activity mechanism `/work`'s minigames already use -- instead of
only ever rolling a probability, and make stealing from a player (including their house)
naturally harder than from an NPC, and make burgling a house impossible while its owner is
actually home.

- **The requested reference game couldn't be embedded as-is.** The request named a specific
  GitHub lockpicking project as a base to embed "the same way the work minigames are embedded."
  That project turned out to be a Unity/C# game (GPLv3-licensed code, CC BY-SA assets) -- not a
  browser page, so it can't load in the iframe `work.html`'s minigames use, and porting its code
  in would pull GPLv3 copyleft into this repository. Built instead: two original, from-scratch
  canvas minigames matching the same *feel* (a moving needle, strike when it's in the lit zone)
  without copying any of that project's code or assets -- `games/lockpick.js` (a dial with a
  bobby-pin-style pick, shared by `/lockpick` and `/burgle`: picking a lock is picking a lock
  whether it's a cell door or a house door) and `games/pickpocket.js` (a timing strike on the
  mark's pocket for `/steal`, adapted from a canvas sketch supplied with the request).
- **Same launch/result round trip `/work` already established**, generalized rather than
  duplicated per command: `panem_bot.activity_launch` (new) holds `/lockpick`'s, `/steal`'s, and
  `/burgle`'s shared "create an attempt, remember the interaction, offer Play + Skip" plumbing,
  mirroring `cogs/jobs.py`'s own `_activity_launch_view`/`_remember_interaction` pair. Unlike a
  `Shift`, a crime attempt has no reason to outlive the one interaction that launched it, so it
  lives entirely in Redis (`redis_keys.crime_attempt_key`/`crime_interaction_key`, ~14 minute
  TTL, matching Discord's own interaction-token window) rather than getting a new DB table.
  `panem_api` gained `GET /activity/crime/{attempt_id}` (who/what, and a `difficulty` float for
  sizing the minigame's target zone) and `POST /activity/crime/{attempt_id}/result` (one-shot --
  the attempt is deleted from Redis before it's applied, so a retried POST 404s instead of
  double-resolving), served from a new `crime.html`/`crime.js` page pair alongside `work.html`/
  `work.js`.
- **Discord's Activity URL Mapping is a single fixed root already pointed at `work.html`**, an
  external Developer Portal setting this session has no way to change -- so unlike `/work`, a
  crime attempt never attempts the `embedded_application` voice-channel invite launch (that
  would open the wrong game entirely). It always uses a plain link instead, which Discord still
  opens in the client's in-app browser overlay rather than a bare external tab -- the same
  fallback path `/work` itself uses when the player isn't in a voice channel.
- **The RNG-driven skill check didn't go away -- it moved.** `panem_shared.stealing` (new) holds
  `apply_steal_outcome`/`apply_burgle_outcome`, the alert/escape/caught chain factored out of
  `panem_bot.services.stealing`'s `resolve_steal`/`resolve_burgle` so `panem_api`'s result
  endpoint can call it directly with the minigame's own win/lose instead of rolling
  `STEAL_FROM_*_BASE_SUCCESS`/`BURGLE_BASE_SUCCESS` itself. Those constants still drive the RNG
  fallback (`roll_and_apply_steal`/`_burgle`, used when no `ACTIVITY_PUBLIC_URL` is configured or
  the player hits Skip) *and* the minigame's difficulty (`steal_difficulty`/`burgle_difficulty`,
  `1 - success_prob`, read by `games/pickpocket.js`/`lockpick.js` to size the target zone/needle
  speed) -- the same tuning surface drives both paths, so a player mark stays a harder skill
  check than an NPC one whichever way it's resolved, and a house stays the hardest tier of all.
  `panem_shared.jail` similarly gained `apply_lockpick_attempt`/`lockpick_difficulty`, and
  `panem_bot.services.jail.attempt_lockpick` (the RNG fallback) now calls the former instead of
  duplicating the tries/release bookkeeping.
- **`/burgle`'s "nobody's home" check** (the other half of this request): `Property` gained a
  `location_id` column, set for every `HOUSE`-kind property at seeding time
  (`panem_sim.world.seed_properties`, preferring a `residential`-kind location, falling back to
  `public` for a district authored with none -- every district schema-guarantees at least one).
  `check_can_burgle` now takes the owner `Character` the cog already looked up and refuses
  (`burgle_owner_home`) when the owner is both approved and physically at that same
  `location_id` right now. A property seeded before this column existed (or any non-`HOUSE`
  kind) has `location_id=None`, which just skips the check rather than refusing every burglary
  against old content.
- **What this doesn't change**: `/steal`'s own difficulty tiering (NPC easier, player harder,
  per the original pass above) and the alert/escape/caught chain after a failed check are
  unchanged -- only the *initial* "did the lift/pick/break-in itself succeed" step moved from a
  pure roll to a real minigame when one's available. The "3 tries" lockpick jail-escape limit is
  still 3 separate `/lockpick` invocations (each one launches its own Activity attempt), not a
  single session with 3 in-game chances.

## Notes on the web dashboard (every player command as tabs on the Activity)

A follow-up request: turn the map-only Activity into a full dashboard where a player can do
everything their slash commands do, organized into tabs -- with a jail tab that shows a cell with
the player's avatar in it while jailed. Built as nine milestones, each its own commit
(`static/tabs/*.js` + a `build_*_router` in `packages/panem_api/src/panem_api/dashboard_routes.py`
per domain), sharing one identity model and one cross-process-move pattern throughout.

- **Identity has no cryptographic auth**, matching the honesty `app.py`'s own module docstring
  already had for `/activity/work|crime/*`: `app.js`'s existing Discord SDK `authenticate()` call
  (previously its return value was discarded) now captures the real logged-in Discord user; a
  manual "Discord ID" text field is the fallback in preview mode / outside a real Activity
  iframe. Every dashboard request carries `discord_id` + the `character_id` picked from
  `POST /activity/dashboard/identify`'s list, and every route calls a shared
  `_resolve_owned_character` that 404s if `User.discord_id` doesn't match `Character.user_id` --
  enough to stop one player acting as another's character by guessing an id, not a real login.
- **The shell** (`static/index.html`/`app.js`): a character picker plus a hash-routed
  (`#map`/`#character`/`#work`/`#market`/`#travel`/`#social`/`#jail`/`#crime`/`#housing`) tab bar,
  each tab a dynamically-`import()`ed `static/tabs/<name>.js` module with a uniform
  `mount(root, ctx) -> {unmount?}` contract (`ctx` = `{discordId, characterId, apiFetch,
  refreshIdentity}`, the same shape the minigame modules under `static/games/*.js` already use).
  `showTab()` guards against two overlapping mounts (a rapid double tab-switch racing a dynamic
  `import()`) with a generation counter; a real bug caught by live Playwright testing, not a
  hypothetical -- the manual-Discord-ID input's `change` handler used to re-mount the current tab
  after its own network round trip, which could wipe out whatever the player had half-typed into
  a form in the meantime. Fixed by dropping that stale re-mount (`ctx.discordId()` is a live read
  already) and adding the generation guard as defense-in-depth for every tab, not just that one.
  `ASSET_VERSION` in `app.js` (bumped every milestone, currently `9`) cache-busts every
  dynamically-imported tab module together with `index.html`'s own `<script src="/app.js?v=N">`.
- **Character** (`character.js`): list/create/edit avatar & proxy tag/retire, mirroring
  `/character list|create|avatar|tag|retire`. Creation writes the `Character` row directly (no
  bot token to post the staff-approval embed from `panem_api`) and district is a plain `<select>`
  rather than inferred from a Discord guild role -- the one deliberate behavioral difference from
  the in-Discord flow, since a wider OAuth scope wasn't worth adding just for this. A dashboard-
  created character reaches staff the same way a Discord-created one eventually would anyway:
  `CharacterCog._announce_pending_characters`, a `tasks.loop` background poll (already needed
  because *any* pending character can go unnoticed) picks up `status=pending AND
  approval_notified_at IS NULL` rows regardless of which path created them and posts the usual
  embed, stamping `approval_notified_at` so it's not posted twice.
- **Jail** (`jail.js`), the tab named explicitly in the request: an inline-SVG cell (bars, floor,
  a dim light -- original art, no external image asset, consistent with this codebase's existing
  lockpick/pickpocket minigame art) with the character's avatar (`Character.avatar_url`, falling
  back to the Discord SDK avatar) composited inside while `jailed_until_tick > now`, an empty cell
  otherwise. Bail pays instantly; "Attempt Lockpick" mints a crime attempt exactly like
  `cogs/jail.py`'s `/lockpick` does and embeds the existing `crime.html?attempt_id=...` page in an
  `<iframe>` to actually play it -- no minigame logic duplicated.
- **Crime** (`crime.js`): steal/burgle target pickers (an improvement over the bare-string
  `owner`/`target` params `/steal`/`/burgle` take today -- these list who's actually reachable)
  launching the same iframe-embedded minigame pattern as jail, plus an instant-resolve poach
  button (`/poach` never launches an Activity on the bot side either).
- **Work** (`work.js`): one new "start" endpoint mirrors only `/work`'s shift-finding half;
  actually playing or skipping a shift reuses the existing `/activity/work/{shift_id}` (status)
  and `.../result` (POST) endpoints unchanged, the same reuse-over-duplication approach as jail/
  crime. The dashboard's ad-hoc shift override only checks `Position.GAMEMAKER` (a real
  in-fiction position), not the Discord staff role half of the bot's `is_staff OR GAMEMAKER`
  check -- the dashboard has no concept of a Discord guild role to check against.
- **Market/Black Market** (`market.js`), one tab with a legal/illicit toggle rather than two:
  mirrors `/market prices|buy|sell`, `/inventory`, and `/blackmarket prices|buy|sell` -- both are
  instant-resolve with no minigame, so no iframe is needed here.
- **Travel + Residents/Social** (`travel.js`, `social.js`): `travel.js` mirrors `/travel` (split
  into its location and cross-district sub-flows, same as the command itself) and `/where`
  (folded into the one status panel rather than a separate call). `social.js` combines two
  read-only panels: Residents (`/resident list|where|profile`, with each row's current location
  included up front rather than needing a second request per NPC) and a character's current
  `/talk`/`/engage`/`/scene` engagement, shown read-only with a "Continue in Discord" deep link
  (`https://discord.com/channels/{guild_id}/{thread_id}`) rather than any write action -- a
  deliberate scope decision made up front: making `/talk`/`/engage`/`/scene` fully interactive
  from the dashboard would need a new dashboard -> Redis -> `panem_bot` relay, since only the bot
  process holds a token and can create/post to a Discord thread. `discord_guild_id` (new,
  optional `create_app` parameter, `Settings.discord_guild_id`) is `0` on a dev/preview server
  that hasn't configured it, and the link is simply omitted rather than pointing at a bogus
  `channels/0/...` URL.
- **Housing** (`housing.js`): mirrors every `/housing` subcommand (`buy` incl. financed,
  `buy-complex`, `refinance`, `sell`/delist, `rent-out`, `auction-start`/`-bid`, `rent`,
  `move-out`, `inn-stay`) plus `/sleep`. The status read goes one step past `/housing status`:
  it also lists *every* property the character owns (not just their current `housing_property_id`
  "home", which only ever tracks the most recently bought house or leased apartment) so the tab
  can offer sell/refinance/auction/rent-out actions per owned property -- something the dashboard
  can show all at once that a single Discord command reply couldn't.
- **The cross-process move pattern**, repeated once per domain needed: a Discord-independent
  module living under `panem_bot/services/` moves verbatim to `panem_shared/`, and
  `panem_bot/services/<name>.py` becomes a one-line-per-symbol re-export shim
  (`from panem_shared.<name> import (x as x)`) so every existing cog/test is untouched. Moved for
  the dashboard this way: `errors.py` (the `ServiceError` hierarchy), `characters.py`, `jobs.py`,
  `travel.py`, `housing.py`, `market.py`, `blackmarket.py`, `poaching.py`, and extensions to the
  already-shared `jail.py`/`stealing.py`/`shifts.py`. One function, `proxy.py`'s
  `has_location_access`, moved alone into a new `panem_shared/location_access.py` rather than the
  whole (otherwise Discord-heavy) `proxy.py` module traveling with it -- the one genuinely
  Discord-independent piece of that file, and needed by `travel.py`/`dialogue.py`/`engagements.py`
  alike, so a small dedicated module fit better than folding it into `travel.py` directly.
- **Fixed along the way, not a dashboard bug but exposed by writing dashboard tests for it**: the
  shared `make_content_with_market()` test fixture (`tests/unit/test_api_app.py`, from the
  Milestone 6/blackmarket build) put both the legal-market and black-market tests at the same
  `illicit=True` location. `market.py`'s `buy`/`sell` roll illicit detection for *any* `illicit`
  location, not only for illicit goods, so a legal grain purchase in that fixture had a small
  unseeded chance of an unrelated fine landing on it mid-test. Split into two locations --
  `legal_market` (not `illicit`) and `market` (`illicit=True`, still required by
  `resolve_black_market_location` for the black-market tests) -- makes the legal-market tests
  deterministic without touching `market.py`'s actual behavior.
- **Verification**: every milestone got the full ruff/mypy-baseline(146 errors, pre-existing and
  unrelated to this feature)/pytest loop, `node --check` on every changed `static/**/*.js`, and a
  live Playwright smoke pass against a real Postgres-backed server using real `data/` district
  content -- clicking through every one of the nine tabs, confirming each renders real content
  (not a placeholder or a load error) with no console errors besides the browser's own
  `favicon.ico` 404. A real Discord-launched Activity iframe handshake (the SDK's `authenticate()`
  call against Discord's actual OAuth flow) can't be verified from this environment -- the same
  documented gap `app.py`'s own module docstring already calls out for `/activity/work|crime/*`.

## Notes on dashboard fixes from live Discord testing

Three issues surfaced once the dashboard was actually launched as a real Discord Activity
(the one path that can't be exercised from this environment -- see the note directly above).

- **Rejected characters showed up in the Character tab.** `GET /activity/dashboard/characters`
  (the tab's own listing, distinct from `/identify`'s header picker which already filtered to
  `APPROVED`) returned every character regardless of status. A rejected character is a dead end
  here -- no appeal/resubmission flow exists in the dashboard -- so it's excluded now; pending,
  approved, and retired characters (still things a player manages) remain visible. `DEAD` is
  excluded for the same reason as `REJECTED`.
- **District 0 (The Capitol) rendered as the literal string "District 0"** in the Character and
  Housing tabs. Those tabs built their own `District ${id}` label client-side instead of using
  the district's real name from content -- true for every district, not just 0, but only
  noticeable there since every other district already has a distinct number people don't read
  literally. `CharacterDetail`/`HousingOwnedProperty`/`HousingStatusResponse` now carry
  `district_name`/`current_district_name`/`home_district_name` resolved server-side from
  `ContentBundle.districts` (the same source `travel.py`'s status endpoint already used), and the
  two tabs display those instead of formatting the id themselves.
- **The header character-selection dropdown did nothing when clicked.** Root cause: Discord
  scales/transforms an Activity's iframe content for its own embedding, which is a known way to
  break Chromium's native `<select>` popup positioning -- the popup either fails to open or
  renders somewhere invisible/unclickable, and this can't be reproduced in a plain (non-Discord)
  browser tab, which is exactly why it was never caught during the original build's Playwright
  passes. Every native `<select>` in the dashboard (`app.js`'s header picker, plus travel's
  location/district selects, crime's target selects, market's legal/black-market toggle, the
  character-creation shift-phase select, and the map tab's district select) is now a hand-rolled
  dropdown built from plain `<div>`/`<button>`/`<ul>` elements (`tabs/_shared.js`'s new
  `dropdown()` helper, mimicking just enough of `<select>`'s surface -- a `value` getter/setter, a
  `disabled` setter, a real `"change"` event -- that call sites needed only mechanical changes).
  Fixing this surfaced a second, genuine bug in the header widget itself: a real click on the
  toggle first blurs whatever previously had focus (the manual "Discord ID" preview field), and a
  browser fires a native `change` event on blur whenever the field's value differs from what it
  was when the field *gained* focus -- true here even with no edit, since the field is filled
  programmatically rather than typed into per visit. That spuriously re-ran `refreshIdentity()` ->
  `renderCharacterOptions()`, which closes the dropdown menu right as the same click opens it. The
  preview-mode Discord-ID input's `change` handler now no-ops when the value hasn't actually
  changed.
- **That `dropdown()` fix immediately broke most tabs a second way**: "The requested module
  './_shared.js' does not provide an export named 'dropdown'". Every tab imports `_shared.js` via
  a plain `import ... from "./_shared.js"` -- a bare specifier with no cache-busting query string,
  unlike every other dashboard asset (`app.js`, each `tabs/*.js`) which already gets one
  (`?v=${ASSET_VERSION}`) for exactly this reason: Discord's Activity iframe embedding caches
  static assets aggressively at its proxy layer, independent of this server's own response
  headers, so a stale cached `_shared.js` from before `dropdown()` existed kept being served.
  `_shared.js` was the one file that had never needed its own cache-busting before, since nothing
  had changed its export list since it was written. Every import site (`app.js` and all nine
  `tabs/*.js` modules) now points at `"./_shared.js?v=2"` / `"./tabs/_shared.js?v=2"`; `_shared.js`'s
  own module docstring documents bumping that literal in every importer whenever this file's
  exports change again.
- **The dropdown still didn't work after that -- "same issue" reported again, this time with the
  OAuth/token/identify flow all logging clean 200s.** That ruled out both prior fixes as the live
  cause: nothing client-side ever surfaced a load error, and the underlying request flow was
  reaching this server correctly. The remaining suspect was the `?v=N` cache-busting convention
  itself -- it only defeats a cache that keys on the *full* URL including the query string, an
  assumption this server has no way to confirm about whatever caching layer sits between a real
  Discord Activity and it. A layer that instead normalizes or drops query strings before caching
  (not unusual for a CDN optimizing static-asset delivery) would keep serving a stale `app.js`/
  `_shared.js`/`index.html` forever, no matter how many times the version literal is bumped --
  the version-bump convention was necessary but, on its own, resting on an unverifiable
  assumption. Added a `NoCacheStaticFiles` subclass (`app.py`) wrapping the dashboard's static
  mount, setting `Cache-Control: no-store` on every response -- the standards-based instruction to
  *any* well-behaved intermediate cache not to retain a response at all, independent of whatever
  key it caches on. This doesn't replace the `?v=N` convention (browsers that ignore `no-store`,
  or a misbehaving cache, still benefit from a changed URL), but it's the stronger guarantee for a
  dashboard under active iteration, and it's what should have been in place from the first
  caching-related fix rather than reached for only after two rounds of the same report.
- **The exact same "does not provide an export named 'dropdown'" error recurred after all of the
  above.** Root cause this time was a genuine process mistake, not a new class of bug: the commit
  that added `?v=2` to every `_shared.js` import (`app.js` and all nine `tabs/*.js` files' content
  all changed) never bumped `ASSET_VERSION` (`app.js`) itself -- so `/app.js?v=11` and every
  `./tabs/<name>.js?v=11` URL stayed byte-for-byte identical to the version already fetched and
  cached *before* that commit, meaning any URL-keyed cache (which the `Cache-Control: no-store` fix
  addresses only for requests made *after* it shipped, not ones already cached before) had every
  right to keep serving those old, pre-`?v=2` files under those unchanged URLs -- including a
  `travel.js`/`crime.js`/etc. that still imported bare, unversioned `_shared.js`. Bumped
  `ASSET_VERSION` to `"12"` and `index.html`'s matching `<script src="/app.js?v=12">` to close the
  gap: every file this feature's static assets are cache-busted through now actually has a version
  bump behind it. The standing lesson for this convention, now also called out in `_shared.js`'s
  own docstring: editing a file inside `static/` is only half the fix -- the file (or files) whose
  *own* content references its URL, all the way up to `index.html`, needs its version literal
  bumped too, or the change never reaches a client relying on any layer of URL-keyed caching.

## Notes on two more live-reported dashboard bugs (Work tab)

Both surfaced together from clicking "Play for your shift"/"Skip (neutral wage)" with no
character selected -- genuinely two separate bugs, not the same caching issue recurring again.

- **"[object Object]" instead of a real error message.** Root cause: with no character selected,
  `work.js`'s `ensureShift()` requested `.../work/null/start`; FastAPI rejects a non-integer path
  parameter with a 422 whose `detail` is a *list* of `{loc, msg, type}` validation-error objects,
  not a string -- unlike every other dashboard error, which carries a plain `reason_key` string.
  `_shared.js`'s `humanize()` only handled the string case, returning the list/object unchanged for
  anything else; `fetchJson`'s `new Error(humanize(body.detail) || ...)` then coerced that non-string
  value with `String(...)`, which is exactly "[object Object]" for an object (or an array of them).
  `humanize()` now extracts each validation error's own `msg` when `detail` is a list, and otherwise
  returns `undefined` so the caller's `||` fallback (`` `${path} -> ${response.status}` ``) takes
  over -- never a raw object reaching `new Error()` again. Also added an explicit guard in
  `ensureShift()` so a click with no character selected never fires that malformed request in the
  first place, surfacing "Pick a character above first." immediately instead.
- **That guard shouldn't have been reachable at all -- `refresh()` already sets `actionsEl.hidden =
  true` whenever no character is selected, so how were the buttons visible enough to click?** They
  were never actually hidden: `actionsEl`'s `class="field-row"` sets `display: flex` in
  `dashboard.css`, which -- being an *author* rule -- overrides the browser's own default
  `[hidden] { display: none }` at equal selector specificity, regardless of which one is defined
  later. Setting `.hidden = true` on any `.field-row`-classed element (also `jail.js`'s Bail/
  Lockpick action row, same bug, not yet reported) toggled the DOM attribute but never actually
  hid anything. Added a single `[hidden] { display: none !important; }` rule to `dashboard.css` --
  the standard fix for this well-known "a component's own `display` beats the `hidden` attribute"
  CSS gotcha (the same one Bootstrap and other frameworks ship), rather than hunting down every
  individual class that sets its own `display` today or the next one that does so tomorrow.

## Notes on four more live-reported dashboard bugs (identity, ordering, dropdown positioning, re-auth)

- **Header character dropdown said "No characters" despite the player having approved
  characters.** Root cause: every dashboard POST/PATCH body built its JSON with
  `discord_id: Number(ctx.discordId())`. A real Discord snowflake is an 18-19 digit integer --
  `175928847299117063` in Discord's own docs example -- while `Number.MAX_SAFE_INTEGER`
  (`2**53 == 9007199254740992`) is only 16 digits, ~100-1000x smaller. `Number(...)` doesn't throw
  past that point; it silently rounds to the nearest representable IEEE-754 double, corrupting the
  low digits. `POST /activity/dashboard/identify` (the header dropdown's data source) got a
  mangled id that matched no `User` row, so it always saw zero characters -- while the Character
  tab's `GET .../characters?discord_id=...` call (a URL query string, never wrapped in `Number()`)
  sent the id untouched and worked correctly the whole time, which is exactly why the two views
  disagreed. Fixed by dropping `Number(...)` around every `discord_id` field dashboard-wide (`app.js`,
  and every `tabs/*.js` POST/PATCH body) -- it's already a string from `getDiscordId()`/the SDK/the
  manual-id input, and FastAPI/Pydantic parses a numeric JSON *string* into an exact-precision
  Python `int` with no float ever involved, so passing it through unchanged is both correct and
  simpler than the broken `Number()` wrapping it replaced. (Every numeric *game* field --
  `qty`, `age`, `district_id`, `destination_id`, etc. -- is a small int these small numbers are the
  right call for, and was left as `Number(...)`; only `discord_id` is a snowflake.) Added a
  regression test (`test_accepts_a_real_snowflake_sent_as_a_json_string`) asserting `/identify`
  resolves a real ID sent the way the fixed frontend now sends it.
- **Character tab listed approved/retired/pending characters in creation order instead of grouped
  by status.** `list_my_characters`' query only had `.order_by(Character.id)`. Added a `CASE`-based
  `status_rank` (approved=0, retired=1, pending=2) ordered ahead of `Character.id`, so approved
  characters always sort first regardless of when each one was created.
- **The shift-phase dropdown on the character-creation form didn't visibly drop down** (and, once
  investigated, neither did *any* other per-tab dropdown built by `tabs/_shared.js`'s `dropdown()`
  factory -- market's legal/black-market toggle, crime's steal/burgle target selects, travel's
  location/district selects, map's district select -- this just happened to be the one instance
  reported). `.custom-select-menu` is `position: absolute`, which anchors to the nearest
  *positioned* ancestor. Only the header's own `#character-select` (an ID rule) ever got
  `position: relative`; the generic `.custom-select` class every `dropdown()` instance actually
  wears never did. With no positioned ancestor, each of those menus anchored to the page's initial
  containing block instead of its own toggle button -- `top: calc(100% + 4px)` of *that* renders far
  down the page, nowhere near where the player clicked. It wasn't failing to open; it was opening
  somewhere invisible, which looks identical to "doesn't drop down" from the UI. Fixed by moving
  `position: relative` onto the generic `.custom-select` class in `dashboard.css`, fixing every
  instance at once instead of only the header's. Verified live with Playwright: the shift-phase
  menu now renders 4px below its own toggle button, matching the CSS's `calc(100% + 4px)`.
- **Discord's authorize consent popup appeared on every single Activity launch.** `app.js` called
  `commands.authorize()` (the step that shows that popup) unconditionally on every load, then threw
  away the resulting `access_token` right after exchanging it for one use. Discord's OAuth2 access
  tokens from this flow stay valid well past a single launch, independent of the iframe/session that
  requested them, so there was no need to re-run the full consent flow every time. Now the token is
  cached in `localStorage` after a successful exchange; on a later launch, `authenticateWithDiscord()`
  tries `commands.authenticate()` directly with the cached token first, and only falls back to the
  full `commands.authorize()` + `/activity/token` exchange if that fails (expired/revoked token, or no
  cached token yet). `identify` is a low-sensitivity scope (just id/username/avatar, no more than any
  slash command's `character:` autocomplete already sees), so caching it client-side is a reasonable
  tradeoff given this process's already-documented lack of a stronger session layer (see
  `dashboard_routes.py`'s module docstring).

## Work tab: result display delay + a client-side work log

Two requested improvements to the dashboard's Work tab, both in `static/tabs/work.js`:

- **The minigame's result screen used to disappear instantly.** `work.js`'s (the standalone
  minigame page, `static/work.js`) `finish()` posts a `work-result` message to the parent window
  the moment it has one, and the dashboard tab's listener used to clear the iframe and refresh in
  that same event -- closing the game the exact instant it announced "X earns N money" (or a
  level-up, or an arrest), before anyone could actually read it. The listener now leaves the iframe
  (and its result screen) on screen for `RESULT_DISPLAY_MS` (5 seconds) before clearing it and
  calling `refresh()`; starting a new shift or unmounting the tab cancels that pending timer so it
  can't fire against a since-replaced iframe.
- **A work log panel** now sits to the right of the Work tab (`.work-layout`'s two columns in
  `dashboard.css`), listing recent shift outcomes for the selected character -- which minigame was
  played (or "Skip" for a neutral-wage skip), win/loss color-coded green/red, the wage earned, and
  any level-up/arrest. There's no server-side shift-history endpoint backing this (nothing else in
  this codebase tracks per-shift history either), so it's kept client-side in `localStorage`, keyed
  per character id, capped at the last 20 entries -- the same kind of convenience-only, not-a-source-
  -of-truth persistence `app.js` already uses for the remembered character selection. The standalone
  minigame page now also reports which game was played (`game: game.label` in its `work-result`
  message, from each `games/*.js` module's own exported `label`) so the log can show it.

## Crime tab / Jail tab: the same instant-close bug, one more time

Same root cause as the Work tab fix above, found in the two other places that embed `crime.html`
in an iframe: `static/tabs/crime.js`'s Steal/Burgle and `static/tabs/jail.js`'s lockpick both
cleared the iframe the instant `crime.html`'s `crime-result` postMessage arrived, wiping its own
result text ("gets away with N money, unnoticed", "caught -- fined and jailed", the lock giving
way or holding) before it could be read. Both now hold the iframe open for `RESULT_DISPLAY_MS`
(5 seconds, matching `tabs/work.js`) before clearing it, with the same pending-timer-cancelled-on-
unmount/on-starting-a-new-attempt handling `tabs/work.js` already has. `/poach` (the Crime tab's
third action) never launches an iframe -- it resolves instantly and its `resultLine` text was
already left alone until the next action, so there was nothing to fix there; verified live that
its result text is unchanged after several seconds, unlike the iframe-based flows.

## Fixed: getting jailed could leave you shown as already free

Live report: "still says i am free when im in jail." Root cause was in `panem_shared.jail.
commit_to_jail` (called by every jailing path -- `/steal`, `/burgle`, illicit-market catches,
poaching, illicit-work heat arrests): a fresh sentence was anchored at `character.jailed_until_tick
or 0`, ignoring the actual current world tick entirely. For a first-time offender (`jailed_until_tick`
starts `None`) that meant `jailed_until_tick` was set to the *sentence length itself* -- e.g. tick 14
-- regardless of what tick the world was really on. Any real game has a world tick well past 0
almost immediately, so `jailed_until_tick (14) <= current_tick (5000)` read as already free (the
same `jailed_until_tick > current_tick` check both `panem_shared.jail.check_is_jailed` and the
dashboard's `/activity/dashboard/jail/{id}` endpoint use) the instant the sentence was set -- the
character was jailed and shown as free in the same breath. A repeat offender whose previous
sentence had already lapsed by the time of a new offense hit the identical bug via the "extend an
existing sentence" branch.

Fixed by adding a `current_tick` parameter to `commit_to_jail` and anchoring at
`max(current_tick, character.jailed_until_tick or 0)` instead of just `character.jailed_until_tick
or 0` -- a sentence always runs at least from *now*, whether it's a fresh one or extending one
that's still actually active. Every call site already had `current_tick` (or an equivalently named
`tick`) in scope except `poaching.resolve_poach`, which didn't take it as a parameter at all (its
two callers -- `dashboard_routes.py`'s `/poach` endpoint and `panem_bot.cogs.poaching`'s `/poach`
command -- didn't fetch the world tick either); added it there and threaded it through both.
Verified live end to end: jailing a character at a non-zero world tick (`commit_to_jail(character,
14, 5000)`) now sets `jailed_until_tick=5014`, and `GET /activity/dashboard/jail/{id}` correctly
reports `"jailed": true`. Added regression tests at every layer this touched (`test_shared_jail.py`,
`test_poaching_service.py`) and updated the existing market/blackmarket/stealing consequence tests,
which had been asserting the old (buggy) `jailed_until_tick == <sentence>` behavior precisely
because they already passed a non-zero tick -- proof the bug was there to catch all along, just
never checked against.

## Fixed: the character selector still read "jailed" after release, and jailed characters could travel/work

Two more bugs found live off the jail fix above. First: "it says i am 'jailed' in the top right and
in characters screen, but also says im free in the jail tab" -- the opposite-looking symptom from
the previous fix, but the same underlying mistake in a different spot. `app.js`'s character-selector
label (`characterLabel`) and `tabs/character.js`'s status line both read `character.jailed_until_tick`
truthiness directly instead of comparing it against the current world tick, so a character whose
sentence had already *lapsed* (a stale, in-the-past `jailed_until_tick` -- exactly what the previous
fix's `commit_to_jail` bug used to produce for every existing jailing before it was fixed) still read
as "(jailed)" everywhere except the Jail tab itself, which was the one place already doing the
correct `jailed_until_tick > current_tick` comparison (`jail_status`'s own logic). Fixed by having
`dashboard_routes.py` compute a proper `jailed: bool` field (same comparison as `jail_status`) on
both `DashboardCharacterSummary` (`/activity/dashboard/identify`) and `CharacterDetail`
(`/activity/dashboard/characters`), and switching `app.js`/`tabs/character.js` to read that instead
of `jailed_until_tick` directly -- `jailed_until_tick` being set no longer means "currently jailed"
by itself, so nothing in the frontend should read it that way. `ASSET_VERSION` bumped 16 -> 17.

Second, a request in the same report: "make sure it isn't possible to travel while jailed or work
while jailed." Cross-district travel (`travel_svc.check_can_travel_district`) already refused a
jailed character (`travel_jailed`), but in-district location travel (`check_can_travel`, used by
both `/travel location:` and the Travel tab's location move) had no jail check at all, and neither
did starting a work shift (`/work`, and the Work tab's `/activity/dashboard/work/{id}/start`) --
both were live gaps, not something this session had built in and then broken. Added
`panem_shared.jail.check_not_jailed(character, current_tick, reason_key)` (the mirror of the
existing `check_is_jailed`, which is for the opposite case -- actions only a jailed character can
take, like bail/lockpick) and wired it into `check_can_travel` (reusing the existing `travel_jailed`
string) and into both `/work` entry points (`work_jailed`, a new string) before either does anything
else. `check_can_travel_district`'s existing inline check was also switched to call the same helper,
for one shared implementation instead of two copies of the same comparison. Verified live: a
character with `jailed_until_tick` past the current world tick gets `{"detail": "travel_jailed"}` /
`{"detail": "work_jailed"}` from both dashboard endpoints, while a character whose sentence had
already lapsed travels and reaches the ordinary `job_no_open_shift` refusal normally -- the jail
check doesn't fire for someone who's actually free. Added regression tests at every layer
(`test_shared_jail.py`'s new `TestCheckNotJailed`, `test_travel_service.py`, `test_api_app.py`).

## Staff: forcibly jail a character, and a Staff tab on the Activity dashboard

Two asks: give staff a way to jail someone directly (jailing had only ever been a *side effect* of
crime/game resolution -- `commit_to_jail` was called from `resolve_illicit_heat` and the
market/stealing/poaching consequence paths, but no staff command called it), and put staff tools on
the web dashboard behind a tab only staff can see.

**Bot side** is the easy half: `/staff jail character:<name> ticks:<n> reason:<text?>`
(`panem_bot/cogs/staff.py`) follows the exact shape every other `StaffCog` command already uses --
`@app_commands.check(_is_staff)` (`PanemBot.is_staff` checking the interacting member's roles against
`Settings.staff_role_id`), calls `panem_shared.jail.commit_to_jail(character, ticks, current_tick)`
directly (the same function every existing jail path already calls), and logs via the existing
`log_staff_action` (writes a `StaffAction` row, best-effort posts a one-line summary to
`Settings.log_channel_id`).

**The dashboard tab is the interesting half**, because nothing like it existed: every other dashboard
route (`dashboard_routes.py`) trusts a client-supplied `discord_id`/`character_id` pair for "does
this player own this character" -- fine for that (not cryptographic auth, just enough to stop
guessing, per that module's own docstring), but wrong for "is this Discord user staff", which is a
privilege decision, not an ownership check. `panem_api` also had zero Discord API access of any
kind before this -- no bot token, no gateway connection, no REST calls beyond one-shot webhook edits
using a per-interaction token `panem_bot` had already minted. There was no way to answer "is this
Discord user staff" from this process at all.

Fixed by giving `panem_api` its own (limited) Discord REST access: a new `panem_api/discord_staff.py`
module with `fetch_is_staff(discord_id, bot_token, guild_id, staff_role_id)`, which calls Discord's
`GET /guilds/{guild_id}/members/{user_id}` with the bot token (`Settings.discord_token` -- already
loaded by every process via the same shared `Settings`, but previously only ever *used* by
`panem_bot`) and checks the returned role list for `staff_role_id`, mirroring what `PanemBot.is_staff`
already does with a live cached `discord.Member`. Any of `bot_token`/`guild_id`/`staff_role_id` being
unset, or the request failing/erroring, reads as "not staff" -- fails closed by default, so a
deployment that hasn't set `STAFF_ROLE_ID`/`DISCORD_TOKEN` for `panem_api` (a new, optional
requirement -- the dashboard works exactly as before without them, just with no Staff tab) never
accidentally exposes staff actions. Verified live: with no staff config passed to `create_app`,
`/activity/dashboard/identify` returns `"is_staff": false` and `POST /activity/dashboard/staff/jail`
403s with `staff_only`, unconditionally.

This check is re-run **server-side on every staff route**, not read off whatever `/identify` last
returned -- `/identify`'s `is_staff` field is only used client-side to decide whether `app.js` shows
the Staff tab's nav button at all; a client lying about it (or the field going stale) can't grant
itself the ability to actually jail someone, since `build_staff_router`'s own `_require_staff` calls
`fetch_is_staff` again before doing anything. `POST /activity/dashboard/staff/jail
{discord_id, character_name, ticks, reason?}` resolves the target by exact name (any character, not
just the caller's own -- there's no `_resolve_owned_character` ownership check here, deliberately,
since staff act on other people's characters), calls the same `commit_to_jail`, writes a `StaffAction`
row, and best-effort posts to `Settings.log_channel_id` via the same new REST module (`post_staff_log`
-- a plain `POST /channels/{id}/messages` with the bot token, no gateway needed) so a dashboard-issued
jail shows up in the same log channel a Discord-issued one does. A missing/misconfigured log channel
is a no-op, same "logging must never fail the action" posture as the bot's own `log_staff_action`.

`app.js` now tracks `state.isStaff` (set from `/identify`'s response) and only adds the Staff tab's
nav button when it's true (`visibleTabs()`); `setupNav()` only rebuilds the nav bar's DOM when staff
status actually *changes*, since `refreshIdentity()` now calls it on every identity refresh (including
the manual Discord-ID field's change handler, which deliberately doesn't remount the current tab) and
a full rebuild on every one of those would otherwise drop the "active" tab highlight until the next
tab switch. The new `static/tabs/staff.js` is a single form (character name, ticks, optional reason)
posting to the new endpoint -- reachable by hash (`#staff`) even without the nav button, which is
fine: the route re-checks staff status itself regardless of how the tab was reached.
`ASSET_VERSION` bumped 17 -> 18.

Verified live: with staff config unset (the default), `/identify` reports `is_staff: false` and
`/staff/jail` 403s. The role-check itself (`fetch_is_staff` returning true/false depending on the
member's roles, failing closed on a lookup error) and the full jail-and-log flow (StaffAction row
written, correct `payload`, best-effort log post firing with the right channel/token/content when
configured) are covered by mocking Discord's REST responses in `test_api_app.py`'s new
`TestDashboardStaff` -- this sandbox has no route to the real Discord API to verify the happy path
any further live than that, same documented limitation as every other Discord-REST-touching test in
this file. Note: this adds two more `mypy` baseline errors (146 -> 148) -- the same pre-existing
`self.bot.db()`/`log_staff_action(bot=self.bot)` typing gap (`commands.Bot` vs. the actual `PanemBot`
subclass) every other `StaffCog` command already has, from the new `/staff jail` command using the
same pattern; not a new category of error.

## Rebuilt the lockpicking minigame as an actual pin-tumbler puzzle, not a reskinned timing game

User feedback: "why is lockpicking the same sliding skill check as pickpocketing? It should have
integrated the lockpick game from the github repo i linked before." Tracing it back, an earlier
session's commit message for the original Activity minigames explained the reference project "turned
out to be a Unity/C# GPLv3 game, not browser-embeddable" and built an original canvas game instead --
but what got built (`games/lockpick.js`) was a needle sweeping across a dial that you strike when it's
over a lit zone, which is *mechanically identical* to `games/pickpocket.js`'s own timing game (same
sweep-and-strike shape, just different numbers). Picking a lock and picking a pocket read as the same
minute of gameplay. The actual repo link wasn't preserved anywhere (not the commit message, not code
comments, not later session context), so rather than guess at a URL, asked the user directly; they
clarified what they actually wanted: "a lock pick minigame where you actually have to fiddle with a
physical lock as opposed to the simple reaction time game."

Rewrote `games/lockpick.js` from scratch as an original pin-tumbler mechanic (still not a port of any
third-party project or code -- a Unity/C# GPLv3 codebase still can't be embedded in a browser iframe
or have its code pulled into this repo regardless of what's referenced, but the general pin-tumbler
*mechanic* itself -- tension + pins pushed to a hidden shear line -- isn't anyone's proprietary
code). `label`/`instructions()`/`mount(boardEl, {onFinish, setStatus, difficulty})`'s contract is
unchanged, so `crime.js` (shared coordinator for `/lockpick`'s and `/burgle`'s Activity launch) needed
no changes at all -- only `games/lockpick.js` itself, plus `crime.css` (new `.lockpick-meta` row
styling) and version bumps (`crime.js`'s `ASSET_VERSION` 2 -> 3, `crime.html`'s `/crime.js?v=`/
`/crime.css?v=` to match).

**The mechanic**: `pinCount` pins (3-6, scaling with `difficulty`), each with a hidden `shear` height
the player has to find by feel -- there's no numeric readout of where it is, only a visual/color cue
the instant a pin actually catches. A vertical tension "wrench" bar free-drifts via a damped random
walk whenever the player isn't actively dragging it back into its safe zone (drift rate scales with
`difficulty`); dragging it sets tension directly, letting go leaves it to wander. Click-and-hold (or
select with ←/→ and hold Space) on a pin raises it at a steady rate; release and it springs
back down at a fixed rate unless it's already caught. A pin only catches (turns green, height locks
to its exact shear value) if it reaches its shear line *while tension is in the safe zone* -- reach it
with bad tension and nothing happens (you have to notice and back off); push *past* the shear line at
all while unset is a strike (three strikes and the pick snaps -- `onFinish(false)`), regardless of
tension. A caught pin isn't permanent either: tension drifting out of the safe zone for more than
`SET_LOSS_GRACE_S` springs it back down, since nothing's holding the cylinder rotated anymore --
juggling multiple caught pins while still working the rest is the actual skill ceiling. All pins
caught simultaneously wins (`onFinish(true)`); a 26-second clock is also a loss condition, so a
stalled attempt can't sit open indefinitely. `catchTolerance` (how wide the "feel" window is) and the
drift rate both scale with the same `difficulty` float `panem_shared.jail.lockpick_difficulty`/
`stealing.burgle_difficulty` already compute server-side for the RNG-fallback path -- a longer jail
sentence, or a house instead of a cell door, reads as a narrower catch window and a twitchier wrench,
not a faster needle anymore.

Verified live end to end (this sandbox has no route to the original Unity/C# reference project either,
same as before, so verification is necessarily against this fresh implementation's own behavior, not
a side-by-side comparison): minted a real lockpick attempt against a seeded jailed character and drove
it with Playwright, confirming via a temporary debug hook (removed before commit) that (a) holding a
pin with tension centered correctly catches it exactly at its randomized, otherwise-invisible shear
value; (b) holding a pin with tension pinned outside the safe zone never catches it and correctly
racks up strikes on overshoot; (c) three strikes ends the attempt via the same `onFinish(false)` ->
`crime.js`'s `/activity/crime/{id}/result` POST -> `apply_lockpick_attempt`'s real failure path,
confirmed by the server's own "The lock holds. 2 attempt(s) left." response. `node --check` clean on
the rewritten file; no Python changed, so the existing 965-test suite and mypy baseline are both
unaffected by this one.

## A comprehensive visual character customizer on the Character tab

The Character tab's creation form (and, now, every existing character's card) got a full visual
customizer instead of just a free-text "appearance" textarea: skin tone, gender presentation, how
young or old the character looks, face shape and expression, hair style and color, facial hair,
build, height, clothing style and color, and jewelry (earrings, necklace, nose ring, glasses,
headband) -- all with a live-updating portrait preview, drawn as original canvas art (no external
assets), same "original art only" precedent this codebase already established for the /work and
/lockpick minigames. The free-text `appearance` field is untouched and still means exactly what it
always did (the player's own written description); the customizer is a separate, additive concern
that drives a *rendered portrait*, stored as structured trait data alongside it.

**Data model**: a new `panem_shared/appearance.py` holds the fixed option palette for every field
(`GENDER_PRESENTATIONS`, `AGE_LOOKS`, `FACE_SHAPES`, `EXPRESSIONS`, `HAIR_STYLES`, `FACIAL_HAIR`,
`BUILDS`, `CLOTHING_STYLES`, `JEWELRY_OPTIONS`, plus fixed hex swatch palettes for
`SKIN_TONES`/`HAIR_COLORS`/`EYE_COLORS`/`CLOTHING_COLORS` and a `140..210` `height_cm` range) and
`validate_appearance_traits(traits) -> dict`, which rejects anything outside that palette and always
returns every field populated (merging onto `DEFAULT_APPEARANCE_TRAITS` for whatever the caller left
out) -- the same "moved to `panem_shared` because `panem_api` needs it and can't import
`panem_bot`" reasoning as `jail.py`/`stealing.py`/`characters.py` this session. `Character` gets a new
nullable `appearance_traits: JSONB` column (migration `f3a9c6e2b7d4`); nullable rather than
backfilled, since API responses fill in the defaults for display (`character.appearance_traits or
appearance.DEFAULT_APPEARANCE_TRAITS`) rather than needing every existing row rewritten.
`panem_shared.characters.create_character` takes an optional `appearance_traits` dict (validated the
same way, defaulted for the Discord-side `/character create` flow which doesn't send one at all).

**API**: `GET /activity/dashboard/characters/appearance-options` is the customizer's single source of
truth for every palette -- the frontend never hardcodes its own copy of the option lists, it just
renders whatever this returns (no `discord_id` needed; this is static, non-sensitive option data,
same trust level as the existing `/districts` endpoint). `POST /activity/dashboard/characters`
accepts an optional `appearance_traits` object; `PATCH /activity/dashboard/characters/{id}` accepts
one too, so a player can restyle an existing character's look after creation, not just at the moment
they make it -- both routes validate through the same `panem_shared.appearance` function and refuse
(400) on an unrecognized value. `DashboardCharacterSummary`/`CharacterDetail` both now carry
`appearance_traits`, always fully populated, so the frontend never needs a second round-trip to render
a character's portrait from the list/identify responses it already has.

**Rendering**: `static/tabs/avatar_creator.js` exports a single pure function, `renderAvatar(canvas,
traits)`, that draws a stylized portrait from canvas primitives (arcs, quadratic curves, clip regions)
-- a torso colored by clothing style/color, a head shaped per `face_shape`, skin/hair/eye colors from
the trait swatches, eyebrows and a mouth curved per `expression`, optional facial hair, one of ten
hair styles (bald/buzz/short/bob/shoulder/long/braid/curly/mohawk/bun -- the longer styles draw a
"back" layer behind the head before the head shape itself, so hair correctly frames rather than
covers the face), and jewelry drawn last on top. It's a stateless redraw on every change, not an
animation loop -- a portrait doesn't need one. `static/tabs/character.js`'s new `appearanceEditor(...)`
component (dropdowns for choice fields via the existing `dropdown()` helper, a row of colored swatch
buttons for each color field, checkboxes for jewelry, a range slider for height) is shared by the
create form and by a "Customize appearance" toggle on every existing character's card, both calling
`renderAvatar` on every control change for an immediate preview, and both sending the same
`appearance_traits` shape to the API on submit/save.

**A real bug caught during live verification, not just a design note**: the first version of the
"short" hair style's front cap used a quadratic-curve bulge whose lowest point landed close enough to
the eyebrow line that, combined with a near-black default hair/eyebrow color, it rendered as a single
solid band across the upper face instead of hair-then-forehead-then-eyebrows. Caught by actually
looking at a live screenshot rather than trusting the code by inspection alone -- fixed by clipping
the shared "crown cap" to a rect well above the brow line for every style, and fixed a second,
related bug the same screenshot pass caught in the "bob" style (its single low dome extended down
*across* the eyes rather than only *beside* the face) by rebuilding it as the same tight crown cap
plus two side flaps that hang past the ears to the jaw. Re-rendered and re-screenshotted all ten hair
styles after each fix to confirm no other style regressed the same way.

Verified: `uv run ruff check`/`ruff format --check` clean, `mypy` baseline unchanged (148, pre-existing
`Bot`-vs-`PanemBot` typing gap, same as every prior milestone this session), the migration round-trips
(`alembic downgrade -1` / `upgrade head`, single head), the full test suite is green (984 tests -- new
`tests/unit/test_shared_appearance.py` for `validate_appearance_traits`'s option/range/type edge cases,
plus new `TestDashboardCharacters` cases in `test_api_app.py` covering create/list/patch with
`appearance_traits` and the new options endpoint), `node --check` clean on both changed/new JS files,
and a live Playwright pass against a real running server: loaded the dashboard in preview mode, opened
the Character tab, changed hair style/skin tone in the live customizer, submitted a real character
creation with those traits, and confirmed via a direct API read-back that the exact submitted values
(not just the defaults) persisted through creation and came back correctly on the next list load.

## The character customizer's portrait upgraded to a real-time 3D model

The 2D canvas portrait above was replaced with a live, orbit-able 3D humanoid model, built from
Three.js primitive geometries and shaded materials rather than flat canvas arcs -- same trait data,
same customizer UI, no server-side or data-model changes at all (every field this touches was already
covered by the previous milestone's `panem_shared.appearance` validation and `appearance_traits`
storage). This is a pure rendering-layer upgrade.

**Three.js is vendored, not CDN-loaded**: `static/vendor/three.module.min.js` (r160, MIT, see
`three.LICENSE.md` next to it) was fetched via `npm pack three` and copied in as a single ES module
file, the same way `/vendor/discord-embedded-app-sdk.js` was vendored earlier this session -- a real
Discord Activity iframe only ever fetches from this server's own origin, so a CDN dependency
(`unpkg.com`, `cdn.jsdelivr.net`) would be a hard runtime dependency this codebase has deliberately
avoided everywhere else.

**Rendering**: `static/tabs/avatar_creator.js` still exports `WIDTH`/`HEIGHT`, but the pure
`renderAvatar(canvas, traits)` function is gone -- a live 3D scene needs a render loop and owns real
GPU resources, so the new API is `mountAvatar(container, traits) -> { update(newTraits), dispose() }`.
The rig is built entirely from primitive geometries (spheres, capsules, cones, tori, boxes) with plain
`MeshStandardMaterial` colors, no textures or external model/image assets -- consistent with this
codebase's "original art only" rule, just in 3D instead of 2D. Every trait still maps to something
visible: `face_shape` picks a head geometry (a box for square, a sphere+cone for a pointed heart chin,
non-uniform sphere scaling for oval/round/long), `hair_style` builds one of ten distinct shapes from a
shared "crown cap" (a partial sphere, capped well above the brow so it never overlaps the eyes) plus
per-style additions (icosahedron clusters for curly, capsule flaps for bob, a box fin for mohawk, a
sphere bun, back-length capsules for shoulder/long, zigzag capsule segments for braid), `build` scales
torso/limb width and clothing color, `height_cm` scales the whole rig's Y axis, `expression` bends a
torus-arc or box mouth and tilts brow boxes, `age_look` adjusts head-to-body proportion and skin
roughness, and `jewelry` adds small tori/spheres (earrings, nose ring, headband, necklace, glasses)
positioned off the head/neck. Since trait combinations vary the rig's real height and width a lot (a
heavyset character with long hair vs. a slim one with none), the camera frames itself from the model's
actual `THREE.Box3` bounding box on every build rather than a guessed fixed distance -- a fixed
distance either clipped the extremes or left everyone else tiny in the middle of the frame.

**Interaction**: dragging the preview rotates the camera around the model (plain pointer-event math,
not a vendored `OrbitControls` module -- a few dozen lines of spherical-coordinate camera positioning
covers exactly what this needs); when idle for a couple of seconds it resumes a slow auto-rotate, so
the character reads as a real 3D object rather than a static image even before anyone touches it.

**Lifecycle management, the real cost of switching from a stateless draw to a live scene**: every
`mountAvatar` call owns a `requestAnimationFrame` loop and a WebGL context, and browsers cap how many
contexts a page can hold at once. `character.js` was reworked so every place that used to call
`renderAvatar(canvas, traits)` and move on now tracks the returned `{ el, dispose }`/`{ getTraits,
dispose }` handles and disposes them at the right time: `characterCard`'s preview and its lazily-built
"Customize appearance" editor, `createForm`'s editor, and `mount()`'s own `refresh()` (before rebuilding
the character list) and `unmount()` (called by `app.js` on tab switch, per its existing
`currentTabHandle.unmount()` convention). Confirmed via Playwright that switching away from the
Character tab and back mounts a fresh, working set of canvases rather than compounding leaked ones.

**A real robustness gap found via a Playwright stress test, not a hypothetical**: mounting ~26 avatars
at once in a single page (a deliberate stress test sweeping all hair styles/face shapes/facial
hair/builds side by side for visual QA, far beyond any real character list) reliably produced
uncaught `pageerror`s from inside Three.js's internals once enough WebGL contexts were live -- the
browser evicts/loses older contexts once a cap is hit, and `renderer.render()` throws once its context
is gone. `mountAvatar` now guards both ends of that: a `try/catch` around the initial
`WebGLRenderer` construction plus a `renderer.getContext()` null-check falls back to a plain "3D
preview unavailable" text node if a context can't be created at all, and a `webglcontextlost` listener
plus a `try/catch` around the per-frame `renderer.render()` call stops that instance's render loop
cleanly if its context is lost or evicted later, instead of throwing and (as observed before the fix)
taking down the rest of the page's script execution with it. A real character list never approaches
anywhere near the ~16-ish context count where this kicks in (even a generous `max_characters_per_user`
override plus every "Customize appearance" panel open at once tops out around 6-7), but a Discord
Activity runs inside an embedded webview sharing its context budget with whatever else Discord itself
draws, so this is real defensive coverage rather than handling only for the test's own sake.

**A real visual bug caught the same way**: the shared hair "crown cap" first used a sphere sweep angle
wide enough to wrap most of the way down the head, rendering as a solid dark helmet covering the eyes
on every style that used it (everything except bald/buzz/mohawk/bob) -- confirmed via an actual
screenshot, not just code inspection, the same live-verification bar this session has held to
throughout. Narrowing the sweep to stop just above brow height fixed it across every affected style at
once, re-verified with a fresh screenshot showing the face clearly visible under the hair.

Verified: `node --check` clean on both changed JS files; a live Playwright pass against a real running
server confirmed the 3D model renders (screenshot-verified, not just "a canvas exists"), drag-to-rotate
actually changes the rendered frame (screenshot hash comparison, since a WebGL canvas's backing buffer
isn't reliably readable out-of-band without `preserveDrawingBuffer`, which production code has no
other reason to set), a trait change rebuilds and re-renders the model, and switching tabs away and
back remounts cleanly with no leaked contexts or console errors beyond the browser's own benign
`favicon.ico` 404. No Python, database, or API changes were needed for this milestone -- `ruff`/`mypy`/
`pytest` all re-run clean and unchanged (984 tests) since nothing on that side was touched.

## The 3D avatar's body is now a downloaded, rigged human mesh, not primitive capsules

The previous milestone's 3D model built its whole body -- torso, arms, legs, hands, neck -- from
Three.js primitives (cylinders, capsules, spheres). Asked for something less pixely/blocky and to use
a real downloaded 3D human model where possible, the body is now a real, free, openly-licensed rigged
human mesh instead, with the procedural system from the previous milestone kept for everything a fixed
mesh can't vary on its own: face features, hair, facial hair, jewelry, and clothing color/style.

**Sourcing the model, given this environment's network restrictions**: general CDNs and asset sites
(`kenney.nl`, `quaternius.com`, `itch.io`, `cdn.jsdelivr.net`) are not reachable from here -- only
`registry.npmjs.org` (already used to vendor Three.js itself) and GitHub's own domains
(`github.com`, `raw.githubusercontent.com`, `codeload.github.com`) are. Searching within that
constraint, the best fit was `examples/models/gltf/Xbot.glb` from the official
[three.js repository](https://github.com/mrdoob/three.js) itself (fetched at its `r160` tag to match
the vendored core exactly) -- a real, fully rigged, human-proportioned character (Mixamo's default "X
Bot"), already used as example content across the majority of three.js's own official
skinned-character/animation demos for years. See `vendor/models/xbot.LICENSE.md` for the
attribution/licensing detail (CC-BY 4.0 via Mixamo/Adobe, bundled here the same way three.js's own repo
bundles it). Loading a glTF also needed two more addon modules three.js ships outside its core bundle
-- `GLTFLoader.js` and `SkeletonUtils.js` (plus `BufferGeometryUtils.js`, one of GLTFLoader's own
dependencies) -- vendored the same way from the same `r160` tag under `vendor/three/`, with their bare
`from 'three'` import specifiers rewritten to point at the already-vendored core module (they're not
installed via npm here, so that specifier would otherwise fail to resolve in a browser).

**Rendering**: `mountAvatar` now loads the model once per page load (cached, ~3MB, same-origin) and
`SkeletonUtils.clone()`s it per mounted avatar, since naively cloning a `SkinnedMesh` shares its
skeleton and would let one character's pose/retint bleed into every other one on screen; materials are
explicitly cloned too, since `SkeletonUtils.clone()` shares those by default. Per-trait customization
now works through the model's own skeleton rather than swapping geometry: `build` scales specific
limb/torso bones' x/z (not y, so bone-chain length is untouched) to fatten or slim the figure via its
own skinning; `face_shape` non-uniformly scales the head bone (wider/flatter for square, stretched for
long, etc.) to reshape the one head mesh per trait instead of needing five different heads; `height_cm`
still scales the whole rig's Y axis as before. The model's raw bind pose is a T-pose (arms out
horizontally, meant for retargeting animations onto) -- useless for a static portrait, so the bundled
`idle` animation clip's very first frame is applied once as a static pose (`AnimationMixer.update(0)`,
never advanced) rather than left in the bind pose or guessed by hand-tuning bone rotations from
scratch.

**Attaching the procedural pieces to a real skeleton**: hair, facial hair, eyes, mouth, and jewelry are
unchanged in how they're built (see the previous section) but now anchor to the body's actual
`mixamorigHead`/`mixamorigNeck` bone world positions instead of a procedurally-built head sphere's own
position -- `computeHeadAnchor()` reads those off the cloned skeleton after posing/scaling, so they
track correctly regardless of build or face-shape distortion (bone *scale* only affects the vertices
skinned to that bone, not the bone's own transform, so anchoring off bone position stays stable). The
initial version anchored the head radius off the `mixamorigHeadTop_End` *bone marker*, which turned out
to sit noticeably lower than the mesh's actual scalp -- caught from the live render, not code review,
because every face feature clustered onto the lower half of the head with a big bare forehead above
it. Fixed by measuring the mesh's own true top via `SkinnedMesh.computeBoundingBox()` (see below)
instead of trusting where the original rig's author placed that marker. Clothing still has nowhere to
live on the downloaded mesh (it's one continuous skin-toned surface, no separate garment geometry), so
the previous milestone's procedural torso shell still carries `clothing_style`/`clothing_color`,
now built from a revolved, tapered `LatheGeometry` profile (shoulders wide, waist pulled in, hem
flared) instead of a plain cylinder tube, positioned from the body's own hips/neck bone span
(`computeTorsoAnchor()`) instead of an assumed fixed height. The mesh does carry a second "joint
accent" skinned surface baked in by its original author (visible at wrists/knees/ankles); tinting that
differently from the main skin (an early attempt used the clothing color there) read as an odd
diaper-and-cuffs patchwork, so both surfaces just get the skin tone -- clothing lives entirely on the
shirt shell.

**A camera-framing bug specific to skinned meshes, also only caught by rendering it**: the existing
`Box3.setFromObject()` bounding-box framing from the previous milestone silently produced a tiny,
wrong box for the new body -- a `SkinnedMesh`'s `geometry.boundingBox` reflects its raw, un-posed
vertex buffer (skinning is applied on the GPU at render time, not to the CPU-side geometry), which
for this mesh looks nothing like its actual standing silhouette. Confirmed by cross-checking against
the mesh's own bone positions (a normal, unaffected-by-skinning ~1.8-unit-tall figure) versus the
naive box (under a meter in every dimension). Fixed with `computeCharacterBounds()`, which calls the
newer `SkinnedMesh.computeBoundingBox()` (evaluates the true posed shape per vertex) for the body and
falls back to plain `geometry.boundingBox` for the ordinary procedural meshes, unioning both into one
correct world-space box.

**Disposal, now that geometry is shared across every mounted avatar**: unlike the fully procedural
version, the downloaded mesh's geometry is the *same* `BufferGeometry` object referenced by every
cloned instance (cloning only duplicates the lightweight `Mesh`/`Skeleton` wrappers, not the ~3MB of
vertex data) -- disposing it when one character unmounts would corrupt every other currently-mounted
avatar sharing it. Body nodes are tagged `userData.sharedGeometry = true` during construction, and
`disposeObject()` skips geometry disposal for those (materials are still disposed, since those *are*
cloned per-instance) and additionally disposes each `SkinnedMesh`'s own cloned `Skeleton` (which can
hold a GPU bone texture for a skeleton this size).

Verified live via Playwright against a real running server, re-running the exact checks from the
previous milestone plus fresh screenshots at each fix: the model loads and renders (screenshot-
verified), drag-to-rotate and a trait change both still visibly alter the frame (hash-compared), tab
switch-away-and-back remounts cleanly, and the 26-avatar simultaneous-mount stress test still produces
zero `pageerror`s (the WebGL context-loss hardening from the previous milestone holds up under the new,
heavier per-instance clone cost). `node --check` clean on every changed/vendored file. No Python,
database, or API changes -- `ruff` and the full `pytest` suite (984 tests) re-run clean and unchanged.

## Job title moved into `/character create`'s main modal, and both fields are now editable via `/character edit`

Job title used to need its own step: `CharacterDetailsModal` (name/age/appearance/backstory/avatar --
its full 5-field budget) submitted, then a button ("Set Job Title") had to be clicked to open a
*second* modal just for that one field, because Discord rejects a modal sent directly in response to
another modal's own submission (see the `629f284` commit this docstring/comment referenced) -- the
button supplied the plain, non-modal interaction that second `send_modal` call needed. Asked to fold
job selection into the modal instead of behind a separate button, job title now lives directly in
`CharacterDetailsModal` as its 5th field, in place of the avatar URL field that used to occupy that
slot.

**Making room**: avatar URL was the one field with a dedicated post-creation escape hatch already --
`/character avatar` exists specifically to set or change it, works before or after approval (its
underlying query only filters by owner + name, not status; only its autocomplete favors approved
characters), and the modal's own inline comment already noted it as a "set later" fallback for anyone
uploading a file instead of a URL. Dropping it from the creation modal removes zero capability, just
moves *where* it's set.

**Flow now**: `CharacterDetailsModal` (name/age/appearance/backstory/**job title**) submits straight
into shift-phase selection (a `Select`-based `View`, unchanged -- a fixed list of choices was never
going to fit as a 6th modal field even after freeing a slot) -- one fewer interaction than before, and
one less place a modal-chaining rule could bite in the future. `JobTitleModal` and
`JobTitlePromptView` are gone; the now-unused `job_title_prompt` string went with them.

**`/character edit`**: previously reused `CharacterDetailsModal` for name/age/appearance/backstory/
avatar but never touched job_title/shift_phase at all -- the *only* way to change those post-creation
was staff running `/staff give job` on an already-approved character. Since `/character edit` shares
the same modal class as creation, job title came along for free once it moved into that modal; shift
phase needed its own follow-up step added, mirroring creation's shape exactly (`ShiftPhaseSelectView`
shown after the modal submits, now with its `SelectOption`s taking an optional `current_phase` so the
character's existing phase shows pre-selected instead of forcing a re-pick of something unchanged).
This is scoped the same way every other edit-command field already was: pending characters only,
resubmitted for staff approval like any other edit -- `/staff give job` remains the only way to change
an *approved* character's job, since that's an intentionally different, staff-gated permission model
this change didn't touch.

Verified: `ruff` clean, `mypy` error count unchanged (148, confirmed identical before/after via
`git stash`), full `pytest` suite unchanged (984 passed), and a direct Python import of
`panem_bot.modals`/`panem_bot.views`/`panem_bot.cogs.characters` confirms both removed classes
(`JobTitleModal`, `JobTitlePromptView`) are actually gone rather than just unreferenced.

## The visual customizer replaced entirely: a Picrew-style upload-your-own-art layer picker

Both prior character-portrait systems -- the fixed-palette procedural renderer (2D canvas, then a
3D Three.js scene) -- are gone. Asked for something closer to [Picrew](https://picrew.me): staff
upload their own artwork, mark each image with which "part" (category) it belongs to and where it
sits in the stack, and players just pick one option per category. There is no seed data or fixed
palette anymore -- the catalog starts completely empty, and stays that way until staff use the new
admin panel to add categories and upload images into them.

**Data model**: two new tables replace the old fixed enum lists. `LayerCategory` (name, `z_index` --
lower renders further back) is a "part" of the portrait (Hair, Base, Eyes, whatever staff decide to
call it); `LayerOption` (`category_id`, name, `image_path`) is one uploaded image within a category.
`Character.appearance_traits` (a `dict` of enum values) is renamed to `appearance_layers` (a `dict`
of `{category_id: option_id}`) via a straight column rename -- there's no way to map old fixed-trait
data onto real uploaded artwork that didn't exist yet, so every character just starts uncustomized
again, the same "never customized" null convention the old column already used for rows saved
before it existed.

**Backend** (`panem_shared/layers.py`, Discord-independent like every other service module here):
`create_category`/`update_category`/`delete_category`, and `create_option`/`delete_option` which
save/unlink the actual image file (`static/uploads/layers/<uuid>.<ext>`, one of `png`/`webp`/`gif`
only -- no SVG, since that's a script-injection surface if ever served with the wrong content-type,
and no JPEG, since it has no alpha channel to make a transparent layer sprite). `validate_layer_
selection` checks a player's submitted `{category_id: option_id}` map actually references real,
matching rows before it's saved -- a stale selection (staff deleted the option *after* a character
picked it) is left alone rather than treated as invalid; that's handled by the frontend at render
time (skip a selection with no matching option), not flagged as an error at save time.

**API**: `GET /activity/dashboard/layers` (public, mirrors the old `appearance-options` endpoint's
trust level -- static non-sensitive catalog data) replaces that endpoint entirely. Character create/
update take `appearance_layers` instead of `appearance_traits`. Staff-only mutations live in `build_
staff_router` alongside `/staff/jail`: `POST/PATCH/DELETE .../staff/layers/categories[/{id}]` and
`POST .../staff/layers/categories/{id}/options` (multipart upload) / `.../staff/layers/options/{id}/
delete`. `create_app` gained a `static_dir` parameter (defaults to the real bundled `static/`) so
tests can point uploads at a `tmp_path` instead of writing real files into the checked-out tree.

**Frontend rendering**: `avatar_creator.js` went from a live WebGL scene to plain DOM -- one
absolutely-positioned `<img>` per category stacked by `z_index` inside a fixed-aspect-ratio
container (`mountAvatar(container, categories, selection)`). No rendering logic at all; the actual
"art" is 100% staff-uploaded. `character.js`'s customizer became a per-category `</>` picker (a
`None` option first, then every uploaded option in that category) instead of dropdowns/color
swatches -- a category with zero options uploaded yet is simply left out of the picker entirely, and
if *no* category has any options yet, the whole customizer shows "No customization options uploaded
yet -- check back soon" instead of an empty picker list.

**Staff admin panel** (`staff.js`, alongside the existing jail tool): "New category" (name + stack
order) at the bottom, and one panel per existing category above it with rename/reorder, a "Delete
category" button, a list of its images with delete buttons, and an upload form (name + file picker).
No `Content-Type` header is set on the upload request -- the browser fills in the multipart boundary
itself from the `FormData` body, and overriding it breaks the upload.

**A real async-ORM bug caught by the new tests, not just theoretical**: the "create category"
endpoint initially read the freshly-created row's `.options` relationship directly to build its
response, which raised `MissingGreenlet` on every single call -- a brand-new row's collection isn't
automatically "loaded empty" once it's been flushed to the database; reading it unrefreshed tries to
lazy-load, which async SQLAlchemy can't do transparently. Fixed by explicitly `session.refresh
(category, attribute_names=["options"])` before building the response, the same pattern the
"update category" endpoint already needed for the same reason. Caught immediately by the new test
suite (an actual `httpx` request through the real app, not a mock) rather than shipping broken.

**Deployment**: staff-uploaded images are real files written at runtime, not source or seed content
-- `deploy/docker-compose.yml`'s `api` service gained a `panem_layer_uploads` named volume mounted
at the uploads path, the same durability-across-rebuilds treatment `panem_pg_data` already gets for
Postgres. `python-multipart` was added as a `panem_api` dependency (FastAPI's `Form`/`File` request
parameters hard-require it, and refuse to even start the app without it once any route uses them).

Verified: `ruff`/`mypy` clean (mypy's 148 pre-existing errors unchanged), migration applies and
round-trips cleanly on a fresh database with a single alembic head, and the full test suite (983
tests -- replacing the old fixed-palette appearance tests 1:1 rather than just deleting coverage) 
passes, including 17 new tests directly exercising the layer catalog, character create/update
against it, and every staff category/option CRUD and upload/delete endpoint (with a `tmp_path`-
backed `static_dir` so test uploads never touch the real repo). Live Playwright verification against
a real running server confirmed both states end-to-end: a totally empty catalog renders the "no
options yet" message with no console errors, and a seeded catalog (screenshot-verified) shows a
working `</>` picker per category, actually composites the selected images in the preview, and
correctly wraps back to "None" after cycling through every option. That same live pass caught a
second real bug -- the preview container had no `max-width`, so `aspect-ratio: 3/4` blew it up to
fill the entire panel's width (nearly 1000px tall) -- fixed by capping it at 320px, the same way the
old canvas-based version was implicitly bounded by its fixed render resolution.

## Rebuilt the lockpicking minigame again: a Stardew Valley-style fishing bar, not a pin-tumbler puzzle

User feedback on the pin-tumbler version (`## Rebuilt the lockpicking minigame as an actual
pin-tumbler puzzle` above): "make it so the user has to use the w and s keys to keep the bar in the
green and make the green part of the bar move around, similarly to how fishing works in stardew
valley." A different mechanic entirely -- one drifting target zone and one player-steered indicator,
not several pins juggled against a wandering tension wrench.

Rewrote `games/lockpick.js` again (same IP-safety posture as every version before it: "the general
shape of a moving target + a player-steered bar + a fill/drain meter" describes a mechanic, not
anyone's code or assets, so this is a fresh canvas implementation, not a port of Stardew Valley's).
`label`/`instructions()`/`mount(boardEl, {onFinish, setStatus, difficulty})`'s contract is unchanged
again, so only `games/lockpick.js` plus `crime.js`'s `ASSET_VERSION` (3 -> 4) and `crime.html`'s
matching `/crime.js?v=` needed to change -- `crime.css`'s existing `.board-lockpick`/`.lockpick-meta`
rules were generic enough to reuse as-is.

**The mechanic**: a single vertical track holds a drifting green "pressure zone" (a damped random
walk, same shape the old tension wrench's drift had) and a white/red "pick" line the player steers
with **W** (push up) / **S** (ease down) -- momentum-based (holding a direction accelerates it, a
constant drag bleeds the velocity back off on release, so it's a "juggle," not a "snap to a spot").
A catch meter next to the track fills while the pick overlaps the zone and drains while it doesn't;
reaching 100% wins (`onFinish(true)`), draining to 0% loses (`onFinish(false)`) -- a generous overall
time limit is still a loss condition too, but the meter is the real clock, the same way it is in the
reference game. `zoneHeight`/`driftRate`/`fillRate`/`drainRate` all scale with the same `difficulty`
float `panem_shared.jail.lockpick_difficulty`/`stealing.burgle_difficulty` already compute -- a
longer sentence or a house door reads as a smaller, twitchier zone and a slower-filling meter, not a
faster needle.

**Two real bugs caught by live testing, not just theoretical**: added a temporary
`window.__lockpickDebug()` hook (removed before commit, same pattern as the pin-tumbler version's own
verification) to read the running game's internal state from Playwright and confirmed via a real
minted attempt against a seeded jailed character that (a) **the pick and zone both defaulted to the
exact same starting position (0.5)** -- at max difficulty, an idle player could win from doing
absolutely nothing for under a second, before the drift ever got a chance to test anything; fixed by
forcing the zone to start at least 1.5 zone-heights away from the pick's fixed center, so every
attempt opens with an actual find-it moment. (b) **that fix alone flipped the exploit into its
mirror image**: the original fill/drain rates could swing the meter from full to empty in well under
a second, meaning the *outcome* was still effectively decided by the random starting gap alone,
before a human had time to register the mismatch and react -- not a skill test, a coin flip. Fixed by
slowing both rates roughly 3x (a full empty-to-full swing now takes several seconds even at max
difficulty), then re-verified: an idling player still reliably loses (~2s), but a scripted controller
that reads the live zone position each frame and steers toward it wins in ~11 seconds at
difficulty 0.95, comfortably inside the ~28s time budget -- confirming the win condition is actually
reachable through tracking skill, not luck in either direction. `node --check` clean on all three
changed files; no Python changed, so the existing 985-test suite and mypy baseline are both
unaffected by this one.

### Fix: W/S did nothing once embedded in the dashboard's iframe -- only clicking worked

User feedback right after the above shipped: "W and S don't move the bar, i have to left click above
or below." The Character/Jail tab's own testing (a direct, top-level load of `crime.html`) never hit
this because a top-level page already owns keyboard focus by default -- the actual player-facing path
(the dashboard's Jail/Crime tabs embed `crime.html` in a plain `<iframe>`, per `jail.js`/`crime.js`)
does not, and nothing was ever telling that iframe to take it.

Reproduced with a small local host page that embeds `crime.html` in an `<iframe>` the same way
`jail.js` does (not committed, deleted after verification) and confirmed via `document.hasFocus()`
inside the frame: clicking the canvas left the iframe's own document unfocused, so the `keydown`
listener on `window` (registered inside that iframe) never actually fired -- only the pointer-based
fallback (a plain DOM event on the canvas element, needing no frame focus at all) responded, which is
exactly "only clicking works." The root cause was this file's own `onPointerDown`: it already called
`event.preventDefault()` (needed to stop touch-scroll/selection on tap), which as a side effect also
suppresses the *default* browser behavior of a click focusing the frame it landed in.

Fixed with an explicit `canvas.focus()` inside `onPointerDown` (a genuine user gesture, so it's
allowed to grab focus even across frames) plus `tabindex="0"` on the canvas so it's actually
focusable at all; also tries `canvas.focus()` once on mount as a best-effort for contexts that allow
it without a prior gesture (harmless no-op where they don't -- the click-driven focus() covers it
either way). `crime.css` gained one rule suppressing the resulting focus ring, since this focus is
functional, not a tab-navigation affordance. Re-verified against the same iframe-embedding repro:
`document.hasFocus()` now reads `true` after a click, and holding W visibly moves the pick. `crime.js`'s
`ASSET_VERSION` (4 -> 5) and `crime.html`'s matching `/crime.js?v=`/`/crime.css?v=` bumped again.

### Fix: the lockpick minigame's green zone barely moved

User feedback: "The green area doesn't really move at all, so once you move the bar into the green
the game is basically over. The green section needs to move back and forth more." The previous
rewrite's pressure zone moved by picking a new random waypoint and drifting toward it on arrival --
but that "random waypoint" pattern has a well-documented bias (the mobility-modeling literature calls
it the border effect): repeatedly retargeting toward independent random points concentrates a
wandering object's dwell time near the *middle* of its range, not spread evenly across it. Since the
pick sits at the exact center of the track by default, the zone spent a disproportionate amount of
time hovering right where the player already was, which is exactly what looked like "barely moves."

Replaced the movement model with a constant-speed bounce (like a ball reflecting off both ends of the
track), plus an occasional small chance of an early random reversal so a sharp player can't just
count out a fixed period. Verified via a headless-browser harness sampling the zone's position every
250ms over several seconds: the bounce model covers roughly 65-75% of the track's range with clear,
regular direction reversals, versus the old model's small, jittery excursions around wherever it
happened to be drifting.

Fixing the *movement* surfaced two further bugs, both caught only by simulating real play rather than
just eyeballing the animation:

**(a) Idle could win outright.** A bounce model has a much more predictable, uniform dwell-time
distribution than the old random-waypoint one -- which is good for fairness, except the existing
fill/drain rates were still tuned against the old model's much lower passive contact rate. Measured
directly: a pick that never moves at all still ends up "on target" a large, predictable fraction of
the time purely from the zone sweeping back across dead center (~40% at easy difficulty, ~20% at max)
-- and the old rates let that alone win the game outright in some runs. Fixed by deriving the
fill/drain rates directly from that measured passive-contact fraction (`zoneHeight / (1 - zoneHeight)`,
confirmed to track the measured numbers closely) with a fixed safety margin, instead of tuning the
rates by feel -- so the win condition always needs a real margin above what doing nothing produces,
at every difficulty.

**(b) The pick had a physics bug that made it literally unmovable.** While tuning the pick's stopping
distance (needed so releasing a key "at" the zone doesn't overshoot straight through it), a constant
`ACCEL` and constant `DRAG` briefly ended up set to the exact same value. Each frame, the pick's
velocity first gets `accel * dt` added, then has a drag magnitude capped at `DRAG * dt` subtracted in
the opposite direction -- with `ACCEL === DRAG`, that cap exactly equals what was just added, so drag
cancelled the acceleration in full on every single frame. The pick's velocity was permanently pinned
at zero no matter how long W or S was held. This went undetected through an entire round of
scripted-controller playtesting because every "held a direction" test silently measured the exact
same thing as doing nothing -- the controller numbers looked bad, but the actual bug (no response to
input at all) never showed up as an obvious crash or console error. Caught by directly inspecting the
pick's velocity over time via a temporary debug hook and noticing it never left `0.000` even while a
key was held; fixed by giving `ACCEL` real headroom over `DRAG` (`5.6` vs `2.6`) so holding a key
actually accelerates the pick, confirmed via the same debug hook showing velocity change immediately.

Re-verified the whole loop end to end with a headless-browser harness that dispatches real keyboard
events in-page (no remote-control round-trip latency, so timing is close to what an actual keypress
looks like) at a human-scale ~140ms reaction interval, run six times per difficulty: idling won 0/6
at every difficulty (~40%/~27%/~18% on-target purely from passive contact, all below breakeven), while
a controller that steers toward the zone based on where the pick's current velocity would carry it if
released right now (accounting for the same drag the real physics use, so it predicts and eases off
rather than reacting to overshoot after the fact) won 6/6 at the easiest difficulty, 5/6 at medium,
and 2/6 at the hardest -- a real, reachable difficulty curve rather than either a free win or a
required-frame-perfect wall. `node --check` clean on both changed JS files; no Python touched, so
ruff and the existing test/mypy baselines are unaffected. `crime.js`'s `ASSET_VERSION` (5 -> 6) and
`crime.html`'s matching `/crime.js?v=` bumped; `crime.css` untouched this pass, so its own `?v=`
stayed put.

### Fixed: `POST /activity/dashboard/crime/{id}/burgle/start` 500ing for a real player

User report: a specific attempt at `/burgle/start` 500'd instead of minting an attempt. Reproduced
locally by seeding an owner who owns *two* houses in the burglar's district and hitting the endpoint --
`start_burgle`'s house lookup used `.scalar_one_or_none()`, which raises `MultipleResultsFound` (an
unhandled exception, not a caught `ServiceError`) the moment a query matches more than one row.
Nothing in the housing system stops a character from buying more than one house in the same district
(no such uniqueness check exists in `panem_bot.services.housing`), so this was reachable by any
player who happened to own two houses in one district being targeted for a burglary -- confirmed via
the exact traceback (`dashboard_routes.py:814`, inside `start_burgle`) against a local reproduction
before writing the fix.

Fixed by switching that lookup to `.scalars().first()` with a stable `order_by(Property.id)`, so it
deterministically picks one of the owner's houses instead of erroring when there's more than one.
(The owner-name lookup two lines above stays `.scalar_one_or_none()` -- `Character.name` carries a
real case-insensitive unique constraint at the DB level, confirmed directly, so that one genuinely
can't return more than one row.) Added a regression test seeding two houses for the same owner in the
same district and asserting `/burgle/start` still returns 200. Full suite (`uv run pytest`, 986
passed), `ruff check`, and the mypy baseline (148, unchanged) all clean; no JS touched, so no
`ASSET_VERSION` bump needed here.

### Lockpick redesigned again: one-button "hover," not a two-key up/down bar

User feedback on the fishing-bar redesign: the white bar should always be sinking, rising only while
the player holds W or a click -- not a two-key (W up, S down) momentum control. Rewrote the physics
around a single control: `GRAVITY` always pulls the pick down every frame, and holding the one key
adds `THRUST` on top of it (net rising accel is `THRUST - GRAVITY`; net falling accel while idle is
just `-GRAVITY`). `holdingDown` and the `ACCEL`/`DRAG` pair are gone entirely -- `onKeyDown`/`onKeyUp`
only listen for `KeyW`/`ArrowUp` now, and a pointer-down anywhere on the canvas (no more top/bottom
split) sets the same one `holdingUp` flag.

This flipped what "idle" means for the passive-contact analysis the previous pass relied on: under
the two-key model an untouched pick sat frozen at the track's center (0.5), the same spot the old
`zoneHeight / (1 - zoneHeight)` breakeven formula assumed sustained interior dwell time for. Under
gravity, an idle pick instead falls straight to the floor (`pickPos=0`) and sits pinned there -- and
because the bounce zone's own center is clamped to `[zoneHeight/2, 1-zoneHeight/2]`, its covered
interval only ever brushes position 0 for a single instant per bounce, not the sustained dwell time an
interior point gets. Measured directly: idle's on-target fraction dropped to ~0.02-0.03 at every
difficulty (down from ~0.18-0.43 under the old model) -- idle winning is now a non-issue, so
`breakeven` only needs a comfortable margin above that ~0.03, not defense against a free win.

Two tuning passes were needed before this held up under measurement, not guesswork:

1. **First-pass physics failed balance testing outright.** With `GRAVITY=1.6, THRUST=4.0,
   MAX_SPEED=0.8` and the *old* passive-contact-derived breakeven formula still in place, a scripted
   predictive controller with human-scale (140ms) reaction time only reached ~0.25 on-target at the
   easiest difficulty against a ~0.51 breakeven the stale formula demanded -- unwinnable. Root cause
   was two compounding things: `MAX_SPEED=0.8` under `GRAVITY=1.6` gives a stopping distance
   (`v^2/(2*GRAVITY) = 0.2`) bigger than the zone height (0.16-0.3), so releasing "at" the target
   overshot straight through it; and the old breakeven formula was demanding a needlessly high bar for
   a risk (idle winning) the new physics had already made structurally near-zero. Fixed by bringing
   physics down to `GRAVITY=1.2, THRUST=2.75, MAX_SPEED=0.47` (stopping distance ~0.09, well under the
   smallest zone height) and replacing breakeven with a flat curve tied to the measured idle rate
   instead of a formula derived for the old model's dynamics.
2. **Second pass: the hardest difficulty was still unwinnable.** Re-running the full balance suite
   with that fix, idle was safely losing everywhere (~0.02-0.03 on-target, all difficulties), but
   `TRACKED(140ms)` at the hardest difficulty (d=0.95) lost 0/6 with only ~0.27-0.43 on-target against
   a ~0.4425 breakeven -- even a well-tuned scripted controller with a *better* 60ms reaction time
   only just scraped 0.43. The zone's top speed at max difficulty (75% of the pick's own `MAX_SPEED`)
   combined with the breakeven curve's steep climb (`0.3 + clamped*0.15`) made max difficulty hard
   regardless of skill, not just hard to master. Since idle's ~0.02-0.03 on-target gives huge headroom
   to work with, eased the zone's top-speed scaling (`0.65-0.75x` -> `0.6-0.67x` of `MAX_SPEED`) and
   flattened breakeven to `0.25 + clamped*0.08` (0.25-0.326, down from 0.30-0.4425).

Re-verified the whole loop with the same headless-browser harness (real dispatched `keydown`/`keyup`
events, ~140ms reaction interval, six trials per case): idle 0/6 wins at every difficulty (~0.02
on-target, safely below breakeven's 0.25 floor everywhere), tracked play 6/6 at the two easier
difficulties (~0.55-0.58 on-target) and 3/6 at the hardest (~0.39 on-target, comfortably above its
0.326 breakeven) -- a real, reachable difficulty curve at every setting, not a wall at the top end.
`node --check` clean; `crime.js`'s `ASSET_VERSION` (7 -> 8) and `crime.html`'s matching `/crime.js?v=`
bumped to cover this pass (the "add instructions" feature below had already bumped it to 7).

### Added: visible "how to play" instructions on every minigame

User report: none of the minigames (the six `/work` games, plus lockpick/pickpocket) showed the player
how to play before they were expected to. Every game module already exported an `instructions()`
function (used nowhere) with real text describing controls and win condition -- this was a
presentation gap, not a missing-content one. `work.js`/`crime.js` were the actual problem: their only
status area (`#status`) gets overwritten with win/lose text the instant a round ends, so folding
instructions into that same line meant they vanished right when a losing round most needed the
reminder.

Fixed by giving both pages a dedicated `#instructions` element (`.instructions` CSS class, muted
italic text) that's set once from `game.instructions()` at mount and left untouched for the rest of
the round, independent of `setStatus()`. Verified with Playwright (`page.route()`-mocked
`/activity/crime/*` and `/activity/work/*` responses) that `#status` and `#instructions` render
distinct, correct text on both pages, and that every one of the 8 game modules' `instructions()`
returns real text via a direct dynamic-import check. `work.js`'s `ASSET_VERSION` (8 -> 9) and
`work.html`'s matching `?v=`, plus `work.css`'s own `?v=` (2 -> 3) for the new `.instructions` rule,
bumped; same for `crime.js` (6 -> 7 at the time, then -> 8 for the physics pass above) and
`crime.html`/`crime.css`.

### Fixed: staff "Jail a character" showing a tick count that didn't match what was typed

User report (with a screenshot): typed `Ticks: 25`, got "Magnus Bane jailed for 43 ticks." Not a bug
-- `commit_to_jail` (`panem_shared/jail.py`) has always scaled the sentence by the character's priors
(`jail_count * JAIL_PRIOR_TICKS_PER_COUNT`, 6 ticks per prior jailing), so a repeat offender's actual
sentence is always longer than the number typed in. Confirmed the exact math against a second report
from the same user (`Ticks: 2` -> "jailed for 26 ticks", i.e. `2 + 4*6`, consistent with `jail_count`
having incremented from 3 to 4 between the two reports) -- the feature was working as designed, the
dashboard just never showed the breakdown, so it read as broken.

Fixed by surfacing the breakdown instead of hiding it: `StaffJailResponse` gained `base_ticks` and
`prior_bonus_ticks` fields (computed from `jail_count` *before* `commit_to_jail` mutates it), the
Staff tab's result line now reads "X ticks (Y entered + Z for repeat priors)" when there's a nonzero
bonus, and a permanent note under the Ticks field explains the priors rule up front rather than only
after the fact. Added a regression test seeding a character with `jail_count=3` and asserting the
response's `base_ticks`/`prior_bonus_ticks`/`applied_ticks` match the reported numbers exactly (`25`,
`18`, `43`). `app.js`'s `ASSET_VERSION` (22 -> 23, which also covers the market-tab change below) and
`index.html`'s matching `?v=` bumped.

### Market tab: per-row Buy/Sell + quantity, no more typing a good id

User report: buying/selling required typing a good's id into a text field at the bottom of the
Market tab instead of acting directly on the row you're looking at. Replaced the single bottom
"good id + qty + Buy/Sell" form with a qty input and an action button on every row: the Prices table
gets a qty input + Buy button per good, and the Inventory list (previously plain text) is now its own
table with a qty input (capped at `max={owned qty}`) + Sell button per owned good. Neither table ever
needs a typed id -- the row's own `good_id` travels straight into the request body from a closure over
that row's data. Verified with a Playwright harness (mocked `/activity/dashboard/market/*` responses)
that clicking Buy/Sell on a specific row sends the correct `good_id`/`qty` pair and renders the
resulting win/caught message. `dashboard.css` gained a `.qty-input`/table-scoped `.btn` sizing rule
(bumped `?v=` 9 -> 10); `app.js`'s `ASSET_VERSION` bump above covers `tabs/market.js` since `app.js`
imports every tab module through that one shared version string.

### `/poach` gets a once-per-day-phase cooldown, and a real minigame: archery target practice

User asks: "add a cooldown to poaching" and "add a target practice minigame for poaching that
requires people to aim a bow and arrow and hit a target 3/5 times they shoot within 30 seconds."

**The cooldown.** `/poach` had no cooldown at all -- unlike `/steal`/`/burgle`, which already gate
on `Character.last_steal_tick` compared via `tick // TICKS_PER_PHASE` (once per in-world day-phase),
poaching could be repeated every tick, making it a strictly better food source than the market
allocation it's meant to only supplement. Added `Character.last_poach_tick` (migration
`b4d7f1a8c3e9`, same nullable `Integer` shape as `last_steal_tick`) and the identical boundary check
in `panem_shared.poaching.check_can_poach`, now taking a `current_tick` parameter; refusal reads
`poach_on_cooldown`. `resolve_poach`/`roll_and_apply_poach` set the cooldown up front, same ordering
`resolve_steal` uses (gate, then mark the attempt spent, then resolve) so a caught attempt still
burns the cooldown.

**The minigame.** Previously `/poach` was a single server-side roll (`POACH_DETECTION_PROB` for
getting caught, otherwise an automatic yield) -- no player skill involved at all, unlike every other
crime (`/lockpick`, `/steal`, `/burgle`) which launches an Activity minigame. Gave it one: a new
`games/archery.js` module, aim with the mouse at a moving bullseye target and click to loose an
arrow -- 5 arrows, 30 real-time seconds, land at least 3 hits to bring the hunt home (exactly the
spec: "hit a target 3/5 times they shoot within 30 seconds"). The target's radius and drift speed
scale with a cosmetic `difficulty` the same way `games/pickpocket.js`'s pocket-zone width/needle
speed do; a canvas HUD row (`Arrows:`/`Hits:`/`Time:`) tracks all three counters live. The game ends
the instant the 5th arrow lands or the clock hits zero, calling `onFinish(hits >= 3)` exactly once --
same single-result contract every other minigame module here follows.

Wiring this in meant `/poach` joining the same attempt-launch shape `/steal`/`/burgle` already use,
since it previously never went through an Activity at all:
- `panem_shared/poaching.py`: `resolve_poach` (the old one-shot roll) split into `check_can_poach`
  (gate, now cooldown-aware), `apply_poach_outcome` (takes the minigame's `success` bool, still rolls
  `POACH_DETECTION_PROB` for getting caught -- a peacekeeper can notice a hunt regardless of whether
  the shots landed, same as before), `roll_and_apply_poach` (the RNG-fallback stand-in for "no
  Activity configured" or the Skip button, rolling a single `POACH_ARCHERY_BASE_SUCCESS` chance
  instead of simulating five shots), and `resolve_poach` itself kept as the top-level gate+cooldown+
  RNG-fallback wrapper -- exactly `panem_shared.stealing`'s own `check_can_steal`/`apply_steal_
  outcome`/`roll_and_apply_steal`/`resolve_steal` split. New `poach_difficulty()` mirrors `burgle_
  difficulty()`'s flat shape (`1 - POACH_ARCHERY_BASE_SUCCESS`).
- `panem_bot/cogs/poaching.py`: `/poach` now mints a Redis crime attempt (`kind: "poach"`, storing
  `good_id` alongside `district_id`/`current_tick` since the good is resolved once up front) and
  shows a Play/Skip launch message via the same `activity_launch` plumbing `/steal`/`/burgle` use,
  falling back to an instant RNG-resolved reply when no `ACTIVITY_PUBLIC_URL` is configured.
- `panem_api/app.py`: `/activity/crime/{id}` and `.../result` gained a `poach` branch alongside
  `lockpick`/`steal`/`burgle`; `CrimeResultResponse` gained `good_name`/`qty`/`fine` fields for it.
- `panem_api/dashboard_routes.py`: the old instant-resolve `POST .../poach` replaced with `POST
  .../poach/start` (mints the same Redis attempt shape), matching `.../steal/start`/`.../burgle/
  start`; `CrimeStartResponse.target_name` made optional since poach has no single target.
- `static/crime.js`: imports `games/archery.js` alongside lockpick/pickpocket, dispatches to it for
  `kind: "poach"`, and `describeResult()` gained a poach branch (caught/success/miss text). `static/
  tabs/crime.js`'s Poach button now mints an attempt and mounts the same `crime.html` iframe steal/
  burgle already use, instead of resolving instantly inline.
- New CSS (`crime.css`): `.board-archery`/`.archery-meta`, matching the existing `.board-lockpick`/
  `.board-pickpocket` pattern.

Verified with a Playwright harness (a temporary standalone page importing `games/archery.js`
directly, deleted before commit): read the game's own rendered canvas pixels back to find the live
target's on-screen position (scanning for its `#7cd992` bullseye-core color) and clicked exactly
there for all 5 shots -- 5/5 hits, `onFinish(true)`, no console errors; a second run firing all 5
shots at a fixed corner away from the target's start position produced 0/5 hits and `onFinish(false)`;
confirmed the arrow counter reaches `0` and the game ends the instant the 5th shot lands (doesn't
hang waiting for more input), and that the on-screen timer counts down in real time rather than
freezing. `crime.js`'s `ASSET_VERSION` (8 -> 9) and `crime.html`'s matching `?v=` (crime.js and
crime.css, 4 -> 5) bumped; `app.js`'s `ASSET_VERSION` (23 -> 24, covering `tabs/crime.js`) and
`index.html`'s matching `?v=` bumped too. New migration `b4d7f1a8c3e9` round-tripped up/down/up
clean; full test suite (`test_poaching_service.py` rewritten for the new split, `test_api_app.py`
gained poach-cooldown/start/status/result coverage) passes at 997 (up from 987).

### Jailed characters: locked out of stealing/burgling/poaching, and confined to a real jail cell thread

User asks: "Disallow jailed players from stealing, burgling, or poaching. They shouldn't be able to
travel either. ... make a forum thread on setup in each forum for the jail and they are only allowed
to RP within the jail thread for their district, or the district in which they were jailed, while
jailed. Other players can also 'visit' the district jail and talk to inmates via simply traveling to
that forum channel without being jailed."

Travel was already fully blocked for a jailed character (`panem_shared.travel.check_can_travel`/
`check_can_travel_district` already call `panem_shared.jail.check_not_jailed` -- confirmed by
reading the code rather than re-adding a check that was already there). `/steal`/`/burgle`/`/poach`
had no such gate at all, so a jailed character could still pickpocket, break into a house, or go
hunting from inside their cell. Added the same `check_not_jailed` call `check_can_travel` already
uses to `panem_shared.stealing.check_can_steal`/`check_can_burgle` and `panem_shared.poaching.check_
can_poach`, each with its own reason key (`steal_jailed`, `burgle_jailed`, `poach_jailed`) -- these
flow straight through the existing generic `ServiceError` -> `t(reason_key)` (bot) / HTTP 400 detail
(dashboard) handling every other refusal already uses, so no cog/route changes were needed beyond
the one `check_not_jailed` line in each service function.

**The jail cell as a real, travelable `Location`.** Rather than build a parallel "confined to a
special channel" system, jail became an ordinary `kind: jail` `Location` (new `LocationKind.JAIL`)
authored once per district (`data/districts/*.yaml`, all 13 -- one `- id: jail` entry each, e.g.
"District Twelve Jail", "The Capitol Jail"). This means `scripts/setup_guild.py` needed *zero* code
changes: its existing `ensure_ambient_posts`/`_location_tags` already iterate every `district.
locations` entry to give each one a forum tag and a pinned ambient thread inside that district's
roleplay forum -- the jail location gets exactly the same treatment as the Square or the Hob,
automatically, the moment content declares it. Same reasoning covers the dashboard's Travel tab
(`dashboard_routes.py`'s `district.locations` iteration) and the map: nothing needed touching there
either. A `Location.kind == "jail"` isn't `restricted` (default `false`), so `has_location_access`
already lets anyone travel there -- this is exactly how "other players can visit ... via simply
traveling to that forum channel" falls out of the existing travel/RP system for free: a free
character who travels to the jail location gets `location_id` set to it like any other location, and
`can_rp_at_location` matches it like any other location. `District._check_locations` now also
rejects more than one `kind: jail` location per district (a real authoring-mistake guard), but
doesn't *require* one -- making it a third hard-required kind (alongside station/public) would have
forced every hand-built `District(...)` test fixture across ~18 files (many written before jail
existed) to grow one just to keep constructing; `panem_shared.jail.find_jail_location` and its one
caller already treat "this district has no jail location" as a normal, handled `None` case rather
than an invariant violation, so the softer check costs nothing in practice while still catching a
real content bug (duplicate jail locations, the same way duplicate location names are already
rejected).

**The actual RP restriction, for the jailed character themselves.** `panem_bot.services.proxy.check_
can_proxy` already refused a jailed character outright (`proxy_character_jailed`) in *every* thread --
stricter than what was asked. Replaced the blanket refusal with a carve-out: a jailed character may
still post, but only in the one thread that models their cell -- the jail location's own scene, and
only in their home district (`Character.district_id`) or the district they were actually jailed in
(`Character.current_district_id`, which stays frozen at whichever district they were in the moment
they were jailed, since travel is refused outright the whole time they're locked up -- no new column
needed to track "where they were jailed," the existing field already can't move). Every other
thread -- including a different location's scene in one of those same two districts -- is still
refused with the same `proxy_character_jailed` reason as before. New `panem_shared.jail.find_jail_
location(district) -> Location | None` looks up a district's jail location by kind; `panem_bot.
services.proxy._jailed_district_allowed` checks the home-or-jailed-in district match.

Verified with new unit coverage: `test_content_loader.py` (every real district has exactly one jail
location), `test_shared_jail.py` (`find_jail_location` found/`None` cases), `test_stealing_service.py`
/`test_poaching_service.py` (jailed refusal + reason key for all three actions, still allowed once the
sentence expires), and `test_proxy_service.py` (five new cases: allowed in the home district's cell,
allowed in the arrest district's cell, still refused elsewhere in an otherwise-allowed district,
refused in an unrelated district's cell, refused when the district has no jail location at all). Full
suite passes at 1010 (up from 997); `uv run python scripts/lemonade_omni.py build` regenerated the two
committed dialogue-context collection JSONs (`data/lemonade/collections/*.json`), which embed a
per-district location summary that the new jail locations changed.

### A `/steal`/`/burgle`/`/poach` activity log: success, what was taken, and who from

User asks: "Add a steal/burgle/poach log that shows whether you were successful, what you stole, and
who you stole it from."

New `CrimeLog` table (migration `c7e2a4f9b1d6`) -- one row per resolved attempt: `kind`, `tick`,
`success`, `caught`, `target_name`, `good_name`, `amount`. `character_id` is deliberately *not* a real
foreign key (the same choice `StaffAction.staff_discord_id` already made): `apply_steal_outcome`/
`apply_burgle_outcome`/`apply_poach_outcome` are exercised by a lot of unit tests against a
lightweight, never-persisted `Character` fixture, and a real Postgres FK would reject every one of
those writes outright. `target_name`/`good_name` are plain snapshot strings rather than ids, so a log
entry stays readable even after the NPC/character/good it names is gone or renamed.

Written from a single new shared module, `panem_shared/crime_log.py` (`record_crime_log`/`list_crime_
log`), called once inside each of `apply_steal_outcome`/`apply_burgle_outcome`/`apply_poach_outcome`
right before they return -- the one funnel both the RNG-fallback roll and the Activity minigame's own
result already share, so every attempt gets logged identically no matter which path resolved it, with
zero changes needed at either call site. `apply_burgle_outcome`'s signature changed from taking just
`house_value: float` to the whole `house: Property`, so it can look up and log the owner's name itself
(`None` for an NPC-owned/unclaimed house, which `/burgle owner:<name>` can't target anyway since it
only searches `Character` rows) -- updated both of its callers (`roll_and_apply_burgle` and `panem_
api/app.py`'s crime-attempt result endpoint) and the handful of unit tests that called it directly.

Two ways to view it, matching this session's usual dual-surface pattern:
- **Bot**: new `/crimelog character:<name>` (ephemeral), listing recent attempts most-recent-first,
  e.g. "**Steal** (tick 120) -- got away with 42 money from Mark." / "**Poach** (tick 118) -- caught,
  fined and jailed."
- **Dashboard**: new `GET /activity/dashboard/crime/{character_id}/log` endpoint, and a "Recent
  Activity" table at the bottom of the Crime tab (`tabs/crime.js`) that loads on mount and refreshes
  automatically after every minigame result (the same `postMessage` listener that already clears the
  iframe). `dashboard.css` gained `table.data-table td.win`/`.lose` color rules (bumped `?v=` 10 -> 11,
  matching `.result-line.win`/`.lose`'s existing colors); `app.js`'s `ASSET_VERSION` (24 -> 25, covering
  `tabs/crime.js`) and `index.html`'s matching `?v=` bumped too.

New tests: `test_crime_log.py` (`record_crime_log`/`list_crime_log` directly), extended `test_stealing_
service.py` (log rows for steal and burgle, including the owner-name lookup with both a character-owned
and an NPC-owned house) and `test_poaching_service.py` (log rows for a clean success and a caught
attempt), and `test_api_app.py` (the dashboard log endpoint: ordering, empty history, non-owner
refusal). Full suite passes at 1023 (up from 1010).

### Minigames wait for the player's first move, and gained an on-demand "how to play" button

User asks: "don't start the lockpick, poaching, or pickpocket minigames until the user interacts the
first time so they have time to prepare. Additionally, add a little circle with an 'i' in it to the
top right corner of every minigame that can be played through work or crime or anything that when
clicked provides detailed instructions on how to play the minigame."

**Pause until first interaction.** All three contraband minigames -- `games/lockpick.js` (shared by
`/lockpick` and `/burgle`), `games/pickpocket.js` (`/steal`), and `games/archery.js` (`/poach`) -- used
to start their `requestAnimationFrame` loop the moment `mount()` ran, so a player who paused to read
the always-visible instructions line first was already losing meter/time/arrows before they'd touched
anything. Each module now gates its loop behind a `started` flag and a `beginIfNeeded()` function, with
a frozen-board overlay ("Hold W or click to begin" / "Click or press Space to begin" / "Move your mouse
or click to begin") shown until the player's first qualifying input:

- **Lockpick**: single hold-to-rise control, so the first `keydown`/`pointerdown` naturally doubles as
  both "start" and "the real first move" -- no special-casing needed.
- **Pickpocket**: single strike-only control (click or Space), so the naive version would burn the
  player's first strike attempt against a needle that hadn't started moving yet, an almost-guaranteed
  miss. `onStrikeClick`/`onKey` now check `if (!started) { beginIfNeeded(); return; }` before reaching
  the real `strike()` logic, so the first input only wakes the needle; the player's *next* input is
  their actual first strike.
- **Archery**: mouse movement wakes the game for free (no arrow spent, since aiming isn't firing), but
  a first input that's a *click* (rather than a preceding mouse move) would otherwise fire at a target
  still sitting dead-center. `onClick` captures whether the game was already started before calling
  `beginIfNeeded()`, and if it wasn't, treats that click as "wake only" and returns without spending an
  arrow or resolving a shot -- `timeLeftMs()` also reports the full time limit while `startedAt` is
  still `null`, so the on-screen clock doesn't appear to tick down before the game has actually begun.

Verified live with Playwright against each game module directly (mounted via small same-origin test
harnesses, deleted before commit): progress/timer/arrow-count all stay frozen through several hundred
milliseconds of idling after mount, the first qualifying input starts the clock/loop without being
treated as a wasted real action, and normal play proceeds correctly afterward (including the
archery case of a synthetic click with no preceding mousemove, to isolate "click is the literal first
event" from a real mouse's usual move-then-click path).

**The "i" button.** Rather than duplicate an info button/popup across all nine minigame modules (work's
six plus crime's three), every module already exports an `instructions()` function, so the button lives
once per coordinator page instead: `work.js` and `crime.js` each gained an identical `mountInfoButton
(text)` helper, called once right after `game.mount(...)` (mounting is what sets `#board`'s contents,
so the button has to be appended after, not before, or it gets wiped out). It renders a small circular
"i" button pinned to `#board`'s top-right corner (`position: absolute`, `#board` itself set to
`position: relative`) that toggles a popup of the same instructions text already shown in the
always-visible `#instructions` line above the board -- available on demand without permanently taking
up screen space, and handy again after a loss scrolls that top line out of view. New `.info-btn`/
`.info-popup` rules added to `style.css` (the one shared stylesheet in this codebase with no `?v=`
cache-busting convention -- left that way here, consistent with its existing state rather than
introducing one unprompted).

Verified live with Playwright loading the real `work.html`/`crime.html` pages (fetches mocked via route
interception): exactly one `.info-btn` renders inside `#board` after mount, the popup starts hidden,
toggles open/closed on repeated clicks, and shows each game's actual `instructions()` text.

`work.js`'s `ASSET_VERSION` bumped 9 -> 10 (covering all six `games/*.js` it imports plus its own
`mountInfoButton` addition) and `crime.js`'s likewise 9 -> 10 (covering `lockpick.js`/`pickpocket.js`/
`archery.js` plus its own addition), with `work.html`'s and `crime.html`'s `<script>` tags' `?v=` bumped
to match. No backend changes -- pure frontend JS/CSS, so the Python test suite (1023 passing) and mypy's
pre-existing 148-error baseline are both unaffected; verification for this change was `node --check` on
every touched file plus the two live Playwright passes above.

### Donor-only dashboard theming: a custom background/accent color, gated by a Discord role

User asks: "Add a way to customize the hex code for the background of the dashboard as well as the hex
code for the accent color used for buttons etc. Only people with a specific role/roles whose IDs are
defined in the .env should be able to utilize this function (so only donors can have custom backgrounds
and accents. The customization should be a little color wheel in the top right corner of the activity
that when you click it it pops up a discord style color picker where you can move around a color picker
and choose any color you want and it adjusts the background live so you can see what it will look like
if you choose it. There also needs to be a reset button that goes back to the default style (the way it
is now)."

**What gets customized.** `static/style.css`'s `:root` already defined exactly two CSS custom
properties driving the whole dashboard's look -- `--bg` (page background) and `--accent` (buttons,
active states, progress fills) -- used throughout `dashboard.css`/`work.css`/`crime.css`/`style.css`
already. This feature adds a per-account override of those two values, live-set on
`document.documentElement.style` rather than editing the stylesheet itself.

**Who's allowed.** New `.env` setting `DONOR_ROLE_IDS` (comma-separated Discord role snowflake ids,
e.g. `111111111111111111,222222222222222222`) -- a donor perk unlike the existing single-role
`STAFF_ROLE_ID` is commonly granted by more than one donation tier, so this is a list
(`Settings.donor_role_id_set()` parses it, tolerating whitespace/trailing commas). `discord_staff.py`
gained `fetch_has_any_role` alongside the existing `fetch_is_staff` -- both now share one `_fetch_
member_role_ids` REST lookup, with `fetch_has_any_role` generalizing the single-role check to "holds at
least one of these role ids." Same fail-closed posture as staff: an unconfigured/unreachable lookup
reads as "not a donor," never the other way around. `/activity/dashboard/identify`'s response gained
`is_donor`, checked server-side the same way `is_staff` already was for the Staff tab.

**Where it's stored.** Two new nullable `String(7)` columns on `users` (migration `653865b0f6f6`):
`dashboard_background_hex`/`dashboard_accent_hex`, each `#rrggbb`. Deliberately on `User`, not
`Character` -- Discord role membership (and therefore the donor gate) is account-wide, not
per-character, so the customization is too. New `panem_shared/theme.py` holds the two defaults
(`DEFAULT_BACKGROUND_HEX`/`DEFAULT_ACCENT_HEX`, mirroring `style.css`'s `:root` values exactly -- what a
reset, a non-donor, or a never-customized account renders) and `validate_hex_color` (strict `#rrggbb`,
normalized to lowercase).

A subtlety worth calling out: a saved theme is only ever *surfaced* while the account currently holds a
donor role. `/identify` computes `is_donor` fresh on every call and only includes the saved hex values
in its `theme` field when that reads true (`dashboard_routes._theme_for_user`); if a donor's role lapses
later, the row isn't cleared (in case the role comes back), but the dashboard silently falls back to the
plain default in the meantime -- honoring "only donors get custom backgrounds and accents" even for
someone who customized once and then lost the role, without the awkwardness of throwing away a
customization that might return.

**The endpoints.** New `dashboard_routes.build_theme_router`: `POST /activity/dashboard/theme
{discord_id, background_hex, accent_hex}` (validates + persists, returns the normalized pair) and `POST
/activity/dashboard/theme/reset {discord_id}` (clears both columns, returns the defaults) -- both
re-check `discord_staff.fetch_has_any_role` themselves on every call rather than trusting `/identify`'s
`is_donor` (the same "never trust the client with a privilege decision" posture `build_staff_router`'s
`_require_staff` already established), so a non-donor gets a 403 regardless of what an earlier
`/identify` response said.

**The color wheel.** A new `<div id="theme-picker" hidden>` sits as the last element in `index.html`'s
header (after `#character-select`, whose own `margin-left: auto` already pushes everything before it
left -- so this lands at the literal top-right corner of the Activity). `app.js` only unhides it when
`/identify` reports `is_donor`, and mounts a brand-new module, `theme_picker.js`, into it via
`mountThemePicker(container, {getTheme, onPreview, onSave, onReset})`. The toggle button itself is a
literal color wheel -- a CSS `conic-gradient`, no image asset -- that opens a Discord-role-color-picker-
style popup: two target swatches (Background / Accent) to switch which color you're editing, a
saturation/value square plus a hue strip (the classic two-layered-CSS-gradient SV square trick -- a
black-to-transparent vertical gradient over a white-to-hue horizontal one, no canvas needed), and a hex
text field, all kept in sync in both directions. Original, canvas-free, hand-rolled widget -- a generic,
well-known color-picker shape, not anyone's particular implementation, matching this codebase's no-
external-library convention for every other minigame/UI widget.

Dragging in the square or hue strip (via pointer-capture, so the drag tracks correctly even past the
element's own edges) or typing a hex value calls `onPreview` on every change, which `app.js` wires
straight to setting `--bg`/`--accent` on `document.documentElement.style` -- exactly the "adjusts the
background live so you can see what it will look like if you choose it" the request asked for, before
anything is actually saved. The two targets (Background/Accent) keep independent in-progress state, so
switching which one you're editing never discards an unsaved edit to the other. **Save** posts both
current values to the theme endpoint and keeps the live preview; **Reset to default** posts to the reset
endpoint and reapplies the defaults. Closing the popup without saving (clicking the color-wheel button
again, clicking anywhere outside it, or pressing Escape) reverts the live preview back to the
last-*saved* theme, discarding whatever was being previewed -- so idly dragging around to see what a
color would look like never leaves the dashboard in an unsaved-but-visually-applied state.

**Reaching the minigame pages.** `work.html`/`crime.html` are separate same-origin pages (opened
directly via a Discord launch link, or embedded as an iframe by the dashboard's Work/Jail/Crime tabs)
with no Discord identity of their own to ask `/identify`. `app.js` mirrors the resolved theme to
`localStorage` (`panem_theme_bg`/`panem_theme_accent`) on every load -- the same "app.js writes,
another same-origin page reads" convention `panem_character_id` already established -- and `work.js`/
`crime.js` each gained a small snippet at module top-level (before their own dynamic game imports) that
reads those two keys and applies them via the same `setProperty` calls, best-effort (a missing/blocked
value just leaves the plain default in place).

Verified live with Playwright in three passes: (1) the picker mounted in the real `index.html` context
with `/identify`/`/activity/dashboard/theme` fetches mocked -- button visibility for donor vs.
non-donor, live preview while dragging both the SV square and hue bar, independent per-target state,
hex-field sync, Save persisting and posting the right body, Reset restoring defaults, and closing an
unsaved edit correctly reverting; (2) confirming a non-donor's identify response leaves the button
hidden and the default theme applied; (3) confirming `work.html` picks up a theme already saved to
localStorage by a prior `index.html` visit. New backend tests: `test_theme.py` (`validate_hex_color`
accept/reject cases, `donor_role_id_set()` parsing) and `test_api_app.py`'s new `TestDashboardTheme` +
extended `TestDashboardIdentify` (is_donor true/false, set/reset happy paths, invalid-hex 400, non-donor
403 on both write routes, and the lapsed-donor case -- a saved theme stops being surfaced the moment
`is_donor` reads false, without the row itself being cleared). Full suite passes at 1046 (up from 1023);
mypy's pre-existing 148-error baseline unaffected; migration `653865b0f6f6` verified up/down/up.

### Extending the theme picker: Panel/Text colors, and named per-character/general profiles

User asks: "For the color picker, add an option to adjust the color of all the panels in each tab (one
color they all share) and the color for all the text. Also, allow users to save 'profiles' for different
color presets that they can assign to different characters and save in general to go back to, and they
should be able to name these profiles as well."

**Two more colors.** `style.css`'s `:root` already had `--panel` (every card/table/input background) and
`--text` (body copy color) sitting right alongside `--bg`/`--accent`, just never exposed to the picker.
`panem_shared/theme.py` gained `DEFAULT_PANEL_HEX`/`DEFAULT_TEXT_HEX` (mirroring those values exactly,
same convention as the original two defaults) and `theme_picker.js`'s `TARGETS` grew from
`["background", "accent"]` to `["background", "accent", "panel", "text"]` -- same SV-square/hue-strip/
hex-field mechanism, now rendered as a 2x2 grid of target buttons (four no longer fit comfortably in one
row at the popup's original 200px width, so the popup widened to 240px alongside it) with four
independent in-progress HSV states.

**From "one slot per account" to named, reusable profiles.** The prior version stored exactly one
`{background_hex, accent_hex}` pair directly on `User`. That shape has no room for "save a few presets
and switch between them" or "a different look per character," so this rework replaces it outright with a
new `ThemeProfile` table (migration `a2520b13ef46`, chained after `653865b0f6f6`) --
`id, user_id, name, background_hex, accent_hex, panel_hex, text_hex` -- plus two new FK columns:
`User.active_theme_profile_id` (the account's general default) and `Character.theme_profile_id` (a
per-character override). There's no migration path from the old two scalar columns to a named profile
(there's no name to give one), so the migration just drops them -- a donor who'd already customized
starts over with one Save, the same "never-customized == default" meaning `NULL` always carried.

Both new FK columns point at `theme_profiles.id`, and `theme_profiles.user_id` points back at `users.id`
-- a mutual reference that `Base.metadata.create_all`/`drop_all` (every test's DB fixture) can't
topologically sort on its own. Fixed the same way this codebase already fixed the identical situation for
`Character.housing_property_id` <-> `Property.owner_id`: `use_alter=True` plus an explicit
`name="fk_users_active_theme_profile_id"` on the `User` side, and the migration adds that one column
without an inline FK, then `op.create_foreign_key(...)` with the same name right after (with
`op.drop_constraint(...)` before the column drop on the way back down).

**Resolution order.** `dashboard_routes._resolve_theme(session, user, character)`: the given character's
own `theme_profile_id` if it has one, else the account's `active_theme_profile_id`, else the plain
default -- "a character overrides the general look, which is there to go back to." `/identify` still
returns a `theme` field (now with `panel_hex`/`text_hex`/`profile_id` added), but it's resolved with no
character context (the account's general theme) since `/identify` runs before a character is even picked
client-side; `theme_profiles` (the donor's saved list, empty for a non-donor or lapsed donor -- same
surfacing rule as before) rides along in the same response so the picker doesn't need a separate fetch
for the common case.

**The profile endpoints**, all under `build_theme_router`, all donor-gated the same way as before (every
write re-checks `fetch_has_any_role` itself): `POST .../theme/profiles` (create, capped at 20 per account
-- `MAX_THEME_PROFILES_PER_USER`), `POST .../theme/profiles/list` (list -- see below for why this is a
POST, not the `GET` its "list" name would suggest), `PATCH .../theme/profiles/{id}` (rename/recolor,
partial), `POST .../theme/profiles/{id}/delete`, `POST .../theme/profiles/{id}/activate` (sets the
account's general default), `POST .../theme/profiles/{id}/assign` (sets one character's override), `POST
.../theme/unassign` (clears a character's override, falling back to general), and `POST .../theme/reset`
(now takes an optional `character_id` -- given, it clears *that character's* assignment, which may fall
back to a still-active general profile rather than the plain default; omitted, it clears the account's
general profile itself, same as before). `POST .../theme/resolve` (also a POST) is what `app.js` calls
every time the selected character changes, to get that specific context's effective theme -- it never
403s, same non-throwing shape `/identify`'s `theme` field already had for a non-donor.

Deleting a profile relies on `ondelete="SET NULL"` on both FKs to clear any account/character still
pointing at it -- nothing to clear by hand in the route itself.

**Why `/profiles/list` and `/resolve` are POST, not GET, despite reading data:** both need a `discord_id`
and go through `_require_donor` -> `discord_staff.fetch_has_any_role`, which does its own outbound
`httpx.AsyncClient.get(...)` call to Discord's REST API. This test suite's existing convention for
mocking that Discord call is `patch.object(httpx.AsyncClient, "get", AsyncMock(...))` -- which patches
the method on the *class*, so it also intercepts the test's own `client.get(...)` call to a `GET` route
under test, returning the fake Discord response instead of ever reaching the app. `POST /activity/
dashboard/identify` (a read, despite the verb) already sidesteps this same issue for the same reason;
these two new endpoints follow that precedent rather than fighting it.

**The picker UI's profile manager.** Below the four color targets: a `<select>` listing every saved
profile (plus "Unsaved colors"), a name text field, and four action buttons -- **Save as New** (creates,
then loads the new profile so Update/Delete/Set as Default are immediately usable on it without a manual
reselect), **Update** (overwrites the loaded profile's name+colors), **Set as Default** (shows "Default
✓" once it is one), and **Delete** -- plus, only when a character is currently selected in the dashboard,
a fifth button that toggles between "Use for `<character>`" and "Stop using for `<character>`" depending
on whether the loaded profile is that character's current override. Picking a profile from the dropdown
loads its colors into the four pickers *and* live-previews it immediately (matches the original request's
"adjusts the background live so you can see what it will look like if you choose it," now extended to
previewing a saved profile before committing to it via Activate/Assign). **Reset to default** now resets
whichever context is open -- the selected character's own assignment if one is selected, else the
account's general profile -- rather than always the account-wide slot.

`app.js` tracks `state.themeProfiles` and `state.activeThemeProfileId` (the general default, kept
separate from whatever theme is *currently applied*, which can be a character-specific override) and adds
`refreshResolvedTheme()`, called after every `/identify` and on every character switch
(`selectCharacter`), to re-resolve and re-apply `--bg`/`--accent`/`--panel`/`--text` for whichever
character (if any) is now selected. `work.js`/`crime.js`'s existing localStorage mirroring extended to the
two new keys (`panem_theme_panel`/`panem_theme_text`), same best-effort fallback as the original two.

Verified live with Playwright: creating a named profile and dragging the SV square first (so it's not
just the default colors), Save as New posting the right body and the select immediately showing it
loaded, Set as Default posting `activate` and `--bg` updating to match, the assign row appearing only
with a character selected and offering "Use for `<name>`", assigning posting the right `character_id` and
the button flipping to "Stop using" on reopen, and Reset (with a character selected) falling back to the
still-active general profile's color rather than the plain default -- plus a live drag on the Panel target
confirming `--panel` updates independently. Full backend suite passes at 1065 (up from 1046); ruff/mypy
clean against the existing baselines; migration `a2520b13ef46` verified up/down/up (round-tripping the
FK-name fix, not just the schema shape). New/extended tests: `test_theme.py`'s
`TestValidateProfileName`, and `test_api_app.py`'s `TestDashboardThemeProfiles` (create/list/update/
delete/activate, the 20-profile cap, and the lapsed-donor case) plus a new `TestDashboardThemeAssignment`
(assign/unassign/resolve, and reset's two different fallback targets depending on whether a
`character_id` was given).

## RP Modes: Story, Life, and Simulation

Every character now picks an **RP mode** at creation, changeable later (`/character mode`, subject to a
3-real-day cooldown): **Story** (pure freeform RP -- no economy, crime, housing, work, or NPC interaction,
no travel cost/delay, and immune to crime targeting), **Life** (the full economy/crime/market/work/travel
loop, minus housing and the needs system, with crime toggleable off for yourself once per real day), and
**Simulation** (today's full experience, unchanged, plus two new meters -- thirst and sanity -- alongside
hunger/health/fatigue). A character's mode defaults to Simulation, matching the game's original,
unmoded behavior for anyone who doesn't opt into something lighter.

### Gating matrix

| Capability                     | Story                          | Life                              | Simulation                        |
| ------------------------------- | ------------------------------- | ---------------------------------- | ----------------------------------- |
| `/rp`, `/tag`, `/scene`         | Yes, from anywhere in-district  | Yes, must be at the location       | Yes, must be at the location       |
| Economy (market/work/black market) | No                          | Yes                                 | Yes                                  |
| Crime (steal/burgle/poach)      | Never a victim or an actor      | Actor if `crime_enabled`; victim only if their own `crime_enabled` is on | Always both |
| Housing                          | No                              | No                                  | Yes                                  |
| NPC interaction (`/talk`, `/engage` with NPCs) | No             | Yes                                  | Yes                                  |
| Cross-district travel            | Instant, free                   | Always 1 tick, normal cost          | Normal `TRANSIT_TICKS`, normal cost |
| Needs (hunger/thirst/sanity/fatigue) | N/A                        | N/A -- no decay, no requirement     | Full system, see below              |
| Afflictions/death                | N/A -- excluded from the system entirely | Manual only (`/character afflict`, `/character die`) | Automatic, staff-threshold-driven |
| `/pay`, `/trade`                 | No                               | Yes                                  | Yes                                  |

A Story character is invisible to crime both ways -- `check_can_steal`/`check_can_burgle`/`check_can_poach`
all refuse the moment either party is Story mode, and target-listing endpoints (bot autocomplete and the
dashboard's Crime tab) filter Story-mode characters out before they're ever offered as a target.

### Needs (Simulation only)

Simulation characters track five meters: health, hunger, fatigue (existing), plus **thirst** and
**sanity** (new). `/eat`/`/drink`/`/entertain` relieve them -- see the **Vitals tab** section below for how
this mechanic works today (it was substantially reworked from this feature's original once-per-day/flat-
cost shape). Life and Story characters are refused outright (`sustenance_mode_forbidden`) -- their meters
never move and these commands are a no-op for them by design, not an oversight.

### Afflictions and death

A staff-authored catalog (`AfflictionType`: name, description, `is_permanent`, and optional
cure/auto-apply stat+threshold pairs -- e.g. "cured once health rises above 60%", "auto-applied once
fatigue falls below 20%") drives two different entry points depending on mode:

- **Simulation**: fully automatic. Every tick, `apply_auto_afflictions_sync`/`apply_auto_death` (pure,
  DB-free functions called from `panem_sim`'s needs system, since the tick loop has no DB session per
  system) check each character's current stats against the catalog's auto-apply thresholds and against
  the death condition, and `check_and_cure_sync` resolves any that have since recovered.
- **Life**: manual only, via `/character afflict type:<catalog autocomplete> cause:<text>` and
  `/character die cause:<text>` -- both confirm-gated (a Discord button, restricted to the invoking user)
  and both refuse outright in any other mode.
- **Story**: excluded from the system entirely -- no afflictions, no death, ever.

Staff manage the catalog from the dashboard's Staff tab (`afflictionTypesPanel` in `staff.js`) -- create,
edit, delete, with validation that a permanent type carries no cure fields and that cure/auto-apply
stat+threshold pairs are complete together, not half-set. The same catalog is read-only-public at
`GET /activity/dashboard/affliction-types` for the autocomplete/preview players see when self-inflicting.
Active afflictions and, if dead, the cause of death show on `/character status` and on the dashboard's new
Home tab.

### `/pay` and `/trade`

Two new player-to-player economy primitives, both Story-mode-forbidden and requiring both parties be
`APPROVED`:

- **`/pay target:<character> amount:<int>`** -- instant, one-way, no confirmation, same posture as
  housing's existing rent/purchase transfers.
- **`/trade offer target:<character> give_good: give_qty: give_money: want_good: want_qty: want_money:`**
  -- one item-or-money offer per side (not a multi-item cart, a deliberate scope choice to keep the
  command surface to plain slash parameters rather than a modal-built cart). Posts an Accept/Decline view
  to the recipient's DMs, restricted to their Discord user id. `accept_trade` **re-validates** both
  sides' money/inventory at accept time (an offer can go stale between propose and accept) before moving
  anything, atomically, in one session. `/trade cancel` (initiator-only, while still pending) withdraws
  an offer; a `tasks.loop` expires stale pending offers after `TRADE_OFFER_EXPIRY_MINUTES`.

The dashboard's Social tab mirrors both 1:1 (`payPanel`/`tradePanel` in `social.js`, reading/writing
`/activity/dashboard/pay/*` and `/activity/dashboard/trade/*`) -- the Trade panel has no push channel of
its own the way the bot's DM does, so a recipient discovers a pending offer by reopening the tab (poll,
not push), the same posture the read-only Engagement panel already has.

### The dashboard's Home tab

A new "Home" tab (now the dashboard's default landing tab) surfaces everything about a character's RP
mode in one place, reading the same status endpoint (`GET /activity/dashboard/mode/{id}/status`) the
bot's own `/character mode` confirm view is built against: current mode with a plain-language description,
the five meters as bar fills (Simulation only -- a "not applicable in this mode" note otherwise), the
crime toggle (Life only, disabled with a countdown while on cooldown), active afflictions, and a "Change
Mode" button that opens an in-page confirmation panel -- never `window.confirm()`, given how much rides on
this choice -- listing the destination mode's consequences as bullet points before the switch actually
posts.

### Verification

Each of this feature's twelve build milestones (schema; shared pure-logic services; character-creation
mode selection; travel/RP-location gating; crime/market/housing/NPC-engagement gating; mode-switch and
crime-toggle commands; Simulation-only needs; the staff affliction catalog; the self-inflict commands;
pay/trade; the dashboard home page; this final pass) landed as its own commit with its own ruff/mypy/
pytest/migration-round-trip check. Full suite: **1208 passed**. `ruff check .`: clean. `mypy` across all
four packages: unchanged pre-existing baseline (156 errors in 7 files, all pre-existing
`self.bot.db()`/`self.bot.content` attribute-defined findings unrelated to this feature). `alembic heads`:
single head. Live Playwright passes (local `http.server` + stateful `page.route()` mocks, this session's
established verification shape) covering: the Home tab landing by default, Simulation-mode meters
rendering, the mode-switch confirmation flow (bullets updating per selected destination mode, the switch
call firing, meters/crime panel reacting correctly post-switch), the crime toggle, the afflictions list
(including a permanent entry), a death-cause line, both cooldown-disabled states (crime toggle and mode
switch, each showing a countdown), and a full trade cycle on the Social tab (an incoming offer's summary
line, Accept removing it from the list, sending a new offer with goods+money on both sides, and
cancelling it).

## Vitals tab: eat/drink/sleep/entertain as a real, played activity

Simulation mode's proactive meter relief (`/eat`/`/drink`/`/entertain`, and the dashboard's new **Vitals**
tab) was reworked from a flat, once-per-sim-day, pay-a-fee-get-a-fixed-relief mechanic into something a
character actually lives through: eating and drinking now consume specific goods you own, cooking/baking
is a real multi-stage minigame with a timing-dependent bonus, sleep gets a live fatigue-restored preview,
and entertainment is a menu of the same minigames `/work` already uses, each with its own sanity value --
on top of a small passive sanity trickle from just roleplaying.

### Per-good hunger/thirst values, and how to add more

`Good` (`panem_shared.content.schemas`) carries three new optional fields: `hunger_value`, `thirst_value`
(both default `0.0`), and `cook_method` (`"stove"`, `"oven"`, or `None`). A good with `hunger_value > 0` is
edible, one with `thirst_value > 0` is drinkable, and a good with `cook_method` set can be cooked/baked for
a bonus (see below). Today's assignment (`data/goods.yaml`): `fish`/`livestock` (Seafood/Meats, cookable on
the stove), `grain` (bakeable in the oven), and `produce` (Fruits/Drinks, the one drinkable good). Adding a
new consumable -- a vegetable, a new drink -- is pure content: add the fields to its `Good` entry in
`goods.yaml`, no code change anywhere.

### Eating and drinking: inventory-gated, no more flat daily credit

`/eat`/`/drink` (and the Vitals tab's Eat/Drink panels) require the character to actually own the good --
`sustenance.eat`/`drink` (`panem_shared/sustenance.py`) consume one unit via `market.adjust_inventory` and
relieve `hunger`/`thirst` by that good's own value. No cooldown: do it as often as inventory allows, since
what keeps this balanced now is the *decay* rate, not a daily cap (see below). A straight `/eat` never gets
the cooking bonus (`bonus=False`) -- that's Activity-only, via the cook/bake minigame.

### The cook/bake minigame

A multi-stage, Papa's-Burgeria-style sequence (`static/games/cook.js`/`bake.js`, launched from the Vitals
tab's Eat panel into `static/vitals.html` the same iframe-embedding way `/work`'s minigames already launch
from the Work tab):

1. **Prep** -- a short click-the-ingredients-in-order QTE (cosmetic, not scored).
2. **Cook/Bake** -- a doneness gauge sweeps through five zones (Raw → Undercooked → **Perfect** →
   Overcooked → Burnt) over a few seconds; the player clicks "Take it off"/"Pull it out" once. Landing
   inside the Perfect zone is the *only* bonus condition -- too early or too late both miss it, same as
   "if they take it off too early, they don't get the bonus... if too late, they also don't get the
   bonus."
3. **Plate** -- a one-click "Serve it" finish (cosmetic).

The result posts straight to `POST /activity/dashboard/vitals/{id}/eat` with `bonus` set to whether Perfect
landed -- the same client-reported-outcome trust model every other Activity minigame here already uses
(`resolve_shift_game`'s `won`). Landing the bonus applies `constants.COOK_BONUS_MULTIPLIER` (2.0, i.e. a
100% bonus) to that good's `hunger_value`.

### Passive hunger/thirst decay: once per phase, not once a night

Hunger and thirst now climb four times a day (`simtime.TICKS_PER_PHASE`, one per day-phase) instead of
once a night, each by a random amount in `[HUNGER_PHASE_DECAY_MIN, HUNGER_PHASE_DECAY_MAX]` /
`[THIRST_PHASE_DECAY_MIN, THIRST_PHASE_DECAY_MAX]` (10-20 by default) drawn from the tick's own seeded
`ctx.rng` (`panem_sim.systems.needs._apply_character_phase_needs`) -- never Python's global `random`, to
keep tick determinism intact. This is fully decoupled from `NIGHTLY_LIVING_COST`, which stays a pure
money/health mechanic with no hunger side effect anymore. Sanity's own decay is unchanged (still once a
night).

### Entertainment: the six `/work` leisure games, plus a passive RP trickle

The Vitals tab's Entertainment panel offers the same six leisure minigames `/work` already uses
(Minesweeper, Snake, Connect Four, Coin Flip, Pick Your Poison, Solitaire -- not the crime skill-check
games, which stay tied to their own crime outcomes), each with a flat sanity value
(`constants.ENTERTAINMENT_SANITY_VALUES`) credited on completion, win or lose. `/entertain` from Discord
credits a flat `SANITY_RELIEF_PER_ENTERTAIN` instead, since there's no minigame to play from Discord.
Separately, every qualifying proxied RP message (the same length gate that already docks fatigue for a
work-shift interaction, `shifts_svc.meets_rp_credit`) now also trickles `sanity` up by
`SANITY_GAIN_PER_INTERACTION`, Simulation mode only -- "sending role play messages... should replenish a
little sanity each time."

### Sleep: unchanged mechanically, now with a live preview

Sleep's own logic (`housing.check_can_sleep`/`apply_fatigue_restoration`/`fatigue_restored`) didn't change
-- the Vitals tab's Sleep panel just adds a client-side "you'll restore ~X fatigue" preview, recomputed on
every keystroke from the Vitals status endpoint's `has_bed`/`max_sleep_ticks`/`fatigue_restore_per_tick`
(no round trip per keystroke), then POSTs to the same existing `/activity/dashboard/housing/{id}/sleep`
endpoint the Housing tab's own simpler sleep control already uses.

### Verification

Each of this feature's eight milestones (content schema; shared services; `panem_sim` per-phase decay;
`panem_bot` `/eat`/`/drink` rework + passive RP sanity; the `panem_api` Vitals router; the cook/bake
minigame page; the dashboard tab; this final pass) landed as its own commit with its own ruff/mypy/pytest
check. Full suite: **1230 passed**. `ruff check .`: clean. `mypy` across all four packages: unchanged
pre-existing baseline (157 errors in 8 files, all pre-existing `self.bot.db()`/`self.bot.content`
attribute-defined findings and one pre-existing `Any`-return finding, none related to this feature).
`alembic heads`: single head, unchanged (this feature needed no migration -- every new value is
content/constants-driven, and the existing `Character`/`Inventory` columns already covered every new piece
of state). `node --check` on every new/changed static JS file. Live Playwright passes (local `http.server`
+ stateful `page.route()` mocks) covering: the Vitals tab's five panels rendering, Eat/Drink/Sleep/
Entertain all round-tripping correctly against a mocked backend, the sleep preview updating live on input,
and -- driving `vitals.html`'s cook minigame directly against its actual real-time sweep, no clock
mocking needed -- confirming all three outcomes: taking the food off mid-sweep lands the bonus, while too
early and too late both miss it.

## Panem-wide staff lore: keyword-tagged history facts, and Alternate Universe notes NPCs always keep in mind

NPC dialogue (`/talk`, `/engage` replies, and ambient NPC-to-NPC chatter -- everywhere
`panem_bot.services.dialogue` calls the LLM) can now draw on two kinds of staff-authored, Panem-wide
canon, on top of an NPC's own personal memories and relationship history:

- **History facts** -- short, staff-written statements of Panem canon, each tagged with one or more
  comma-separated keywords (e.g. `dark days, district thirteen`). A fact only rides into a given dialogue
  request when one of its keywords actually appears in the line being replied to
  (`panem_shared.lore.match_history_entries`, a plain case-insensitive substring check, capped at
  `constants.MAX_HISTORY_ENTRIES_PER_REPLY` so a busy table can't crowd out the rest of the prompt) --
  the request header's new `[HISTORY]` block.
- **Alternate Universe notes** -- one free-form block of prose every NPC in the nation is assumed to
  know, unconditionally included whenever set (no keyword matching), for describing how this particular
  Panem's canon diverges from anyone's default expectations, or any other standing fact staff want every
  resident to act on. Rendered as the request header's `[WORLD]` block, right after `[MODE]`.

### Why this is a database, not a content-YAML edit

Every other "world knowledge" an NPC draws on already lives in one of two places, and this feature
deliberately sits in the one that changes fast: `lemonade/system_prompt.md`'s own hand-written `## Panem`
section is *static* canon, baked into the registered Lemonade collection at build time
(`scripts/lemonade_omni.py build`) -- editing it needs a rebuild-and-reregister step, appropriate for
slow-changing world design, not for "staff wants to add a fact mid-session." History entries and AU notes
instead live in two new tables (`PanemHistoryEntry`, multi-row and keyword-tagged; `WorldLoreSettings`, a
single-row staff-tunable settings row mirroring `EngagementSettings`'/`WorldClock`'s own singleton shape),
fetched fresh on every dialogue request (`dialogue.build_request_context`/`build_npc_to_npc_context`) the
same way `EngagementCog`'s idle-close task already reads `EngagementSettings` fresh every pass -- a staff
edit takes effect on the very next line an NPC speaks, no redeploy.

### `/staff lore` commands

- `/staff lore history-add keywords:<comma-separated> text:<fact>` -- add a new history entry.
- `/staff lore history-remove entry_id:<id>` -- remove one, by the id shown in `history-list`.
- `/staff lore history-list` -- list every entry with its id and keywords.
- `/staff lore au-set text:<notes>` -- replace the Alternate Universe notes.
- `/staff lore au-show` -- show the current Alternate Universe notes.

All five follow the existing `/staff` group's conventions: staff-only (`_is_staff`), every write logged
via `log_staff_action` (audit row + a one-line post to the staff log channel), direct DB writes with no
intermediate cache to invalidate.

### Verification

`ruff check .`: clean. `mypy` across all four packages: unchanged pre-existing baseline (159 -> 162
errors, the +3 all the same pre-existing `self.bot.db()`/`self.bot.redis`-style `"Bot" has no attribute`
category every other cog already carries, none a new category). `alembic upgrade head` /
`alembic downgrade -1` / `alembic upgrade head` round-trips cleanly to a single new head; no existing
table changed. `uv run python scripts/lemonade_omni.py build --check` passes -- the committed
`lemonade/Panem-Omni-*.json` collection files were rebuilt after `system_prompt.md`'s new `[WORLD]`/
`[HISTORY]` header-block documentation, so CI's own staleness check stays green. New unit coverage:
`tests/unit/test_lore.py` (keyword matching: case-insensitivity, no-match, an entry with no keywords never
matching, the `MAX_HISTORY_ENTRIES_PER_REPLY` cap, match order), extended `test_dialogue_service.py` and
`test_lemonade_omni.py` for the new `RequestContext` fields and header rendering. Full suite:
**1257 passed**.

## Notes on snap packaging (strict confinement) + LXD deploy

A third deploy path alongside `deploy/docker-compose.yml`/`deploy/systemd/`: `snap/snapcraft.yaml`
packages `panem_bot`/`panem_sim`/`panem_api` as one strictly confined snap (`panem`, three daemons plus a
`migrate` command), and `deploy/lxd/provision.sh` stands up an LXD container on an external server running
that snap alongside Postgres/Redis (apt-installed in the same container, bound to `127.0.0.1` -- nothing
outside the container needs to reach either). Full walkthrough: `deploy/snap/README.md`.

The one real wrinkle: `snap/snapcraft.yaml`'s build does **not** use `uv sync`'s normal `.venv` the way
`deploy/Dockerfile` does. A uv-managed venv's `bin/python` is a symlink back to a shared, absolute-path
standalone Python install, and its `pyvenv.cfg` records that same absolute build-time path as where to
find the standard library at runtime -- neither survives being copied from the snapcraft build environment
into `/snap/panem/<rev>`, whether the copy dereferences symlinks or not (dereferencing only fixes the
interpreter binary itself, not the venv's separate, still-external stdlib lookup). Instead the build
installs every dependency (including this workspace's own four packages, non-editable) straight into the
standalone interpreter's own site-packages via `uv pip install --python`, then ships that whole interpreter
tree -- genuinely relocatable by design, which is what `python-build-standalone` (what `uv python install`
fetches) is *for* -- as `$SNAP/python`.

Because that install is non-editable, `packages/*/src/*/main.py` can no longer assume it's running from
inside a full checkout to find `data/` the way it always has (`REPO_ROOT = Path(__file__).resolve().
parents[4]`, which only lines up with a real `data/` dir under an editable/dev-mode install -- true for
both `uv run` and `deploy/Dockerfile`'s `uv sync`, coincidentally, since both install this workspace
editable). Added `Settings.data_dir`/`Settings.static_uploads_dir`
(`packages/panem_shared/src/panem_shared/settings.py`) so a packaged install can override both explicitly;
every `main.py` falls back to the old parents[4] trick when they're unset, so `uv run`/Docker/systemd
behavior is completely unchanged. `static_uploads_dir` exists because `panem_api`'s staff layer-image
uploads and `district_mottos.json` need a real writable directory, and a strict-confinement snap's `$SNAP`
is a read-only squashfs -- `panem_api.app.create_app` already took a `static_dir` override for tests
(writing into the checked-out `static/` tree there too), but the app's actual static-file *mount* always
served the real bundled `STATIC_DIR` regardless, meaning uploads written elsewhere were saved but 404'd
when fetched back; fixed by mounting a second, more specific `/uploads` route ahead of the catch-all `/`
mount whenever the override differs from `STATIC_DIR` (harmless no-op when it doesn't). The snap's wrapper
scripts (`snap/local/bin/panem-*`) point both settings at `$SNAP/data`/`$SNAP_DATA/uploads`.

Running snapd itself inside an LXD container needs `security.nesting=true` on the container (for snapd's
own mount namespace) -- `deploy/lxd/provision.sh` sets it on launch and fixes it up (with a restart) on an
existing container missing it, since `snap install` either fails outright or installs but never actually
starts the daemons without it.

Not independently verified end-to-end (no snapcraft/LXD build environment available in this session --
building a real `.snap` needs network access to fetch `uv`+Python 3.14+PyPI packages during
`snapcraft`'s build step, and installing/running it needs a real LXD host); reviewed line-by-line against
documented `uv`/`snapcraft`/LXD behavior instead. Sanity-checked here: every edited/new Python file
(`ast.parse`), every new shell script (`bash -n`/`sh -n`), and `tests/unit/test_api_app.py`'s existing
`staff_app_with_uploads` fixture (which passes `static_dir=tmp_path`, i.e. already exercises the "override
differs from STATIC_DIR" branch) confirmed by inspection to still pass -- it only asserted the uploaded
file landed on disk before, never that `image_url` was actually fetchable, which is exactly the gap the new
`/uploads` mount closes rather than a behavior it could have broken.

## Switching from Story into Life/Simulation for the first time re-opens the job-info application

`switch_mode` (`panem_shared.rp_modes`) never touched `job_title`/`shift_phase` -- fine for a character
that had already been Life/Simulation at some point (that info just sits there across a switch, which is
why picking Story and then coming back has always "remembered" it), but a character that started and
stayed in Story has no job on file at all, and nothing collected one or told staff about the switch before
it took effect.

Added `Character.pending_rp_mode`/`pending_job_title`/`pending_shift_phase`/`pending_job_is_illicit`/
`pending_mode_switch_notified_at` (migration `c6b8f2a4d1e9`) and three new `panem_shared.rp_modes`
functions: `mode_switch_needs_job_info` (`new_mode != story and job_title is None`), `stage_mode_switch`,
and `apply_staged_mode_switch`/`discard_staged_mode_switch`. Staging writes only the `pending_*` columns --
`rp_mode`/`job_title`/`shift_phase` stay exactly as they were the whole time a switch is awaiting approval,
so the character keeps playing in their current mode (this also matters functionally: `panem_sim.systems.
jobs._open_shifts_for_due_characters` opens a shift purely off `job_title`/`shift_phase` being set, with no
`rp_mode` check of its own -- writing those columns immediately, before approval, would have let a still-
Story character start working shifts).

Declining a staged switch must never delete the character -- unlike rejecting a fresh application (which
never became a real character), this one already exists and is playing. That ruled out reusing `Character
Status.PENDING`/`ApprovalView`'s "Reject" button at all, so this got a deliberately separate,
non-destructive `ModeSwitchApprovalView` (Approve/Decline only, `panem_bot/views.py`) with its own handlers
(`_handle_mode_switch_approve`/`_decline`, `panem_bot/cogs/characters.py`) -- approve calls `apply_staged_
mode_switch`, decline just calls `discard_staged_mode_switch`.

- **`/character mode`**: unchanged for switching to Story, or to Life/Simulation with `job_title` already
  set -- still the instant `ConfirmView`-then-`switch_mode` flow. Needing job info instead reopens the same
  job-title/shift-phase/illicit-declare prompts `/character create` uses (`JobInfoModal` in `modals.py`,
  reusing `ShiftPhaseSelectView`/`IllicitDeclareView`), stages the switch, and posts a staff embed (mirrors
  `_post_approval_embed`'s idempotency-via-timestamp shape, on its own `pending_mode_switch_notified_at`
  column and its own poll/pubsub pair -- `_announce_pending_mode_switches`/`_listen_for_pending_mode_
  switches`/`CHARACTER_MODE_SWITCH_PENDING_CHANNEL` -- since a mode-switch post and a fresh-application post
  are different facts and shouldn't share one "already posted?" guard).
- **Dashboard mode-switch panel** (`tabs/home.js`): `RpModeStatusResponse` gained `has_job_info`/
  `pending_mode`; picking a non-Story mode without `has_job_info` reveals job-title/shift-phase/illicit
  fields before "Confirm switch" is enabled, and a pending switch replaces the panel with a plain status
  line until staff resolve it. `/activity/dashboard/mode/{id}/switch` stages instead of applying when job
  info is needed and publishes on the new channel for the bot to pick up, same duty `panem_api` already has
  for a dashboard-created character with no bot token of its own.

Verified: new `panem_shared.rp_modes` tests (`mode_switch_needs_job_info`, stage/apply/discard, the
already-pending guard on `check_can_switch_mode`); new `CharacterCog` tests for the approval-embed
idempotency and the approve/decline handlers, including one asserting decline leaves the character row
intact; new dashboard-route tests for the staged-switch and switching-to-story-never-needs-job-info paths;
fixed `TestDashboardRpMode.test_switch_moves_to_the_new_mode`'s fixture (it seeded a Simulation-mode
character with no `job_title` at all, a state `create_character` itself would never produce). `ruff check`/
`ruff format --check` clean on every touched file (both already carried unrelated pre-existing drift
elsewhere in the repo, confirmed via `git stash`). `mypy packages/panem_shared/src packages/panem_sim/src`
(the CI-gated pair) clean; the `panem_bot`/`panem_api` baseline (not CI-gated, NFR-11 only covers the first
two) grew from 165 to 183 errors, entirely the same three pre-existing categories every other cog method
already carries (`"Bot" has no attribute "db"`, missing generic type args on `discord.ui.Button`/`Select`,
missing `var-annotated` on a modal's own `TextInput` fields) applied to the new code, none a new category.
Full suite: **1441 passed**, 1 pre-existing unrelated failure (`test_serves_the_vendored_discord_sdk_not_a_
cdn_url`, confirmed failing identically on a clean checkout via `git stash`). Migration verified
up/down/up.

## Fixing /engage end, the black market's dead-end location, and NPC opinions from roleplay itself

Four independent bugs/gaps reported from live play, none related to each other except that all four sat in
the "NPC social/crime" corner of the codebase.

**1. `/engage end` refused the person who started it, and staff got a 500.** Two separate bugs, same
command. First: `EngagementCog._actor_can_manage` only ever checked `Scene.created_by_character_id` --
but `/engage start` run inside an *existing* scene (an ambient thread, an open `/scene`) attaches NPCs to
a scene it didn't create, and never touched that column. The character who just ran `/engage start` there
had no way to satisfy the "only the person who started it or staff can end it" check at all. Fixed by
recording `participants["engaged_by_character_id"]` on every `/engage start` (`cogs/engagements.py`) and
having `_actor_can_manage` accept either that or `created_by_character_id`. Second, and the actual cause
of staff's own `discord.errors.NotFound: 404 ... Unknown interaction`: `end` called `_persist_engagement_
summaries` (one LLM call per joined NPC, via `dialogue.summarize_engagement`) *before* ever responding to
the interaction -- routinely well past Discord's 3-second response window, so the token had already expired
by the time `interaction.response.send_message` ran, for anyone, staff included. Fixed by deferring
(`interaction.response.defer(ephemeral=True, thinking=True)`) as the very first line and switching every
reply in the command to `interaction.followup.send`, which has a 15-minute window instead.

**2. The black market's own designated location was unreachable to ordinary players.** `/blackmarket`
requires standing at the district's `kind: outskirts` location, at night (a deliberate earlier redesign --
see "Gate the outskirts and black market to night" in git history -- moving it off the old per-district
"Underground Exchange"-style market location). What broke it: every district's `outskirts` location was
still authored `restricted: true` with two `access_jobs` catalog ids left over from before the job system
became free-typed text (e.g. District 1's outskirts required `job_title` to be exactly `"d1_jeweler"` or
`"d1_polisher"` -- two catalog ids with nothing to do with the outskirts thematically, just whatever two
jobs existed there under the old system). Since `Character.job_title` is free-typed now, `has_location_
access` (`panem_shared/location_access.py`) would essentially never match, and no non-staff, non-Positioned
Life/Simulation character could satisfy it -- the *only* place `/blackmarket`/`/poach` can be reached from
was gated behind an access rule nobody could ever pass, on top of (redundantly, since it's already the
real gate) the night-only check. Fixed by exempting `kind: outskirts` from `access_jobs`/`restricted`
entirely in `has_location_access` -- the day/night check its callers (`travel.check_can_travel`, `proxy.
check_can_proxy`) already run *is* its access control by design, not an addition to a job gate. No content
file changes needed; this was a code-level bug, not a missing location (every district already has one).

**3. Nobody could learn who a district's black-market fence NPC is.** `NpcContent.black_market_contact`
existed and drove `/blackmarket` itself (`resolve_fence`), but nothing anywhere -- not `/resident profile`,
not the dashboard's Social tab -- ever surfaced which NPC that flag actually points to; a player had no
in-game way to find out short of staff telling them out of character. Added `is_black_market_contact` to
both `ResidentSummary` (the Residents/Social tab's list) and `ResidentProfileResponse` (an NPC's own
dossier), computed server-side and `True` only once the viewer is allowed to know it: staff always (a real
`discord_staff.fetch_is_staff` check, not a cosmetic client flag -- this gates real information disclosure,
unlike `/identify`'s own UI-only `is_staff`), or a player whose relationship with that specific NPC has
already reached `blackmarket.TRUSTED_STANCES` (Likes/Loves) -- the exact same threshold `/blackmarket`
itself requires before trading, so "you can tell who they are" and "they'll actually trade with you" land
at the same moment. `build_residents_router` gained `discord_token`/`discord_guild_id`/`staff_role_id`
parameters (wired through `app.py`, mirroring `build_district_lore_router`'s own pattern) to make that staff
check. `social.js`'s Residents table gets a small "Black Market" badge next to a revealed contact's name,
and the dossier modal gets an equivalent "Known black market contact" row.

**4. Roleplay itself never touched NPC opinions.** `RelationshipRow` only ever moved from `panem_sim.
systems.social`'s passive per-tick "shared a location" proximity nudge -- nothing read what a player's
message to or around an NPC actually *said*. Added `panem_shared/hostility.py`: `is_hostile_action(message)`,
a plain case-insensitive substring check against a curated list of hostile-action roots (spit, hit, punch,
attack, stab, strangle, harm, annoy, harass, threaten, ...) -- the same "keyword match standing in for a
real detector" tradeoff `panem_shared.lore.match_history_entries` already makes, not an LLM sentiment call.
Wired into `ProxyCog.post_engagement_replies` (`cogs/proxy.py`), the one function that already handles
*every* RP message an NPC might react to -- both `/talk` (which calls this directly, per its own docstring)
and ordinary scene/engagement proxying -- so one hook covers both surfaces. Targeting reuses `engagements_
svc.npcs_that_should_reply`'s existing `speaking` list (every joined NPC in a strict 1:1, only the ones
actually named otherwise) rather than a second name-matching pass, and runs independently of whether the
NPC ends up actually replying (low stamina, etc.) -- being spit on doesn't require the NPC to have enough
stamina left to talk back. A hostile hit knocks `RelationshipRow.affinity` down by a flat `HOSTILE_ACTION_
AFFINITY_PENALTY` (40, bigger than a passing proximity nudge and even the steal-victim penalty below --
"significantly lower" reads as one sharp hit, not a slow drift) via the new shared `relationships.apply_
affinity_delta`, which also recomputes `stance` immediately (so `/resident profile`/the dashboard reflect it
right away, not on some future tick the two happen to share a location again) and bumps `interaction_
count`.

Separately, but in the same relationship-mutation code: **stealing from an NPC now crashes that
relationship to the floor immediately**, not a smaller decrement only on getting caught. Previously a
*successful, undetected* `/steal` against an NPC left the relationship completely untouched (they never
"knew"), and only the caught branch docked a modest `REP_STEAL_CAUGHT_VICTIM_PENALTY` (20). Per the
explicit ask ("if a player steals from an NPC, the NPC should immediately lower their friendship meter to
the lowest possible") this doesn't hinge on the NPC consciously noticing: a clean success now calls the new
`relationships.crash_to_hated` (sets `affinity` to the new `constants.AFFINITY_FLOOR`, -100, well past the
`hates` threshold, and forces `interaction_count` up to `STANCE_MIN_INTERACTIONS_EXTREME` so `stance` reads
`hates` immediately rather than the lesser `dislikes` a low interaction count would otherwise cap it at) --
and getting caught red-handed uses the exact same call rather than a smaller one, since being caught is at
least as damning as a theft the victim never noticed. The alert-but-escaped branch (nothing was actually
taken, and Spec's own existing test asserts it "changes nothing") is deliberately untouched -- "stealing
from" reads as a completed act, not a failed attempt.

Both the hostile-action penalty and the steal-crash share the same two new `panem_shared.relationships`
helpers, added alongside `panem_sim.systems.social`'s own `_stance_for` moving there (`stance_for_affinity`,
re-exported back into `social.py` as a local alias) so every affinity mutator in the codebase classifies a
stance the identical way: `get_or_create_relationship` (the get-or-create pattern `stealing.py` and this new
code both needed, previously duplicated inline) and `apply_affinity_delta`/`crash_to_hated`.

Verified: new `panem_shared.location_access`/`proxy_service` tests for the outskirts exemption (including
confirming an *ordinary* `restricted` location, e.g. a job-gated workplace, is untouched -- this only
carves out `kind: outskirts` specifically); new `EngagementCog._actor_can_manage` test covering the exact
previously-broken case (an ambient scene with no `created_by_character_id` but an `engaged_by_character_id`
recorded); new `panem_shared.relationships` tests for `stance_for_affinity`/`get_or_create_relationship`/
`apply_affinity_delta`/`crash_to_hated`; new `panem_shared.hostility` tests; new `TestDashboardResidentsBlack
MarketReveal` tests covering the stranger/trusted/staff paths on both the list and profile endpoints, plus a
control case confirming a non-contact NPC never reveals regardless of staff; updated `test_stealing_
service.py` assertions for the new crash-to-floor behavior on both the clean-success and caught paths.
`ASSET_VERSION` bumped 48 -> 49 (`tabs/social.js` changed) and `index.html`'s `app.js?v=` 49 -> 50 (`app.js`'s
own `ASSET_VERSION` edit). `ruff check`/`ruff format --check` clean on every touched file (the one drift this
session introduced, in `cogs/engagements.py`, was fixed by running `ruff format`; pre-existing drift
elsewhere, e.g. `dashboard_routes.py`/`test_api_app.py`, confirmed via `git stash` and left alone). `mypy
packages/panem_shared/src packages/panem_sim/src` (CI-gated) clean; the `panem_bot`/`panem_api` baseline
(not CI-gated) stayed at exactly 182 errors before and after (confirmed via `git stash`) -- this pass added
no new mypy errors at all, gated or not. Full suite: **1467 passed**, the same 1 pre-existing unrelated
failure as every prior pass (`test_serves_the_vendored_discord_sdk_not_a_cdn_url`).
