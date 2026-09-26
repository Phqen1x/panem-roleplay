"""`/resident list|where|profile` -- NPC residents aren't narrated for
most of their movement anymore (`panem_sim.systems.schedule` only
announces a work-shift or end-of-night arrival), so this is how a
player finds out who lives in a district, where one of them actually is
right now, and (Phase 3) what they're like and how they feel about a
character -- `Npc.traits`/`speech_style` (seeded procedurally, see
`panem_sim.world`) and the `RelationshipRow` `panem_sim.systems.social`
maintains between them."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete
from panem_bot.services import characters as characters_svc
from panem_bot.services import jobs as jobs_svc
from panem_bot.strings import t
from panem_shared.db.models import Character, Npc, RelationshipRow
from panem_shared.enums import OwnerKind
from panem_shared.relationships import relationship_key

EMBED_FIELD_VALUE_LIMIT = 1024


class ResidentCog(commands.Cog):
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

    group = app_commands.Group(name="resident", description="Look up a district's NPC residents")

    @group.command(name="list", description="List residents of a character's current district")
    @app_commands.describe(character="Character name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def resident_list(self, interaction: discord.Interaction, character: str) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            content = self.bot.content  # type: ignore[attr-defined]
            npcs = (
                (
                    await session.execute(
                        select(Npc)
                        .where(Npc.district_id == char.current_district_id)
                        .order_by(Npc.name)
                    )
                )
                .scalars()
                .all()
            )
            if not npcs:
                await interaction.response.send_message(
                    t("no_residents_in_district"), ephemeral=True
                )
                return

            all_jobs = await jobs_svc.get_all_jobs(session, content)
            district_name = content.district(char.current_district_id).name
            lines = []
            for npc in npcs:
                job = all_jobs.get(npc.job_id) if npc.job_id else None
                lines.append(f"**{npc.name}** -- {job.title if job else 'Unemployed'}")

        value = "\n".join(lines)
        if len(value) > EMBED_FIELD_VALUE_LIMIT:
            value = value[: EMBED_FIELD_VALUE_LIMIT - 1] + "…"
        embed = discord.Embed(title=f"Residents of {district_name}", description=value)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="where", description="Show where a specific resident is right now")
    @app_commands.describe(character="Character name", resident="Resident's name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def resident_where(
        self, interaction: discord.Interaction, character: str, resident: str
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            npc = (
                await session.execute(
                    select(Npc).where(
                        Npc.district_id == char.current_district_id, Npc.name == resident
                    )
                )
            ).scalar_one_or_none()
            if npc is None:
                await interaction.response.send_message(t("resident_not_found"), ephemeral=True)
                return

            content = self.bot.content  # type: ignore[attr-defined]
            district = content.district(char.current_district_id)
            location = next((loc for loc in district.locations if loc.id == npc.location_id), None)
            location_name = location.name if location else "Unknown"
            job = await jobs_svc.get_job(session, content, npc.job_id) if npc.job_id else None
            job_name = job.title if job else "Unemployed"
            name = npc.name

        await interaction.response.send_message(
            t("resident_where_ok", name=name, job=job_name, location=location_name),
            ephemeral=True,
        )

    @resident_where.autocomplete("resident")
    async def resident_where_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._resident_name_choices(interaction, current)

    @group.command(
        name="profile",
        description="See a resident's personality and how they see your character",
    )
    @app_commands.describe(character="Character name", resident="Resident's name")
    @app_commands.autocomplete(character=autocomplete.own_approved)
    async def resident_profile(
        self, interaction: discord.Interaction, character: str, resident: str
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            char = await self._get_character(session, interaction.user.id, character)
            if char is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return

            npc = (
                await session.execute(
                    select(Npc).where(
                        Npc.district_id == char.current_district_id, Npc.name == resident
                    )
                )
            ).scalar_one_or_none()
            if npc is None:
                await interaction.response.send_message(t("resident_not_found"), ephemeral=True)
                return

            content = self.bot.content  # type: ignore[attr-defined]
            job = await jobs_svc.get_job(session, content, npc.job_id) if npc.job_id else None
            job_name = job.title if job else "Unemployed"

            key = relationship_key(
                (OwnerKind.CHARACTER.value, str(char.id)), (OwnerKind.NPC.value, npc.id)
            )
            relationship = await session.get(RelationshipRow, key)
            stance = relationship.stance if relationship is not None else "stranger"

            name = npc.name
            traits = ", ".join(npc.traits) if npc.traits else "unknown"
            tone = npc.speech_style.get("tone", "unknown") if npc.speech_style else "unknown"
            authored = content.npcs.get(npc.id)
            # A staff edit (`/staff npc set-appearance`/`set-background`)
            # always wins over the authored content -- a staff-created NPC
            # (`/staff npc add`) has no authored entry at all, so the
            # override is the only place this can live for them.
            appearance = npc.appearance_override or (authored.appearance if authored else "")
            backstory = npc.backstory_override or (authored.backstory if authored else "")

        embed = discord.Embed(title=name)
        embed.add_field(name="Job", value=job_name)
        embed.add_field(name="Traits", value=traits.capitalize())
        embed.add_field(name="Speech", value=tone.capitalize())
        embed.add_field(name=f"Opinion of {char.name}", value=stance.capitalize())
        if appearance:
            embed.add_field(name="Appearance", value=appearance, inline=False)
        if backstory:
            if len(backstory) > EMBED_FIELD_VALUE_LIMIT:
                backstory = backstory[: EMBED_FIELD_VALUE_LIMIT - 1] + "…"
            embed.add_field(name="Backstory", value=backstory, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @resident_profile.autocomplete("resident")
    async def resident_profile_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._resident_name_choices(interaction, current)

    async def _resident_name_choices(
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
                        select(Npc.name)
                        .where(
                            Npc.district_id == char.current_district_id,
                            Npc.name.ilike(f"%{current}%"),
                        )
                        .order_by(Npc.name)
                        .limit(25)
                    )
                )
                .scalars()
                .all()
            )
        return [app_commands.Choice(name=name, value=name) for name in names]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ResidentCog(bot))
