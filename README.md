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
  `name` field in its YAML) rather than a stored role id, so the district
  YAMLs and `setup_guild.py` must stay in agreement on naming.
- Item-gated `restricted` locations (`access_items`) always deny access
  for now, since inventories don't exist until Phase 2 — only
  `access_jobs` and `is_victor` grant access in Phase 0.
