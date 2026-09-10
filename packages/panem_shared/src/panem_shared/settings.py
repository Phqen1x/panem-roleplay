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
    discord_guild_id: DiscordId = 0

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

    staff_role_id: DiscordId = 0
    approval_channel_id: DiscordId = 0
    log_channel_id: DiscordId = 0

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
    max_characters_per_user: int = 3

    world_seed: str = Field(default="panem-long-year")

    games_api_key: str = ""

    def role_id_override_for_district(self, district_id: int) -> int:
        """0 means unset (auto-manage by name); see `*_role_id` fields above."""
        if district_id == 0:
            return self.capitol_role_id
        return int(getattr(self, f"district_{district_id}_role_id", 0))


def get_settings() -> Settings:
    """Load settings fresh from the environment.

    Not cached: tests construct their own `Settings(...)` instances freely,
    and content hot-reload (`NFR-12`) should not require a process restart
    to see updated environment values either.
    """
    return Settings()
