from __future__ import annotations

import discord
from discord import app_commands

from panem_bot.cogs.help import GENERAL_KEY, _build_categories, _build_embed, _label_for, _usage


async def _noop(interaction: discord.Interaction) -> None:
    pass


async def _with_args(interaction: discord.Interaction, character: str, amount: int = 0) -> None:
    pass


def make_command(
    name: str, callback: object = _noop, description: str = "desc"
) -> app_commands.Command:
    return app_commands.Command(name=name, description=description, callback=callback)  # type: ignore[arg-type]


def make_group(
    name: str, *children: app_commands.Command, description: str = "desc"
) -> app_commands.Group:
    group = app_commands.Group(name=name, description=description)
    for child in children:
        group.add_command(child)
    return group


class TestBuildCategories:
    def test_ungrouped_commands_land_in_general(self):
        top = [make_command("where"), make_command("time")]
        categories = _build_categories(top)
        names = {name for name, _, _ in categories[GENERAL_KEY]}
        assert names == {"where", "time"}

    def test_help_itself_is_excluded(self):
        top = [make_command("help"), make_command("where")]
        categories = _build_categories(top)
        names = {name for name, _, _ in categories[GENERAL_KEY]}
        assert "help" not in names
        assert "where" in names

    def test_group_commands_get_their_own_category_keyed_by_group_name(self):
        job_group = make_group("job", make_command("list"), make_command("apply"))
        categories = _build_categories([job_group])
        assert categories[GENERAL_KEY] == []
        names = {name for name, _, _ in categories["job"]}
        assert names == {"job list", "job apply"}

    def test_nested_groups_flatten_with_full_dotted_path(self):
        give_group = make_group("give", make_command("money"), make_command("item"))
        staff_group = app_commands.Group(name="staff", description="staff")
        staff_group.add_command(give_group)
        categories = _build_categories([staff_group])
        names = {name for name, _, _ in categories["staff"]}
        assert names == {"staff give money", "staff give item"}

    def test_every_registered_top_level_command_is_captured(self):
        """Regression test for the actual bug reported: /job, /market,
        /resident, /travel, /where, /time, /work, /inventory used to be
        silently dropped by a hardcoded category whitelist."""
        top = [
            make_command("work"),
            make_command("inventory"),
            make_command("rp"),
            make_command("ooc"),
            make_command("where"),
            make_command("time"),
            make_command("travel"),
            make_group("job", make_command("list")),
            make_group("market", make_command("prices")),
            make_group("resident", make_command("list")),
            make_group("scene", make_command("start")),
            make_group("character", make_command("create")),
        ]
        categories = _build_categories(top)
        all_names = {name for entries in categories.values() for name, _, _ in entries}
        assert all_names == {
            "work",
            "inventory",
            "rp",
            "ooc",
            "where",
            "time",
            "travel",
            "job list",
            "market prices",
            "resident list",
            "scene start",
            "character create",
        }


class TestUsage:
    def test_required_and_optional_params_render_differently(self):
        cmd = make_command("give", callback=_with_args)
        assert _usage(cmd) == "<character> [amount]"


class TestLabelFor:
    def test_known_key_uses_override_label(self):
        assert _label_for("job") == "Jobs"

    def test_unknown_key_falls_back_to_title_case(self):
        assert _label_for("position") == "Position"


class TestBuildEmbed:
    def test_empty_category_says_so(self):
        embed = _build_embed(GENERAL_KEY, [])
        assert "No commands" in (embed.description or "")

    def test_nonempty_category_lists_each_command(self):
        embed = _build_embed("job", [("job list", "List jobs", "<character>")])
        assert "/job list" in (embed.description or "")
        assert "List jobs" in (embed.description or "")
