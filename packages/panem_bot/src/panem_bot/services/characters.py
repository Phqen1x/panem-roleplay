"""Character lifecycle (Spec §3.1 FR-CHR)."""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot.errors import LimitReached, NotAllowed, NotFound, ValidationFailed
from panem_shared import constants
from panem_shared.content.schemas import District
from panem_shared.db.models import Character, Shift, User
from panem_shared.enums import CharacterStatus, ShiftResult
from panem_shared.settings import Settings

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z '\-]{0,31}$")


def max_age_for_district(district_id: int) -> int:
    """Only the Capitol is exempt from the reaping age range -- every other
    district's characters must be reaping-eligible age (12-18)."""
    if district_id == constants.CAPITOL_DISTRICT_ID:
        return constants.CHARACTER_AGE_MAX
    return constants.NON_CAPITOL_AGE_MAX


def validate_character_fields(
    *, district_id: int, name: str, age: int, appearance: str, backstory: str
) -> None:
    """Raise `ValidationFailed` on the first FR-CHR-2 violation."""
    if not _NAME_RE.match(name) or len(name) > constants.CHARACTER_NAME_MAX_LEN:
        raise ValidationFailed("invalid_name")
    max_age = max_age_for_district(district_id)
    if not (constants.CHARACTER_AGE_MIN <= age <= max_age):
        raise ValidationFailed("invalid_age", min=constants.CHARACTER_AGE_MIN, max=max_age)
    if len(appearance) > constants.CHARACTER_APPEARANCE_MAX_LEN:
        raise ValidationFailed("invalid_appearance", max=constants.CHARACTER_APPEARANCE_MAX_LEN)
    if len(backstory) > constants.CHARACTER_BACKSTORY_MAX_LEN:
        raise ValidationFailed("invalid_backstory", max=constants.CHARACTER_BACKSTORY_MAX_LEN)


def validate_avatar_url(url: str) -> None:
    if len(url) > constants.AVATAR_URL_MAX_LEN:
        raise ValidationFailed("invalid_avatar_url")
    if not url.startswith("https://"):
        raise ValidationFailed("invalid_avatar_url")
    if not url.lower().split("?")[0].endswith(constants.AVATAR_URL_EXTENSIONS):
        raise ValidationFailed("invalid_avatar_url")


def validate_proxy_tag(tag: str) -> None:
    if not (constants.PROXY_TAG_MIN_LEN <= len(tag) <= constants.PROXY_TAG_MAX_LEN):
        raise ValidationFailed("invalid_proxy_tag")
    if tag.startswith("/") or tag.startswith("(("):
        raise ValidationFailed("invalid_proxy_tag")


async def get_or_create_user(session: AsyncSession, discord_id: int) -> User:
    user = (
        await session.execute(select(User).where(User.discord_id == discord_id))
    ).scalar_one_or_none()
    if user is not None:
        return user
    user = User(discord_id=discord_id)
    session.add(user)
    await session.flush()
    return user


def effective_max_characters(user: User, settings: Settings) -> int:
    """Everyone gets `settings.max_characters_per_user` active (pending +
    approved) characters at a time unless staff set a per-user override
    with `/staff character_limit` (Spec FR-CHR-1)."""
    return (
        user.max_characters_override
        if user.max_characters_override is not None
        else settings.max_characters_per_user
    )


async def ensure_name_available(
    session: AsyncSession, name: str, *, exclude_character_id: int | None = None
) -> None:
    """FR-CHR-2: names must be unique (case-insensitively) across every
    character that is or was real. Every by-name lookup in the bot
    (proxying, `/character edit`, `/staff kill`, ...) assumes at most one
    match, so this must hold for pending/approved/retired/dead alike;
    rejected applications never became real characters, so their names are
    free to reuse. This mirrors the partial unique index on `characters`
    and exists to give a friendly `ValidationFailed` instead of a raw
    `IntegrityError` on the (rare) simultaneous-submission race.
    """
    stmt = (
        select(func.count())
        .select_from(Character)
        .where(
            func.lower(Character.name) == name.lower(),
            Character.status != CharacterStatus.REJECTED.value,
        )
    )
    if exclude_character_id is not None:
        stmt = stmt.where(Character.id != exclude_character_id)
    if int((await session.execute(stmt)).scalar_one()) > 0:
        raise ValidationFailed("name_taken", name=name)


