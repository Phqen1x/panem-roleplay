from __future__ import annotations

import datetime as dt

from panem_bot.services import scenes as scenes_svc
from panem_shared.content.schemas import District, DistrictCulture, DistrictMap, Location
from panem_shared.db.models import Scene
from panem_shared.enums import SceneKind, SceneStatus


def make_district() -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="hob", name="The Hob", kind="market", illicit=True),
        Location(id="station", name="Rail Station", kind="station"),
    ]
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


class TestResolveLocationTag:
    def test_single_valid_tag(self):
        district = make_district()
        result = scenes_svc.resolve_location_tag(["The Square", "Open"], district)
        assert result.location_id == "square"
        assert result.error is None

    def test_no_tag(self):
        district = make_district()
        result = scenes_svc.resolve_location_tag(["Open"], district)
        assert result.error == "no_tag"

    def test_multiple_location_tags(self):
        district = make_district()
        result = scenes_svc.resolve_location_tag(["The Square", "The Hob"], district)
        assert result.error == "multiple_tags"


class TestIsSceneCapReached:
    def test_below_cap(self):
        assert not scenes_svc.is_scene_cap_reached(open_non_ambient_count=5, cap=60)

    def test_at_cap(self):
        assert scenes_svc.is_scene_cap_reached(open_non_ambient_count=60, cap=60)


def make_scene(
    *, scene_id: int, kind: str, status: str, last_message_at: dt.datetime | None
) -> Scene:
    return Scene(
        id=scene_id,
        district_id=12,
        location_id="square",
        thread_id=1000 + scene_id,
        forum_channel_id=1,
        kind=kind,
        title=f"scene {scene_id}",
        status=status,
        last_message_at=last_message_at,
    )


class TestPickScenesToArchive:
    def test_none_when_under_cap(self):
        now = dt.datetime.now(dt.UTC)
        scenes = [
            make_scene(
                scene_id=1,
                kind=SceneKind.PLAYER.value,
                status=SceneStatus.OPEN.value,
                last_message_at=now - dt.timedelta(hours=48),
            )
        ]
        assert scenes_svc.pick_scenes_to_archive(scenes, cap=10, now=now) == []

    def test_picks_oldest_first_over_cap(self):
        now = dt.datetime.now(dt.UTC)
        scenes = [
            make_scene(
                scene_id=i,
                kind=SceneKind.PLAYER.value,
                status=SceneStatus.OPEN.value,
                last_message_at=now - dt.timedelta(hours=25 + i),
            )
            for i in range(5)
        ]
        # cap=3 with 5 open scenes -> archive the 2 oldest (largest offset = oldest)
        to_archive = scenes_svc.pick_scenes_to_archive(scenes, cap=3, now=now)
        assert to_archive == [4, 3]

    def test_never_archives_ambient(self):
        now = dt.datetime.now(dt.UTC)
        scenes = [
            make_scene(
                scene_id=1,
                kind=SceneKind.AMBIENT.value,
                status=SceneStatus.OPEN.value,
                last_message_at=now - dt.timedelta(days=30),
            )
        ]
        assert scenes_svc.pick_scenes_to_archive(scenes, cap=0, now=now) == []

    def test_ignores_recently_active_scenes(self):
        now = dt.datetime.now(dt.UTC)
        scenes = [
            make_scene(
                scene_id=i,
                kind=SceneKind.PLAYER.value,
                status=SceneStatus.OPEN.value,
                last_message_at=now,
            )
            for i in range(5)
        ]
        # over cap, but nothing idle >= 24h -> nothing eligible
        assert scenes_svc.pick_scenes_to_archive(scenes, cap=2, now=now) == []


class TestCanManageScene:
    def test_staff_always_can(self):
        scene = make_scene(
            scene_id=1,
            kind=SceneKind.PLAYER.value,
            status=SceneStatus.OPEN.value,
            last_message_at=None,
        )
        scene.created_by_character_id = None
        assert scenes_svc.can_manage_scene(actor_character_id=None, scene=scene, is_staff=True)

    def test_creator_can(self):
        scene = make_scene(
            scene_id=1,
            kind=SceneKind.PLAYER.value,
            status=SceneStatus.OPEN.value,
            last_message_at=None,
        )
        scene.created_by_character_id = 7
        assert scenes_svc.can_manage_scene(actor_character_id=7, scene=scene, is_staff=False)

    def test_non_creator_cannot(self):
        scene = make_scene(
            scene_id=1,
            kind=SceneKind.PLAYER.value,
            status=SceneStatus.OPEN.value,
            last_message_at=None,
        )
        scene.created_by_character_id = 7
        assert not scenes_svc.can_manage_scene(actor_character_id=8, scene=scene, is_staff=False)
