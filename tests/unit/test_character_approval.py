"""`CharacterCog`'s staff-approval-embed posting (`panem_bot.cogs.
characters`) -- specifically the idempotency guard added alongside the
instant Redis-pubsub announce path (`_listen_for_pending_characters`),
which now races the pre-existing `_announce_pending_characters` poll for
the same character. Mirrors `test_narrator.py`'s `FakeBot` pattern: real
DB session, mocked Discord-facing calls.
"""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from panem_bot.cogs.characters import CharacterCog
from panem_shared.db.models import Character, User
from panem_shared.enums import CharacterStatus


class FakeLocationKind:
    def __init__(self, value: str) -> None:
        self.value = value


class FakeLocation:
    """Just enough of `panem_shared.content.schemas.Location` for
    `characters_svc.approve_character` -- it only ever reads `.kind.value`
    (to find the district's one `public` location) and `.id` (to drop the
    newly-approved character there)."""

    def __init__(self, id: str, kind: str) -> None:
        self.id = id
        self.kind = FakeLocationKind(kind)


class FakeDistrict:
    id = 1
    name = "District 1"
    locations: ClassVar = [FakeLocation("square", "public")]


class FakeContent:
    def district(self, district_id: int) -> FakeDistrict:
        return FakeDistrict()


class FakeSettings:
    approval_channel_id = 777
    character_announce_channel_id = 0


class FakeBot:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self.settings = FakeSettings()
        self.content = FakeContent()
        approval_channel = MagicMock()
        approval_channel.send = AsyncMock()
        announce_channel = MagicMock()
        announce_channel.send = AsyncMock()
        self._channels = {777: approval_channel, 888: announce_channel}
        # Real `Client.get_channel` semantics: an id with nothing
        # registered (including the "unset" sentinel, 0) reads back as
        # not found, same as it would on a real guild.
        self.get_channel = MagicMock(side_effect=lambda cid: self._channels.get(cid))

    @asynccontextmanager
    async def db(self):
        async with self._session_factory() as session, session.begin():
            yield session

    @property
    def channel(self):
        return self._channels[777]

    @property
    def announce_channel(self):
        return self._channels[888]


@pytest.fixture
def bot(db_session_factory) -> FakeBot:
    return FakeBot(db_session_factory)


@pytest.fixture
def cog(bot: FakeBot) -> CharacterCog:
    cog = CharacterCog.__new__(CharacterCog)
    cog.bot = bot
    cog.approval_view = MagicMock()
    cog.mode_switch_approval_view = MagicMock()
    cog.profile_edit_approval_view = MagicMock()
    return cog


async def _seed_character(
    db_session, *, approval_notified_at: dt.datetime | None, avatar_url: str | None = None
) -> Character:
    user = User(discord_id=42)
    db_session.add(user)
    await db_session.flush()
    character = Character(
        user_id=user.id,
        district_id=1,
        current_district_id=1,
        name="Wren",
        age=20,
        status=CharacterStatus.PENDING.value,
        approval_notified_at=approval_notified_at,
        avatar_url=avatar_url,
    )
    db_session.add(character)
    await db_session.flush()
    return character


