"""`python -m panem_api` entrypoint."""

from __future__ import annotations

from pathlib import Path

import redis.asyncio as redis
import uvicorn

from panem_api.app import create_app
from panem_shared.content.loader import load_content
from panem_shared.db.session import make_engine, make_session_factory
from panem_shared.logging import configure_logging
from panem_shared.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[4]


def main() -> None:
    configure_logging(component="api")
    settings = get_settings()
    data_dir = Path(settings.data_dir) if settings.data_dir else REPO_ROOT / "data"
    content = load_content(data_dir)
    redis_client: redis.Redis = redis.from_url(settings.redis_url, decode_responses=True)
    session_factory = make_session_factory(make_engine(settings))

    app = create_app(
        content=content,
        redis_client=redis_client,
        discord_client_id=settings.discord_client_id,
        discord_client_secret=settings.discord_client_secret,
        session_factory=session_factory,
        max_characters_per_user=settings.max_characters_per_user,
        discord_guild_id=settings.discord_guild_id,
        discord_token=settings.discord_token,
        staff_role_id=settings.staff_role_id,
        donor_role_ids=settings.donor_role_id_set(),
        log_channel_id=settings.log_channel_id,
        static_dir=Path(settings.static_uploads_dir) if settings.static_uploads_dir else None,
        activity_public_url=settings.activity_public_url,
    )
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
