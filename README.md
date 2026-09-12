# Panem: The Long Year

A persistent, NPC-driven Discord roleplay world that runs between Hunger
Games sessions. Built from `panem-long-year-build-plan-python.md` (the
Plan) and `panem-long-year-spec.md` (the Spec, which wins on any
disagreement with the Plan).

This repository implements **Phase 0 — Foundation** (character creation and
staff approval, Discord Forum-based scenes, and character proxying),
**Phase 1 — World Simulation** (Plan §4: a deterministic tick loop, NPC
movement, ambient narration, intra-district `/travel`/`/where`), and
**Phase 2 — Economy** (Plan §5.1–§5.4/5.6: nightly hunger/health, job
shifts, `/work`, `/job list|apply|quit`, and now district-level supply/
demand pricing, exports, quotas, shopkeeper restocking, and `/market
prices|buy|sell`/`/inventory`). Cross-district travel (Plan §5.5,
`/travel district:<id>`, tickets/transit/visitor roles) and
`scripts/calibrate.py`'s real implementation are the one piece of Phase 2
still outstanding. Phases 3-6 (NPC minds, crises, the Activity, and LLM
dialogue) are scaffolded as empty packages and land in that order — see
the Plan for the full roadmap.

## Layout

```
packages/
  panem_shared/   data model (SQLAlchemy), content YAML schemas/loaders, settings, enums, world event types
  panem_bot/      the discord.py process: commands, proxying, scenes, staff tools, narration, travel, jobs
  panem_sim/      world tick loop, NPC movement (Phase 1), needs/jobs/economy (Phase 2); cross-district travel lands later
  panem_api/      FastAPI REST/WebSocket bridge for the Activity (Phase 5+, not yet implemented)
data/             districts, goods, jobs, routes (content YAML, validated at boot)
migrations/       Alembic migrations
scripts/          setup_guild.py (Phase 0) plus stubs for later-phase scripts
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
  `access_jobs` and `is_victor` grant access in Phase 0.
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
  a district's `#board` channel by `ChannelKind.BOARD` — `scripts/
  setup_guild.py` creates a read-only board channel per district (see the
  Milestone D notes below); this path sat wired-but-inert until the
  economy system started actually emitting `Bulletin`s.
- **`/travel` and `/where` take a character name** (`own_approved`
  autocomplete), matching every other character-scoped command in the
  bot, rather than resolving an "active" character from `/rp`'s
  thread-session mechanism — that exists specifically for "who is
  speaking in this thread," a different concern from "where is my
  character." Both are intra-district only; cross-district travel
  (tickets, transit ticks, visitor roles — FR-LOC-7/8/9) is Phase 2 scope.

## Notes on this Phase 2 build

- **Needs, jobs, shifts, and now the economy (markets/quotas/exports/
  shopkeepers) are built — cross-district travel is the one piece left.**
  `panem_sim/systems/crisis.py`, `social.py`, and `memory.py` are still
  no-op stubs (Phase 3+ scope); `/travel district:<id>` (tickets, transit,
  visitor roles, FR-LOC-7/8/9) isn't built yet, and `scripts/calibrate.py`
  (the 12-month headless economy check) is still the stub that raises
  "not implemented" — see the Milestone D notes below for what *is* built.
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
- **The daily `Bulletin` (FR-ECO-8) needed a real `#board` channel to post
  to, which `scripts/setup_guild.py` never created** -- found live, after
  the economy system started actually emitting `Bulletin`s: every day
  boundary, `panem_bot/narrator.py` looked up each district's
  `ChannelKind.BOARD` row and either found none (a guild set up before
  this milestone) or, if one existed anyway, a channel id Discord no
  longer recognized, and logged an `Unknown Channel` failure per district
  instead of posting. `setup_guild.py` now creates a read-only
  `#district-N-board` (`#capitol-board` for the Capitol) per district
  alongside its forum, reconciled by name on every run the same way the
  forum/ambient-thread channels already were -- re-running it against a
  guild that hit this fixes it, no manual DB cleanup needed.
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
- **Cross-district travel (`/travel district:<id>`, FR-LOC-7/8/9) and
  `scripts/calibrate.py`'s real implementation are not built in this
  pass** -- the remaining pieces of Plan §5.5/5.6 to complete Phase 2.

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
