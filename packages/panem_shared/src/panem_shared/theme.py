"""Donor dashboard theming: named, savable `ThemeProfile` rows (`POST
/activity/dashboard/theme/profiles`), each a set of four CSS custom
property overrides `panem_api/static/style.css`'s `:root` defines (`--bg`,
`--accent`, `--panel`, `--text`), gated to whoever holds a configured donor
role (`Settings.donor_role_id_set()`). A donor can save any number of named
profiles, set one as their account's general default
(`User.active_theme_profile_id`), and/or assign different ones to
different characters (`Character.theme_profile_id`) -- resolution order is
documented on `dashboard_routes._resolve_theme`. The gate itself is
enforced server-side in `panem_api.dashboard_routes.build_theme_router`
(never trust a client-supplied "I'm a donor" flag for a privilege
decision, the same posture `discord_staff.fetch_is_staff` already applies
to the Staff tab) -- this module only holds the color/name values
themselves and their validation.
"""

from __future__ import annotations

import re

from panem_shared.errors import ValidationFailed

# Mirrors static/style.css's `:root` values exactly -- these are what a
# reset (or an account that's never customized, or a donor role that's
# lapsed since customizing) actually renders. Keep in sync with that file
# if its defaults ever change.
DEFAULT_BACKGROUND_HEX = "#0a0c10"
DEFAULT_ACCENT_HEX = "#c5a059"
DEFAULT_PANEL_HEX = "#12161f"
DEFAULT_TEXT_HEX = "#f1f3f7"

PROFILE_NAME_MAX_LEN = 40

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


def validate_profile_name(value: str) -> str:
    """A profile's display name -- whatever the donor typed into the save
    dialog, trimmed of surrounding whitespace. Rejects blank (nothing to
    show in the profile list) and overlong names; no character-set
    restriction beyond that, unlike a character's own name."""
    stripped = value.strip()
    if not stripped or len(stripped) > PROFILE_NAME_MAX_LEN:
        raise ValidationFailed("theme_invalid_profile_name", max=PROFILE_NAME_MAX_LEN)
    return stripped
