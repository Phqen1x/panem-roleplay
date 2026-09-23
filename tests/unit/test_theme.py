"""Tests for `panem_shared.theme` (hex validation) and `Settings.donor_
role_id_set()` (its .env parsing) -- the two pieces of the donor dashboard
theming feature that don't need a running app to exercise directly."""

from __future__ import annotations

import pytest

from panem_shared.errors import ValidationFailed
from panem_shared.settings import Settings
from panem_shared.theme import (
    DEFAULT_ACCENT_HEX,
    DEFAULT_BACKGROUND_HEX,
    DEFAULT_PANEL_HEX,
    DEFAULT_TEXT_HEX,
    PROFILE_NAME_MAX_LEN,
    validate_hex_color,
    validate_profile_name,
)


class TestValidateHexColor:
    def test_accepts_a_well_formed_hex_color(self):
        assert validate_hex_color("#112233") == "#112233"

    def test_lowercases_the_result(self):
        assert validate_hex_color("#ABCDEF") == "#abcdef"

    @pytest.mark.parametrize(
        "value",
        [
            "112233",  # missing leading #
            "#11223",  # too short
            "#1122334",  # too long
            "#gggggg",  # not hex digits
            "red",
            "",
            "#112233;background:url(x)",  # not a plain hex color at all
        ],
    )
    def test_rejects_anything_else(self, value):
        with pytest.raises(ValidationFailed):
            validate_hex_color(value)

    def test_defaults_match_style_css(self):
        """These constants are meant to mirror `static/style.css`'s `:root`
        values exactly -- a reset (or a never-customized account) renders
        these. Regression guard against the two drifting apart silently."""
        assert DEFAULT_BACKGROUND_HEX == "#14161c"
        assert DEFAULT_ACCENT_HEX == "#e0a72e"
        assert DEFAULT_PANEL_HEX == "#1b1f27"
        assert DEFAULT_TEXT_HEX == "#d7dbe4"


class TestValidateProfileName:
    def test_accepts_a_plain_name(self):
        assert validate_profile_name("Midnight") == "Midnight"

    def test_strips_surrounding_whitespace(self):
        assert validate_profile_name("  Midnight  ") == "Midnight"

    def test_rejects_blank(self):
        with pytest.raises(ValidationFailed):
            validate_profile_name("   ")

    def test_accepts_the_max_length(self):
        name = "x" * PROFILE_NAME_MAX_LEN
        assert validate_profile_name(name) == name

    def test_rejects_over_the_max_length(self):
        with pytest.raises(ValidationFailed):
            validate_profile_name("x" * (PROFILE_NAME_MAX_LEN + 1))


class TestDonorRoleIdSet:
    def test_empty_by_default(self):
        assert Settings(donor_role_ids="").donor_role_id_set() == frozenset()

    def test_parses_a_single_id(self):
        assert Settings(donor_role_ids="123").donor_role_id_set() == frozenset({123})

    def test_parses_comma_separated_ids(self):
        assert Settings(donor_role_ids="123,456,789").donor_role_id_set() == frozenset(
            {123, 456, 789}
        )

    def test_tolerates_whitespace_and_trailing_commas(self):
        assert Settings(donor_role_ids=" 123 , 456,").donor_role_id_set() == frozenset({123, 456})
