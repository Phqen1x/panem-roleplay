"""`/help` -- a categorized reference of every slash command.

Reads the live command tree instead of a hand-maintained list, so it can't
drift out of sync with what's actually registered: adding, renaming, or
re-describing a command elsewhere is automatically reflected here. This
also means every top-level command/group is captured automatically --
earlier versions of this file paired a hardcoded category whitelist with
the tree walk, which silently dropped any group/command whose name wasn't
in that list (`/job`, `/market`, `/resident`, `/travel`, `/where`, `/time`,
`/work`, `/inventory` all went missing this way).
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

GENERAL_KEY = "general"
STAFF_KEY = "staff"
CATEGORY_LABELS: dict[str, str] = {
    GENERAL_KEY: "General",
    "character": "Characters",
    "job": "Jobs",
    "market": "Market",
    "resident": "Residents",
    "scene": "Scenes",
    STAFF_KEY: "Staff",
}
FIELD_VALUE_LIMIT = 1024
SELECT_TIMEOUT_SECONDS = 180.0


def _label_for(key: str) -> str:
    return CATEGORY_LABELS.get(key, key.replace("_", " ").title())


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


def _build_categories(
    tree_commands: list[app_commands.Group | app_commands.Command | app_commands.ContextMenu],
) -> dict[str, list[tuple[str, str, str]]]:
    """Every top-level group becomes its own category, keyed by the group's
    own name; every top-level *command* (not in a group) falls into a
    shared `GENERAL_KEY` bucket -- this is what makes new commands show up
    automatically instead of needing a category list edited by hand.
    Context menu entries (right-click commands, not slash commands) are
    skipped -- `/help` only documents the slash-command tree."""
    categories: dict[str, list[tuple[str, str, str]]] = {GENERAL_KEY: []}
    for top in tree_commands:
        if isinstance(top, app_commands.ContextMenu) or top.name == "help":
            continue
        if isinstance(top, app_commands.Group):
            categories.setdefault(top.name, []).extend(_flatten(top))
        else:
            categories[GENERAL_KEY].extend(_flatten(top))
    return categories


def _split_sections(
    key: str, entries: list[tuple[str, str, str]]
) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """Splits a category's entries by the immediate subgroup a command sits
    under -- every `/staff give ...` command lands in one "Give" section,
    every `/staff scene ...` command in one "Scene" section, and so on;
    bare top-level commands in the category (`/staff whois`, `/staff kill`,
    ...) share one section labeled after the category itself. A category
    with no nested subgroups (`/job`, `/market`, ...) ends up as a single
    section, same as before this split existed."""
    prefix = f"{key} "
    bare: list[tuple[str, str, str]] = []
    subgroups: dict[str, list[tuple[str, str, str]]] = {}
    for entry in entries:
        name = entry[0]
        remainder = name[len(prefix) :] if name.startswith(prefix) else name
        if " " in remainder:
            subgroup, _, _ = remainder.partition(" ")
            subgroups.setdefault(subgroup, []).append(entry)
        else:
            bare.append(entry)

    sections: list[tuple[str, list[tuple[str, str, str]]]] = []
    if bare:
        sections.append((_label_for(key), bare))
    for subgroup in sorted(subgroups):
        sections.append((_label_for(subgroup), subgroups[subgroup]))
    return sections


def _chunk_lines(lines: list[str], limit: int = FIELD_VALUE_LIMIT) -> list[str]:
    """Greedily packs `lines` into as few `\\n\\n`-joined chunks as fit
    under `limit` each, splitting a section across multiple embed fields
    instead of truncating it with an ellipsis and losing every command
    past the cutoff -- what a single, hard-truncated field used to do
    once a subgroup (`/staff give ...`, `/staff npc ...`) grew past
    `FIELD_VALUE_LIMIT`. A single entry that's somehow longer than
    `limit` all on its own (not possible with today's descriptions, but
    not assumed away either) still gets truncated -- there's no line to
    split it across."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        extra = len(line) + (2 if current else 0)  # "\n\n" joiner
        if current and current_len + extra > limit:
            chunks.append("\n\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += extra
    if current:
        chunks.append("\n\n".join(current))
    return [c if len(c) <= limit else c[: limit - 1] + "…" for c in chunks]


def _build_embed(key: str, entries: list[tuple[str, str, str]]) -> discord.Embed:
    """One embed field per section (see `_split_sections`) rather than one
    giant block of text in `embed.description` -- the latter is what this
    used to do, silently cutting the Staff category off partway through
    once `/staff give ...`/`/staff job ...`/`/staff scene ...` pushed it
    past `FIELD_VALUE_LIMIT`. Fields also read better once a category has
    more than a handful of commands, staff or not. A section itself can
    still outgrow one field's `FIELD_VALUE_LIMIT` (`/staff give ...`/
    `/staff npc ...` both do) -- `_chunk_lines` splits it across as many
    fields as it needs (labeled "(cont.)") rather than truncating it and
    silently dropping the commands past the cutoff.
    """
    embed = discord.Embed(
        title=f"Commands -- {_label_for(key)}",
        description="`<required>` / `[optional]` show each command's arguments.",
        color=discord.Color.blurple(),
    )
    if not entries:
        embed.description = "No commands in this category."
        return embed
    for label, section_entries in _split_sections(key, entries):
        lines = [
            f"**/{name}** {usage}\n{desc}".strip() for name, desc, usage in sorted(section_entries)
        ]
        for i, value in enumerate(_chunk_lines(lines)):
            field_label = label if i == 0 else f"{label} (cont.)"
            embed.add_field(name=field_label, value=value, inline=False)
    return embed


class HelpCategorySelect(discord.ui.Select["HelpView"]):
    def __init__(
        self, categories: dict[str, list[tuple[str, str, str]]], *, is_staff: bool
    ) -> None:
        keys = [GENERAL_KEY, *sorted(k for k in categories if k not in (GENERAL_KEY, STAFF_KEY))]
        if is_staff and categories.get(STAFF_KEY):
            keys.append(STAFF_KEY)
        options = [
            discord.SelectOption(label=_label_for(key), value=key, default=(key == GENERAL_KEY))
            for key in keys
            if categories.get(key)
        ]
        super().__init__(
            placeholder="Choose a category…", options=options, min_values=1, max_values=1
        )
        self.categories = categories

    async def callback(self, interaction: discord.Interaction) -> None:
        key = self.values[0]
        for option in self.options:
            option.default = option.value == key
        embed = _build_embed(key, self.categories.get(key, []))
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    def __init__(
        self, categories: dict[str, list[tuple[str, str, str]]], *, is_staff: bool
    ) -> None:
        super().__init__(timeout=SELECT_TIMEOUT_SECONDS)
        self.add_item(HelpCategorySelect(categories, is_staff=is_staff))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="List every command, grouped by category")
    async def help(self, interaction: discord.Interaction) -> None:
        is_staff = isinstance(interaction.user, discord.Member) and await self.bot.is_staff(
            interaction.user
        )
        categories = _build_categories(self.bot.tree.get_commands())
        embed = _build_embed(GENERAL_KEY, categories.get(GENERAL_KEY, []))
        view = HelpView(categories, is_staff=is_staff)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
