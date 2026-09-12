"""Ambient narration listener (Plan §4.3, FR-NPC-3).

Subscribes to Redis `world:events` (published by `panem_sim`'s tick loop)
and turns each event into a Discord post through the existing
`OutboundQueue`, so narration follows the same per-thread priority and
rate-limit rules player and NPC messages already do rather than bypassing
them. `NarrationLine` posts into the location's pinned ambient thread,
and `Bulletin` posts into a pinned `Board` thread -- both live inside
that district's own roleplay forum and post through the same forum
webhook, matching how player proxy messages are posted (`scripts/
setup_guild.py`'s `ensure_ambient_posts` / `ensure_board_thread`). A
guild not yet re-run through that script since it started creating the
`Board` thread (or a `discord_channels` row left pointing at a deleted
thread) falls back to a loudly logged no-op / a caught, logged send
failure rather than crashing the tick loop -- re-running `setup_guild.py`
is idempotent and fixes both.

Runs as one long-lived background task for the process's lifetime,
started from `PanemBot.setup_hook`, not a cog -- it owns no commands or
`discord.py` event listeners.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord
from sqlalchemy import select

from panem_bot.outbound import OutboundMessage, SendPriority
from panem_shared.db.models import DiscordChannel, Scene
from panem_shared.enums import ChannelKind, SceneKind
from panem_shared.events import WORLD_EVENTS_CHANNEL, Bulletin, NarrationLine, parse_message
from panem_shared.logging import get_logger

if TYPE_CHECKING:
    from panem_bot.bot import PanemBot

logger = get_logger(component="narrator")


async def _handle_narration(bot: PanemBot, event: NarrationLine) -> None:
    async with bot.db() as session:
        scene = (
            await session.execute(
                select(Scene).where(
                    Scene.district_id == event.district_id,
                    Scene.location_id == event.location_id,
                    Scene.kind == SceneKind.AMBIENT.value,
                )
            )
        ).scalar_one_or_none()
        if scene is None:
            logger.warning(
                "narration_no_ambient_scene",
                district_id=event.district_id,
                location_id=event.location_id,
            )
            return

        forum_row = (
            await session.execute(
                select(DiscordChannel).where(
                    DiscordChannel.channel_id == scene.forum_channel_id,
                    DiscordChannel.kind == ChannelKind.FORUM.value,
                )
            )
        ).scalar_one_or_none()

    if forum_row is None or forum_row.webhook_id is None or forum_row.webhook_token is None:
        logger.warning(
            "narration_no_webhook", district_id=event.district_id, location_id=event.location_id
        )
        return

    webhook = discord.Webhook.partial(forum_row.webhook_id, forum_row.webhook_token, client=bot)
    thread_id = scene.thread_id
    forum_channel_id = scene.forum_channel_id

    async def send() -> None:
        await webhook.send(
            event.text, username="The Narrator", thread=discord.Object(id=thread_id), wait=True
        )

    await bot.outbound.enqueue(
        OutboundMessage(
            thread_id=thread_id,
            forum_channel_id=forum_channel_id,
            priority=SendPriority.NARRATOR,
            send=send,
        )
    )


async def _handle_bulletin(bot: PanemBot, event: Bulletin) -> None:
    """The district's `Board` thread lives inside its own roleplay forum
    (`scripts/setup_guild.py`'s `ensure_board_thread`), so this posts the
    same way `_handle_narration` does -- through that forum's webhook,
    into a thread -- rather than to a standalone channel."""
    async with bot.db() as session:
        board_row = (
            await session.execute(
                select(DiscordChannel).where(
                    DiscordChannel.district_id == event.district_id,
                    DiscordChannel.kind == ChannelKind.BOARD.value,
                )
            )
        ).scalar_one_or_none()
        forum_row = (
            await session.execute(
                select(DiscordChannel).where(
                    DiscordChannel.district_id == event.district_id,
                    DiscordChannel.kind == ChannelKind.FORUM.value,
                )
            )
        ).scalar_one_or_none()

    if board_row is None or forum_row is None:
        logger.warning("bulletin_no_board_channel", district_id=event.district_id)
        return
    if forum_row.webhook_id is None or forum_row.webhook_token is None:
        logger.warning("bulletin_no_webhook", district_id=event.district_id)
        return

    webhook = discord.Webhook.partial(forum_row.webhook_id, forum_row.webhook_token, client=bot)
    thread_id = board_row.channel_id
    forum_channel_id = forum_row.channel_id

    async def send() -> None:
        await webhook.send(
            event.text, username="The Narrator", thread=discord.Object(id=thread_id), wait=True
        )

    await bot.outbound.enqueue(
        OutboundMessage(
            thread_id=thread_id,
            forum_channel_id=forum_channel_id,
            priority=SendPriority.NARRATOR,
            send=send,
        )
    )


async def _dispatch(bot: PanemBot, raw: Any) -> None:
    event = parse_message(raw)
    if isinstance(event, NarrationLine):
        await _handle_narration(bot, event)
    elif isinstance(event, Bulletin):
        await _handle_bulletin(bot, event)


async def run(bot: PanemBot) -> None:
    """Long-lived pubsub loop; runs until cancelled at bot shutdown."""
    pubsub = bot.redis.pubsub()
    await pubsub.subscribe(WORLD_EVENTS_CHANNEL)
    logger.info("narrator_started")
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                await _dispatch(bot, message["data"])
            except Exception:
                logger.exception("narrator_dispatch_failed")
    finally:
        await pubsub.unsubscribe(WORLD_EVENTS_CHANNEL)
        # redis-py's own PubSub.aclose() lacks a return annotation.
        await pubsub.aclose()  # type: ignore[no-untyped-call]
