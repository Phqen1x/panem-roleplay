from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord

from panem_bot.services.staff import log_staff_action
from panem_shared.db.models import StaffAction


class FakeSettings:
    def __init__(self, log_channel_id: int = 555) -> None:
        self.log_channel_id = log_channel_id


class FakeBot:
    def __init__(self, channel: object | None) -> None:
        self.settings = FakeSettings()
        self.get_channel = MagicMock(return_value=channel)


class TestLogStaffAction:
    async def test_writes_a_row_regardless_of_bot(self, db_session):
        row = await log_staff_action(db_session, staff_discord_id=42, action="kill", target="7")
        await db_session.flush()

        assert row.id is not None
        assert row.staff_discord_id == 42
        assert row.action == "kill"
        assert row.target == "7"
        assert row.payload == {}

    async def test_defaults_payload_to_empty_dict(self, db_session):
        row = await log_staff_action(
            db_session, staff_discord_id=1, action="note", target="1", payload=None
        )
        assert row.payload == {}

    async def test_posts_to_the_log_channel_when_bot_given(self, db_session):
        log_channel = AsyncMock(spec=discord.TextChannel)
        bot = FakeBot(log_channel)

        await log_staff_action(
            db_session,
            staff_discord_id=42,
            action="give_money",
            target="7",
            payload={"amount": 100},
            bot=bot,  # type: ignore[arg-type]
        )

        log_channel.send.assert_awaited_once()
        text = log_channel.send.await_args.args[0]
        assert "give_money" in text
        assert "7" in text

    async def test_no_op_when_log_channel_not_configured(self, db_session):
        bot = FakeBot(None)
        await log_staff_action(
            db_session,
            staff_discord_id=1,
            action="kill",
            target="1",
            bot=bot,  # type: ignore[arg-type]
        )  # no raise

    async def test_send_failure_is_caught_and_logged(self, db_session):
        log_channel = AsyncMock(spec=discord.TextChannel)
        log_channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "nope"))
        bot = FakeBot(log_channel)

        await log_staff_action(
            db_session,
            staff_discord_id=1,
            action="kill",
            target="1",
            bot=bot,  # type: ignore[arg-type]
        )  # no raise

    async def test_no_bot_means_no_discord_call_at_all(self, db_session):
        # Regression guard: log_staff_action must remain usable with no
        # `bot` at all (its signature default), same as before this
        # feature existed.
        row = await log_staff_action(db_session, staff_discord_id=1, action="kill", target="1")
        assert isinstance(row, StaffAction)
