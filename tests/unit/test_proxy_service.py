from __future__ import annotations

import pytest

from panem_bot.services import proxy as proxy_svc
from panem_shared.constants import PROXY_MESSAGE_MAX_LEN
from panem_shared.content.schemas import Location
from panem_shared.db.models import Character
from panem_shared.enums import CharacterStatus


def make_character(**overrides) -> Character:
    defaults = dict(
        id=1,
        user_id=1,
        district_id=12,
        current_district_id=12,
        name="Katniss",
        age=16,
        status=CharacterStatus.APPROVED.value,
        job_id=None,
        positions=[],
        jailed_until_tick=None,
    )
    defaults.update(overrides)
    return Character(**defaults)


class TestIsOoc:
    @pytest.mark.parametrize("content", ["((hey))", "  ((typing OOC))  "])
    def test_true(self, content):
        assert proxy_svc.is_ooc(content)

    @pytest.mark.parametrize("content", ["(no)", "((half", "half))", "normal message"])
    def test_false(self, content):
        assert not proxy_svc.is_ooc(content)


class TestResolveProxyTarget:
    def test_ooc_never_proxied(self):
        assert (
            proxy_svc.resolve_proxy_target(
                content="((not in character))", session_character_id=5, user_tags={}
            )
            is None
        )

    def test_session_wins(self):
        target = proxy_svc.resolve_proxy_target(
            content="Hello there", session_character_id=5, user_tags={"md": 9}
        )
        assert target is not None
        assert target.character_id == 5
        assert target.via_tag is False

    def test_tag_prefix_resolves(self):
        target = proxy_svc.resolve_proxy_target(
            content="md: hello", session_character_id=None, user_tags={"md": 9}
        )
        assert target is not None
        assert target.character_id == 9
        assert target.via_tag is True

    def test_no_session_no_tag_leaves_untouched(self):
        assert (
            proxy_svc.resolve_proxy_target(
                content="plain message", session_character_id=None, user_tags={"md": 9}
            )
            is None
        )

    def test_tag_must_be_prefix_not_substring(self):
        assert (
            proxy_svc.resolve_proxy_target(
                content="I said md: earlier", session_character_id=None, user_tags={"md": 9}
            )
            is None
        )


class TestStripTagPrefix:
    def test_strips(self):
        assert proxy_svc.strip_tag_prefix("md: hello there", "md") == "hello there"

    def test_no_match_returns_unchanged(self):
        assert proxy_svc.strip_tag_prefix("hello there", "md") == "hello there"


class TestCanRpInDistrict:
    def test_home_district_always_allowed(self):
        char = make_character(district_id=12, positions=[])
        assert proxy_svc.can_rp_in_district(char, 12)

    def test_other_district_denied_without_a_position(self):
        char = make_character(district_id=12, positions=[])
        assert not proxy_svc.can_rp_in_district(char, 5)

    def test_gamemaker_allowed_anywhere(self):
        char = make_character(district_id=12, positions=["gamemaker"])
        assert proxy_svc.can_rp_in_district(char, 5)
        assert proxy_svc.can_rp_in_district(char, 0)

    def test_victor_allowed_in_the_capitol(self):
        char = make_character(district_id=12, positions=["victor"])
        assert proxy_svc.can_rp_in_district(char, 0)

    def test_victor_denied_in_a_third_district(self):
        char = make_character(district_id=12, positions=["victor"])
        assert not proxy_svc.can_rp_in_district(char, 5)

    def test_governor_gets_no_extra_access(self):
        char = make_character(district_id=12, positions=["governor"])
        assert not proxy_svc.can_rp_in_district(char, 5)
        assert not proxy_svc.can_rp_in_district(char, 0)


class TestHasLocationAccess:
    def test_unrestricted_always_true(self):
        loc = Location(id="square", name="The Square", kind="public")
        assert proxy_svc.has_location_access(job_id=None, has_position=False, location=loc)

    def test_restricted_with_a_position_true(self):
        loc = Location(id="village", name="Victor's Village", kind="residential", restricted=True)
        assert proxy_svc.has_location_access(job_id=None, has_position=True, location=loc)

    def test_restricted_job_match_true(self):
        loc = Location(
            id="mine", name="Mine", kind="workplace", restricted=True, access_jobs=["miner"]
        )
        assert proxy_svc.has_location_access(job_id="miner", has_position=False, location=loc)

    def test_restricted_no_access_false(self):
        loc = Location(
            id="mine", name="Mine", kind="workplace", restricted=True, access_jobs=["miner"]
        )
        assert not proxy_svc.has_location_access(job_id="baker", has_position=False, location=loc)


