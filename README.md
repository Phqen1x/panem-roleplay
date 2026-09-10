# Panem: The Long Year

A persistent, NPC-driven Discord roleplay world that runs between Hunger
Games sessions. Built from `panem-long-year-build-plan-python.md` (the
Plan) and `panem-long-year-spec.md` (the Spec, which wins on any
disagreement with the Plan).

This repository currently implements **Phase 0 — Foundation**: character
creation and staff approval, Discord Forum-based scenes, and character
proxying. Phases 1-6 (world simulation, economy, NPC minds, crises, the
Activity, and LLM dialogue) are scaffolded as empty packages and land in
that order — see the Plan for the full roadmap.

## Layout

```
packages/
  panem_shared/   data model (SQLAlchemy), content YAML schemas/loaders, settings, enums
  panem_bot/      the discord.py process: commands, proxying, scenes, staff tools (Phase 0)
  panem_sim/      world tick loop, economy, NPC AI, dialogue (Phase 1+, not yet implemented)
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
- Item-gated `restricted` locations (`access_items`) always deny access
  for now, since inventories don't exist until Phase 2 — only
  `access_jobs` and `is_victor` grant access in Phase 0.
- **Character age**: reaping-eligible districts (1-12) are capped at age 18;
  only the Capitol (district 0) may create adult characters, up to 80.
- **Jobs are editable in Discord**, not just in `data/jobs.yaml`: `/staff
  job set <job_id> <district> <json>` adds or overrides a job (same schema
  as `jobs.yaml`, validated the same way, including that its `workplace`
  is a real location in that district), `/staff job remove <job_id>` takes
  one out (whether it came from YAML or a prior override), `/staff job
  list <district>` and `/staff job show <job_id>` inspect the current
  merged view. Changes take effect immediately, no restart needed — every
  job lookup in the bot goes through `panem_bot.services.jobs`, which
  layers `job_overrides` (Postgres) on top of `jobs.yaml` at read time.
- **Autocomplete** replaces free typing everywhere a command takes a
  character name, job id, or district: `/character edit|retire|status|
  avatar|tag`, `/staff kill|note|job set|job remove|job show|job list`, and
  `/scene start|move|invite` all suggest matching options as you type,
  scoped to what's relevant (e.g. `/character edit` only offers your own
  pending submissions). See `panem_bot/autocomplete.py` for the
  shared callbacks; district/scene-scoped ones live next to their commands
  in `cogs/scenes.py`.
- `/staff delete_pending <character> [reason]` removes a pending
  application outright (optionally DMing the applicant why), for
  submissions staff want gone rather than rejected-and-kept.
- **Character names must be unique** (case-insensitively), enforced both
  in the bot and by a DB-level unique index — see "Upgrading past
  duplicate character names" below if you're updating an existing guild.

## Upgrading past duplicate character names

The migration that adds the name-uniqueness index (`7116c3213f6e`) will
fail to apply if your database already has two non-rejected characters
sharing a name (case-insensitively) — this can only happen from before
this change existed. Find and resolve them *before* running `alembic
upgrade head`:

```sql
-- List every name collision, oldest id first, excluding rejected:
SELECT lower(name) AS name_ci,
       array_agg(id ORDER BY id)     AS ids,
       array_agg(status ORDER BY id) AS statuses
FROM characters
WHERE status <> 'rejected'
GROUP BY lower(name)
HAVING count(*) > 1;
```

For each group, keep one and resolve the rest:
- **Pending duplicates**: use `/staff delete_pending <character>` in
  Discord. If two pending rows have the *exact* same name (so the
  autocomplete can't tell them apart), delete the newer one directly
  instead: `DELETE FROM characters WHERE id = <id>;`.
- **Approved/retired/dead duplicates**: there's no in-bot rename for
  these, so rename the one you're keeping second directly:
  `UPDATE characters SET name = '<new unique name>' WHERE id = <id>;`
  (tell the player, since it changes how they proxy).

Then run `uv run alembic upgrade head` as usual. Going forward the bot
refuses same-name submissions and renames itself, so this is a one-time
cleanup.
