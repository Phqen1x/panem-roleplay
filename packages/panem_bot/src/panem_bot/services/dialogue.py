"""LLM-driven NPC dialogue (Plan Phase 6; see `lemonade/README.md`).

`/talk` is a live, one-shot bot request -- unlike `panem_sim`'s tick-driven
systems, nothing here writes `Memory`/`RelationshipRow` rows. Those stay
sim-owned (formed from `state.notable_events` during a tick, per
`panem_sim.systems.memory`/`social`), so a conversation shapes an NPC's
future memory/opinion of a character only once the sim's own systems
decide it was notable -- same as every other NPC-facing interaction.

`Settings.dialogue_provider` ("template"/anything else) picks the default
path; `Npc.provider_override` lets a specific NPC override that (e.g. a
named Mentor forced onto the LLM even while the world default stays
"template"). Any LLM failure (timeout, connection error, bad response)
falls back to the template reply rather than failing the command --
immersion-breaking silently beats a visible error for a roleplay bot.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

import httpx
import structlog
from redis.asyncio import Redis

from panem_bot.errors import NotAllowed
from panem_shared import constants
from panem_shared.content.schemas import District, Location
from panem_shared.db.models import Character, Memory, Npc
from panem_shared.enums import CharacterStatus
from panem_shared.lemonade import omni
from panem_shared.memory import retrieve as retrieve_memories
from panem_shared.settings import Settings

logger = structlog.get_logger()

STAMINA_KEY_PREFIX = "talk:stamina"


def check_can_talk(character: Character) -> None:
    if character.status != CharacterStatus.APPROVED.value:
        raise NotAllowed("character_not_approved")


def _stamina_key(npc_id: str, tick: int) -> str:
    return f"{STAMINA_KEY_PREFIX}:{npc_id}:{tick}"


async def check_and_spend_stamina(
    redis_client: Redis, *, npc: Npc, tick: int, ttl_seconds: int
) -> None:
    """FR-ish: an NPC only has so much patience for talk in a given
    in-world hour (`TALK_STAMINA_PER_HOUR`, one tick == one hour per
    `TICKS_PER_DAY`). Counted in Redis rather than a DB column since it's
    disposable, per-hour state -- the key expires on its own once the tick
    it was spent in is long past."""
    key = _stamina_key(npc.id, tick)
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, ttl_seconds)
    if count > constants.TALK_STAMINA_PER_HOUR:
        raise NotAllowed("npc_needs_a_moment", name=npc.name)


def resolve_provider(npc: Npc, settings: Settings) -> str:
    return npc.provider_override or settings.dialogue_provider


def build_request_context(
    *,
    npc: Npc,
    district: District,
    location: Location,
    character: Character,
    stance: str,
    memories: list[Memory],
    present: Sequence[str] = (),
) -> omni.RequestContext:
    """`present` lists everyone else in the scene besides `character`
    (other engaged NPCs, other joined characters) -- the system prompt
    already documents and expects a `[SCENE] ... present: ...` field
    (see `lemonade/system_prompt.md`'s request-contract example), this is
    just the first real caller to populate it, for a group `/engage`
    thread rather than `/talk`'s always-1:1 case."""
    relevant = retrieve_memories(memories, "npc", npc.id)
    tone = (npc.speech_style or {}).get("tone", "plain")
    scene: dict[str, str] = {"location": location.name, "district": district.name}
    if present:
        scene["present"] = ", ".join(present)
    return omni.RequestContext(
        mode=omni.RequestMode.DIALOGUE,
        npc={"name": npc.name, "stance": stance, "tone": tone},
        scene=scene,
        speaker={"name": character.name},
        memories=tuple(m.text for m in relevant),
    )


_STANCE_OPENERS: dict[str, tuple[str, ...]] = {
    "loves": ("brightens and says", "grins warmly and says"),
    "likes": ("nods and says", "smiles a little and says"),
    "neutral": ("looks over and says", "shrugs and says"),
    "dislikes": ("frowns and says", "eyes you warily and says"),
    "hates": ("glares and snaps", "scowls and says"),
    "stranger": ("looks you over and says", "raises an eyebrow and says"),
}

_TONE_LINES: dict[str, tuple[str, ...]] = {
    "warm": ('"Good to see a friendly face around here."', '"What can I do for you?"'),
    "blunt": ('"Make it quick, I\'ve got work."', '"Say what you came to say."'),
    "reserved": ('"..."', '"Hm. Go on."'),
    "plain": ('"What do you need?"', '"Yeah?"'),
}


def template_reply(npc: Npc, stance: str, message: str) -> str:
    """The `dialogue_provider="template"` (or LLM-fallback) path: no
    real language understanding, just a canned line varied by the NPC's
    `speech_tone` (`panem_shared.content.traits.speech_tone`) and the
    speaking character's `stance` with this NPC -- seeded by the message
    text so the same line doesn't repeat every call."""
    tone = (npc.speech_style or {}).get("tone", "plain")
    openers = _STANCE_OPENERS.get(stance, _STANCE_OPENERS["stranger"])
    lines = _TONE_LINES.get(tone, _TONE_LINES["plain"])
    rng = random.Random(hash((npc.id, message)))
    opener = rng.choice(openers)
    line = rng.choice(lines)
    return f"*{npc.name} {opener},* {line}"


async def generate_llm_reply(
    ctx: omni.RequestContext,
    message: str,
    settings: Settings,
    *,
    history: Sequence[dict[str, str]] = (),
) -> str:
    """`history` is prior `{role, content}` turns (oldest first) from
    earlier in the same conversation -- an engagement thread's
    `SceneMessage` rows, most recently. `/talk`'s always-fresh 1:1 calls
    just pass none, exactly as before this parameter existed."""
    model = settings.llm_model or omni.PROFILES[settings.lemonade_profile].model_name
    body: dict[str, object] = {
        "model": model,
        "messages": omni.build_messages(ctx, [*history, {"role": "user", "content": message}]),
        "max_tokens": 200,
        "temperature": 0.8,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
    timeout = settings.llm_timeout_ms / 1000
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            json=body,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
    reply: str = data["choices"][0]["message"]["content"]
    return reply.strip()


async def generate_reply(
    *,
    npc: Npc,
    district: District,
    location: Location,
    character: Character,
    stance: str,
    memories: list[Memory],
    message: str,
    settings: Settings,
    history: Sequence[dict[str, str]] = (),
    present: Sequence[str] = (),
) -> str:
    provider = resolve_provider(npc, settings)
    if provider == "template":
        return template_reply(npc, stance, message)

    ctx = build_request_context(
        npc=npc,
        district=district,
        location=location,
        character=character,
        stance=stance,
        memories=memories,
        present=present,
    )
    try:
        return await generate_llm_reply(ctx, message, settings, history=history)
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        logger.warning("dialogue_llm_failed", npc_id=npc.id, error=str(exc))
        return template_reply(npc, stance, message)
