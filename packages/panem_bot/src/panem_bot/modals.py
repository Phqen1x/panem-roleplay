"""Modals for `/character create` and `/character edit` (FR-CHR-2).

Discord modals cap out at 5 text inputs and don't support select menus, so
the full field set (name, age, district, appearance, backstory,
avatar URL, job title, shift phase) is collected across four interaction
steps: a district select, `CharacterDetailsModal`
(name/age/appearance/backstory/avatar -- the modal's full 5-input
budget), `JobTitleModal` (job title needs its own modal -- there's no
room left in the first one), then a shift-phase select (a `View`, not a
modal, since a phase is a fixed list of choices, not free text). See
`panem_bot.cogs.characters` for how the steps chain together. An avatar
set here goes to staff for review with the rest of the application, same
as `/character avatar` does for a later change.
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
    # File uploads (`/character avatar`'s `image` option) aren't available in
    # a modal, only a URL -- someone wanting to upload a file instead sets it
    # after approval with that command, same as changing it later.
    avatar = discord.ui.TextInput(
        label="Avatar URL (optional)",
        required=False,
        max_length=512,
        placeholder="https://.../image.png -- or set later with /character avatar",
    )

    def __init__(
        self,
        *,
        on_submit: Callable[[discord.Interaction, str, str, str, str, str], Awaitable[None]],
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
            self.avatar.default = prefill.get("avatar")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit(
            interaction,
            str(self.name.value),
            str(self.age.value),
            str(self.appearance.value or ""),
            str(self.backstory.value or ""),
            str(self.avatar.value or ""),
        )


class JobTitleModal(discord.ui.Modal, title="Desired Job"):
    # `send_modal` here responds to a MODAL_SUBMIT interaction (this modal
    # is opened from CharacterDetailsModal.on_submit, chaining a second
    # modal onto the first) -- Discord rejects that chained response if it
    # uses the legacy Action-Row-wrapped TextInput a plain `label=` kwarg
    # produces, so this needs the newer `ui.Label`-wrapped form instead
    # (discord.py >= 2.6). CharacterDetailsModal itself opens in response
    # to a plain component interaction, where the legacy form still works.
    job_title = discord.ui.Label(
        text="What job does your character want?",
        component=discord.ui.TextInput(
            max_length=80,
            placeholder="e.g. Coal miner, Seamstress, Fisherman's apprentice",
        ),
    )

    def __init__(
        self,
        *,
        on_submit: Callable[[discord.Interaction, str], Awaitable[None]],
        prefill: str | None = None,
    ) -> None:
        super().__init__()
        self._on_submit = on_submit
        if prefill:
            self.job_title.component.default = prefill  # type: ignore[attr-defined]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit(
            interaction,
            str(self.job_title.component.value),  # type: ignore[attr-defined]
        )