class TestCheckCanProxy:
    def make_district(self, locations):
        from panem_shared.content.schemas import District, DistrictCulture, DistrictMap

        # Every district needs >= 1 public and >= 1 station location
        # (FR-LOC-7, FR-CHR-4); pad with either unless the test already has one.
        if not any(loc.kind.value == "station" for loc in locations):
            locations = [*locations, Location(id="station", name="Station", kind="station")]
        if not any(loc.kind.value == "public" for loc in locations):
            locations = [*locations, Location(id="public_pad", name="Public Pad", kind="public")]
        coords = {loc.id: (0, 0) for loc in locations}
        return District(
            id=12,
            name="District Twelve",
            industry="coal",
            locations=locations,
            culture=DistrictCulture(),
            population_base=100,
            map=DistrictMap(image="x.png", width=10, height=10, location_coords=coords),
        )

    def test_dead_refused(self):
        character = make_character(status=CharacterStatus.DEAD.value)
        district = self.make_district([Location(id="sq", name="Square", kind="public")])
        refusal = proxy_svc.check_can_proxy(
            character=character, district=district, location_id=None, current_tick=0
        )
        assert refusal is not None and refusal.reason_key == "character_dead"

    def test_not_approved_refused(self):
        character = make_character(status=CharacterStatus.PENDING.value)
        district = self.make_district([Location(id="sq", name="Square", kind="public")])
        refusal = proxy_svc.check_can_proxy(
            character=character, district=district, location_id=None, current_tick=0
        )
        assert refusal is not None and refusal.reason_key == "character_not_approved"

    def test_jailed_refused(self):
        character = make_character(jailed_until_tick=100)
        district = self.make_district([Location(id="sq", name="Square", kind="public")])
        refusal = proxy_svc.check_can_proxy(
            character=character, district=district, location_id=None, current_tick=10
        )
        assert refusal is not None and refusal.reason_key == "proxy_character_jailed"

    def test_jail_expired_allowed(self):
        character = make_character(jailed_until_tick=5)
        district = self.make_district([Location(id="sq", name="Square", kind="public")])
        refusal = proxy_svc.check_can_proxy(
            character=character, district=district, location_id=None, current_tick=10
        )
        assert refusal is None

    def test_restricted_location_refused(self):
        character = make_character()
        loc = Location(
            id="meadow", name="The Meadow", kind="outskirts", restricted=True, access_jobs=["miner"]
        )
        district = self.make_district([loc])
        refusal = proxy_svc.check_can_proxy(
            character=character, district=district, location_id="meadow", current_tick=0
        )
        assert refusal is not None and refusal.reason_key == "proxy_location_restricted"

    def test_ok(self):
        character = make_character()
        district = self.make_district([Location(id="sq", name="Square", kind="public")])
        refusal = proxy_svc.check_can_proxy(
            character=character, district=district, location_id="sq", current_tick=0
        )
        assert refusal is None


class TestSplitForWebhook:
    def test_short_content_single_chunk(self):
        assert proxy_svc.split_for_webhook("hello") == ["hello"]

    def test_splits_at_paragraph_boundary(self):
        para_a = "a" * 1500
        para_b = "b" * 1500
        content = f"{para_a}\n\n{para_b}"
        chunks = proxy_svc.split_for_webhook(content, max_len=2000)
        assert len(chunks) == 2
        assert chunks[0] == para_a
        assert chunks[1] == para_b

    def test_hard_wraps_oversized_paragraph(self):
        content = "x" * 5000
        chunks = proxy_svc.split_for_webhook(content, max_len=2000)
        assert all(len(c) <= 2000 for c in chunks)
        assert "".join(chunks) == content

    def test_default_max_len_matches_constant(self):
        content = "y" * (PROXY_MESSAGE_MAX_LEN + 10)
        chunks = proxy_svc.split_for_webhook(content)
        assert all(len(c) <= PROXY_MESSAGE_MAX_LEN for c in chunks)
