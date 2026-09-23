"""Donor dashboard theming (`POST /activity/dashboard/theme`): a per-user
override of the two CSS custom properties `panem_api/static/style.css`'s
`:root` defines (`--bg`, `--accent`), gated to whoever holds a configured
donor role (`Settings.donor_role_id_set()`). The gate itself is enforced
server-side in `panem_api.dashboard_routes.build_theme_router` (never trust
a client-supplied "I'm a donor" flag for a privilege decision, the same
posture `discord_staff.fetch_is_staff` already applies to the Staff tab) --
this module only holds the color values themselves and their validation.
"""

from __future__ import annotations

import re

from panem_shared.errors import ValidationFailed

# Mirrors static/style.css's `:root` values exactly -- these are what a
# reset (or an account that's never customized, or a donor role that's
# lapsed since customizing) actually renders. Keep in sync with that file
# if its defaults ever change.
DEFAULT_BACKGROUND_HEX = "#14161c"
DEFAULT_ACCENT_HEX = "#e0a72e"

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def validate_hex_color(value: str) -> str:
    """Accepts only `#rrggbb` (a leading `#` plus exactly 6 hex digits) --
    the one shape both the frontend's saturation/hue picker and its hex
    text field always produce, and the only shape a CSS custom property
    assignment needs. Normalizes to lowercase so `#ABCDEF` and `#abcdef`
    don't compare or store as different values."""
    if not _HEX_COLOR_RE.match(value):
        raise ValidationFailed("theme_invalid_hex_color")
    return value.lower()
