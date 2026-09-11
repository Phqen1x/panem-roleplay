"""Persistent/ephemeral UI components shared across cogs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord

CHAR_ID_FOOTER_PREFIX = "Character Number: "


def character_id_from_message(message: discord.Message) -> int | None:
    """Approval embeds carry the character id in the footer (FR-CHR-3);
    static custom_ids can't, so button callbacks parse it back out here."""
    if not message.embeds:
        return None
    footer = message.embeds[0].footer.text or ""
    if not footer.startswith(CHAR_ID_FOOTER_PREFIX):
        return None
    try:
        return int(footer.removeprefix(CHAR_ID_FOOTER_PREFIX))
    except ValueError:
        return None


class JobSelect(discord.ui.Select):
    def __init__(
        self,
        jobs: list[tuple[str, str]],
        on_choose: Callable[[discord.Interaction, str | None], Awaitable[None]],
    ) -> None:
        options = [discord.SelectOption(label="Unemployed", value="__none__")]
        options += [discord.SelectOption(label=title, value=jid) for jid, title in jobs]
        super().__init__(placeholder="Choose a desired job...", options=options[:25])
        self._on_choose = on_choose

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        await self._on_choose(interaction, None if value == "__none__" else value)


class JobSelectView(discord.ui.View):
    def __init__(
        self,
        jobs: list[tuple[str, str]],
        on_choose: Callable[[discord.Interaction, str | None], Awaitable[None]],
    ) -> None:
        super().__init__(timeout=300)
        self.add_item(JobSelect(jobs, on_choose))


class ChangesNoteModal(discord.ui.Modal, title="Request Changes"):
    note = discord.ui.TextInput(
        label="Note to applicant", style=discord.TextStyle.paragraph, max_length=1000
    )

    def __init__(self, on_submit: Callable[[discord.Interaction, str], Awaitable[None]]) -> None:
        super().__init__()
        self._on_submit = on_submit

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit(interaction, str(self.note.value))


class RejectReasonModal(discord.ui.Modal, title="Reject Character"):
    reason = discord.ui.TextInput(
        label="Reason", style=discord.TextStyle.paragraph, max_length=1000
    )

    def __init__(self, on_submit: Callable[[discord.Interaction, str], Awaitable[None]]) -> None:
        super().__init__()
        self._on_submit = on_submit

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit(interaction, str(self.reason.value))


class ApprovalView(discord.ui.View):
    """Persistent (static custom_ids) — registered once via `bot.add_view()`
    so it keeps working across a bot restart (FR-CHR-3)."""

    def __init__(
        self,
        *,
        is_staff: Callable[[discord.Interaction], Awaitable[bool]],
        on_approve: Callable[[discord.Interaction, int], Awaitable[None]],
        on_changes: Callable[[discord.Interaction, int, str], Awaitable[None]],
        on_reject: Callable[[discord.Interaction, int, str], Awaitable[None]],
    ) -> None:
        super().__init__(timeout=None)
        self._is_staff = is_staff
        self._on_approve = on_approve
        self._on_changes = on_changes
        self._on_reject = on_reject

    async def _check(self, interaction: discord.Interaction) -> int | None:
        if not await self._is_staff(interaction):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return None
        character_id = character_id_from_message(interaction.message)
        if character_id is None:
            await interaction.response.send_message(
                "Couldn't read the character id.", ephemeral=True
            )
            return None
        return character_id

    @discord.ui.button(
        label="Approve", style=discord.ButtonStyle.success, custom_id="panem:char_approve"
    )
    async def approve(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        character_id = await self._check(interaction)
        if character_id is not None:
            await self._on_approve(interaction, character_id)

    @discord.ui.button(
        label="Request Changes", style=discord.ButtonStyle.secondary, custom_id="panem:char_changes"
    )
    async def request_changes(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        character_id = await self._check(interaction)
        if character_id is None:
            return

        async def _submit(modal_interaction: discord.Interaction, note: str) -> None:
            await self._on_changes(modal_interaction, character_id, note)

        await interaction.response.send_modal(ChangesNoteModal(_submit))

    @discord.ui.button(
        label="Reject", style=discord.ButtonStyle.danger, custom_id="panem:char_reject"
    )
    async def reject(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        character_id = await self._check(interaction)
        if character_id is None:
            return

        async def _submit(modal_interaction: discord.Interaction, reason: str) -> None:
            await self._on_reject(modal_interaction, character_id, reason)

        await interaction.response.send_modal(RejectReasonModal(_submit))
