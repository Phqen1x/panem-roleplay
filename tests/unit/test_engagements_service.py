from __future__ import annotations

import datetime as dt

import pytest

from panem_bot.errors import NotAllowed
from panem_bot.services import engagements as engagements_svc
from panem_shared.content.schemas import Job, JobOption
from panem_shared.db.models import Character, Npc, Scene
from panem_shared.enums import CharacterStatus, DayPhase, SceneKind, SceneStatus


def make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = dict(
        id="job",
        district=1,
        title="Job",
        workplace="market",
        wage=10.0,
        shift_phase="morning",
        slots=5,
        options=[JobOption(label="a"), JobOption(label="b"), JobOption(label="c")],
    )
    defaults.update(overrides)
    return Job(**defaults)  # type: ignore[arg-type]


def make_npc(**overrides: object) -> Npc:
    defaults: dict[str, object] = dict(
        id="npc-1",
        district_id=1,
        name="Old Ferro",
        age=61,
        location_id="hob",
        home_location_id="seam",
        job_id=None,
    )
    defaults.update(overrides)
    return Npc(**defaults)  # type: ignore[arg-type]


def make_scene(id_: int, **overrides: object) -> Scene:
    defaults: dict[str, object] = dict(
        district_id=1,
        location_id="hob",
        thread_id=1000 + id_,
        forum_channel_id=1,
        kind=SceneKind.ENGAGEMENT.value,
        title="An Engagement",
        status=SceneStatus.OPEN.value,
        last_message_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        participants={"characters": [1], "pending_characters": [], "npcs": ["npc-1"]},
    )
    defaults.update(overrides)
    scene = Scene(**defaults)  # type: ignore[arg-type]
    scene.id = id_
    return scene


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Wren Calder",
        age=20,
        status=CharacterStatus.APPROVED.value,
        money=0,
        location_id="hob",
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = 1
    return character


class TestNpcIsBusy:
    def test_working_a_shift_is_busy(self):
        job = make_job(workplace="mine", shift_phase="morning")
        npc = make_npc(location_id="mine", home_location_id="seam", job_id="job")
        assert engagements_svc.npc_is_busy(npc, job, DayPhase.MORNING) == "shift"

    def test_at_workplace_outside_shift_phase_is_not_busy(self):
        job = make_job(workplace="mine", shift_phase="morning")
        npc = make_npc(location_id="mine", home_location_id="seam", job_id="job")
        assert engagements_svc.npc_is_busy(npc, job, DayPhase.EVENING) is None

    def test_working_a_shift_is_available_when_engaging_at_the_workplace_itself(self):
        job = make_job(workplace="mine", shift_phase="morning")
        npc = make_npc(location_id="mine", home_location_id="seam", job_id="job")
        assert (
            engagements_svc.npc_is_busy(npc, job, DayPhase.MORNING, at_location_id="mine") is None
        )

    def test_working_a_shift_is_still_busy_toward_a_different_location(self):
        job = make_job(workplace="mine", shift_phase="morning")
        npc = make_npc(location_id="mine", home_location_id="seam", job_id="job")
        assert (
            engagements_svc.npc_is_busy(npc, job, DayPhase.MORNING, at_location_id="square")
            == "shift"
        )

    def test_asleep_at_home_at_night_is_busy(self):
        npc = make_npc(location_id="seam", home_location_id="seam", job_id=None)
        assert engagements_svc.npc_is_busy(npc, None, DayPhase.NIGHT) == "sleep"

    def test_at_home_during_the_day_is_not_busy(self):
        npc = make_npc(location_id="seam", home_location_id="seam", job_id=None)
        assert engagements_svc.npc_is_busy(npc, None, DayPhase.AFTERNOON) is None

    def test_elsewhere_with_no_job_is_free(self):
        npc = make_npc(location_id="square", home_location_id="seam", job_id=None)
        assert engagements_svc.npc_is_busy(npc, None, DayPhase.MORNING) is None


class TestResolveNpcParticipants:
    def test_matches_case_insensitively(self):
        ferro = make_npc(id="npc-1", name="Old Ferro")
        matched, unmatched = engagements_svc.resolve_npc_participants(["old ferro"], [ferro])
        assert matched == [ferro]
        assert unmatched == []

    def test_unmatched_names_pass_through(self):
        ferro = make_npc(id="npc-1", name="Old Ferro")
        matched, unmatched = engagements_svc.resolve_npc_participants(
            ["Old Ferro", "Nobody"], [ferro]
        )
        assert matched == [ferro]
        assert unmatched == ["Nobody"]


class TestResolveCharacterParticipants:
    def test_matches_case_insensitively(self):
        wren = make_character(name="Wren Calder")
        matched, unmatched = engagements_svc.resolve_character_participants(["wren calder"], [wren])
        assert matched == [wren]
        assert unmatched == []

    def test_unmatched_names_pass_through(self):
        wren = make_character(name="Wren Calder")
        matched, unmatched = engagements_svc.resolve_character_participants(
            ["Wren Calder", "Ghost"], [wren]
        )
        assert matched == [wren]
        assert unmatched == ["Ghost"]


