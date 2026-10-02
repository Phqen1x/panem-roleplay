from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared.db.models import Character, User

async def get_character_case_insensitive(
    session: AsyncSession, user_id: int, name: str
) -> Character | None:
    """Fetch a character belonging to the given discord user, case-insensitively."""
    from panem_bot.services import characters as characters_svc
    user = await characters_svc.get_or_create_user(session, user_id)
    return (
        await session.execute(
            select(Character).where(
                Character.user_id == user.id, func.lower(Character.name) == name.strip().lower()
            )
        )
    ).scalar_one_or_none()
