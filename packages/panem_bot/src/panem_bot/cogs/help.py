"""`/help` -- a categorized reference of every slash command.

Reads the live command tree instead of a hand-maintained list, so it can't
drift out of sync with what's actually registered: adding, renaming, or
re-describing a command elsewhere is automatically reflected here.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

CATEGORY_LABELS: dict[str, str] = {
    "roleplay": "Roleplay",
    "character": "Characters",
    "scene": "Scenes",
    "staff": "Staff",
}
CATEGORY_ORDER = ["roleplay", "character", "scene", "staff"]
ROLEPLAY_COMMANDS = {"rp", "ooc"}
FIELD_VALUE_LIMIT = 1024


def _usage(command: app_commands.Command) -> str:
    parts = []
    for param in command.parameters:
        token = param.display_name or param.name
        parts.append(f"<{token}>" if param.required else f"[{token}]")
    return " ".join(parts)


def _flatten(
    command: app_commands.Group | app_commands.Command, prefix: str = ""
) -> list[tuple[str, str, str]]:
    """Returns `(full_name, description, usage)` for every leaf command under `command`."""
    full_name = f"{prefix}{command.name}"
    if isinstance(command, app_commands.Group):
        lines: list[tuple[str, str, str]] = []
        for child in command.commands:
            lines.extend(_flatten(child, prefix=f"{full_name} "))
        return lines
    return [(full_name, command.description or "-", _usage(command))]


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="List every command, grouped by category")
    async def help(self, interaction: discord.Interaction) -> None:
        is_staff = isinstance(interaction.user, discord.Member) and await self.bot.is_staff(
            interaction.user
        )
        categories: dict[str, list[tuple[str, str, str]]] = {key: [] for key in CATEGORY_ORDER}
        for top in self.bot.tree.get_commands():
            if isinstance(top, app_commands.Group):
                bucket = top.name if top.name in categories else None
            else:
                bucket = "roleplay" if top.name in ROLEPLAY_COMMANDS else None
            if bucket is None:
                continue
            categories[bucket].extend(_flatten(top))

        embed = discord.Embed(
            title="Commands",
            description="Every slash command, grouped by what it's for. "
            "`<required>` / `[optional]` show each command's arguments.",
            color=discord.Color.blurple(),
        )
        for key in CATEGORY_ORDER:
            if key == "staff" and not is_staff:
                continue
            entries = categories.get(key, [])
            if not entries:
                continue
            lines = [
                f"**/{name}** {usage}\n{desc}".strip() for name, desc, usage in sorted(entries)
            ]
            value = "\n\n".join(lines)
            if len(value) > FIELD_VALUE_LIMIT:
                value = value[: FIELD_VALUE_LIMIT - 1] + "…"
            embed.add_field(name=CATEGORY_LABELS.get(key, key.title()), value=value, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
