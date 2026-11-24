"""Server-side Discord REST helpers backing the dashboard's role-gated
surfaces (`build_staff_router`'s staff-only routes and `build_theme_
router`'s donor-only routes, both in `dashboard_routes.py`). `panem_api`
has no gateway connection the way `panem_bot` does -- `PanemBot.is_staff`
checks a live `discord.Member`'s cached roles -- so this hits Discord's
REST API directly with the same bot token, same as the one-off
webhook-edit calls already in `app.py`."""

from __future__ import annotations

from collections.abc import Collection

import httpx

from panem_shared.logging import get_logger
from panem_shared.staff_log import build_staff_log_embed

logger = get_logger(component="api")

DISCORD_API_BASE = "https://discord.com/api/v10"

# Discord's own VIEW_CHANNEL permission bitflag (https://discord.com/
# developers/docs/topics/permissions) that `set_role_channel_locked`/
# `fetch_role_can_view_channel` need -- `panem_api` has no `discord.py`
# dependency (no gateway connection, see this module's docstring), so
# this is a hand-kept constant rather than a `discord.Permissions` flag.
_VIEW_CHANNEL = 1 << 10


async def _fetch_member_role_ids(
    discord_id: int, *, bot_token: str, guild_id: int
) -> set[int] | None:
    """The one REST lookup every role check below shares. `None` means "the
    lookup couldn't be answered" (missing config, a network error, a
    non-200 -- most commonly a 404 for someone who isn't a guild member) --
    every caller treats that the same as "holds no roles", which is what
    makes every check below fail closed rather than fail open."""
    if not bot_token or not guild_id:
        return None
    url = f"{DISCORD_API_BASE}/guilds/{guild_id}/members/{discord_id}"
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(url, headers={"Authorization": f"Bot {bot_token}"})
        except httpx.HTTPError as exc:
            logger.warning("member_role_lookup_failed", discord_id=discord_id, error=str(exc))
            return None
    if response.status_code != 200:
        return None
    return {int(r) for r in response.json().get("roles", [])}


async def fetch_is_staff(
    discord_id: int,
    *,
    bot_token: str,
    guild_id: int,
    staff_role_ids: Collection[int],
    staff_user_ids: Collection[int],
) -> bool:
    """Mirrors `PanemBot.is_staff` for a process with no gateway connection.
    "Panem staff" is an admin-configured set of specific roles and/or
    specific members (`Settings.staff_role_id_set`/`staff_user_id_set`),
    not a single auto-created role -- checking `staff_user_ids` first
    needs no REST call at all, since the caller's own id is already
    known. Unconfigured, or the role lookup failing/404ing (not a member
    of the guild), reads as "not staff" -- fails closed, and is the
    actual enforcement point (never trust a client-supplied flag for a
    privilege decision -- see `build_staff_router`'s own docstring)."""
    if discord_id in staff_user_ids:
        return True
    if not staff_role_ids:
        return False
    role_ids = await _fetch_member_role_ids(discord_id, bot_token=bot_token, guild_id=guild_id)
    return role_ids is not None and not role_ids.isdisjoint(staff_role_ids)


async def fetch_has_any_role(
    discord_id: int, *, bot_token: str, guild_id: int, role_ids: Collection[int]
) -> bool:
    """The donor-gate counterpart to `fetch_is_staff`, generalized to a set
    of role ids rather than one -- a donor perk is commonly granted by more
    than one role tier (`Settings.donor_role_id_set()`). True if the member
    holds at least one of `role_ids`; same fail-closed posture as `fetch_
    is_staff` for an unconfigured/unreachable lookup."""
    if not role_ids:
        return False
    member_role_ids = await _fetch_member_role_ids(
        discord_id, bot_token=bot_token, guild_id=guild_id
    )
    return member_role_ids is not None and not member_role_ids.isdisjoint(role_ids)


