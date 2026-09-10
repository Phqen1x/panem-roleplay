"""`python -m panem_bot` entrypoint."""

from __future__ import annotations

from pathlib import Path

from panem_bot.bot import PanemBot
from panem_shared.logging import configure_logging, get_logger
from panem_shared.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = REPO_ROOT / "data"


def main() -> None:
    configure_logging(component="bot")
    logger = get_logger()
    settings = get_settings()

    if not settings.discord_token:
        logger.error("missing_discord_token")
        raise SystemExit("DISCORD_TOKEN is not set (see .env.example)")

    bot = PanemBot(settings=settings, data_dir=DATA_DIR)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
