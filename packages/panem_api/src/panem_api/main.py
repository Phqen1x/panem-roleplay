"""`python -m panem_api` entrypoint."""

from __future__ import annotations

from pathlib import Path

import redis.asyncio as redis
import uvicorn

from panem_api.app import create_app
from panem_shared.content.loader import load_content
from panem_shared.logging import configure_logging
from panem_shared.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = REPO_ROOT / "data"


def main() -> None:
    configure_logging(component="api")
    settings = get_settings()
    content = load_content(DATA_DIR)
    redis_client: redis.Redis = redis.from_url(settings.redis_url, decode_responses=True)

    app = create_app(
        content=content,
        redis_client=redis_client,
        discord_client_id=settings.discord_client_id,
        discord_client_secret=settings.discord_client_secret,
    )
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
