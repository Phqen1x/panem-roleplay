"""Server-side Discord REST helpers backing the dashboard's staff-only
surface (`build_staff_router` in `dashboard_routes.py`). `panem_api` has no
gateway connection the way `panem_bot` does -- `PanemBot.is_staff` checks a
live `discord.Member`'s cached roles -- so this hits Discord's REST API
directly with the same bot token, same as the one-off webhook-edit calls
already in `app.py`."""

from __future__ import annotations

import httpx

from panem_shared.logging import get_logger

logger = get_logger(component="api")

DISCORD_API_BASE = "https://discord.com/api/v10"


async def fetch_is_staff(
    discord_id: int, *, bot_token: str, guild_id: int, staff_role_id: int
) -> bool:
    """Mirrors `PanemBot.is_staff` for a process with no gateway connection:
    fetches the member's role ids from Discord's REST API and checks for
    `staff_role_id` among them. Any of these being unconfigured, or the
    lookup failing/404ing (not a member of the guild), reads as "not
    staff" -- fails closed, and is the actual enforcement point (never
    trust a client-supplied flag for a privilege decision -- see
    `build_staff_router`'s own docstring)."""
    if not bot_token or not guild_id or not staff_role_id:
        return False
    url = f"{DISCORD_API_BASE}/guilds/{guild_id}/members/{discord_id}"
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(url, headers={"Authorization": f"Bot {bot_token}"})
        except httpx.HTTPError as exc:
            logger.warning("staff_role_lookup_failed", discord_id=discord_id, error=str(exc))
            return False
    if response.status_code != 200:
        return False
    role_ids = {int(r) for r in response.json().get("roles", [])}
    return staff_role_id in role_ids


async def post_staff_log(*, channel_id: int, bot_token: str, content: str) -> None:
    """Best-effort: posts a one-line audit summary to the same log channel
    `panem_bot`'s `log_staff_action` uses, so a dashboard-issued staff
    action shows up in the same place a Discord-issued one does. A missing
    channel_id/token or a failed request just means no log line -- the
    `StaffAction` DB row every staff route writes is the durable record,
    same "logging must never fail the action" posture as `_post_to_log_
    channel` on the bot side."""
    if not bot_token or not channel_id:
        return
    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                url, json={"content": content}, headers={"Authorization": f"Bot {bot_token}"}
            )
        except httpx.HTTPError as exc:
            logger.warning("staff_log_post_failed", channel_id=channel_id, error=str(exc))
            return
    if response.status_code >= 300:
        logger.warning("staff_log_post_failed", channel_id=channel_id, status=response.status_code)
