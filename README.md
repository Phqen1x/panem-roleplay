# Panem: The Long Year

A persistent, NPC-driven Discord roleplay world that runs between Hunger
Games sessions. Built from `panem-long-year-build-plan-python.md` (the
Plan) and `panem-long-year-spec.md` (the Spec, which wins on any
disagreement with the Plan).

This repository implements **Phase 0 — Foundation** (character creation and
staff approval, Discord Forum-based scenes, and character proxying),
**Phase 1 — World Simulation** (Plan §4: a deterministic tick loop, NPC
movement, ambient narration, intra-district `/travel`/`/where`), and the
first half of **Phase 2 — Economy** (Plan §5.1–§5.3: nightly hunger/health,
job shifts, `/work`, `/job list|apply|quit`, `/tesserae claim`). Markets,
shopkeepers, quotas, and cross-district travel (Plan §5.4–§5.6) don't have
runtime logic yet, though their schema and content do. Phases 3-6 (NPC
minds, crises, the Activity, and LLM dialogue) are scaffolded as empty
packages and land in that order — see the Plan for the full roadmap.

## Layout

```
packages/
  panem_shared/   data model (SQLAlchemy), content YAML schemas/loaders, settings, enums, world event types
  panem_bot/      the discord.py process: commands, proxying, scenes, staff tools, narration, travel, jobs
  panem_sim/      world tick loop, NPC movement (Phase 1), needs/jobs (Phase 2 partial); markets land later
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
  a district's `#board` channel by `ChannelKind.BOARD`, but
  `scripts/setup_guild.py` doesn't create that channel yet — deliberately
  deferred there to Phase 2 economy content — and no Phase 1/2-stub system
  emits a `Bulletin` yet either, so that path is wired and tested but
  inert until Phase 2 lands.
- **`/travel` and `/where` take a character name** (`own_approved`
  autocomplete), matching every other character-scoped command in the
  bot, rather than resolving an "active" character from `/rp`'s
  thread-session mechanism — that exists specifically for "who is
  speaking in this thread," a different concern from "where is my
  character." Both are intra-district only; cross-district travel
  (tickets, transit ticks, visitor roles — FR-LOC-7/8/9) is Phase 2 scope.

## Notes on this Phase 2 (partial) build

- **Only Plan §5.1–§5.3 (needs, jobs, shifts) — not the whole of Phase 2.**
  `panem_sim/systems/economy.py`, `crisis.py`, `social.py`, and `memory.py`
  are still no-op stubs; markets, shopkeepers, quotas/exports, and
  cross-district travel (`/travel district:<id>`, tickets, transit) aren't
  built. `scripts/calibrate.py` (the 12-month headless economy check) is
  still the stub that raises "not implemented".
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
  "completes" it in place each phase boundary
  (`NPC_JOB_COMPLETION_PROB`, also a placeholder), feeding `Npc.money`
  only — no district production yet, since that's the economy system's
  job (Milestone D).
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
- **Tesserae (`/tesserae claim`, FR-ECO-7)** is once per real day (a
  Redis key with a 24h TTL, not tied to the sim's tick clock — a player
  action, not a game-time one) and refuses Capitol characters
  (`CAPITOL_DISTRICT_ID`); the payout (`TICKET_BASE` money, `+1`
  `tesserae_count`) is the existing spec-sourced tunable, not a guess.
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