class TestNameMentioned:
    def test_matches_first_name(self):
        npc = make_npc(name="Old Ferro")
        assert engagements_svc.name_mentioned(npc, "Hey Ferro, got a minute?") is True

    def test_matches_last_name_case_insensitively(self):
        npc = make_npc(name="Old Ferro")
        assert engagements_svc.name_mentioned(npc, "hey OLD, how's it going") is True

    def test_no_match_when_neither_name_part_present(self):
        npc = make_npc(name="Old Ferro")
        assert engagements_svc.name_mentioned(npc, "Anyone selling bread today?") is False

    def test_does_not_match_a_substring_inside_another_word(self):
        npc = make_npc(name="Ann Cole")
        assert engagements_svc.name_mentioned(npc, "There was an announcement earlier.") is False

    def test_short_name_part_below_min_len_never_matches(self):
        npc = make_npc(name="Al Ox")
        assert engagements_svc.name_mentioned(npc, "Al and Ox both live here.") is False


class TestNpcsThatShouldReply:
    def test_one_on_one_always_replies(self):
        npc = make_npc(name="Old Ferro")
        result = engagements_svc.npcs_that_should_reply(
            [npc], "anything at all", is_one_on_one=True
        )
        assert result == [npc]

    def test_group_only_the_named_npc_replies(self):
        ferro = make_npc(id="npc-1", name="Old Ferro")
        greasy = make_npc(id="npc-2", name="Greasy Sae")
        result = engagements_svc.npcs_that_should_reply(
            [ferro, greasy], "Ferro, got a minute?", is_one_on_one=False
        )
        assert result == [ferro]

    def test_group_with_nobody_named_gets_no_replies(self):
        ferro = make_npc(id="npc-1", name="Old Ferro")
        greasy = make_npc(id="npc-2", name="Greasy Sae")
        result = engagements_svc.npcs_that_should_reply(
            [ferro, greasy], "Anyone selling bread?", is_one_on_one=False
        )
        assert result == []


class TestCheckCanStart:
    def test_refuses_a_dead_character(self):
        character = make_character(status=CharacterStatus.DEAD.value)
        with pytest.raises(NotAllowed) as exc_info:
            engagements_svc.check_can_start(character=character)
        assert exc_info.value.reason_key == "character_dead"

    def test_refuses_an_unapproved_character(self):
        character = make_character(status=CharacterStatus.PENDING.value)
        with pytest.raises(NotAllowed) as exc_info:
            engagements_svc.check_can_start(character=character)
        assert exc_info.value.reason_key == "character_not_approved"

    def test_allows_an_approved_character(self):
        character = make_character(status=CharacterStatus.APPROVED.value)
        engagements_svc.check_can_start(character=character)


class TestScenesToClose:
    NOW = dt.datetime(2026, 1, 1, 1, 0, tzinfo=dt.UTC)

    def test_closes_a_scene_past_the_timeout(self):
        scene = make_scene(1, last_message_at=self.NOW - dt.timedelta(minutes=31))
        assert engagements_svc.scenes_to_close([scene], timeout_minutes=30, now=self.NOW) == [1]

    def test_leaves_a_recently_active_scene_open(self):
        scene = make_scene(1, last_message_at=self.NOW - dt.timedelta(minutes=5))
        assert engagements_svc.scenes_to_close([scene], timeout_minutes=30, now=self.NOW) == []

    def test_a_non_engagement_kind_scene_with_npcs_attached_still_closes(self):
        # /talk and /engage start can attach NPCs to any scene (an ambient
        # thread, an open /scene), not just a dedicated engagement thread --
        # the idle-release logic doesn't care about `kind`, only whether
        # NPCs are actually present.
        scene = make_scene(
            1, kind=SceneKind.PLAYER.value, last_message_at=self.NOW - dt.timedelta(hours=2)
        )
        assert engagements_svc.scenes_to_close([scene], timeout_minutes=30, now=self.NOW) == [1]

    def test_ignores_a_scene_with_no_npcs_attached(self):
        scene = make_scene(
            1,
            participants={"characters": [1], "pending_characters": [], "npcs": []},
            last_message_at=self.NOW - dt.timedelta(hours=2),
        )
        assert engagements_svc.scenes_to_close([scene], timeout_minutes=30, now=self.NOW) == []

    def test_ignores_an_already_archived_scene(self):
        scene = make_scene(
            1,
            status=SceneStatus.ARCHIVED.value,
            last_message_at=self.NOW - dt.timedelta(hours=2),
        )
        assert engagements_svc.scenes_to_close([scene], timeout_minutes=30, now=self.NOW) == []

    def test_ignores_a_scene_with_no_messages_yet(self):
        scene = make_scene(1, last_message_at=None)
        assert engagements_svc.scenes_to_close([scene], timeout_minutes=30, now=self.NOW) == []

    def test_closes_every_idle_engagement_not_just_one(self):
        scene_a = make_scene(1, last_message_at=self.NOW - dt.timedelta(hours=1))
        scene_b = make_scene(2, last_message_at=self.NOW - dt.timedelta(hours=2))
        result = engagements_svc.scenes_to_close(
            [scene_a, scene_b], timeout_minutes=30, now=self.NOW
        )
        assert set(result) == {1, 2}
