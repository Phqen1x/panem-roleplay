"""The single `discord.py` process (Plan §0): owns all Discord I/O."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import discord
import redis.asyncio as redis
from discord.ext import commands
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from panem_bot.outbound import OutboundQueue
from panem_bot.services import jobs as jobs_svc
from panem_shared.content.loader import ContentBundle, load_content
from panem_shared.content.schemas import Job
from panem_shared.db.session import make_engine, make_session_factory
from panem_shared.logging import get_logger
from panem_shared.settings import Settings

logger = get_logger(component="bot")

INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.guilds = True
INTENTS.members = True
INTENTS.messages = True
INTENTS.reactions = True

COGS = (
    "panem_bot.cogs.characters",
    "panem_bot.cogs.scenes",
    "panem_bot.cogs.proxy",
    "panem_bot.cogs.staff",
    "panem_bot.cogs.help",
)


class PanemBot(commands.Bot):
    """Holds every process-wide dependency cogs need: settings, content,
    a DB session factory, Redis, and the outbound send queue."""

    def __init__(self, *, settings: Settings, data_dir: Path) -> None:
        super().__init__(command_prefix=commands.when_mentioned, intents=INTENTS)
        self.settings = settings
        self.data_dir = data_dir
        self.content: ContentBundle = load_content(data_dir)

        engine = make_engine(settings)
        self.session_factory: async_sessionmaker[AsyncSession] = make_session_factory(engine)
        self.redis: redis.Redis = redis.from_url(settings.redis_url, decode_responses=True)
        self.outbound = OutboundQueue()

    @asynccontextmanager
    async def db(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session, session.begin():
            yield session

    def reload_content(self) -> None:
        """NFR-12: hot-reload; raises `ContentValidationError` and leaves
        `self.content` untouched on failure."""
        self.content = load_content(self.data_dir)

    async def setup_hook(self) -> None:
        for ext in COGS:
            await self.load_extension(ext)

        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("commands_synced", guild_id=self.settings.discord_guild_id)
        else:
            logger.warning("no_guild_id_configured_skipping_command_sync")

    async def close(self) -> None:
        await self.redis.aclose()
        await super().close()

    async def is_staff(self, member: discord.Member) -> bool:
        return any(role.id == self.settings.staff_role_id for role in member.roles)

    async def all_jobs(self) -> dict[str, Job]:
        """`jobs.yaml` with staff-edited `job_overrides` layered on top
        (`/staff job set|remove`) -- always the source of truth for job
        lookups, never `self.content.jobs` directly."""
        async with self.db() as session:
            return await jobs_svc.get_all_jobs(session, self.content)

    async def jobs_for_district(self, district_id: int) -> list[Job]:
        jobs = await self.all_jobs()
        return [job for job in jobs.values() if job.district == district_id]
