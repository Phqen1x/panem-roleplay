"""Modals for `/character create` and `/character edit` (FR-CHR-2).

Discord modals cap out at 5 text inputs and don't support select menus, so
the full field set from FR-CHR-2 (name, age, district, appearance,
backstory, desired_job) is collected across three interaction steps:
a district select, this modal (name/age/appearance/backstory), then a job
select. See `panem_bot.cogs.characters` for how the steps chain together.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord


class CharacterDetailsModal(discord.ui.Modal, title="New Character"):
    name = discord.ui.TextInput(label="Name", max_length=32)
    age = discord.ui.TextInput(label="Age", max_length=3, placeholder="12-80")
    appearance = discord.ui.TextInput(
        label="Appearance", style=discord.TextStyle.paragraph, max_length=400, required=False
    )
    backstory = discord.ui.TextInput(
        label="Backstory", style=discord.TextStyle.paragraph, max_length=1500, required=False
    )

    def __init__(
        self,
        *,
        on_submit: Callable[[discord.Interaction, str, str, str, str], Awaitable[None]],
        age_placeholder: str = "12-80",
        prefill: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._on_submit = on_submit
        self.age.placeholder = age_placeholder
        if prefill:
            self.name.default = prefill.get("name")
            self.age.default = prefill.get("age")
            self.appearance.default = prefill.get("appearance")
            self.backstory.default = prefill.get("backstory")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit(
            interaction,
            str(self.name.value),
            str(self.age.value),
            str(self.appearance.value or ""),
            str(self.backstory.value or ""),
        )
