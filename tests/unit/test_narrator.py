from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from panem_bot import narrator
from panem_shared.db.models import DiscordChannel, Scene
from panem_shared.enums import ChannelKind, SceneKind, SceneStatus
from panem_shared.events import Bulletin, NarrationLine


class FakeBot:
    """Just enough of `PanemBot` for `narrator.py`'s handlers: a `db()`
    context manager bound to the test's own session factory, plus mocked
    `outbound`/`get_channel`/`fetch_channel`."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self.outbound = MagicMock()
        self.outbound.enqueue = AsyncMock()
        self.get_channel = MagicMock(return_value=None)
        self.fetch_channel = AsyncMock()

    @asynccontextmanager
    async def db(self):
        async with self._session_factory() as session, session.begin():
            yield session


@pytest.fixture
def bot(db_session_factory) -> FakeBot:
    return FakeBot(db_session_factory)


class TestHandleNarration:
    async def test_enqueues_webhook_send_with_correct_targets(
        self, db_session, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
    ):
        db_session.add(
            Scene(
                district_id=1,
                location_id="square",
                thread_id=111,
                forum_channel_id=222,
                kind=SceneKind.AMBIENT.value,
                title="Square -- ambient",
                status=SceneStatus.OPEN.value,
            )
        )
        db_session.add(
            DiscordChannel(
                district_id=1,
                kind=ChannelKind.FORUM.value,
                channel_id=222,
                webhook_id=333,
                webhook_token="tok",
            )
        )
        await db_session.flush()

        sent: dict[str, object] = {}

        async def fake_send(text, **kwargs):
            sent["text"] = text
            sent.update(kwargs)
            return MagicMock()

        monkeypatch.setattr(
            discord.Webhook, "partial", MagicMock(return_value=MagicMock(send=fake_send))
        )

        event = NarrationLine(tick=1, district_id=1, location_id="square", text="A crowd gathers.")
        await narrator._handle_narration(bot, event)

        assert bot.outbound.enqueue.await_count == 1
        message = bot.outbound.enqueue.await_args.args[0]
        assert message.thread_id == 111
        assert message.forum_channel_id == 222
        assert message.priority == narrator.SendPriority.NARRATOR

        await message.send()
        assert sent["text"] == "A crowd gathers."
        assert sent["username"] == "The Narrator"
        assert sent["thread"].id == 111

    async def test_no_op_when_no_ambient_scene(self, bot: FakeBot):
        event = NarrationLine(tick=1, district_id=1, location_id="nowhere", text="...")
        await narrator._handle_narration(bot, event)
        bot.outbound.enqueue.assert_not_called()

    async def test_no_op_when_forum_has_no_webhook_credentials(self, db_session, bot: FakeBot):
        db_session.add(
            Scene(
                district_id=1,
                location_id="square",
                thread_id=111,
                forum_channel_id=222,
                kind=SceneKind.AMBIENT.value,
                title="Square -- ambient",
                status=SceneStatus.OPEN.value,
            )
        )
        db_session.add(DiscordChannel(district_id=1, kind=ChannelKind.FORUM.value, channel_id=222))
        await db_session.flush()

        event = NarrationLine(tick=1, district_id=1, location_id="square", text="...")
        await narrator._handle_narration(bot, event)
        bot.outbound.enqueue.assert_not_called()


class TestHandleBulletin:
    async def test_enqueues_channel_send_for_board_channel(self, db_session, bot: FakeBot):
        db_session.add(DiscordChannel(district_id=1, kind=ChannelKind.BOARD.value, channel_id=444))
        await db_session.flush()

        fetched_channel = AsyncMock(spec=discord.TextChannel)
        bot.fetch_channel = AsyncMock(return_value=fetched_channel)

        event = Bulletin(tick=1, district_id=1, text="The Capitol announces a decree.")
        await narrator._handle_bulletin(bot, event)

        assert bot.outbound.enqueue.await_count == 1
        message = bot.outbound.enqueue.await_args.args[0]
        assert message.thread_id == 444
        assert message.forum_channel_id == 444
        assert message.priority == narrator.SendPriority.NARRATOR

        await message.send()
        fetched_channel.send.assert_awaited_once_with("The Capitol announces a decree.")

    async def test_no_op_when_no_board_channel(self, bot: FakeBot):
        event = Bulletin(tick=1, district_id=1, text="...")
        await narrator._handle_bulletin(bot, event)
        bot.outbound.enqueue.assert_not_called()


class TestDispatch:
    async def test_routes_narration_line_to_its_handler(self, bot: FakeBot, monkeypatch):
        handle_narration = AsyncMock()
        handle_bulletin = AsyncMock()
        monkeypatch.setattr(narrator, "_handle_narration", handle_narration)
        monkeypatch.setattr(narrator, "_handle_bulletin", handle_bulletin)

        event = NarrationLine(tick=1, district_id=1, location_id="square", text="...")
        await narrator._dispatch(bot, event.model_dump_json())

        handle_narration.assert_awaited_once()
        handle_bulletin.assert_not_awaited()

    async def test_routes_bulletin_to_its_handler(self, bot: FakeBot, monkeypatch):
        handle_narration = AsyncMock()
        handle_bulletin = AsyncMock()
        monkeypatch.setattr(narrator, "_handle_narration", handle_narration)
        monkeypatch.setattr(narrator, "_handle_bulletin", handle_bulletin)

        event = Bulletin(tick=1, district_id=1, text="...")
        await narrator._dispatch(bot, event.model_dump_json())

        handle_bulletin.assert_awaited_once()
        handle_narration.assert_not_awaited()


class FakePubSub:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self._messages = messages
        self.subscribed_to: str | None = None
        self.unsubscribed_from: str | None = None
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed_to = channel

    async def listen(self):
        for message in self._messages:
            yield message

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed_from = channel

    async def aclose(self) -> None:
        self.closed = True


class FakeRedis:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self._pubsub = FakePubSub(messages)

    def pubsub(self):
        return self._pubsub


class TestRun:
    async def test_survives_a_malformed_payload_and_cleans_up_on_exit(self, bot: FakeBot):
        good_event = NarrationLine(tick=1, district_id=1, location_id="nowhere", text="...")
        messages: list[dict[str, object]] = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": "not valid json"},
            {"type": "message", "data": good_event.model_dump_json()},
        ]
        fake_redis = FakeRedis(messages)
        bot.redis = fake_redis  # type: ignore[attr-defined]

        await narrator.run(bot)

        assert fake_redis._pubsub.subscribed_to == narrator.WORLD_EVENTS_CHANNEL
        assert fake_redis._pubsub.unsubscribed_from == narrator.WORLD_EVENTS_CHANNEL
        assert fake_redis._pubsub.closed is True
        # the malformed message didn't stop the loop from reaching the good one
        bot.outbound.enqueue.assert_not_called()  # no ambient scene seeded for "nowhere"