async def _active_character_count(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(Character)
        .where(
            Character.user_id == user_id,
            Character.status.in_([CharacterStatus.PENDING.value, CharacterStatus.APPROVED.value]),
        )
    )
    return int(result.scalar_one())


async def create_character(
    session: AsyncSession,
    *,
    user: User,
    district_id: int,
    name: str,
    age: int,
    appearance: str,
    backstory: str,
    desired_job_id: str | None,
    max_characters: int,
    avatar_url: str | None = None,
) -> Character:
    if user.banned_at is not None:
        raise NotAllowed("banned")

    validate_character_fields(
        district_id=district_id, name=name, age=age, appearance=appearance, backstory=backstory
    )
    if avatar_url:
        validate_avatar_url(avatar_url)
    await ensure_name_available(session, name)

    if await _active_character_count(session, user.id) >= max_characters:
        raise LimitReached("too_many_characters", limit=max_characters)

    character = Character(
        user_id=user.id,
        district_id=district_id,
        current_district_id=district_id,
        name=name,
        age=age,
        appearance=appearance,
        backstory=backstory,
        avatar_url=avatar_url or None,
        status=CharacterStatus.PENDING.value,
        job_id=desired_job_id,
    )
    session.add(character)
    await session.flush()
    return character


async def _job_slot_free(session: AsyncSession, job_id: str, slots: int) -> bool:
    result = await session.execute(
        select(func.count())
        .select_from(Character)
        .where(Character.job_id == job_id, Character.status == CharacterStatus.APPROVED.value)
    )
    return int(result.scalar_one()) < slots


async def approve_character(
    session: AsyncSession,
    character: Character,
    *,
    district: District,
    job_slots: dict[str, int],
) -> Character:
    """FR-CHR-4. `job_slots` maps job_id -> slots, from `jobs.yaml`."""
    if character.status != CharacterStatus.PENDING.value:
        raise NotAllowed("not_pending")

    # Content validation (Spec §5.4) guarantees every district has >= 1 public location.
    public_location = next(loc for loc in district.locations if loc.kind.value == "public")

    # Resolve the job slot before mutating `character` at all: `_job_slot_free`
    # counts approved characters against this same job_id, and autoflush would
    # otherwise make this character's own pending status="approved" update
    # visible to that count, making it appear to occupy its own slot.
    desired = character.job_id
    keep_job = (
        desired is not None
        and desired in job_slots
        and await _job_slot_free(session, desired, job_slots[desired])
    )

    character.status = CharacterStatus.APPROVED.value
    character.money = constants.STARTING_MONEY
    character.location_id = public_location.id
    character.current_district_id = district.id
    character.job_id = desired if keep_job else None

    await session.flush()
    return character


def reject_character(character: Character) -> Character:
    """Validates the character can be rejected; the caller (cog) logs it to
    Discord and deletes the row -- a rejected application never became a
    real character, so nothing about it is kept in `characters`."""
    if character.status != CharacterStatus.PENDING.value:
        raise NotAllowed("not_pending")
    return character


def request_changes(character: Character) -> Character:
    if character.status != CharacterStatus.PENDING.value:
        raise NotAllowed("not_pending")
    # Already pending; FR-CHR-4 only requires it stay pending and unlock /character edit.
    return character


async def retire_character(session: AsyncSession, character: Character) -> Character:
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")

    await session.execute(
        Shift.__table__.update()
        .where(Shift.character_id == character.id, Shift.result.is_(None))
        .values(result=ShiftResult.EXCUSED.value)
    )

    character.status = CharacterStatus.RETIRED.value
    character.job_id = None
    await session.flush()
    return character


def is_frozen(character: Character) -> bool:
    """FR-CHR-8: a dead character accepts no commands except list/status."""
    return character.status == CharacterStatus.DEAD.value


async def get_character(session: AsyncSession, character_id: int) -> Character:
    character = await session.get(Character, character_id)
    if character is None:
        raise NotFound("character_not_found")
    return character
