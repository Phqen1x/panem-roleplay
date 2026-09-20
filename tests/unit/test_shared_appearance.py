"""`panem_shared.appearance`: the dashboard Character tab customizer's
trait-palette validation, exercised standalone (no DB/session needed --
`validate_appearance_traits` is a pure function)."""

from __future__ import annotations

import pytest

from panem_shared import appearance
from panem_shared.errors import ValidationFailed


class TestValidateAppearanceTraits:
    def test_empty_dict_returns_full_defaults(self):
        result = appearance.validate_appearance_traits({})
        assert result == appearance.DEFAULT_APPEARANCE_TRAITS
        # Must be a fresh copy, not the same mutable object every caller shares.
        assert result is not appearance.DEFAULT_APPEARANCE_TRAITS

    def test_accepts_a_full_valid_submission(self):
        submission = {
            "gender_presentation": "masculine",
            "age_look": "weathered",
            "face_shape": "square",
            "expression": "fierce",
            "hair_style": "mohawk",
            "hair_color": appearance.HAIR_COLORS[3],
            "facial_hair": "full",
            "build": "stocky",
            "clothing_style": "hunter",
            "clothing_color": appearance.CLOTHING_COLORS[2],
            "skin_tone": appearance.SKIN_TONES[1],
            "eye_color": appearance.EYE_COLORS[5],
            "height_cm": 190,
            "jewelry": ["glasses", "earrings"],
        }
        result = appearance.validate_appearance_traits(submission)
        assert result["gender_presentation"] == "masculine"
        assert result["height_cm"] == 190
        # Canonical order, not submission order.
        assert result["jewelry"] == ["earrings", "glasses"]

    def test_partial_submission_fills_the_rest_from_defaults(self):
        result = appearance.validate_appearance_traits({"hair_style": "bald"})
        assert result["hair_style"] == "bald"
        assert result["build"] == appearance.DEFAULT_APPEARANCE_TRAITS["build"]

    def test_rejects_a_choice_field_outside_its_palette(self):
        with pytest.raises(ValidationFailed):
            appearance.validate_appearance_traits({"hair_style": "rainbow-mohawk"})

    def test_rejects_a_color_outside_its_palette(self):
        with pytest.raises(ValidationFailed):
            appearance.validate_appearance_traits({"skin_tone": "#ff00ff"})

    def test_rejects_height_below_the_minimum(self):
        with pytest.raises(ValidationFailed):
            appearance.validate_appearance_traits({"height_cm": appearance.HEIGHT_CM_MIN - 1})

    def test_rejects_height_above_the_maximum(self):
        with pytest.raises(ValidationFailed):
            appearance.validate_appearance_traits({"height_cm": appearance.HEIGHT_CM_MAX + 1})

    def test_rejects_a_non_integer_height(self):
        with pytest.raises(ValidationFailed):
            appearance.validate_appearance_traits({"height_cm": 172.5})

    def test_rejects_a_bool_disguised_as_height(self):
        # isinstance(True, int) is True in Python -- guard against it explicitly.
        with pytest.raises(ValidationFailed):
            appearance.validate_appearance_traits({"height_cm": True})

    def test_rejects_an_unknown_jewelry_item(self):
        with pytest.raises(ValidationFailed):
            appearance.validate_appearance_traits({"jewelry": ["crown"]})

    def test_rejects_jewelry_that_is_not_a_list(self):
        with pytest.raises(ValidationFailed):
            appearance.validate_appearance_traits({"jewelry": "glasses"})

    def test_rejects_a_non_dict(self):
        with pytest.raises(ValidationFailed):
            appearance.validate_appearance_traits("not-a-dict")  # type: ignore[arg-type]
