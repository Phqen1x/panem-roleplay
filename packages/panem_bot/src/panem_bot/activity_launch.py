"""Shared Discord Activity-launch plumbing for crime attempts (`/lockpick`,
`/steal`, `/burgle`) -- mirrors `cogs/jobs.py`'s own `_activity_launch_
view`/`_remember_interaction` pair for `/work`, generalized here since
three separate cogs need the identical Redis round-trip rather than
each cog copying it.

Unlike `/work`, a crime attempt never tries an `embedded_application`
voice-channel invite launch: Discord's Activity URL Mapping is a single
fixed root already pointed at `work.html` (an external Developer Portal
setting this codebase has no way to change -- see `cogs/jobs.py`'s own
`_activity_launch_view` docstring), so an invite-launch here would open
the wrong game entirely. A crime attempt always uses a plain link
instead -- Discord still opens it in the client's in-app browser
overlay rather than a bare external tab, serving the very same
`panem_api` Activity frontend `/work`'s own fallback link does, just
without that extra embedded-iframe step.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Awaitable, Callable

import discord
from discord.ext import commands

from panem_shared import redis_keys


def new_attempt_id() -> str:
    return secrets.token_urlsafe(16)


async def create_crime_attempt(
    bot: commands.Bot, attempt_id: str, payload: dict[str, object]
) -> None:
    await bot.redis.set(  # type: ignore[attr-defined]
        redis_keys.crime_attempt_key(attempt_id),
        json.dumps(payload),
        ex=redis_keys.CRIME_ATTEMPT_TTL_S,
    )


async def remember_crime_interaction(
    bot: commands.Bot, attempt_id: str, interaction: discord.Interaction
) -> None:
    application_id = interaction.application_id or bot.application_id
    if application_id is None:
        return
    await bot.redis.set(  # type: ignore[attr-defined]
        redis_keys.crime_interaction_key(attempt_id),
        json.dumps({"application_id": application_id, "token": interaction.token}),
        ex=redis_keys.CRIME_INTERACTION_TTL_S,
    )


async def forget_crime_attempt(bot: commands.Bot, attempt_id: str) -> None:
    await bot.redis.delete(  # type: ignore[attr-defined]
        redis_keys.crime_attempt_key(attempt_id), redis_keys.crime_interaction_key(attempt_id)
    )


def crime_launch_view(
    activity_url: str,
    kind: str,
    attempt_id: str,
    on_skip: Callable[[discord.Interaction], Awaitable[None]],
) -> discord.ui.View:
    """A "Play" link button to `crime.html` plus a Skip button that
    resolves the attempt right away via the RNG-fallback path instead --
    same shape as `/work`'s launch-message view."""
    url = f"{activity_url.rstrip('/')}/crime.html?attempt_id={attempt_id}&kind={kind}"
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Play", url=url, style=discord.ButtonStyle.link))

    class _SkipButton(discord.ui.Button["discord.ui.View"]):
        def __init__(self) -> None:
            super().__init__(label="Skip (auto-resolve)", style=discord.ButtonStyle.secondary)

        async def callback(self, interaction: discord.Interaction) -> None:
            await on_skip(interaction)

    view.add_item(_SkipButton())
    return view
