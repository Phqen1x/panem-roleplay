"""Proxy decision logic (Spec §3.2 FR-PRX). No Discord I/O here: this module
decides *whether and as whom* a message should be proxied and how to split
oversized content; the cog does the actual delete/webhook-send/store dance.
"""

from __future__ import annotations

from dataclasses import dataclass

from panem_shared.constants import PROXY_MESSAGE_MAX_LEN
from panem_shared.content.schemas import District, Location
from panem_shared.db.models import Character, Scene
from panem_shared.enums import CharacterStatus, Position, SceneKind


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


def _is_gamemaker(character: Character) -> bool:
    return Position.GAMEMAKER.value in character.positions


def can_rp_in_district(character: Character, district_id: int) -> bool:
    """Which district a character may be played in -- ordinarily their
    assigned `district_id` (set at creation from the player's Discord
    district role) or wherever `/travel district:<id>` has actually taken
    them (`current_district_id`), never a third district they've never
    been near. A Gamemaker's characters (Capitol staff overseeing every
    Games, wherever it's held) may be played in any district without
    traveling there at all."""
    if district_id in (character.district_id, character.current_district_id):
        return True
    return _is_gamemaker(character)


def can_rp_at_location(character: Character, location_id: str) -> bool:
    """Within a district a character may otherwise RP in, they still need
    to have actually traveled to this specific location
    (`Character.location_id`, set by `/travel location:<id>`) -- posting
    in a scene doesn't teleport them there for free. A Gamemaker's
    any-district access (`can_rp_in_district`) extends to skipping this
    too, since they were never going to have traveled there either."""
    if character.location_id == location_id:
        return True
    return _is_gamemaker(character)


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


def scene_location_id(scene: Scene | None) -> str | None:
    """A scene's location, for both the restricted-location and
    "have you actually traveled here" checks below -- `None` for a staff
    scene that doesn't pin location (`Scene.pins_location`), which is
    deliberately a roaming scene nobody needs to have traveled to."""
    if scene is None:
        return None
    if scene.kind == SceneKind.STAFF.value and not scene.pins_location:
        return None
    return scene.location_id


@dataclass(frozen=True, slots=True)
class ProxyRefusal:
    reason_key: str


def check_can_proxy(
    *, character: Character, district: District, location_id: str | None, current_tick: int
) -> ProxyRefusal | None:
    """FR-PRX-3. `district` must be the scene's actual district (not
    necessarily the character's home one -- see `can_rp_in_district`).
    Returns the refusal reason, or `None` if proxying may proceed."""
    if character.status == CharacterStatus.DEAD.value:
        return ProxyRefusal("character_dead")
    if character.status != CharacterStatus.APPROVED.value:
        return ProxyRefusal("character_not_approved")
    if character.jailed_until_tick is not None and character.jailed_until_tick > current_tick:
        return ProxyRefusal("proxy_character_jailed")
    if not can_rp_in_district(character, district.id):
        return ProxyRefusal("proxy_wrong_district")

    if location_id is not None:
        if not can_rp_at_location(character, location_id):
            return ProxyRefusal("proxy_not_traveled")
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
