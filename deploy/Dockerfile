FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
ENV UV_PYTHON_PREFERENCE=only-managed \
    UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY alembic.ini ./
COPY migrations ./migrations

RUN uv python install 3.14 && uv sync --frozen --no-dev --package panem-bot

COPY data ./data

CMD ["uv", "run", "python", "-m", "panem_bot.main"]
