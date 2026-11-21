"""The Capitol Market pet store's service layer -- `panem_api.map3d_routes`
is the only caller today (no `/pet` Discord command exists; see that
module's docstring for why this stayed dashboard-only). Mirrors
`panem_shared.market`'s buy flow (funds check, debit, insert row) for a
one-off purchase rather than the stocked/restocked goods system, since a
pet is bought once and kept, not a `good_id` traded repeatedly."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared.content.schemas import PetSpecies
from panem_shared.db.models import Character, Pet
from panem_shared.errors import NotAllowed, ValidationFailed

MAX_PET_NAME_LENGTH = 32


def validate_pet_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValidationFailed("pets_name_required")
    if len(cleaned) > MAX_PET_NAME_LENGTH:
        raise ValidationFailed("pets_name_too_long", max_length=MAX_PET_NAME_LENGTH)
    return cleaned


async def purchase_pet(
    session: AsyncSession,
    *,
    character: Character,
    species: PetSpecies,
    name: str,
    current_tick: int,
) -> Pet:
    cleaned_name = validate_pet_name(name)
    if character.money < species.price:
        raise NotAllowed("pets_insufficient_funds", name=character.name)
    character.money -= round(species.price)
    pet = Pet(
        owner_character_id=character.id,
        species_id=species.id,
        name=cleaned_name,
        acquired_tick=current_tick,
    )
    session.add(pet)
    await session.flush()
    return pet
