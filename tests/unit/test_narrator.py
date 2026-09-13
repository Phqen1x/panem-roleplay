from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from panem_bot import narrator
from panem_shared.db.models import Character, DiscordChannel, Scene, User
from panem_shared.enums import ChannelKind, CharacterStatus, SceneKind, SceneStatus
from panem_shared.events import Bulletin, CharacterArrived, NarrationLine


class FakeSettings:
    def __init__(self) -> None:
        self.discord_guild_id = 999
        self.log_channel_id = 555

    def role_id_override_for_district(self, district_id: int) -> int:
        return 0  # no override configured -- resolve by role name instead


class FakeDistrict:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeContent:
    def __init__(self) -> None:
        self.districts = {
            1: FakeDistrict("District 1"),
            2: FakeDistrict("District 2"),
            3: FakeDistrict("District 3"),
        }


class FakeBot:
    """Just enough of `PanemBot` for `narrator.py`'s handlers: a `db()`
    context manager bound to the test's own session factory, plus mocked
    `outbound`/`settings`/`content`/`get_guild`."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self.outbound = MagicMock()
        self.outbound.enqueue = AsyncMock()
        self.settings = FakeSettings()
        self.content = FakeContent()
        self.get_guild = MagicMock(return_value=None)
        self.get_channel = MagicMock(return_value=None)

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
    async def test_enqueues_webhook_send_with_correct_targets(
        self, db_session, bot: FakeBot, monkeypatch: pytest.MonkeyPatch
    ):
        db_session.add(DiscordChannel(district_id=1, kind=ChannelKind.BOARD.value, channel_id=555))
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

        event = Bulletin(tick=1, district_id=1, text="The Capitol announces a decree.")
        await narrator._handle_bulletin(bot, event)

        assert bot.outbound.enqueue.await_count == 1
        message = bot.outbound.enqueue.await_args.args[0]
        assert message.thread_id == 555
        assert message.forum_channel_id == 222
        assert message.priority == narrator.SendPriority.NARRATOR

        await message.send()
        assert sent["text"] == "The Capitol announces a decree."
        assert sent["username"] == "The Narrator"
        assert sent["thread"].id == 555

    async def test_no_op_when_no_board_channel(self, bot: FakeBot):
        event = Bulletin(tick=1, district_id=1, text="...")
        await narrator._handle_bulletin(bot, event)
        bot.outbound.enqueue.assert_not_called()

    async def test_no_op_when_forum_has_no_webhook_credentials(self, db_session, bot: FakeBot):
        db_session.add(DiscordChannel(district_id=1, kind=ChannelKind.BOARD.value, channel_id=555))
        db_session.add(DiscordChannel(district_id=1, kind=ChannelKind.FORUM.value, channel_id=222))
        await db_session.flush()

        event = Bulletin(tick=1, district_id=1, text="...")
        await narrator._handle_bulletin(bot, event)
        bot.outbound.enqueue.assert_not_called()


def _make_role(name: str) -> MagicMock:
    role = MagicMock()
    role.name = name
    return role


def _make_guild(roles: list[MagicMock], member: MagicMock | None) -> MagicMock:
    guild = MagicMock()
    guild.roles = roles
    guild.get_role = MagicMock(return_value=None)
    guild.get_member = MagicMock(return_value=member)
    guild.fetch_member = AsyncMock(return_value=member)
    return guild


class TestHandleCharacterArrived:
    async def _seed_character(self, db_session, *, home_district_id: int) -> None:
        user = User(discord_id=42)
        db_session.add(user)
        await db_session.flush()
        db_session.add(
            Character(
                id=1,
                user_id=user.id,
                district_id=home_district_id,
                current_district_id=home_district_id,
                name="Traveler",
                age=20,
                status=CharacterStatus.APPROVED.value,
            )
        )
        await db_session.flush()

    async def test_no_op_when_guild_not_found(self, bot: FakeBot):
        bot.get_guild = MagicMock(return_value=None)
        event = CharacterArrived(tick=1, character_id=1, district_id=2, origin_district_id=1)
        await narrator._handle_character_arrived(bot, event)  # no raise

    async def test_no_op_when_character_not_found(self, bot: FakeBot):
        bot.get_guild = MagicMock(return_value=_make_guild([], None))
        event = CharacterArrived(tick=1, character_id=999, district_id=2, origin_district_id=1)
        await narrator._handle_character_arrived(bot, event)  # no raise

    async def test_grants_destination_and_revokes_origin_when_neither_is_home(
        self, db_session, bot: FakeBot
    ):
        await self._seed_character(db_session, home_district_id=1)
        role_a = _make_role("District 2")
        role_b = _make_role("District 3")
        member = MagicMock()
        member.roles = [role_a]
        member.add_roles = AsyncMock()
        member.remove_roles = AsyncMock()
        bot.get_guild = MagicMock(return_value=_make_guild([role_a, role_b], member))

        event = CharacterArrived(tick=1, character_id=1, district_id=3, origin_district_id=2)
        await narrator._handle_character_arrived(bot, event)

        member.remove_roles.assert_awaited_once_with(role_a, reason="Panem travel: departed")
        member.add_roles.assert_awaited_once_with(role_b, reason="Panem travel: visiting")

    async def test_never_grants_or_revokes_the_home_district_role(self, db_session, bot: FakeBot):
        await self._seed_character(db_session, home_district_id=1)
        role_home = _make_role("District 1")
        member = MagicMock()
        member.roles = [role_home]
        member.add_roles = AsyncMock()
        member.remove_roles = AsyncMock()
        bot.get_guild = MagicMock(return_value=_make_guild([role_home], member))

        # Returning home: origin_district_id=2 (visitor role dropped),
        # district_id=1 is home (never granted -- already held via onboarding).
        event = CharacterArrived(tick=1, character_id=1, district_id=1, origin_district_id=2)
        await narrator._handle_character_arrived(bot, event)

        member.add_roles.assert_not_called()

    async def test_role_swap_failure_is_caught_and_logged(self, db_session, bot: FakeBot):
        await self._seed_character(db_session, home_district_id=1)
        role_a = _make_role("District 2")
        member = MagicMock()
        member.roles = [role_a]
        member.remove_roles = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "nope"))
        bot.get_guild = MagicMock(return_value=_make_guild([role_a], member))

        event = CharacterArrived(tick=1, character_id=1, district_id=3, origin_district_id=2)
        await narrator._handle_character_arrived(bot, event)  # no raise


class TestHandleSimAlert:
    async def test_posts_to_the_log_channel(self, bot: FakeBot):
        log_channel = AsyncMock(spec=discord.TextChannel)
        bot.get_channel = MagicMock(return_value=log_channel)

        await narrator._handle_sim_alert(bot, "Tick failed twice in a row, pausing: boom")

        log_channel.send.assert_awaited_once()
        assert "boom" in log_channel.send.await_args.args[0]

    async def test_no_op_when_log_channel_not_configured(self, bot: FakeBot):
        bot.get_channel = MagicMock(return_value=None)
        await narrator._handle_sim_alert(bot, "boom")  # no raise

    async def test_send_failure_is_caught_and_logged(self, bot: FakeBot):
        log_channel = AsyncMock(spec=discord.TextChannel)
        log_channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "nope"))
        bot.get_channel = MagicMock(return_value=log_channel)

        await narrator._handle_sim_alert(bot, "boom")  # no raise


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

    async def test_routes_character_arrived_to_its_handler(self, bot: FakeBot, monkeypatch):
        handle_arrived = AsyncMock()
        monkeypatch.setattr(narrator, "_handle_character_arrived", handle_arrived)

        event = CharacterArrived(tick=1, character_id=1, district_id=2, origin_district_id=1)
        await narrator._dispatch(bot, event.model_dump_json())

        handle_arrived.assert_awaited_once()

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
        self.subscribed_to: tuple[str, ...] = ()
        self.unsubscribed_from: tuple[str, ...] = ()
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        self.subscribed_to = channels

    async def listen(self):
        for message in self._messages:
            yield message

    async def unsubscribe(self, *channels: str) -> None:
        self.unsubscribed_from = channels

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
            {"type": "subscribe", "data": 1, "channel": narrator.WORLD_EVENTS_CHANNEL},
            {
                "type": "message",
                "data": "not valid json",
                "channel": narrator.WORLD_EVENTS_CHANNEL,
            },
            {
                "type": "message",
                "data": good_event.model_dump_json(),
                "channel": narrator.WORLD_EVENTS_CHANNEL,
            },
        ]
        fake_redis = FakeRedis(messages)
        bot.redis = fake_redis  # type: ignore[attr-defined]

        await narrator.run(bot)

        assert fake_redis._pubsub.subscribed_to == (
            narrator.WORLD_EVENTS_CHANNEL,
            narrator.SIM_ALERTS_CHANNEL,
        )
        assert fake_redis._pubsub.unsubscribed_from == (
            narrator.WORLD_EVENTS_CHANNEL,
            narrator.SIM_ALERTS_CHANNEL,
        )
        assert fake_redis._pubsub.closed is True
        # the malformed message didn't stop the loop from reaching the good one
        bot.outbound.enqueue.assert_not_called()  # no ambient scene seeded for "nowhere"

    async def test_routes_a_sim_alert_to_the_log_channel(self, bot: FakeBot):
        log_channel = AsyncMock(spec=discord.TextChannel)
        bot.get_channel = MagicMock(return_value=log_channel)
        messages: list[dict[str, object]] = [
            {
                "type": "message",
                "data": "Tick failed twice in a row, pausing: boom",
                "channel": narrator.SIM_ALERTS_CHANNEL,
            }
        ]
        bot.redis = FakeRedis(messages)  # type: ignore[attr-defined]

        await narrator.run(bot)

        log_channel.send.assert_awaited_once()
        assert "boom" in log_channel.send.await_args.args[0]
