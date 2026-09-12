"""The single `discord.py` process (Plan §0): owns all Discord I/O."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import discord
import redis.asyncio as redis
from discord import app_commands
from discord.ext import commands
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from panem_bot import narrator
from panem_bot.outbound import OutboundQueue
from panem_bot.services import jobs as jobs_svc
from panem_bot.strings import t
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
    "panem_bot.cogs.travel",
    "panem_bot.cogs.jobs",
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
        self.narrator_task: asyncio.Task[None] | None = None

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

        self.tree.error(self._on_app_command_error)

        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            if self.settings.discord_sync_commands:
                await self.tree.sync(guild=guild)
                logger.info("commands_synced", guild_id=self.settings.discord_guild_id)
            else:
                logger.info(
                    "commands_sync_skipped_by_setting", guild_id=self.settings.discord_guild_id
                )
        else:
            logger.warning("no_guild_id_configured_skipping_command_sync")

        self.narrator_task = asyncio.create_task(narrator.run(self))

    async def close(self) -> None:
        if self.narrator_task is not None:
            self.narrator_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.narrator_task
        await self.redis.aclose()
        await super().close()

    async def _on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            # Every check in this bot (e.g. `_is_staff`) already tells the
            # user why -- "Staff only.", etc. -- before returning False, so
            # there's nothing left to do here and it isn't a bug; without
            # this, discord.py's default handling logs a scary-looking
            # traceback for every single non-staff member who tries a staff
            # command.
            return

        original = error.original if isinstance(error, app_commands.CommandInvokeError) else error
        ref = uuid.uuid4().hex[:8]
        logger.error(
            "app_command_error",
            ref=ref,
            command=interaction.command.qualified_name if interaction.command else None,
            exc_info=(type(original), original, original.__traceback__),
        )
        message = t("unexpected_error", ref=ref)
        with contextlib.suppress(discord.HTTPException):
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)

    async def is_staff(self, member: discord.Member) -> bool:
        return any(role.id == self.settings.staff_role_id for role in member.roles)

    def districts_for_member(self, member: discord.Member) -> list[int]:
        """Every district (or Capitol) role this member holds, resolved the
        same way `setup_guild.py` resolves a district's role: an `.env`
        `CAPITOL_ROLE_ID` / `DISTRICT_N_ROLE_ID` override by id if set,
        otherwise a role named after the district. Members pick their
        district role during onboarding, so this -- not a menu at character
        creation -- is what a character's district is allowed to be."""
        member_role_ids = {role.id for role in member.roles}
        matches: list[int] = []
        for district in self.content.districts.values():
            override_id = self.settings.role_id_override_for_district(district.id)
            if override_id:
                if override_id in member_role_ids:
                    matches.append(district.id)
                continue
            if discord.utils.get(member.roles, name=district.name) is not None:
                matches.append(district.id)
        return matches

    async def all_jobs(self) -> dict[str, Job]:
        """`jobs.yaml` with staff-edited `job_overrides` layered on top
        (`/staff job set|remove`) -- always the source of truth for job
        lookups, never `self.content.jobs` directly."""
        async with self.db() as session:
            return await jobs_svc.get_all_jobs(session, self.content)

    async def jobs_for_district(self, district_id: int) -> list[Job]:
        jobs = await self.all_jobs()
        return [job for job in jobs.values() if job.district == district_id]
