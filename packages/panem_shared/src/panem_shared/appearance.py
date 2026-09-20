"""Structured character-appearance traits: a fixed palette of options per
category that the dashboard's Character tab customizer
(`static/tabs/avatar_creator.js`) renders live as a simple original-art
canvas portrait (no external art assets, same precedent as the lockpick/
pickpocket minigames), and stores on `Character.appearance_traits` as
plain JSON. This is deliberately separate from the free-text `appearance`
field (which stays exactly what it always was -- the player's own words,
shown in the staff-approval embed and `/character status`): the
customizer drives *the portrait*, not the prose.

Every option list here is mirrored by `GET /activity/dashboard/characters/
appearance-options` (`dashboard_routes.py`) so the frontend never
hardcodes its own copy of the palette -- add an option here and it shows
up in the customizer with no JS change needed.
"""

from __future__ import annotations

from typing import Any

from panem_shared.errors import ValidationFailed

GENDER_PRESENTATIONS: tuple[str, ...] = ("feminine", "masculine", "androgynous")
AGE_LOOKS: tuple[str, ...] = ("youthful", "average", "weathered")
FACE_SHAPES: tuple[str, ...] = ("oval", "round", "square", "heart", "long")
EXPRESSIONS: tuple[str, ...] = ("neutral", "smiling", "serious", "fierce", "gentle")
HAIR_STYLES: tuple[str, ...] = (
    "bald",
    "buzz",
    "short",
    "bob",
    "shoulder",
    "long",
    "braid",
    "curly",
    "mohawk",
    "bun",
)
FACIAL_HAIR: tuple[str, ...] = ("none", "stubble", "mustache", "beard", "full")
BUILDS: tuple[str, ...] = ("slim", "athletic", "average", "stocky", "heavyset")
CLOTHING_STYLES: tuple[str, ...] = (
    "worker",
    "formal",
    "ragged",
    "capitol_fashion",
    "hunter",
    "merchant",
)
JEWELRY_OPTIONS: tuple[str, ...] = ("earrings", "necklace", "nose_ring", "glasses", "headband")

# Swatch palettes: fixed hex values rather than a free color picker, so
# every portrait is drawn from the same small, deliberately-chosen set
# (keeps the canvas rendering simple and avoids a garish-color griefing
# vector on a field nothing else validates the "taste" of).
SKIN_TONES: tuple[str, ...] = (
    "#3d2418",
    "#5a3825",
    "#7a4f34",
    "#a06b45",
    "#c68e5f",
    "#dba876",
    "#e8c39e",
    "#f2d9bd",
)
HAIR_COLORS: tuple[str, ...] = (
    "#0b0b0b",
    "#2b1a10",
    "#4a2c17",
    "#7a4a24",
    "#a9702f",
    "#c9a24a",
    "#d9c58a",
    "#e8e8e8",
    "#b23a3a",
    "#4a5fc9",
)
EYE_COLORS: tuple[str, ...] = (
    "#3a2a1a",
    "#5c4326",
    "#4a7a3a",
    "#2f6f7a",
    "#3a5fa0",
    "#8a8a8a",
    "#6a4a9a",
    "#2a2a2a",
)
CLOTHING_COLORS: tuple[str, ...] = (
    "#5a1f1f",
    "#1f3a5a",
    "#1f5a2e",
    "#5a4b1f",
    "#3a3a3a",
    "#6a2f5a",
    "#c9a24a",
    "#2a2a2a",
)

HEIGHT_CM_MIN = 140
HEIGHT_CM_MAX = 210

_CHOICE_FIELDS: dict[str, tuple[str, ...]] = {
    "gender_presentation": GENDER_PRESENTATIONS,
    "age_look": AGE_LOOKS,
    "face_shape": FACE_SHAPES,
    "expression": EXPRESSIONS,
    "hair_style": HAIR_STYLES,
    "facial_hair": FACIAL_HAIR,
    "build": BUILDS,
    "clothing_style": CLOTHING_STYLES,
}
_COLOR_FIELDS: dict[str, tuple[str, ...]] = {
    "skin_tone": SKIN_TONES,
    "hair_color": HAIR_COLORS,
    "eye_color": EYE_COLORS,
    "clothing_color": CLOTHING_COLORS,
}

DEFAULT_APPEARANCE_TRAITS: dict[str, Any] = {
    "gender_presentation": "androgynous",
    "age_look": "average",
    "face_shape": "oval",
    "expression": "neutral",
    "hair_style": "short",
    "hair_color": HAIR_COLORS[0],
    "facial_hair": "none",
    "build": "average",
    "clothing_style": "worker",
    "clothing_color": CLOTHING_COLORS[0],
    "skin_tone": SKIN_TONES[4],
    "eye_color": EYE_COLORS[0],
    "height_cm": 170,
    "jewelry": [],
}


def validate_appearance_traits(traits: dict[str, Any]) -> dict[str, Any]:
    """Raise `ValidationFailed("invalid_appearance_traits")` on anything
    outside the fixed palette above. Returns a fresh dict seeded from
    `DEFAULT_APPEARANCE_TRAITS` with every recognized field overridden --
    a partial submission (or a row saved before some field existed) still
    round-trips through the customizer with every key present."""
    if not isinstance(traits, dict):
        raise ValidationFailed("invalid_appearance_traits")

    result = dict(DEFAULT_APPEARANCE_TRAITS)

    for field, options in _CHOICE_FIELDS.items():
        if field not in traits:
            continue
        value = traits[field]
        if value not in options:
            raise ValidationFailed("invalid_appearance_traits")
        result[field] = value

    for field, palette in _COLOR_FIELDS.items():
        if field not in traits:
            continue
        value = traits[field]
        if value not in palette:
            raise ValidationFailed("invalid_appearance_traits")
        result[field] = value

    if "height_cm" in traits:
        height = traits["height_cm"]
        if isinstance(height, bool) or not isinstance(height, int):
            raise ValidationFailed("invalid_appearance_traits")
        if not (HEIGHT_CM_MIN <= height <= HEIGHT_CM_MAX):
            raise ValidationFailed("invalid_appearance_traits")
        result["height_cm"] = height

    if "jewelry" in traits:
        jewelry = traits["jewelry"]
        if not isinstance(jewelry, list) or any(item not in JEWELRY_OPTIONS for item in jewelry):
            raise ValidationFailed("invalid_appearance_traits")
        # Canonical order (not submission order) keeps storage/output stable.
        result["jewelry"] = [item for item in JEWELRY_OPTIONS if item in jewelry]

    return result
