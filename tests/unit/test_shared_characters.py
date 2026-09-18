"""Smoke test that panem_shared.characters works standalone (no panem_bot
dependency) -- panem_api's dashboard character endpoints import it
directly, same reason panem_shared.jail/stealing/shifts do.
"""

from __future__ import annotations

import pytest

from panem_shared import characters as shared_characters
from panem_shared.errors import ValidationFailed


class TestMaxAgeForDistrict:
    def test_capitol_allows_adults(self):
        assert shared_characters.max_age_for_district(0) > 18

    def test_other_districts_cap_at_the_reaping_range(self):
        assert shared_characters.max_age_for_district(1) == 18


class TestValidateAvatarUrl:
    def test_accepts_a_plain_https_image_url(self):
        shared_characters.validate_avatar_url("https://example.com/pic.png")  # no raise

    def test_rejects_non_https(self):
        with pytest.raises(ValidationFailed):
            shared_characters.validate_avatar_url("http://example.com/pic.png")

    def test_rejects_a_non_image_extension(self):
        with pytest.raises(ValidationFailed):
            shared_characters.validate_avatar_url("https://example.com/pic.txt")


class TestValidateProxyTag:
    def test_rejects_a_tag_that_looks_like_a_command(self):
        with pytest.raises(ValidationFailed):
            shared_characters.validate_proxy_tag("/foo")
