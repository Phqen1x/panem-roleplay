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


SHIFT_PHASE_LABELS: dict[str, str] = {
    "morning": "Morning",
    "afternoon": "Afternoon",
    "evening": "Evening",
    "night": "Night",
}
"""Display order/labels for `ShiftPhaseSelect` -- keys are `DayPhase`
values (`panem_shared.enums.DayPhase`)."""


class ShiftPhaseSelect(discord.ui.Select):
    def __init__(
        self,
        on_choose: Callable[[discord.Interaction, str], Awaitable[None]],
        *,
        current_phase: str | None = None,
    ) -> None:
        options = [
            discord.SelectOption(label=label, value=phase, default=phase == current_phase)
            for phase, label in SHIFT_PHASE_LABELS.items()
        ]
        super().__init__(placeholder="Choose when you work your shift...", options=options)
        self._on_choose = on_choose

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_choose(interaction, self.values[0])


class ShiftPhaseSelectView(discord.ui.View):
    def __init__(
        self,
        on_choose: Callable[[discord.Interaction, str], Awaitable[None]],
        *,
        current_phase: str | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self.add_item(ShiftPhaseSelect(on_choose, current_phase=current_phase))


class IllicitDeclareView(discord.ui.View):
    """The last step of character creation, right after `ShiftPhaseSelect`
    -- self-declares `Character.job_is_illicit`. A plain two-button choice
    (not a `Select`, there's only ever two options) mirroring
    `ApprovalView`'s button shape."""

    def __init__(self, on_choose: Callable[[discord.Interaction, bool], Awaitable[None]]) -> None:
        super().__init__(timeout=300)
        self._on_choose = on_choose

    @discord.ui.button(label="No, it's legal work", style=discord.ButtonStyle.secondary)
    async def legal(
        self, interaction: discord.Interaction, _button: discord.ui.Button[IllicitDeclareView]
    ) -> None:
        await self._on_choose(interaction, False)

    @discord.ui.button(label="Yes, it's illicit", style=discord.ButtonStyle.danger)
    async def illicit(
        self, interaction: discord.Interaction, _button: discord.ui.Button[IllicitDeclareView]
    ) -> None:
        await self._on_choose(interaction, True)


RP_MODE_DESCRIPTIONS: dict[str, str] = {
    "story": "Freeform RP only -- no economy, crime, housing, work, or NPC interaction.",
    "life": "The full economy/crime/market/work/travel loop, minus housing and daily needs.",
    "simulation": "The full experience -- economy, crime, housing, and daily needs (hunger/thirst/sanity/fatigue).",
}
"""Shown on both the creation prompt (`RpModeSelectView`) and the
dashboard's mode-switch panel -- keys are `RpMode` values."""


class RpModeSelectView(discord.ui.View):
    """The first step of character creation -- picks `Character.rp_mode`.
    A plain three-button choice (mirrors `IllicitDeclareView`'s shape)
    rather than a `Select`, since there are only ever three options and a
    button lets each carry its own one-line description as a tooltip-free
    label instead of a bare value string."""

    def __init__(self, on_choose: Callable[[discord.Interaction, str], Awaitable[None]]) -> None:
        super().__init__(timeout=300)
        self._on_choose = on_choose

    @discord.ui.button(label="Story", style=discord.ButtonStyle.secondary)
    async def story(
        self, interaction: discord.Interaction, _button: discord.ui.Button[RpModeSelectView]
    ) -> None:
        await self._on_choose(interaction, "story")

    @discord.ui.button(label="Life", style=discord.ButtonStyle.primary)
    async def life(
        self, interaction: discord.Interaction, _button: discord.ui.Button[RpModeSelectView]
    ) -> None:
        await self._on_choose(interaction, "life")

    @discord.ui.button(label="Simulation", style=discord.ButtonStyle.success)
    async def simulation(
        self, interaction: discord.Interaction, _button: discord.ui.Button[RpModeSelectView]
    ) -> None:
        await self._on_choose(interaction, "simulation")


class ConfirmView(discord.ui.View):
    """A generic "are you sure" gate for actions with real, hard-to-undo
    consequences (switching RP mode, the crime-enabled toggle, self-
    inflicting an affliction/death) -- restricted to the one player it's
    for, same shape as `_InviteResponseButton`. The caller supplies the
    message text (what's about to happen, spelled out) separately; this
    view is just the two buttons."""

    def __init__(
        self,
        *,
        target_discord_id: int,
        on_confirm: Callable[[discord.Interaction], Awaitable[None]],
        confirm_label: str = "Confirm",
        cancel_label: str = "Cancel",
    ) -> None:
        super().__init__(timeout=120)
        self._target_discord_id = target_discord_id
        self._on_confirm = on_confirm
        self.confirm.label = confirm_label
        self.cancel.label = cancel_label

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._target_discord_id:
            await interaction.response.send_message("That's not yours to confirm.", ephemeral=True)
            return False
        return True

    @discord.ui.button(style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, _button: discord.ui.Button[ConfirmView]
    ) -> None:
        if not await self._guard(interaction):
            return
        await self._on_confirm(interaction)

    @discord.ui.button(style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, _button: discord.ui.Button[ConfirmView]
    ) -> None:
        if not await self._guard(interaction):
            return
        await interaction.response.edit_message(content="Cancelled -- nothing changed.", view=None)


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
