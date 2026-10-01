from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

import discord
import pytest
from discord import app_commands

from panem_bot import autocomplete
from panem_bot.bot import PanemBot
from panem_shared.content.loader import load_content
from panem_shared.db.models import AfflictionType, Character, Npc, User
from panem_shared.enums import CharacterStatus, RpMode
from panem_shared.settings import Settings


@pytest.fixture(autouse=True)
def _clear_autocomplete_caches():
    autocomplete._OWN_CHARACTERS_CACHE.clear()
    autocomplete._NPC_CACHE = None
    autocomplete._AFFLICTION_CACHE = None


class FakeInteraction:
    def __init__(self, discord_id: int, bot: MagicMock):
        self.user = MagicMock(spec=discord.User)
        self.user.id = discord_id
        self.client = bot
        self.response = MagicMock()
        self.command = MagicMock()


@pytest.mark.asyncio
async def test_own_approved_autocomplete(db_session, db_session_factory):
    # Setup test user and characters
    user = User(discord_id=999001)
    db_session.add(user)
    await db_session.flush()

    c1 = Character(
        user_id=user.id,
        district_id=12,
        current_district_id=12,
        name="Katniss",
        age=16,
        status=CharacterStatus.APPROVED.value,
        rp_mode=RpMode.SIMULATION.value,
    )
    c2 = Character(
        user_id=user.id,
        district_id=12,
        current_district_id=12,
        name="Peeta",
        age=16,
        status=CharacterStatus.APPROVED.value,
        rp_mode=RpMode.SIMULATION.value,
    )
    c_pending = Character(
        user_id=user.id,
        district_id=12,
        current_district_id=12,
        name="Gale",
        age=18,
        status=CharacterStatus.PENDING.value,
        rp_mode=RpMode.SIMULATION.value,
    )
    db_session.add_all([c1, c2, c_pending])
    await db_session.commit()

    content = load_content(Path("data"))
    bot = MagicMock()
    bot.session_factory = db_session_factory
    bot.content = content

    interaction = FakeInteraction(discord_id=999001, bot=bot)

    # First call: query & populate cache
    choices = await autocomplete.own_approved(interaction, "")
    assert len(choices) == 2
    names = [c.value for c in choices]
    assert "Katniss" in names
    assert "Peeta" in names
    assert "Gale" not in names

    # Filtered call (hits memory cache)
    choices_k = await autocomplete.own_approved(interaction, "kat")
    assert len(choices_k) == 1
    assert choices_k[0].value == "Katniss"

    # Pending characters
    choices_p = await autocomplete.own_pending(interaction, "")
    assert len(choices_p) == 1
    assert choices_p[0].value == "Gale"


@pytest.mark.asyncio
async def test_any_npc_autocomplete(db_session, db_session_factory):
    npc = Npc(id="npc_haymitch", district_id=12, name="Haymitch Abernathy", age=40)
    db_session.add(npc)
    await db_session.commit()

    content = load_content(Path("data"))
    bot = MagicMock()
    bot.session_factory = db_session_factory
    bot.content = content

    interaction = FakeInteraction(discord_id=999001, bot=bot)
    choices = await autocomplete.any_npc(interaction, "hay")
    assert len(choices) == 1
    assert choices[0].value == "npc_haymitch"


@pytest.mark.asyncio
async def test_affliction_types_autocomplete(db_session, db_session_factory):
    aff = AfflictionType(name="Broken Leg", is_permanent=False)
    db_session.add(aff)
    await db_session.commit()

    content = load_content(Path("data"))
    bot = MagicMock()
    bot.session_factory = db_session_factory
    bot.content = content

    interaction = FakeInteraction(discord_id=999001, bot=bot)
    choices = await autocomplete.affliction_types(interaction, "broken")
    assert len(choices) == 1
    assert choices[0].value == "Broken Leg"


@pytest.mark.asyncio
async def test_any_good_and_districts_autocomplete():
    content = load_content(Path("data"))
    bot = MagicMock()
    bot.content = content

    interaction = FakeInteraction(discord_id=999001, bot=bot)
    goods = await autocomplete.any_good(interaction, "coal")
    assert any(g.value == "coal" for g in goods)

    dist_choices = await autocomplete.districts(interaction, "12")
    assert any(d.value == 12 for d in dist_choices)


@pytest.mark.asyncio
async def test_bot_suppresses_unknown_interaction_error():
    settings = Settings()
    bot = PanemBot(settings=settings, data_dir=Path("data"))

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response.is_done.return_value = False
    interaction.command.qualified_name = "engage start"

    # Simulate discord.NotFound with code 10062 (Unknown interaction)
    mock_response = MagicMock()
    mock_response.status = 404
    data = {"code": 10062, "message": "Unknown interaction"}
    not_found = discord.NotFound(mock_response, data)
    error = app_commands.CommandInvokeError(interaction.command, not_found)

    # _on_app_command_error should return cleanly without raising or calling send_message
    await bot._on_app_command_error(interaction, error)
    interaction.response.send_message.assert_not_called()
    interaction.followup.send.assert_not_called()


@pytest.mark.asyncio
async def test_cog_autocompletes(db_session, db_session_factory):
    from panem_bot.cogs.travel import TravelCog
    from panem_bot.cogs.stealing import StealingCog
    from panem_bot.cogs.engagements import EngagementCog

    user = User(discord_id=999002)
    db_session.add(user)
    await db_session.flush()

    char = Character(
        user_id=user.id,
        district_id=12,
        current_district_id=12,
        location_id="square",
        name="Prim",
        age=12,
        status=CharacterStatus.APPROVED.value,
        rp_mode=RpMode.SIMULATION.value,
    )
    db_session.add(char)
    await db_session.commit()

    content = load_content(Path("data"))
    bot = MagicMock()
    bot.session_factory = db_session_factory
    bot.content = content

    travel_cog = TravelCog(bot)
    stealing_cog = StealingCog(bot)
    engagement_cog = EngagementCog(bot)

    interaction = FakeInteraction(discord_id=999002, bot=bot)
    interaction.namespace = MagicMock()
    interaction.namespace.character = "Prim"
    interaction.namespace.location = None

    # Test travel location autocomplete
    loc_choices = await travel_cog.travel_location_autocomplete(interaction, "square")
    assert any(c.value == "square" for c in loc_choices)

    # Test stealing target autocomplete
    target_choices = await stealing_cog.steal_target_autocomplete(interaction, "")
    assert isinstance(target_choices, list)

    # Test engagement location and participant autocomplete
    eng_locs = await engagement_cog.start_location_autocomplete(interaction, "")
    assert len(eng_locs) > 0
    eng_parts = await engagement_cog.start_participant_autocomplete(interaction, "")
    assert isinstance(eng_parts, list)

