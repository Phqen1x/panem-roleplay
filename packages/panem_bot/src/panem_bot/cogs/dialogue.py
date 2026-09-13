"""`/talk` -- LLM-driven NPC dialogue (Plan Phase 6, `lemonade/README.md`)."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete
from panem_bot.errors import NotAllowed, NotFound
from panem_bot.services import characters as characters_svc
from panem_bot.services import dialogue as dialogue_svc
from panem_bot.strings import t
from panem_shared.db.models import Character, Memory, Npc, RelationshipRow, WorldClock
from panem_shared.enums import OwnerKind
from panem_shared.relationships import relationship_key


class DialogueCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _get_character(
        self, session: AsyncSession, user_id: int, name: str
    ) -> Character | None:
        user = await characters_svc.get_or_create_user(session, user_id)
        return (
            await session.execute(
                select(Character).where(Character.user_id == user.id, Character.name == name)
            )
        ).scalar_one_or_none()

    @app_commands.command(name="talk", description="Talk to a resident NPC at your location")
    @app_commands.describe(
        character="Character name",
        resident="Resident's name",
        message="What your character says to them",
    )
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def talk(
        self, interaction: discord.Interaction, character: str, resident: str, message: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.followup.send(t("character_not_found"), ephemeral=True)
                return

            npc = (
                await session.execute(
                    select(Npc).where(
                        Npc.district_id == char.current_district_id, Npc.name == resident
                    )
                )
            ).scalar_one_or_none()
            if npc is None:
                await interaction.followup.send(t("resident_not_found"), ephemeral=True)
                return

            try:
                dialogue_svc.check_can_talk(char)
                if npc.location_id != char.location_id:
                    raise NotAllowed("talk_not_here", name=npc.name)

                clock = await session.get(WorldClock, 1)
                tick = clock.tick if clock is not None else 0
                await dialogue_svc.check_and_spend_stamina(
                    self.bot.redis,  # type: ignore[attr-defined]
                    npc=npc,
                    tick=tick,
                    ttl_seconds=self.bot.settings.tick_interval_seconds * 2,  # type: ignore[attr-defined]
                )
            except (NotAllowed, NotFound) as exc:
                await interaction.followup.send(t(exc.reason_key, **exc.fmt), ephemeral=True)
                return

            content = self.bot.content  # type: ignore[attr-defined]
            district = content.district(char.current_district_id)
            location = next((loc for loc in district.locations if loc.id == npc.location_id), None)
            if location is None:
                await interaction.followup.send(t("talk_not_here", name=npc.name), ephemeral=True)
                return

            key = relationship_key(
                (OwnerKind.CHARACTER.value, str(char.id)), (OwnerKind.NPC.value, npc.id)
            )
            relationship = await session.get(RelationshipRow, key)
            stance = relationship.stance if relationship is not None else "stranger"

            memories = (
                (
                    await session.execute(
                        select(Memory).where(Memory.owner_kind == "npc", Memory.owner_id == npc.id)
                    )
                )
                .scalars()
                .all()
            )

            reply = await dialogue_svc.generate_reply(
                npc=npc,
                district=district,
                location=location,
                character=char,
                stance=stance,
                memories=list(memories),
                message=message,
                settings=self.bot.settings,  # type: ignore[attr-defined]
            )
            npc_name = npc.name

        embed = discord.Embed(description=reply)
        embed.set_author(name=npc_name)
        embed.set_footer(text=f"{character}: {message}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @talk.autocomplete("resident")
    async def talk_resident_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        character_name = getattr(interaction.namespace, "character", None)
        if not character_name:
            return []
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character_name)
            if char is None:
                return []
            names = (
                (
                    await session.execute(
                        select(Npc.name).where(Npc.district_id == char.current_district_id)
                    )
                )
                .scalars()
                .all()
            )
        current_lower = current.lower()
        matches = sorted(n for n in names if current_lower in n.lower())
        return [app_commands.Choice(name=n, value=n) for n in matches[:25]]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DialogueCog(bot))
