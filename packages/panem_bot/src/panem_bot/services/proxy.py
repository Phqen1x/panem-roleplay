"""Proxy decision logic (Spec §3.2 FR-PRX). No Discord I/O here: this module
decides *whether and as whom* a message should be proxied and how to split
oversized content; the cog does the actual delete/webhook-send/store dance.
"""

from __future__ import annotations

from dataclasses import dataclass

from panem_shared.constants import CAPITOL_DISTRICT_ID, PROXY_MESSAGE_MAX_LEN
from panem_shared.content.schemas import District, Location
from panem_shared.db.models import Character
from panem_shared.enums import CharacterStatus, Position


def is_ooc(content: str) -> bool:
    stripped = content.strip()
    return stripped.startswith("((") and stripped.endswith("))")


def strip_tag_prefix(content: str, tag: str) -> str:
    prefix = f"{tag}:"
    if content.startswith(prefix):
        return content[len(prefix) :].lstrip()
    return content


@dataclass(frozen=True, slots=True)
class ProxyTarget:
    """Who a message resolves to, before any access checks."""

    character_id: int
    via_tag: bool


def resolve_proxy_target(
    *,
    content: str,
    session_character_id: int | None,
    user_tags: dict[str, int],
) -> ProxyTarget | None:
    """FR-PRX-2. `user_tags` maps this user's registered `proxy_tag` values to
    their character ids. Returns `None` for messages that should be left
    untouched: explicitly OOC, no active session, and no matching tag prefix."""
    if is_ooc(content):
        return None
    if session_character_id is not None:
        return ProxyTarget(character_id=session_character_id, via_tag=False)
    for tag, character_id in user_tags.items():
        if content.startswith(f"{tag}:"):
            return ProxyTarget(character_id=character_id, via_tag=True)
    return None


def can_rp_in_district(character: Character, district_id: int) -> bool:
    """Which district a character may be played in (`/scene start`'s
    `_caller_character`) -- ordinarily just their assigned `district_id`,
    the same district a player's Discord role assigns them at creation,
    regardless of where `/travel` has physically taken them (RP location
    is a narrative assignment, not simulated movement -- see
    `Character.current_district_id` for that). A Gamemaker's characters
    (Capitol staff overseeing every Games, wherever it's held) may be
    played in any district without traveling there first; a Victor's may
    be played in their own district or the Capitol, matching how
    `travel.is_free_victor_route` waives the fare between exactly those
    two."""
    if character.district_id == district_id:
        return True
    positions = set(character.positions)
    if Position.GAMEMAKER.value in positions:
        return True
    return Position.VICTOR.value in positions and district_id == CAPITOL_DISTRICT_ID


def has_location_access(*, job_id: str | None, has_position: bool, location: Location) -> bool:
    """FR-LOC-3. Item-based access (`access_items`) needs inventory, which
    doesn't exist before Phase 2, so it's treated as never satisfied here —
    a restricted item-gated location is inaccessible to everyone until then,
    which is the safe direction to fail in.

    `has_position` generalizes what used to be a single `is_victor` check:
    holding *any* staff-granted `Position` (Victor, Gamemaker, Governor)
    grants the same restricted-location access a Victor always had."""
    if not location.restricted:
        return True
    if has_position:
        return True
    return job_id is not None and job_id in location.access_jobs


@dataclass(frozen=True, slots=True)
class ProxyRefusal:
    reason_key: str


def check_can_proxy(
    *, character: Character, district: District, location_id: str | None, current_tick: int
) -> ProxyRefusal | None:
    """FR-PRX-3. Returns the refusal reason, or `None` if proxying may proceed."""
    if character.status == CharacterStatus.DEAD.value:
        return ProxyRefusal("character_dead")
    if character.status != CharacterStatus.APPROVED.value:
        return ProxyRefusal("character_not_approved")
    if character.jailed_until_tick is not None and character.jailed_until_tick > current_tick:
        return ProxyRefusal("proxy_character_jailed")

    if location_id is not None:
        location = next((loc for loc in district.locations if loc.id == location_id), None)
        if location is not None and not has_location_access(
            job_id=character.job_id, has_position=bool(character.positions), location=location
        ):
            return ProxyRefusal("proxy_location_restricted")

    return None


def split_for_webhook(content: str, max_len: int = PROXY_MESSAGE_MAX_LEN) -> list[str]:
    """FR-PRX-6: split at paragraph boundaries into consecutive webhook messages."""
    if len(content) <= max_len:
        return [content]

    paragraphs = content.split("\n\n")
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= max_len:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(para) <= max_len:
            current = para
        else:
            # A single paragraph longer than the limit: hard-wrap it.
            for i in range(0, len(para), max_len):
                chunks.append(para[i : i + max_len])
            current = ""
    if current:
        chunks.append(current)
    return chunks
