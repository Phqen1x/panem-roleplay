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

Also subscribes to Redis `SIM_ALERTS_CHANNEL` (`panem_shared.redis_keys`)
and forwards whatever `panem_sim` publishes there straight to
`Settings.log_channel_id` -- previously nothing in the bot listened on
that channel at all, so a tick failure that paused the whole sim was
completely invisible in Discord.

Runs as one long-lived background task for the process's lifetime,
started from `PanemBot.setup_hook`, not a cog -- it owns no commands or
`discord.py` event listeners.
"""

from __future__ import annotations

import contextlib
import random
from typing import TYPE_CHECKING, Any

import discord
from sqlalchemy import select

from panem_bot.outbound import OutboundMessage, SendPriority
from panem_bot.services import dialogue as dialogue_svc
from panem_bot.strings import t
from panem_shared import constants
from panem_shared.db.models import (
    Character,
    DialogueSettings,
    DiscordChannel,
    Npc,
    PanemHistoryEntry,
    Scene,
    User,
    WorldLoreSettings,
)
from panem_shared.enums import ChannelKind, SceneKind
from panem_shared.events import (
    WORLD_EVENTS_CHANNEL,
    Bulletin,
    CharacterDied,
    NarrationLine,
    NpcChatter,
    VitalsLow,
    parse_message,
)
from panem_shared.logging import get_logger
from panem_shared.redis_keys import SIM_ALERTS_CHANNEL
from panem_shared.vitals import Severity

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


_NPC_CHATTER_OPENER = (
    "(( You notice each other nearby and strike up a brief, in-character conversation. ))"
)


async def _handle_npc_chatter(bot: PanemBot, event: NpcChatter) -> None:
    """Two NPCs talking to each other with nobody prompting it --
    `panem_sim.systems.npc_chatter` only decides *that* and *who* (the
    sim never calls the LLM anywhere in this codebase); this generates
    the actual lines and posts each one into the location's pinned
    ambient thread through the district forum's webhook, as that NPC
    (name + avatar) -- the same visual treatment a player's own proxied
    line gets, not "The Narrator". `SendPriority.NPC` (documented on
    `OutboundQueue` itself as existing for exactly this: a burst of NPC
    chatter never delays a player's own message in the same thread)."""
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
                "npc_chatter_no_ambient_scene",
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

        first_id, second_id = event.npc_ids
        first = await session.get(Npc, first_id)
        second = await session.get(Npc, second_id)

        history_entries = (await session.execute(select(PanemHistoryEntry))).scalars().all()
        world_lore = await session.get(WorldLoreSettings, 1)
        world_notes = world_lore.alternate_universe_notes if world_lore is not None else None
        dialogue_settings = await session.get(DialogueSettings, 1)
        dialogue_enabled = dialogue_settings is None or dialogue_settings.enabled

    if not dialogue_enabled:
        # The staff kill switch (`/staff dialogue disable`) silences NPCs
        # entirely, not just the LLM -- no ambient chatter at all rather
        # than falling back to a burst of canned lines.
        return

    if forum_row is None or forum_row.webhook_id is None or forum_row.webhook_token is None:
        logger.warning(
            "npc_chatter_no_webhook", district_id=event.district_id, location_id=event.location_id
        )
        return
    if first is None or second is None:
        return

    district = bot.content.district(event.district_id)
    location = next((loc for loc in district.locations if loc.id == event.location_id), None)
    if location is None:
        return

    speakers = (first, second)
    line_count = random.randint(constants.NPC_CHATTER_MIN_LINES, constants.NPC_CHATTER_MAX_LINES)
    transcript: list[tuple[str, str]] = []
    lines: list[tuple[Npc, str]] = []
    for i in range(line_count):
        speaker = speakers[i % 2]
        listener = speakers[(i + 1) % 2]
        prior = transcript[:-1] if transcript else []
        trigger = transcript[-1][1] if transcript else _NPC_CHATTER_OPENER
        history = [
            {"role": "assistant" if npc_id == speaker.id else "user", "content": text}
            for npc_id, text in prior
        ]
        reply = await dialogue_svc.generate_npc_to_npc_reply(
            npc=speaker,
            other_npc=listener,
            district=district,
            location=location,
            message=trigger,
            settings=bot.settings,
            history=history,
            history_entries=history_entries,
            world_notes=world_notes,
            dialogue_enabled=dialogue_enabled,
        )
        transcript.append((speaker.id, reply))
        lines.append((speaker, reply))

    webhook = discord.Webhook.partial(forum_row.webhook_id, forum_row.webhook_token, client=bot)
    thread_id = scene.thread_id
    forum_channel_id = scene.forum_channel_id

    for speaker, reply in lines:

        async def send(speaker: Npc = speaker, reply: str = reply) -> None:
            await webhook.send(
                reply,
                username=speaker.name,
                avatar_url=speaker.avatar_url or discord.utils.MISSING,
                thread=discord.Object(id=thread_id),
                wait=True,
            )

        await bot.outbound.enqueue(
            OutboundMessage(
                thread_id=thread_id,
                forum_channel_id=forum_channel_id,
                priority=SendPriority.NPC,
                send=send,
            )
        )


async def _handle_character_died(bot: PanemBot, event: CharacterDied) -> None:
    """A Simulation-mode character's `health` bottomed out
    (`panem_sim.systems.needs`/`apply_auto_death`) -- DM the owner so a
    death that happened with nobody watching the tick doesn't go
    unnoticed."""
    guild = bot.get_guild(bot.settings.discord_guild_id)
    if guild is None:
        logger.warning("character_died_no_guild", character_id=event.character_id)
        return

    async with bot.db() as session:
        character = await session.get(Character, event.character_id)
        user = await session.get(User, character.user_id) if character is not None else None

    if character is None or user is None:
        logger.warning("character_died_no_character", character_id=event.character_id)
        return

    member = guild.get_member(user.discord_id)
    if member is None:
        try:
            member = await guild.fetch_member(user.discord_id)
        except discord.HTTPException:
            logger.warning("character_died_no_member", character_id=event.character_id)
            return

    with contextlib.suppress(discord.Forbidden):
        await member.send(
            t(
                "character_death_dm",
                name=character.name,
                cause=character.death_cause or "Unknown.",
            )
        )


VITAL_LABELS: dict[str, str] = {
    "health": "health",
    "hunger": "hunger",
    "thirst": "thirst",
    "fatigue": "fatigue",
    "sanity": "sanity",
}
"""`VitalsLow.vital`/`dock_fatigue`'s crossing-severity values are plain
lowercase keys (`panem_shared.vitals`'s own vocabulary) -- these are the
words that actually read naturally mid-sentence in `vitals_low_dm`
("their **hunger** is...", not "their **Hunger** is...")."""

VITAL_ADVICE: dict[str, str] = {
    "health": "Use a medical good (`/heal`) -- health this low risks death.",
    "hunger": "Eat something (`/eat`) before it starts costing you health.",
    "thirst": "Drink something (`/drink`) before it starts costing you health.",
    "fatigue": (
        "Rest restores it automatically overnight (more with a roof over your head, "
        "`/housing`), or energize with a coffee/tea good (`/energize`)."
    ),
    "sanity": "Entertain yourself (`/entertain`) or spend some time roleplaying to bring it back up.",
}
"""What `vitals_low_dm` tells the player to actually do about it --
keyed the same as `VITAL_LABELS`."""

SEVERITY_WORDS: dict[Severity, str] = {"warn": "getting low", "danger": "critically low"}


async def send_vitals_low_dm(
    bot: PanemBot, *, character_id: int, vital: str, severity: Severity, percent_remaining: float
) -> None:
    """DMs a character's owner that one of their meters just crossed a
    warning threshold -- shared by `_handle_vitals_low` (events `panem_
    sim.systems.needs` publishes for hunger/thirst/health/sanity) and
    `panem_bot.cogs.proxy._apply_rp_credit` (fatigue's own crossing,
    detected directly from `panem_shared.housing.dock_fatigue`'s return
    value rather than a published event -- fatigue never changes inside
    `panem_sim` at all, only here, so there's nothing for the sim to
    publish). Same "DM best-effort, never raises" posture as `_handle_
    character_died` above."""
    guild = bot.get_guild(bot.settings.discord_guild_id)
    if guild is None:
        logger.warning("vitals_low_no_guild", character_id=character_id)
        return

    async with bot.db() as session:
        character = await session.get(Character, character_id)
        user = await session.get(User, character.user_id) if character is not None else None

    if character is None or user is None:
        logger.warning("vitals_low_no_character", character_id=character_id)
        return

    member = guild.get_member(user.discord_id)
    if member is None:
        try:
            member = await guild.fetch_member(user.discord_id)
        except discord.HTTPException:
            logger.warning("vitals_low_no_member", character_id=character_id)
            return

    with contextlib.suppress(discord.Forbidden):
        await member.send(
            t(
                "vitals_low_dm",
                name=character.name,
                vital=VITAL_LABELS.get(vital, vital),
                severity=SEVERITY_WORDS[severity],
                percent=round(percent_remaining),
                advice=VITAL_ADVICE.get(vital, ""),
            )
        )


async def _handle_vitals_low(bot: PanemBot, event: VitalsLow) -> None:
    await send_vitals_low_dm(
        bot,
        character_id=event.character_id,
        vital=event.vital,
        severity=event.severity,
        percent_remaining=event.percent_remaining,
    )


async def _dispatch(bot: PanemBot, raw: Any) -> None:
    event = parse_message(raw)
    if isinstance(event, NarrationLine):
        await _handle_narration(bot, event)
    elif isinstance(event, Bulletin):
        await _handle_bulletin(bot, event)
    elif isinstance(event, NpcChatter):
        await _handle_npc_chatter(bot, event)
    elif isinstance(event, CharacterDied):
        await _handle_character_died(bot, event)
    elif isinstance(event, VitalsLow):
        await _handle_vitals_low(bot, event)


async def _handle_sim_alert(bot: PanemBot, raw: str) -> None:
    """FR-TCK-3: `panem_sim` publishes here when a tick fails twice in a
    row and pauses the whole world -- the single most operationally
    important thing this bot can tell staff about, so it goes straight to
    `Settings.log_channel_id` rather than through the `OutboundQueue`
    (there's no roleplay/narration priority this should ever wait behind).
    A missing/misconfigured log channel is loudly logged rather than
    silently dropping the alert."""
    channel = bot.get_channel(bot.settings.log_channel_id)
    if not isinstance(channel, discord.TextChannel):
        logger.warning("sim_alert_no_log_channel", message=raw)
        return
    embed = discord.Embed(
        title="🚨 Sim Alert",
        description=raw,
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        logger.exception("sim_alert_send_failed")


async def run(bot: PanemBot) -> None:
    """Long-lived pubsub loop; runs until cancelled at bot shutdown."""
    pubsub = bot.redis.pubsub()
    await pubsub.subscribe(WORLD_EVENTS_CHANNEL, SIM_ALERTS_CHANNEL)
    logger.info("narrator_started")
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                if message["channel"] == SIM_ALERTS_CHANNEL:
                    await _handle_sim_alert(bot, message["data"])
                else:
                    await _dispatch(bot, message["data"])
            except Exception:
                logger.exception("narrator_dispatch_failed")
    finally:
        await pubsub.unsubscribe(WORLD_EVENTS_CHANNEL, SIM_ALERTS_CHANNEL)
        # redis-py's own PubSub.aclose() lacks a return annotation.
        await pubsub.aclose()  # type: ignore[no-untyped-call]