async def post_staff_log(
    *,
    channel_id: int,
    bot_token: str,
    action: str,
    staff_discord_id: int,
    target: str,
    payload: dict[str, object] | None = None,
    target_label: str | None = None,
) -> None:
    """Best-effort: posts a human-readable embed audit summary to the same
    log channel `panem_bot`'s `log_staff_action` uses, so a dashboard-
    issued staff action shows up in the same place a Discord-issued one
    does, in the same format (`panem_shared.staff_log.build_staff_log_
    embed` -- this module has no `discord.py` dependency, see its own
    docstring, so it sends that embed dict straight to Discord's REST API
    rather than building a `discord.Embed`). A missing channel_id/token or
    a failed request just means no log line -- the `StaffAction` DB row
    every staff route writes is the durable record, same "logging must
    never fail the action" posture as `_post_to_log_channel` on the bot
    side."""
    if not bot_token or not channel_id:
        return
    embed = build_staff_log_embed(
        action=action,
        staff_discord_id=staff_discord_id,
        target=target,
        payload=payload,
        target_label=target_label,
    )
    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                url, json={"embeds": [embed]}, headers={"Authorization": f"Bot {bot_token}"}
            )
        except httpx.HTTPError as exc:
            logger.warning("staff_log_post_failed", channel_id=channel_id, error=str(exc))
            return
    if response.status_code >= 300:
        logger.warning("staff_log_post_failed", channel_id=channel_id, status=response.status_code)


async def fetch_role_can_view_channel(
    channel_id: int, role_id: int, *, bot_token: str
) -> bool | None:
    """Whether `role_id` currently has an explicit `allow` for VIEW_CHANNEL
    on `channel_id` -- the Activity Staff tab's "locked"/"unlocked" state
    for a channel, mirroring what `panem_bot.cogs.staff.StaffCog.
    channel_unlock`/`channel_lock` itself sets. `None` means the lookup
    couldn't be answered (missing config, a network error, an unknown
    channel) -- the caller shows "unknown" rather than guessing either
    way."""
    if not bot_token or not channel_id or not role_id:
        return None
    url = f"{DISCORD_API_BASE}/channels/{channel_id}"
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(url, headers={"Authorization": f"Bot {bot_token}"})
        except httpx.HTTPError as exc:
            logger.warning("channel_lookup_failed", channel_id=channel_id, error=str(exc))
            return None
    if response.status_code != 200:
        return None
    for overwrite in response.json().get("permission_overwrites", []):
        # type 0 == role overwrite (1 == member) -- id/allow/deny all
        # arrive as strings since Discord snowflakes/bitfields can exceed
        # a JS/double-precision-safe integer.
        if overwrite.get("type") == 0 and int(overwrite.get("id", 0)) == role_id:
            return bool(int(overwrite.get("allow", 0)) & _VIEW_CHANNEL)
    return False


async def set_role_channel_locked(
    channel_id: int,
    role_id: int,
    *,
    bot_token: str,
    locked: bool,
) -> bool:
    """Grants/revokes `role_id`'s *view-only* access to `channel_id` -- the
    Activity Staff tab's counterpart to `/staff channel lock`/`unlock`.
    `locked=True` explicitly denies VIEW_CHANNEL and leaves every other
    bit neutral (same as `discord.PermissionOverwrite(view_channel=False,
    send_messages=None, send_messages_in_threads=None)` on the bot side);
    `locked=False` explicitly allows VIEW_CHANNEL only and denies nothing
    else -- a role is never granted posting access this way (or `/staff
    channel unlock`'s), only a specific approved member is, automatically,
    to their own district's forum (`panem_bot.cogs.characters.
    _handle_approve`). Returns whether the Discord API call actually
    succeeded."""
    if not bot_token or not channel_id or not role_id:
        return False
    if locked:
        allow, deny = 0, _VIEW_CHANNEL
    else:
        allow, deny = _VIEW_CHANNEL, 0
    url = f"{DISCORD_API_BASE}/channels/{channel_id}/permissions/{role_id}"
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.put(
                url,
                json={"allow": str(allow), "deny": str(deny), "type": 0},
                headers={"Authorization": f"Bot {bot_token}"},
            )
        except httpx.HTTPError as exc:
            logger.warning("channel_permission_edit_failed", channel_id=channel_id, error=str(exc))
            return False
    if response.status_code not in (200, 204):
        logger.warning(
            "channel_permission_edit_failed",
            channel_id=channel_id,
            status=response.status_code,
        )
        return False
    return True