class TestPostApprovalEmbedIdempotency:
    async def test_posts_and_stamps_when_not_yet_notified(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        character = await _seed_character(db_session, approval_notified_at=None)

        await cog._post_approval_embed(character.id, applicant_discord_id=42)

        bot.channel.send.assert_awaited_once()
        # `cog._post_approval_embed` commits through its own session (`bot.db()`);
        # `populate_existing` forces this session's identity-mapped copy to
        # re-read rather than return its now-stale cached version.
        refreshed = await db_session.get(Character, character.id, populate_existing=True)
        assert refreshed.approval_notified_at is not None

    async def test_no_op_when_already_notified(self, db_session, bot: FakeBot, cog: CharacterCog):
        already = dt.datetime.now(dt.UTC)
        character = await _seed_character(db_session, approval_notified_at=already)

        await cog._post_approval_embed(character.id, applicant_discord_id=42)

        bot.channel.send.assert_not_awaited()

    async def test_no_op_when_no_approval_channel_configured(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        bot.get_channel = MagicMock(return_value=None)
        character = await _seed_character(db_session, approval_notified_at=None)

        await cog._post_approval_embed(character.id, applicant_discord_id=42)  # no raise

    async def test_posts_the_faceclaim_as_the_embeds_image(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        # `set_image` (full-width, below the fields), not `set_thumbnail`
        # (a small corner icon) -- staff reviewing a faceclaim need to
        # actually see it, not just notice one is attached.
        character = await _seed_character(
            db_session, approval_notified_at=None, avatar_url="https://example.com/avatar.png"
        )

        await cog._post_approval_embed(character.id, applicant_discord_id=42)

        embed = bot.channel.send.await_args.kwargs["embed"]
        assert embed.image.url == "https://example.com/avatar.png"
        assert embed.thumbnail.url is None

    async def test_no_image_when_character_has_no_avatar_url(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        character = await _seed_character(db_session, approval_notified_at=None, avatar_url=None)

        await cog._post_approval_embed(character.id, applicant_discord_id=42)

        embed = bot.channel.send.await_args.kwargs["embed"]
        assert embed.image.url is None


def _make_approve_interaction() -> MagicMock:
    """Just enough of a staff member's button-click `discord.Interaction`
    for `_handle_approve` to run to completion: a guild whose `get_member`
    comes back empty (so the DM/forum-grant branch it gates is skipped
    without needing its own mocking -- that's `TestGrantDistrictForum
    Access`'s job, not this one), and the two interaction responses
    `_handle_approve` always makes regardless (disabling the approval
    message, and its own ephemeral "Approved" reply)."""
    interaction = MagicMock()
    interaction.message.embeds = []
    interaction.message.edit = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.guild.get_member = MagicMock(return_value=None)
    return interaction


class TestHandleApprove:
    """The public "new citizen" announcement `_handle_approve` posts to
    `Settings.character_announce_channel_id` once a character is
    approved -- same embed the staff-only approval channel got (`_build_
    character_embed`, covered by `TestPostApprovalEmbedIdempotency`
    above), just without the Approve/Reject buttons view."""

    async def test_posts_to_the_announce_channel_when_configured(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        bot.settings.character_announce_channel_id = 888
        character = await _seed_character(
            db_session, approval_notified_at=None, avatar_url="https://example.com/avatar.png"
        )

        await cog._handle_approve(_make_approve_interaction(), character.id)

        bot.announce_channel.send.assert_awaited_once()
        embed = bot.announce_channel.send.await_args.kwargs["embed"]
        assert embed.image.url == "https://example.com/avatar.png"
        # The staff-only approval-channel embed takes `view=self.
        # approval_view` (the Approve/Reject buttons); this one never
        # does -- there's nothing left to approve.
        assert "view" not in bot.announce_channel.send.await_args.kwargs

    async def test_skips_the_announcement_when_not_configured(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        assert bot.settings.character_announce_channel_id == 0
        character = await _seed_character(db_session, approval_notified_at=None)

        await cog._handle_approve(_make_approve_interaction(), character.id)

        bot.announce_channel.send.assert_not_awaited()

    async def test_already_handled_character_is_a_no_op(self, db_session, bot: FakeBot, cog: CharacterCog):
        bot.settings.character_announce_channel_id = 888
        character = await _seed_character(
            db_session, approval_notified_at=dt.datetime.now(dt.UTC)
        )
        character.status = CharacterStatus.APPROVED.value
        await db_session.flush()

        await cog._handle_approve(_make_approve_interaction(), character.id)

        bot.announce_channel.send.assert_not_awaited()


class TestHandleEditSubmit:
    """`/character edit`'s resubmission -- specifically the "Request
    Changes" round trip: staff's "Request Changes" button leaves
    `approval_notified_at` set (from the character's *original* post), so
    without clearing it here, `_post_approval_embed`'s own idempotency
    guard (see `TestPostApprovalEmbedIdempotency` above) would silently
    swallow every resubmission after the first one."""

    async def test_reposts_after_changes_were_requested(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        already = dt.datetime.now(dt.UTC)
        character = await _seed_character(db_session, approval_notified_at=already)
        interaction = MagicMock()
        interaction.user.id = 42
        interaction.response.send_message = AsyncMock()

        await cog._handle_edit_submit(
            interaction,
            character.id,
            "Wren",
            21,
            "Tall.",
            "A weaver.",
            "",
            "morning",
        )

        bot.channel.send.assert_awaited_once()
        refreshed = await db_session.get(Character, character.id, populate_existing=True)
        assert refreshed.approval_notified_at is not None
        assert refreshed.age == 21


class TestTryAnnounce:
    async def test_announces_a_pending_unnotified_character(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        character = await _seed_character(db_session, approval_notified_at=None)

        await cog._try_announce(character.id)

        bot.channel.send.assert_awaited_once()

    async def test_skips_an_already_notified_character(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        already = dt.datetime.now(dt.UTC)
        character = await _seed_character(db_session, approval_notified_at=already)

        await cog._try_announce(character.id)

        bot.channel.send.assert_not_awaited()

    async def test_skips_an_unknown_character_id(self, bot: FakeBot, cog: CharacterCog):
        await cog._try_announce(999999)  # no raise

        bot.channel.send.assert_not_awaited()


async def _seed_approved_character(
    db_session,
    *,
    rp_mode: str = "story",
    job_title: str | None = None,
    pending_rp_mode: str | None = None,
    pending_job_title: str | None = None,
    pending_shift_phase: str | None = None,
    pending_mode_switch_notified_at: dt.datetime | None = None,
) -> Character:
    user = User(discord_id=42)
    db_session.add(user)
    await db_session.flush()
    character = Character(
        user_id=user.id,
        district_id=1,
        current_district_id=1,
        name="Wren",
        age=20,
        status=CharacterStatus.APPROVED.value,
        rp_mode=rp_mode,
        job_title=job_title,
        pending_rp_mode=pending_rp_mode,
        pending_job_title=pending_job_title,
        pending_shift_phase=pending_shift_phase,
        pending_mode_switch_notified_at=pending_mode_switch_notified_at,
    )
    db_session.add(character)
    await db_session.flush()
    return character


class TestPostModeSwitchApprovalEmbedIdempotency:
    async def test_posts_and_stamps_a_staged_switch(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        character = await _seed_approved_character(
            db_session,
            pending_rp_mode="simulation",
            pending_job_title="Baker",
            pending_shift_phase="morning",
        )

        await cog._post_mode_switch_approval_embed(character.id, applicant_discord_id=42)

        bot.channel.send.assert_awaited_once()
        refreshed = await db_session.get(Character, character.id, populate_existing=True)
        assert refreshed.pending_mode_switch_notified_at is not None
        # Staging the switch never touches the real, currently-live mode.
        assert refreshed.rp_mode == "story"

    async def test_no_op_when_nothing_is_staged(self, db_session, bot: FakeBot, cog: CharacterCog):
        character = await _seed_approved_character(db_session)

        await cog._post_mode_switch_approval_embed(character.id, applicant_discord_id=42)

        bot.channel.send.assert_not_awaited()

    async def test_no_op_when_already_notified(self, db_session, bot: FakeBot, cog: CharacterCog):
        already = dt.datetime.now(dt.UTC)
        character = await _seed_approved_character(
            db_session,
            pending_rp_mode="simulation",
            pending_job_title="Baker",
            pending_shift_phase="morning",
            pending_mode_switch_notified_at=already,
        )

        await cog._post_mode_switch_approval_embed(character.id, applicant_discord_id=42)

        bot.channel.send.assert_not_awaited()


class TestFinishModeSwitchRequest:
    async def test_stages_the_switch_and_posts_for_approval(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        from panem_shared.enums import RpMode

        character = await _seed_approved_character(db_session, rp_mode="story")
        interaction = MagicMock()
        interaction.user.id = 42
        interaction.response.send_message = AsyncMock()

        await cog._finish_mode_switch_request(
            interaction, character.id, "Wren", RpMode.SIMULATION, "Baker", "morning", False
        )

        bot.channel.send.assert_awaited_once()
        refreshed = await db_session.get(Character, character.id, populate_existing=True)
        assert refreshed.rp_mode == "story"
        assert refreshed.pending_rp_mode == "simulation"
        assert refreshed.pending_job_title == "Baker"


class TestHandleModeSwitchApprove:
    async def test_applies_the_staged_switch(self, db_session, bot: FakeBot, cog: CharacterCog):
        character = await _seed_approved_character(
            db_session,
            rp_mode="story",
            pending_rp_mode="simulation",
            pending_job_title="Baker",
            pending_shift_phase="morning",
        )
        interaction = MagicMock()
        interaction.guild = None
        interaction.message.embeds = []
        interaction.message.edit = AsyncMock()
        interaction.response.send_message = AsyncMock()

        await cog._handle_mode_switch_approve(interaction, character.id)

        refreshed = await db_session.get(Character, character.id, populate_existing=True)
        assert refreshed.rp_mode == "simulation"
        assert refreshed.job_title == "Baker"
        assert refreshed.shift_phase == "morning"
        assert refreshed.pending_rp_mode is None

    async def test_no_op_when_nothing_is_staged(self, db_session, bot: FakeBot, cog: CharacterCog):
        character = await _seed_approved_character(db_session, rp_mode="story")
        interaction = MagicMock()
        interaction.response.send_message = AsyncMock()

        await cog._handle_mode_switch_approve(interaction, character.id)

        refreshed = await db_session.get(Character, character.id, populate_existing=True)
        assert refreshed.rp_mode == "story"


class TestHandleModeSwitchDecline:
    async def test_discards_the_staged_switch_without_deleting_the_character(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        character = await _seed_approved_character(
            db_session,
            rp_mode="story",
            pending_rp_mode="simulation",
            pending_job_title="Baker",
            pending_shift_phase="morning",
        )
        interaction = MagicMock()
        interaction.guild = None
        interaction.message.embeds = []
        interaction.message.edit = AsyncMock()
        interaction.response.send_message = AsyncMock()

        await cog._handle_mode_switch_decline(interaction, character.id, "Not a fit right now.")

        # The character -- already existing and playing -- must never be
        # deleted by a decline, unlike rejecting a fresh application.
        refreshed = await db_session.get(Character, character.id, populate_existing=True)
        assert refreshed is not None
        assert refreshed.rp_mode == "story"
        assert refreshed.job_title is None
        assert refreshed.pending_rp_mode is None
        assert refreshed.pending_job_title is None


class TestGrantDistrictForumAccess:
    """`CharacterCog._grant_district_forum_access` -- the per-member (not
    per-role) permission grant `_handle_approve` makes once a character's
    approved, since there's no district role anymore for a channel's own
    overwrites to key off of."""

    async def test_grants_view_and_post_access_to_the_forum(self, cog: CharacterCog):
        forum = MagicMock()
        forum.overwrites_for = MagicMock(return_value=discord.PermissionOverwrite())
        forum.set_permissions = AsyncMock()
        guild = MagicMock()
        guild.get_channel = MagicMock(return_value=forum)
        member = MagicMock()

        await cog._grant_district_forum_access(
            guild,
            member=member,
            forum_channel_id=123,
            character_id=1,
            district_id=1,
            char_name="Katniss",
        )

        guild.get_channel.assert_called_once_with(123)
        forum.set_permissions.assert_awaited_once()
        _, kwargs = forum.set_permissions.call_args
        overwrite = kwargs["overwrite"]
        assert overwrite.view_channel is True
        assert overwrite.send_messages is True
        assert overwrite.send_messages_in_threads is True

    async def test_no_op_when_district_has_no_registered_forum(self, cog: CharacterCog):
        guild = MagicMock()
        member = MagicMock()

        await cog._grant_district_forum_access(
            guild,
            member=member,
            forum_channel_id=None,
            character_id=1,
            district_id=1,
            char_name="Katniss",
        )

        guild.get_channel.assert_not_called()

    async def test_no_op_when_the_forum_channel_no_longer_exists(self, cog: CharacterCog):
        guild = MagicMock()
        guild.get_channel = MagicMock(return_value=None)
        member = MagicMock()

        await cog._grant_district_forum_access(
            guild,
            member=member,
            forum_channel_id=123,
            character_id=1,
            district_id=1,
            char_name="Katniss",
        )  # no raise

    async def test_permission_grant_failure_is_caught_and_logged(self, cog: CharacterCog):
        forum = MagicMock()
        forum.overwrites_for = MagicMock(return_value=discord.PermissionOverwrite())
        forum.set_permissions = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "nope"))
        guild = MagicMock()
        guild.get_channel = MagicMock(return_value=forum)
        member = MagicMock()

        await cog._grant_district_forum_access(
            guild,
            member=member,
            forum_channel_id=123,
            character_id=1,
            district_id=1,
            char_name="Katniss",
        )  # no raise


async def _seed_character_for_profile_edit(db_session, **overrides: object) -> Character:
    user = User(discord_id=42)
    db_session.add(user)
    await db_session.flush()
    defaults: dict[str, object] = dict(
        user_id=user.id,
        district_id=1,
        current_district_id=1,
        name="Wren",
        age=20,
        gender="male",
        appearance="Tall, dark-haired.",
        backstory="A quiet baker.",
        job_title="Baker",
        avatar_url="https://example.com/wren.png",
        status=CharacterStatus.APPROVED.value,
        rp_mode="simulation",
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    db_session.add(character)
    await db_session.flush()
    return character


class TestPostProfileEditApprovalEmbed:
    async def test_posts_the_diff_as_the_embeds_fields(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        character = await _seed_character_for_profile_edit(
            db_session,
            pending_profile_name="Wren",
            pending_profile_age=21,
            pending_profile_gender="male",
            pending_profile_appearance="Tall, dark-haired.",
            pending_profile_backstory="A new backstory entirely.",
            pending_profile_job_title="Baker",
            pending_profile_avatar_url="https://example.com/wren.png",
        )

        await cog._post_profile_edit_approval_embed(character.id, applicant_discord_id=42)

        bot.channel.send.assert_awaited_once()
        embed = bot.channel.send.await_args.kwargs["embed"]
        field_values = {field.name: field.value for field in embed.fields}
        assert "20" in field_values["Age"] and "21" in field_values["Age"]
        assert "A quiet baker." in field_values["Backstory"]
        assert "A new backstory entirely." in field_values["Backstory"]
        # Nothing about appearance changed -- it shouldn't show up at all.
        assert "Appearance" not in field_values

    async def test_no_op_when_nothing_is_staged(self, db_session, bot: FakeBot, cog: CharacterCog):
        character = await _seed_character_for_profile_edit(db_session)

        await cog._post_profile_edit_approval_embed(character.id, applicant_discord_id=42)

        bot.channel.send.assert_not_awaited()


class TestHandleProfileEditApprove:
    async def test_applies_the_staged_edit(self, db_session, bot: FakeBot, cog: CharacterCog):
        character = await _seed_character_for_profile_edit(
            db_session,
            pending_profile_name="Wren",
            pending_profile_age=21,
            pending_profile_gender="male",
            pending_profile_appearance="Tall, dark-haired.",
            pending_profile_backstory="A new backstory entirely.",
            pending_profile_job_title="Baker",
            pending_profile_avatar_url="https://example.com/wren.png",
        )
        interaction = _make_approve_interaction()

        await cog._handle_profile_edit_approve(interaction, character.id)

        refreshed = await db_session.get(Character, character.id, populate_existing=True)
        assert refreshed.backstory == "A new backstory entirely."
        assert refreshed.age == 21
        assert refreshed.pending_profile_name is None

    async def test_no_op_when_nothing_is_staged(self, db_session, bot: FakeBot, cog: CharacterCog):
        character = await _seed_character_for_profile_edit(db_session)
        interaction = _make_approve_interaction()
        interaction.response.send_message = AsyncMock()

        await cog._handle_profile_edit_approve(interaction, character.id)

        refreshed = await db_session.get(Character, character.id, populate_existing=True)
        assert refreshed.backstory == "A quiet baker."


class TestHandleProfileEditDecline:
    async def test_discards_the_staged_edit_without_touching_the_real_profile(
        self, db_session, bot: FakeBot, cog: CharacterCog
    ):
        character = await _seed_character_for_profile_edit(
            db_session,
            pending_profile_name="Wren",
            pending_profile_age=21,
            pending_profile_gender="male",
            pending_profile_appearance="Tall, dark-haired.",
            pending_profile_backstory="A new backstory entirely.",
            pending_profile_job_title="Baker",
            pending_profile_avatar_url="https://example.com/wren.png",
        )
        interaction = _make_approve_interaction()

        await cog._handle_profile_edit_decline(interaction, character.id, "Not a good fit.")

        refreshed = await db_session.get(Character, character.id, populate_existing=True)
        assert refreshed.backstory == "A quiet baker."
        assert refreshed.pending_profile_name is None
