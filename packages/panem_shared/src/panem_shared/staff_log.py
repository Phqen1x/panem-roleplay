"""Human-readable Discord embed formatting for the staff-action log line
every `/staff ...` command and its dashboard equivalent posts to
`Settings.log_channel_id`. Lives here (not `panem_bot`) because both
`panem_bot.services.staff.log_staff_action` (which posts through its own
gateway `discord.Embed`) and `panem_api.discord_staff.post_staff_log`
(REST-only, no `discord.py` dependency -- see that module's own
docstring) need the exact same `{action, target, payload}` -> embed
mapping, and `panem_api` can't depend on `panem_bot` to get it (same
reasoning as `panem_shared.characters`/`panem_shared.jail`). The dict
this returns is Discord's own embed JSON shape (decimal `color`, ISO8601
`timestamp`) so `panem_bot` only needs `discord.Embed.from_dict(...)` and
`panem_api` can send it to Discord's REST API completely unchanged.

Before this, every staff action posted as one plain-text line with the
raw `payload` dict's Python `repr()` inline (`` `{'ticks': 50, 'reason':
'brawling'}` ``) -- exactly the "raw code-like" output normal players/
staff can't parse at a glance. This formats the same data as an embed
with one field per payload key, a title-cased action name, and a few
common value shapes (`None`, `bool`, lists) spelled out in English."""

from __future__ import annotations

import datetime as dt

_ABBREVIATIONS = {"npc": "NPC", "au": "AU", "rp": "RP", "ooc": "OOC"}

STAFF_ACTION_COLOR = 0x5865F2
"""Discord's own "blurple" -- matches the brand color already used
elsewhere for a neutral/informational embed (no severity signal here;
every staff action gets the same color)."""


def _title_words(raw: str) -> str:
    return " ".join(_ABBREVIATIONS.get(word, word.capitalize()) for word in raw.split("_"))


def _humanize_value(value: object) -> str:
    if value is None:
        return "--"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list | tuple | set):
        return ", ".join(str(item) for item in value) if value else "--"
    text = str(value)
    return text if text else "--"


def build_staff_log_embed(
    *,
    action: str,
    staff_discord_id: int,
    target: str,
    payload: dict[str, object] | None = None,
    target_label: str | None = None,
) -> dict[str, object]:
    """One Discord embed object (raw REST JSON shape, as `discord.Embed.
    from_dict`/Discord's own `POST .../messages` both expect it) for a
    single staff action. `target_label`, when the caller already has a
    human-readable name/mention for whatever `target` points at (a
    character, a channel, a role...), replaces the bare `` `{target}` ``
    fallback -- most call sites pass a raw id/snowflake as `target` for
    the DB row's sake (`StaffAction.target` is an indexed `str`), which
    reads fine in an audit query but not in a chat message."""
    fields: list[dict[str, object]] = [
        {"name": "Staff", "value": f"<@{staff_discord_id}>", "inline": True},
        {"name": "Target", "value": target_label or f"`{target}`", "inline": True},
    ]
    for key, value in (payload or {}).items():
        fields.append({"name": _title_words(key), "value": _humanize_value(value), "inline": True})
    return {
        "title": f"Staff Action: {_title_words(action)}",
        "color": STAFF_ACTION_COLOR,
        "fields": fields,
        "timestamp": dt.datetime.now(dt.UTC).isoformat(),
    }
