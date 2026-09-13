"""NPC engagements: group RP threads with one or more NPCs (and, with their
player's acceptance, other players' characters). Pure logic, no Discord/DB
I/O -- the cog owns thread creation, NPC relocation, and message posting;
this module decides *who can join*, *who's busy*, and *which NPCs should
reply* to a given message.

An engagement is a `Scene` (`SceneKind.ENGAGEMENT`), not a separate table --
see the README's "Notes on NPC engagements" for why. `Scene.participants`
(a JSONB column that existed unused before this feature) holds the roster:

    {"characters": [id, ...], "pending_characters": [id, ...], "npcs": ["npc_id", ...]}

`characters` are joined players, `pending_characters` are other players'
characters invited but not yet accepted, `npcs` are joined NPCs.
"""

from __future__ import annotations

import datetime as dt
import re

from panem_bot.errors import NotAllowed
from panem_shared import constants
from panem_shared.content.schemas import Job
from panem_shared.db.models import Character, Npc, Scene
from panem_shared.enums import CharacterStatus, DayPhase, SceneStatus


def npc_is_busy(npc: Npc, job: Job | None, phase: DayPhase) -> str | None:
    """Whether `npc` can be pulled into an engagement right now, reusing
    exactly the predicate `panem_sim.systems.schedule._arrival_reason`
    already uses to decide whether an arrival is narration-worthy: at
    their own job's workplace during its shift phase (`"shift"`), or at
    home during the night phase (`"sleep"`). Checked against the NPC's
    *current* actual location, not the schedule's weights -- an NPC who
    happens not to be at either place right now is free even if their
    schedule would usually put them there."""
    if job is not None and npc.location_id == job.workplace and phase == job.shift_phase:
        return "shift"
    if npc.location_id == npc.home_location_id and phase == DayPhase.NIGHT:
        return "sleep"
    return None


def resolve_npc_participants(
    names: list[str], candidates: list[Npc]
) -> tuple[list[Npc], list[str]]:
    """Case-insensitive match against `candidates` (already filtered to
    the target district by the caller). Returns (matched NPCs, names that
    matched no NPC -- which the caller then tries against characters)."""
    by_lower = {npc.name.lower(): npc for npc in candidates}
    matched: list[Npc] = []
    unmatched: list[str] = []
    for name in names:
        npc = by_lower.get(name.lower())
        if npc is not None:
            matched.append(npc)
        else:
            unmatched.append(name)
    return matched, unmatched


def resolve_character_participants(
    names: list[str], candidates: list[Character]
) -> tuple[list[Character], list[str]]:
    """Case-insensitive match against `candidates` (already filtered to
    characters physically at the engagement's location). Returns (matched
    characters, names that matched nobody present)."""
    by_lower = {c.name.lower(): c for c in candidates}
    matched: list[Character] = []
    unmatched: list[str] = []
    for name in names:
        character = by_lower.get(name.lower())
        if character is not None:
            matched.append(character)
        else:
            unmatched.append(name)
    return matched, unmatched


def check_can_start(*, character: Character) -> None:
    if character.status == CharacterStatus.DEAD.value:
        raise NotAllowed("character_dead")
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")


def _name_parts(full_name: str) -> list[str]:
    """First and last name-part, long enough to be a safe match
    (`NPC_NAME_MATCH_MIN_LEN`) -- a short first name like "Al" is dropped
    rather than risk firing on an unrelated word that happens to contain
    it; the NPC is simply not addressable by that short part alone."""
    parts = full_name.split()
    candidates = [parts[0], parts[-1]] if len(parts) > 1 else parts
    seen: set[str] = set()
    result = []
    for part in candidates:
        lowered = part.lower()
        if len(part) >= constants.NPC_NAME_MATCH_MIN_LEN and lowered not in seen:
            seen.add(lowered)
            result.append(part)
    return result


def name_mentioned(npc: Npc, content: str) -> bool:
    """Whether `content` addresses `npc` by first or last name -- a
    whole-word match (`\\bName\\b`), not a bare substring, so "Ann" in an
    engagement doesn't fire on "announcement"."""
    return any(
        re.search(rf"\b{re.escape(part)}\b", content, re.IGNORECASE)
        for part in _name_parts(npc.name)
    )


def npcs_that_should_reply(npcs: list[Npc], content: str, *, is_one_on_one: bool) -> list[Npc]:
    """All joined NPCs reply in a strict 1:1 engagement (there's no one
    else the player could be addressing); in anything bigger, only the
    ones actually named reply."""
    if is_one_on_one:
        return list(npcs)
    return [npc for npc in npcs if name_mentioned(npc, content)]


def scenes_to_close(scenes: list[Scene], *, timeout_minutes: int, now: dt.datetime) -> list[int]:
    """Which open scenes with NPC participants have gone `timeout_minutes`
    without a *player* speaking (`Scene.last_message_at` -- only ever
    touched by a player's own proxied line, never an NPC reply, see
    `ProxyCog.post_engagement_replies`), mirroring `services.scenes.
    pick_scenes_to_archive`'s shape: pure decision logic the cog's
    `tasks.loop` acts on, unit-testable with no Discord thread involved.
    Unlike that function, there's no district cap here -- every idle
    engagement closes, not just enough to get back under one. Checked by
    `participants["npcs"]` rather than `kind == ENGAGEMENT`, since `/talk`/
    `/engage start` can attach NPCs to any scene (an ambient thread, an
    open `/scene`), not just a dedicated engagement thread."""
    return [
        scene.id
        for scene in scenes
        if scene.participants.get("npcs")
        and scene.status == SceneStatus.OPEN.value
        and scene.last_message_at is not None
        and (now - scene.last_message_at) >= dt.timedelta(minutes=timeout_minutes)
    ]
