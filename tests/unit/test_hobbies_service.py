from __future__ import annotations

import random

import pytest
from sqlalchemy import select

from panem_bot.errors import LimitReached, NotAllowed, ValidationFailed
from panem_shared import constants
from panem_shared import hobbies as hobbies_svc
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import Collectible, CollectibleDrop, Hobby
from panem_shared.db.models import (
    Character,
    CharacterHobby,
    CollectibleInstance,
    Inventory,
    User,
)
from panem_shared.enums import CharacterStatus, OwnerKind


def make_user(**overrides: object) -> User:
    defaults: dict[str, object] = dict(discord_id=12345)
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Angler",
        age=20,
        status=CharacterStatus.APPROVED.value,
        money=0,
        reputation=0.0,
    )
    defaults.update(overrides)
    return Character(**defaults)  # type: ignore[arg-type]


def make_content() -> ContentBundle:
    fishing = Hobby(
        id="fishing",
        name="Fishing",
        verb="cast your line",
        tool_good_id="fishing_rod",
        rare_find_chain="lost_item",
        drops=[
            CollectibleDrop(collectible_id="minnow", weight=90, min_level="apprentice"),
            CollectibleDrop(collectible_id="ancient_turtle_shell", weight=10, min_level="master"),
        ],
    )
    return ContentBundle(
        districts={},
        goods={},
        jobs={},
        routes=[],
        hobbies={"fishing": fishing},
        collectibles={
            "minnow": Collectible(id="minnow", name="Minnow", hobby_id="fishing"),
            "ancient_turtle_shell": Collectible(
                id="ancient_turtle_shell", name="Ancient Turtle Shell", hobby_id="fishing",
                rarity="legendary", donation_reward=100,
            ),
        },
    )


async def _persist(session, *rows: object) -> None:
    for row in rows:
        session.add(row)
    await session.flush()


async def _persist_character(session, character: Character) -> None:
    user = make_user()
    session.add(user)
    await session.flush()
    character.user_id = user.id
    session.add(character)
    await session.flush()


class TestSelectHobby:
    async def test_grants_a_free_tool_and_creates_the_row(self, db_session):
        content = make_content()
        character = make_character()
        await _persist_character(db_session, character)

        row = await hobbies_svc.select_hobby(db_session, character, "fishing", content)
        await db_session.flush()

        assert row.hobby_id == "fishing"
        inv = await db_session.get(
            Inventory, (OwnerKind.CHARACTER.value, str(character.id), "fishing_rod")
        )
        assert inv is not None and inv.qty == 1

    async def test_does_not_grant_a_second_tool_if_already_owned(self, db_session):
        content = make_content()
        character = make_character()
        await _persist_character(db_session, character)
        await _persist(
            db_session,
            Inventory(
                owner_kind=OwnerKind.CHARACTER.value,
                owner_id=str(character.id),
                good_id="fishing_rod",
                qty=3,
            ),
        )

        await hobbies_svc.select_hobby(db_session, character, "fishing", content)
        await db_session.flush()

        inv = await db_session.get(
            Inventory, (OwnerKind.CHARACTER.value, str(character.id), "fishing_rod")
        )
        assert inv.qty == 3

    async def test_refuses_picking_the_same_hobby_twice(self, db_session):
        content = make_content()
        character = make_character()
        await _persist_character(db_session, character)
        await hobbies_svc.select_hobby(db_session, character, "fishing", content)
        await db_session.flush()

        with pytest.raises(ValidationFailed):
            await hobbies_svc.select_hobby(db_session, character, "fishing", content)

    async def test_refuses_past_the_cap(self, db_session):
        content = ContentBundle(
            districts={},
            goods={},
            jobs={},
            routes=[],
            hobbies={
                "a": Hobby(id="a", name="A", verb="do a"),
                "b": Hobby(id="b", name="B", verb="do b"),
                "c": Hobby(id="c", name="C", verb="do c"),
                "d": Hobby(id="d", name="D", verb="do d"),
            },
        )
        character = make_character()
        await _persist_character(db_session, character)
        for hobby_id in ("a", "b", "c"):
            await hobbies_svc.select_hobby(db_session, character, hobby_id, content)
            await db_session.flush()

        with pytest.raises(LimitReached):
            await hobbies_svc.select_hobby(db_session, character, "d", content)


class TestCheckCanPlay:
    def test_no_cooldown_on_first_play(self):
        row = CharacterHobby(character_id=1, hobby_id="fishing", last_played_tick=None)
        hobbies_svc.check_can_play(row, 100)  # does not raise

    def test_raises_within_cooldown_window(self):
        row = CharacterHobby(character_id=1, hobby_id="fishing", last_played_tick=10)
        with pytest.raises(NotAllowed):
            hobbies_svc.check_can_play(row, 10 + constants.HOBBY_SESSION_COOLDOWN_TICKS - 1)

    def test_allowed_once_cooldown_elapses(self):
        row = CharacterHobby(character_id=1, hobby_id="fishing", last_played_tick=10)
        hobbies_svc.check_can_play(row, 10 + constants.HOBBY_SESSION_COOLDOWN_TICKS)


class TestRollDrop:
    def test_excludes_drops_above_current_mastery(self):
        content = make_content()
        hobby = content.hobbies["fishing"]
        rng = random.Random(0)
        # Apprentice-level character should never roll the master-gated drop.
        seen = {
            hobbies_svc.roll_drop(hobby, content.collectibles, 0, rng).id for _ in range(200)
        }
        assert seen == {"minnow"}

    def test_master_level_can_roll_the_rare_drop(self):
        content = make_content()
        hobby = content.hobbies["fishing"]
        rng = random.Random(1)
        seen = {
            hobbies_svc.roll_drop(hobby, content.collectibles, 999, rng).id for _ in range(500)
        }
        assert "ancient_turtle_shell" in seen


class TestResolveSession:
    async def test_miss_stamps_cooldown_but_grants_nothing(self, db_session):
        content = make_content()
        character = make_character()
        await _persist_character(db_session, character)
        char_hobby = CharacterHobby(character_id=character.id, hobby_id="fishing")
        await _persist(db_session, char_hobby)

        result = await hobbies_svc.resolve_session(
            db_session,
            character=character,
            character_hobby=char_hobby,
            hobby=content.hobbies["fishing"],
            content=content,
            current_tick=5,
            success=False,
            rng=random.Random(0),
        )
        assert result.success is False
        assert char_hobby.last_played_tick == 5
        assert char_hobby.sessions_played == 0

    async def test_hit_increments_sessions_and_grants_a_collectible(self, db_session):
        content = make_content()
        character = make_character()
        await _persist_character(db_session, character)
        char_hobby = CharacterHobby(character_id=character.id, hobby_id="fishing")
        await _persist(db_session, char_hobby)

        # rng.random() is consulted for the chain roll first (forced above
        # HOBBY_RARE_CHAIN_CHANCE so it never fires), then for drop weighting.
        rng = random.Random()
        rng.random = lambda: 0.99  # type: ignore[method-assign]

        result = await hobbies_svc.resolve_session(
            db_session,
            character=character,
            character_hobby=char_hobby,
            hobby=content.hobbies["fishing"],
            content=content,
            current_tick=5,
            success=True,
            rng=rng,
        )
        assert result.success is True
        assert result.collectible is not None
        assert char_hobby.sessions_played == 1

        row = (
            await db_session.execute(
                select(CollectibleInstance).where(
                    CollectibleInstance.owner_character_id == character.id
                )
            )
        ).scalar_one()
        assert row.collectible_id == result.collectible.id
