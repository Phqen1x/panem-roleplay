#!/usr/bin/env python3
"""Idempotent one-time (and re-runnable) guild setup (Plan §3.1 deliverable 2).

For every district (0 = Capitol, 1-12) creates:
  - a category
  - a Forum channel (`#dNN-rp`) with one tag per location plus `Open`/`Closed`,
    `require_tag=True`, default auto-archive = `SCENE_AUTO_ARCHIVE_MINUTES`
  - `#dNN-ooc` and `#dNN-board` text channels
  - a district role (view/post the forum; everyone else can view only)
  - one webhook on the forum channel
  - a pinned, never-archived ambient post per location

Plus guild-wide staff role, approval channel, and log channel.

Every Discord object created is recorded in `discord_channels` /
`scenes` (kind=ambient) so re-running this script is a no-op for anything
already set up: existing DB rows are reconciled against the guild by
name/id rather than recreated blindly.

Usage: `uv run python scripts/setup_guild.py`
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import discord
from sqlalchemy import select

from panem_shared.content.loader import load_content
from panem_shared.content.schemas import District
from panem_shared.db.models import DiscordChannel, Scene
from panem_shared.db.session import make_engine, make_session_factory
from panem_shared.enums import ChannelKind, SceneKind, SceneStatus
from panem_shared.logging import configure_logging, get_logger
from panem_shared.settings import Settings, get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"

OPEN_TAG = "Open"
CLOSED_TAG = "Closed"
STAFF_CATEGORY_NAME = "Panem Staff"
APPROVAL_CHANNEL_NAME = "character-approvals"
LOG_CHANNEL_NAME = "panem-log"
GLOBAL_DISTRICT_SENTINEL = 0  # approval/log channels are guild-wide, filed under district 0

logger = get_logger(component="setup_guild")


def district_category_name(district: District) -> str:
    return district.name


def forum_name(district: District) -> str:
    return f"d{district.id}-rp"


def ooc_name(district: District) -> str:
    return f"d{district.id}-ooc"


def board_name(district: District) -> str:
    return f"d{district.id}-board"


async def ensure_role(guild: discord.Guild, name: str) -> discord.Role:
    role = discord.utils.get(guild.roles, name=name)
    if role is not None:
        return role
    role = await guild.create_role(name=name, reason="Panem setup")
    logger.info("role_created", name=name)
    return role


async def ensure_category(guild: discord.Guild, name: str) -> discord.CategoryChannel:
    category = discord.utils.get(guild.categories, name=name)
    if category is not None:
        return category
    category = await guild.create_category(name, reason="Panem setup")
    logger.info("category_created", name=name)
    return category


async def ensure_text_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    *,
    overwrites: dict[discord.Role, discord.PermissionOverwrite],
) -> discord.TextChannel:
    existing = discord.utils.get(category.text_channels, name=name)
    if existing is not None:
        await existing.edit(overwrites=overwrites)
        return existing
    channel = await guild.create_text_channel(
        name, category=category, overwrites=overwrites, reason="Panem setup"
    )
    logger.info("text_channel_created", name=name)
    return channel


def _location_tags(district: District) -> list[discord.ForumTag]:
    tags = [discord.ForumTag(name=loc.name) for loc in district.locations]
    tags.append(discord.ForumTag(name=OPEN_TAG))
    tags.append(discord.ForumTag(name=CLOSED_TAG))
    return tags


async def ensure_forum(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    district: District,
    *,
    overwrites: dict[discord.Role, discord.PermissionOverwrite],
    auto_archive_minutes: int,
) -> discord.ForumChannel:
    name = forum_name(district)
    existing = discord.utils.get(
        [c for c in category.channels if isinstance(c, discord.ForumChannel)], name=name
    )
    tags = _location_tags(district)
    if existing is not None:
        await existing.edit(
            available_tags=tags,
            require_tag=True,
            default_auto_archive_duration=auto_archive_minutes,
            overwrites=overwrites,
        )
        return existing
    forum = await guild.create_forum(
        name,
        category=category,
        available_tags=tags,
        require_tag=True,
        default_auto_archive_duration=auto_archive_minutes,
        overwrites=overwrites,
        reason="Panem setup",
    )
    logger.info("forum_created", name=name)
    return forum


async def ensure_webhook(forum: discord.ForumChannel) -> discord.Webhook:
    webhooks = await forum.webhooks()
    existing = discord.utils.get(webhooks, name="Panem Proxy")
    if existing is not None:
        return existing
    webhook = await forum.create_webhook(name="Panem Proxy", reason="Panem proxy posting")
    logger.info("webhook_created", forum=forum.name)
    return webhook


async def upsert_discord_channel(
    session,
    *,
    district_id: int,
    kind: ChannelKind,
    channel_id: int,
    webhook: discord.Webhook | None,
) -> None:
    row = (
        await session.execute(
            select(DiscordChannel).where(
                DiscordChannel.district_id == district_id, DiscordChannel.kind == kind.value
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = DiscordChannel(district_id=district_id, kind=kind.value, channel_id=channel_id)
        session.add(row)
    row.channel_id = channel_id
    if webhook is not None:
        row.webhook_id = webhook.id
        row.webhook_token = webhook.token
    await session.flush()


async def ensure_ambient_posts(
    session, guild: discord.Guild, forum: discord.ForumChannel, district: District
) -> None:
    for loc in district.locations:
        title = f"{loc.name} — ambient"
        existing_scene = (
            await session.execute(
                select(Scene).where(
                    Scene.district_id == district.id,
                    Scene.location_id == loc.id,
                    Scene.kind == SceneKind.AMBIENT.value,
                )
            )
        ).scalar_one_or_none()

        if existing_scene is not None:
            thread = guild.get_channel_or_thread(existing_scene.thread_id)
            if thread is not None:
                if isinstance(thread, discord.Thread):
                    await thread.join()
                    if thread.archived:
                        await thread.edit(archived=False)
                continue
            # DB row survived but the thread is gone -- fall through and recreate.

        location_tag = discord.utils.get(forum.available_tags, name=loc.name)
        open_tag = discord.utils.get(forum.available_tags, name=OPEN_TAG)
        applied = [tg for tg in (location_tag, open_tag) if tg is not None]
        thread_with_message = await forum.create_thread(
            name=title,
            content=f"*The narrator settles in at {loc.name}.*",
            applied_tags=applied,
        )
        thread = thread_with_message.thread
        await thread.join()
        try:
            await thread_with_message.message.pin(reason="Ambient post")
        except discord.HTTPException:
            logger.warning("ambient_pin_failed", location=loc.id, district=district.id)

        if existing_scene is not None:
            existing_scene.thread_id = thread.id
            existing_scene.forum_channel_id = forum.id
            existing_scene.status = SceneStatus.OPEN.value
        else:
            session.add(
                Scene(
                    district_id=district.id,
                    location_id=loc.id,
                    thread_id=thread.id,
                    forum_channel_id=forum.id,
                    kind=SceneKind.AMBIENT.value,
                    title=title,
                    created_by_character_id=None,
                    status=SceneStatus.OPEN.value,
                )
            )
        await session.flush()
        logger.info("ambient_post_ready", district=district.id, location=loc.id)


async def setup_district(
    session,
    guild: discord.Guild,
    district: District,
    *,
    staff_role: discord.Role,
    auto_archive_minutes: int,
) -> None:
    role = await ensure_role(guild, district.name)
    category = await ensure_category(guild, district_category_name(district))

    forum_overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True, send_messages=False, send_messages_in_threads=False
        ),
        role: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, send_messages_in_threads=True
        ),
        staff_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            send_messages_in_threads=True,
            manage_threads=True,
        ),
    }
    forum = await ensure_forum(
        guild,
        category,
        district,
        overwrites=forum_overwrites,
        auto_archive_minutes=auto_archive_minutes,
    )
    webhook = await ensure_webhook(forum)
    await upsert_discord_channel(
        session,
        district_id=district.id,
        kind=ChannelKind.FORUM,
        channel_id=forum.id,
        webhook=webhook,
    )

    text_overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        staff_role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    ooc = await ensure_text_channel(guild, category, ooc_name(district), overwrites=text_overwrites)
    await upsert_discord_channel(
        session, district_id=district.id, kind=ChannelKind.OOC, channel_id=ooc.id, webhook=None
    )

    board = await ensure_text_channel(
        guild, category, board_name(district), overwrites=text_overwrites
    )
    await upsert_discord_channel(
        session, district_id=district.id, kind=ChannelKind.BOARD, channel_id=board.id, webhook=None
    )

    await ensure_ambient_posts(session, guild, forum, district)


async def setup_staff_channels(session, guild: discord.Guild, staff_role: discord.Role) -> None:
    category = await ensure_category(guild, STAFF_CATEGORY_NAME)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        staff_role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    approval = await ensure_text_channel(
        guild, category, APPROVAL_CHANNEL_NAME, overwrites=overwrites
    )
    await upsert_discord_channel(
        session,
        district_id=GLOBAL_DISTRICT_SENTINEL,
        kind=ChannelKind.APPROVAL,
        channel_id=approval.id,
        webhook=None,
    )
    log = await ensure_text_channel(guild, category, LOG_CHANNEL_NAME, overwrites=overwrites)
    await upsert_discord_channel(
        session,
        district_id=GLOBAL_DISTRICT_SENTINEL,
        kind=ChannelKind.LOG,
        channel_id=log.id,
        webhook=None,
    )
    logger.info(
        "staff_channels_ready",
        approval_channel_id=approval.id,
        log_channel_id=log.id,
        hint="put these IDs in .env as APPROVAL_CHANNEL_ID / LOG_CHANNEL_ID",
    )


async def run(settings: Settings) -> None:
    content = load_content(DATA_DIR)
    engine = make_engine(settings)
    session_factory = make_session_factory(engine)

    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = True
    client = discord.Client(intents=intents)

    done = asyncio.Event()

    @client.event
    async def on_ready() -> None:
        try:
            guild = client.get_guild(settings.discord_guild_id)
            if guild is None:
                raise SystemExit(
                    f"Bot is not in guild {settings.discord_guild_id}, or DISCORD_GUILD_ID is unset."
                )

            async with session_factory() as session, session.begin():
                staff_role = await ensure_role(guild, "Panem Staff")
                await setup_staff_channels(session, guild, staff_role)
                for district in sorted(content.districts.values(), key=lambda d: d.id):
                    await setup_district(
                        session,
                        guild,
                        district,
                        staff_role=staff_role,
                        auto_archive_minutes=settings.scene_auto_archive_minutes,
                    )
            logger.info("setup_complete", guild_id=guild.id)
        finally:
            done.set()
            await client.close()

    await client.start(settings.discord_token)
    await done.wait()


def main() -> None:
    configure_logging(component="setup_guild")
    settings = get_settings()
    if not settings.discord_token or not settings.discord_guild_id:
        raise SystemExit("DISCORD_TOKEN and DISCORD_GUILD_ID must be set (see .env.example)")
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
