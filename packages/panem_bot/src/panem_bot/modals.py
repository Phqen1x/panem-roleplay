"""Modals for `/character create` and `/character edit` (FR-CHR-2).

Discord modals cap out at 5 text inputs and don't support select menus, so
the full field set (name, age, district, appearance, backstory, job
title, shift phase, avatar URL) is collected across three interaction
steps: a district select, `CharacterDetailsModal`
(name/age/appearance/backstory/job title -- the modal's full 5-input
budget), then a shift-phase select (a `View`, not a modal, since a phase
is a fixed list of choices, not free text). See `panem_bot.cogs.characters`
for how the steps chain together. Job title lives in this modal (not a
second one) precisely because it's *this* modal -- the one opened directly
from the slash command, not from another modal's own submission -- that
has room; a second modal chained off `CharacterDetailsModal`'s submit
isn't an option at all (Discord rejects a modal sent in direct response to
another modal's MODAL_SUBMIT interaction), which is also why shift phase
stays a plain `View`+`Select` step rather than a modal. Avatar URL isn't
collected here at all -- there's no room left in the 5-field budget, and
`/character avatar` already exists to set/change it (before or after
approval), so nothing about it needs collecting twice.
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
    job_title = discord.ui.TextInput(
        label="What job does your character want?",
        max_length=80,
        placeholder="e.g. Coal miner, Seamstress, Fisherman's apprentice",
    )

    def __init__(
        self,
        *,
        on_submit: Callable[[discord.Interaction, str, str, str, str, str], Awaitable[None]],
        age_placeholder: str = "12-80",
        prefill: dict[str, str] | None = None,
        is_story: bool = False,
    ) -> None:
        super().__init__()
        self._on_submit = on_submit
        self.age.placeholder = age_placeholder
        if is_story:
            # Story-mode characters never work ("no ... work") -- the field
            # stays visible (removing a class-level `discord.ui.TextInput`
            # dynamically per-instance isn't worth the complexity for this),
            # just optional; anything typed here is discarded by
            # `characters_svc.create_character`'s own Story-mode branch.
            self.job_title.required = False
            self.job_title.placeholder = "Not used in Story mode"
        if prefill:
            self.name.default = prefill.get("name")
            self.age.default = prefill.get("age")
            self.appearance.default = prefill.get("appearance")
            self.backstory.default = prefill.get("backstory")
            self.job_title.default = prefill.get("job_title")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit(
            interaction,
            str(self.name.value),
            str(self.age.value),
            str(self.appearance.value or ""),
            str(self.backstory.value or ""),
            str(self.job_title.value),
        )
