"""`CharacterCog`'s staff-approval-embed posting (`panem_bot.cogs.
characters`) -- specifically the idempotency guard added alongside the
instant Redis-pubsub announce path (`_listen_for_pending_characters`),
which now races the pre-existing `_announce_pending_characters` poll for
the same character. Mirrors `test_narrator.py`'s `FakeBot` pattern: real
DB session, mocked Discord-facing calls.
"""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from panem_bot.cogs.characters import CharacterCog
from panem_shared.db.models import Character, User
from panem_shared.enums import CharacterStatus


class FakeDistrict:
    name = "District 1"


class FakeContent:
    def district(self, district_id: int) -> FakeDistrict:
        return FakeDistrict()


class FakeSettings:
    approval_channel_id = 777


class FakeBot:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self.settings = FakeSettings()
        self.content = FakeContent()
        channel = MagicMock()
        channel.send = AsyncMock()
        self.get_channel = MagicMock(return_value=channel)

    @asynccontextmanager
    async def db(self):
        async with self._session_factory() as session, session.begin():
            yield session

    @property
    def channel(self):
        return self.get_channel.return_value


@pytest.fixture
def bot(db_session_factory) -> FakeBot:
    return FakeBot(db_session_factory)


@pytest.fixture
def cog(bot: FakeBot) -> CharacterCog:
    cog = CharacterCog.__new__(CharacterCog)
    cog.bot = bot
    cog.approval_view = MagicMock()
    return cog


async def _seed_character(db_session, *, approval_notified_at: dt.datetime | None) -> Character:
    user = User(discord_id=42)
    db_session.add(user)
    await db_session.flush()
    character = Character(
        user_id=user.id,
        district_id=1,
        current_district_id=1,
        name="Wren",
        age=20,
        status=CharacterStatus.PENDING.value,
        approval_notified_at=approval_notified_at,
    )
    db_session.add(character)
    await db_session.flush()
    return character


class TestPostApprovalEmbedIdempotency:
    async def test_posts_and_stamps_when_not_yet_notified(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        character = await _seed_character(db_session, approval_notified_at=None)

        await cog._post_approval_embed(character.id, applicant_discord_id=42)

        bot.channel.send.assert_awaited_once()
        # `cog._post_approval_embed` commits through its own session (`bot.db()`);
        # `populate_existing` forces this session's identity-mapped copy to
        # re-read rather than return its now-stale cached version.
        refreshed = await db_session.get(Character, character.id, populate_existing=True)
        assert refreshed.approval_notified_at is not None

    async def test_no_op_when_already_notified(self, db_session, bot: FakeBot, cog: CharacterCog):
        already = dt.datetime.now(dt.UTC)
        character = await _seed_character(db_session, approval_notified_at=already)

        await cog._post_approval_embed(character.id, applicant_discord_id=42)

        bot.channel.send.assert_not_awaited()

    async def test_no_op_when_no_approval_channel_configured(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        bot.get_channel = MagicMock(return_value=None)
        character = await _seed_character(db_session, approval_notified_at=None)

        await cog._post_approval_embed(character.id, applicant_discord_id=42)  # no raise


class TestHandleEditSubmit:
    """`/character edit`'s resubmission -- specifically the "Request
    Changes" round trip: staff's "Request Changes" button leaves
    `approval_notified_at` set (from the character's *original* post), so
    without clearing it here, `_post_approval_embed`'s own idempotency
    guard (see `TestPostApprovalEmbedIdempotency` above) would silently
    swallow every resubmission after the first one."""

    async def test_reposts_after_changes_were_requested(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        already = dt.datetime.now(dt.UTC)
        character = await _seed_character(db_session, approval_notified_at=already)
        interaction = MagicMock()
        interaction.user.id = 42
        interaction.response.send_message = AsyncMock()

        await cog._handle_edit_submit(
            interaction,
            character.id,
            "Wren",
            21,
            "Tall.",
            "A weaver.",
            "",
            "morning",
        )

        bot.channel.send.assert_awaited_once()
        refreshed = await db_session.get(Character, character.id, populate_existing=True)
        assert refreshed.approval_notified_at is not None
        assert refreshed.age == 21


class TestTryAnnounce:
    async def test_announces_a_pending_unnotified_character(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        character = await _seed_character(db_session, approval_notified_at=None)

        await cog._try_announce(character.id)

        bot.channel.send.assert_awaited_once()

    async def test_skips_an_already_notified_character(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        already = dt.datetime.now(dt.UTC)
        character = await _seed_character(db_session, approval_notified_at=already)

        await cog._try_announce(character.id)

        bot.channel.send.assert_not_awaited()

    async def test_skips_an_unknown_character_id(self, bot: FakeBot, cog: CharacterCog):
        await cog._try_announce(999999)  # no raise

        bot.channel.send.assert_not_awaited()
