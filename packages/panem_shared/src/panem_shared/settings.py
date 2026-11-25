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
    llm_base_url: str = "http://127.0.0.1:13305/v1"
    llm_model: str = "panem-omni"
    llm_api_key: str = ""
    llm_timeout_ms: int = 60000
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

    approval_channel_id: DiscordId = 0
    log_channel_id: DiscordId = 0

    # Comma-separated Discord role snowflake ids (e.g. "111,222") -- who
    # counts as "Panem staff" (`PanemBot.is_staff`, `discord_staff.
    # fetch_is_staff`) is no longer a single auto-created role: an admin
    # names whichever existing role(s) should count, same shape as
    # `donor_role_ids` below for the same reason (more than one tier --
    # e.g. "Admin" and "Moderator" -- commonly needs to count). See
    # `staff_role_id_set()`.
    staff_role_ids: str = ""
    # Comma-separated Discord *user* snowflake ids -- lets an admin grant
    # staff-ness to a specific member directly, with no role at all
    # (someone trusted without wanting a whole role's other side effects,
    # or before any staff role exists on a fresh server). See
    # `staff_user_id_set()`. Checking this needs no Discord API call at
    # all (unlike a role check) since the caller's own id is already
    # known, so `discord_staff.fetch_is_staff` tries this set first.
    staff_user_ids: str = ""

    # The role the Activity's Staff tab grants/revokes when staff flip a
    # channel's lock/unlock toggle there (`StaffChannelAccessEntry` in
    # `panem_api.dashboard_routes`) -- deliberately a single configured
    # role rather than always `@everyone`, since "open to players" means
    # a different role per server (a general roleplaying role from
    # onboarding, say). Unset (0) means the toggle is disabled in the
    # Activity; staff can still grant/revoke any role, including this one,
    # per-channel via Discord's own `/staff channel lock`/`unlock`
    # (`panem_bot.cogs.staff.StaffCog`), which always takes an explicit
    # role rather than reading this setting. Granting this role only ever
    # gives *view* access to a channel -- posting is per-user only, granted
    # automatically on character approval to that character's own district
    # forum (`panem_bot.cogs.characters._handle_approve`).
    roleplay_role_id: DiscordId = 0

    # Comma-separated Discord role snowflake ids (e.g. "111,222") -- a
    # donor perk is commonly granted by more than one role tier (multiple
    # donation levels), so this is a list rather than a single `DiscordId`
    # field. Whoever holds at least one of these can customize the
    # Activity dashboard's background/accent colors (`POST /activity/
    # dashboard/theme`); see `donor_role_id_set()`.
    donor_role_ids: str = ""

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

    # Where `panem_bot` reaches `panem_api` server-to-server (persisting an
    # uploaded `/character avatar` attachment's bytes, `panem_shared.
    # avatars`) -- deliberately separate from `activity_public_url`, which
    # is what a Discord *client* needs and may be a tunnel/CDN domain this
    # container can't necessarily reach itself. `deploy/docker-compose.yml`
    # sets this to "http://api:8000" (the compose service name); left
    # unset, `resolved_api_internal_url()` assumes every process is on the
    # same host and falls back to `api_port` on localhost.
    api_internal_url: str = ""

    def resolved_api_internal_url(self) -> str:
        return self.api_internal_url or f"http://localhost:{self.api_port}"

    def staff_role_id_set(self) -> frozenset[int]:
        """Parses `staff_role_ids` ("111,222" -> {111, 222}); blank entries
        (an empty string, a trailing comma) are dropped rather than
        raising, same posture as `donor_role_id_set()` below -- `.env`
        shipping this unset means "no staff roles configured", not an
        error."""
        return frozenset(
            int(part.strip()) for part in self.staff_role_ids.split(",") if part.strip()
        )

    def staff_user_id_set(self) -> frozenset[int]:
        """Parses `staff_user_ids` the same way `staff_role_id_set` parses
        `staff_role_ids`."""
        return frozenset(
            int(part.strip()) for part in self.staff_user_ids.split(",") if part.strip()
        )

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
