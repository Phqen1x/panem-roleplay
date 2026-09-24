"""`EngagementCog._actor_can_manage` -- who's allowed to run `/engage end`
(and every other creator-gated engagement action): the character who
started the engagement, or staff. A participant who merely joined isn't
enough (mirrors `SceneCog._actor_can_manage`).
"""

from __future__ import annotations

import pytest

from panem_bot.cogs.engagements import EngagementCog
from panem_shared.db.models import Character, Scene, User
from panem_shared.enums import CharacterStatus, SceneKind, SceneStatus


class FakeBot:
    pass


@pytest.fixture
def cog() -> EngagementCog:
    return EngagementCog(FakeBot())  # type: ignore[arg-type]


async def _seed(
    db_session, *, starter_discord_id: int, joiner_discord_id: int
) -> tuple[Scene, Character]:
    starter_user = User(discord_id=starter_discord_id)
    joiner_user = User(discord_id=joiner_discord_id)
    db_session.add_all([starter_user, joiner_user])
    await db_session.flush()

    starter_char = Character(
        user_id=starter_user.id,
        district_id=1,
        current_district_id=1,
        name="Starter",
        age=20,
        status=CharacterStatus.APPROVED.value,
    )
    joiner_char = Character(
        user_id=joiner_user.id,
        district_id=1,
        current_district_id=1,
        name="Joiner",
        age=20,
        status=CharacterStatus.APPROVED.value,
    )
    db_session.add_all([starter_char, joiner_char])
    await db_session.flush()

    scene = Scene(
        district_id=1,
        location_id="square",
        thread_id=111,
        forum_channel_id=222,
        kind=SceneKind.ENGAGEMENT.value,
        title="Test engagement",
        status=SceneStatus.OPEN.value,
        created_by_character_id=starter_char.id,
        participants={"characters": [starter_char.id, joiner_char.id], "npcs": ["npc1"]},
    )
    db_session.add(scene)
    await db_session.flush()
    return scene, joiner_char


class TestActorCanManage:
    async def test_the_character_who_started_it_can_manage(self, db_session, cog: EngagementCog):
        scene, _joiner = await _seed(db_session, starter_discord_id=1, joiner_discord_id=2)
        assert await cog._actor_can_manage(
            db_session, discord_user_id=1, scene=scene, is_staff=False
        )

    async def test_a_participant_who_only_joined_cannot_manage(
        self, db_session, cog: EngagementCog
    ):
        scene, _joiner = await _seed(db_session, starter_discord_id=1, joiner_discord_id=2)
        assert not await cog._actor_can_manage(
            db_session, discord_user_id=2, scene=scene, is_staff=False
        )

    async def test_an_unrelated_user_cannot_manage(self, db_session, cog: EngagementCog):
        scene, _joiner = await _seed(db_session, starter_discord_id=1, joiner_discord_id=2)
        assert not await cog._actor_can_manage(
            db_session, discord_user_id=999, scene=scene, is_staff=False
        )

    async def test_staff_can_always_manage_even_without_starting_it(
        self, db_session, cog: EngagementCog
    ):
        scene, _joiner = await _seed(db_session, starter_discord_id=1, joiner_discord_id=2)
        assert await cog._actor_can_manage(
            db_session, discord_user_id=999, scene=scene, is_staff=True
        )

    async def test_no_creator_recorded_means_only_staff_can_manage(
        self, db_session, cog: EngagementCog
    ):
        scene = Scene(
            district_id=1,
            location_id="square",
            thread_id=333,
            forum_channel_id=222,
            kind=SceneKind.AMBIENT.value,
            title="Ambient",
            status=SceneStatus.OPEN.value,
            created_by_character_id=None,
            participants={"npcs": ["npc1"]},
        )
        db_session.add(scene)
        await db_session.flush()
        assert not await cog._actor_can_manage(
            db_session, discord_user_id=1, scene=scene, is_staff=False
        )
        assert await cog._actor_can_manage(
            db_session, discord_user_id=1, scene=scene, is_staff=True
        )
