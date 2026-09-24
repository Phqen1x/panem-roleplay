"""Process configuration, loaded from environment / .env (Plan §0)."""

from __future__ import annotations

from typing import Annotated

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _empty_str_to_zero(value: object) -> object:
    """`.env` ships Discord snowflake IDs unset (`FOO_ID=`), which parses as
    `""`, not absent -- pydantic would otherwise reject that as an invalid
    int rather than falling back to the field default."""
    return 0 if value == "" else value


DiscordId = Annotated[int, BeforeValidator(_empty_str_to_zero)]


class Settings(BaseSettings):
    """Shared configuration for all panem processes.

    Every process (`panem_bot`, `panem_sim`, `panem_api`) loads the same
    `.env` file; a process that doesn't need a given field simply ignores
    it, so this stays one flat model rather than per-process subclasses.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_token: str = ""
    discord_client_id: str = ""
    # Only panem_api's Activity OAuth token exchange (`POST /activity/token`)
    # reads this -- the bot process authenticates with `discord_token`
    # instead, and never needs an OAuth client secret. Get this from the
    # Developer Portal's OAuth2 page, same app as `discord_client_id`.
    discord_client_secret: str = ""
    discord_guild_id: DiscordId = 0
    # Every command sync is one call to Discord's (tightly rate-limited)
    # guild command-overwrite endpoint. Restarting the bot repeatedly during
    # local dev re-syncs every time even though the commands haven't
    # changed, which can trip that rate limit. Set this to false after your
    # first successful sync and flip it back only when you've actually
    # changed a command's signature.
    discord_sync_commands: bool = True

    database_url: str = "postgresql+asyncpg://panem:panem@localhost:5432/panem"
    redis_url: str = "redis://localhost:6379/0"

    tick_interval_seconds: int = 600

    dialogue_provider: str = "template"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout_ms: int = 8000
    llm_max_concurrent: int = 4
    llm_min_importance: int = 2
    llm_json_mode: bool = False
    llm_daily_token_budget: int = 0

    # Lemonade OmniModel (lemonade/README.md). `lemonade_home` is only needed
    # when running Embeddable Lemonade from this checkout / a package; a
    # system-wide lemonade-server is reached through llm_base_url alone.
    lemonade_profile: str = "lite"
    lemonade_home: str = ""
    lemonade_embeddable_version: str = "11.9.0"

    staff_role_id: DiscordId = 0
    approval_channel_id: DiscordId = 0
    log_channel_id: DiscordId = 0

    # Comma-separated Discord role snowflake ids (e.g. "111,222") -- a
    # donor perk unlike `staff_role_id` above is commonly granted by more
    # than one role tier (multiple donation levels), so this is a list
    # rather than a single `DiscordId` field. Whoever holds at least one
    # of these can customize the Activity dashboard's background/accent
    # colors (`POST /activity/dashboard/theme`); see `donor_role_id_set()`.
    donor_role_ids: str = ""

    # Optional pre-existing role per district (+ the Capitol) that
    # scripts/setup_guild.py should use instead of creating/finding a role
    # by name. Unset (0) means "auto-manage by name", the default behavior.
    capitol_role_id: DiscordId = 0
    district_1_role_id: DiscordId = 0
    district_2_role_id: DiscordId = 0
    district_3_role_id: DiscordId = 0
    district_4_role_id: DiscordId = 0
    district_5_role_id: DiscordId = 0
    district_6_role_id: DiscordId = 0
    district_7_role_id: DiscordId = 0
    district_8_role_id: DiscordId = 0
    district_9_role_id: DiscordId = 0
    district_10_role_id: DiscordId = 0
    district_11_role_id: DiscordId = 0
    district_12_role_id: DiscordId = 0

    scene_auto_archive_minutes: int = 1440
    max_active_scenes_per_district: int = 60
    max_characters_per_user: int = 1

    world_seed: str = Field(default="panem-long-year")

    games_api_key: str = ""

    # Every `main.py` (bot/sim/api) defaults `DATA_DIR` to `data/` found by
    # walking up from its own installed location, which only lines up with
    # the real content dir when the process runs from an editable/dev-mode
    # checkout (true for `uv run` and the Docker image's `uv sync`, both of
    # which install this workspace editable). A packaged, non-editable
    # install (e.g. the snap's `uv sync --no-editable` venv, whose
    # `panem_*` modules land in `site-packages` with no relation to the
    # original checkout) has no such directory to walk up to, so it must
    # set this explicitly instead (the snap wrapper scripts do, to
    # `$SNAP/data`). Empty keeps every existing deployment's behavior
    # unchanged.
    data_dir: str = ""

    # Same story as `data_dir`, for `panem_api`'s `static/` tree specifically
    # -- but for a different reason: `static/` *is* package data (shipped
    # inside `panem_api`'s own wheel, so it resolves fine even from
    # `site-packages`), the problem is that it isn't writable there. Staff
    # layer-image uploads and `district_mottos.json` need a real writable
    # directory (`build_staff_router`'s `static_dir` param), which a
    # read-only install location (a strict-confinement snap's squashfs
    # `$SNAP`) can never be. Empty keeps writing into the bundled `static/`
    # tree itself, as every non-snap deployment already does.
    static_uploads_dir: str = ""

    # panem_api (Phase 5, Plan §8): the REST/WebSocket bridge for the
    # Activity's live map, plus a static frontend and the OAuth token
    # exchange it needs (`GET /activity/config`, `POST /activity/token`).
    # The data endpoints themselves (`/districts*`) still enforce no auth
    # of their own -- anyone who can reach the process can read them --
    # this was never verified against a live Discord Activity install
    # (no credentials/flow this session could test against), so treat it
    # as an explicit, documented gap (see the README) rather than
    # unverified placeholder access control.
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # `panem_bot`'s `/work` reads this to link to `panem_api`'s Activity
    # frontend for its minigame (Minesweeper today) -- an externally
    # reachable base URL (e.g. https://yourdomain.example or a tunnel URL),
    # NOT `api_host`/`api_port`, which are only a bind address. Leave unset
    # to keep `/work`'s classic option-select flow instead (no minigame,
    # no dependency on panem_api being reachable from Discord clients).
    activity_public_url: str = ""

    def role_id_override_for_district(self, district_id: int) -> int:
        """0 means unset (auto-manage by name); see `*_role_id` fields above."""
        if district_id == 0:
            return self.capitol_role_id
        return int(getattr(self, f"district_{district_id}_role_id", 0))

    def donor_role_id_set(self) -> frozenset[int]:
        """Parses `donor_role_ids` ("111,222" -> {111, 222}); blank entries
        (an empty string, a trailing comma) are dropped rather than raising,
        since `.env` shipping this unset should mean "no donor roles
        configured" -- the same "empty means disabled" posture `_empty_str_
        to_zero` gives every single-role `*_role_id` field above."""
        return frozenset(
            int(part.strip()) for part in self.donor_role_ids.split(",") if part.strip()
        )


def get_settings() -> Settings:
    """Load settings fresh from the environment.

    Not cached: tests construct their own `Settings(...)` instances freely,
    and content hot-reload (`NFR-12`) should not require a process restart
    to see updated environment values either.
    """
    return Settings()
