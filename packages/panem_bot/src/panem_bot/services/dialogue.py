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
from collections.abc import Mapping, Sequence

import httpx
import structlog
from redis.asyncio import Redis

from panem_bot.errors import NotAllowed
from panem_shared import constants
from panem_shared.content.schemas import District, Location
from panem_shared.db.models import Character, DistrictState, Memory, Npc
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
    npc_job_title: str | None = None,
    npc_background: str | None = None,
    district_state: DistrictState | None = None,
    character_job_title: str | None = None,
    character_home_district: District | None = None,
    known: str | None = None,
    constraints: Mapping[str, str] | None = None,
    district_on_edge: bool = False,
) -> omni.RequestContext:
    """`present` lists everyone else in the scene besides `character`
    (other engaged NPCs, other joined characters) -- the system prompt
    already documents and expects a `[SCENE] ... present: ...` field
    (see `lemonade/system_prompt.md`'s request-contract example), this is
    just the first real caller to populate it, for a group `/engage`
    thread rather than `/talk`'s always-1:1 case.

    The rest of the new, optional fields fill in blocks the system prompt
    has always documented (`[NPC] ... job or role; ... personality`,
    `[SCENE] ... crisis level if any`, `[SPEAKER] ... district, job,
    reputation`) but that nothing populated before this -- so an NPC's
    opinion of the speaker (`stance`) was the only thing actually shaping
    a reply; their own background, the speaker's own standing, and the
    district's state never reached the model at all. `None`/empty
    callers (npc-to-npc chatter, any caller that skips them) simply don't
    get those header lines -- `render_request_header` already omits an
    empty block.

    `district_on_edge` (contraband system) is the caller's pre-resolved
    `panem_shared.jail.is_crackdown_active` check -- this function stays
    tick-unaware, matching every other already-resolved field here
    (`known`, `district_state`, ...); when true it adds a line to the
    scene block so an NPC's reply can reflect peacekeepers cracking down
    without the model needing to infer it from crisis/unrest numbers
    alone.

    `known` is `RelationshipRow.summary` for this NPC-character pair -- a
    running recap of every past engagement between them, already trimmed
    to `RELATIONSHIP_SUMMARY_MAX_WORDS` by `summarize_engagement`. It fills
    the `[SPEAKER] ... what the NPC knows of them` line the system prompt
    has always documented but nothing populated before this. `constraints`
    lets a caller override `render_request_header`'s default `max_words`
    (see `_length_matched_max_words`) without touching anything else."""
    relevant = retrieve_memories(memories, "npc", npc.id)
    tone = (npc.speech_style or {}).get("tone", "plain")

    npc_block: dict[str, str] = {
        "name": npc.name,
        "age": str(npc.age),
        "stance": stance,
        "tone": tone,
    }
    if npc_job_title:
        npc_block["job"] = npc_job_title
    if npc.traits:
        npc_block["personality"] = ", ".join(npc.traits)
    if npc_background:
        npc_block["background"] = npc_background

    scene: dict[str, str] = {"location": location.name, "district": district.name}
    if present:
        scene["present"] = ", ".join(present)
    if district_state is not None:
        crisis = f"level {district_state.crisis_level}"
        if district_state.crisis_kind:
            crisis += f" ({district_state.crisis_kind})"
        scene["crisis"] = crisis
        scene["district_mood"] = (
            f"morale {district_state.morale:.0f}/100, unrest {district_state.unrest:.0f}/100"
        )
    if district_on_edge:
        scene["peacekeeper_crackdown"] = (
            "active -- peacekeepers are cracking down, people are visibly on edge"
        )

    speaker: dict[str, str] = {"name": character.name}
    if character_job_title:
        speaker["job"] = character_job_title
    if character_home_district is not None:
        speaker["district"] = character_home_district.name
    speaker["reputation"] = f"{character.reputation or 0.0:.1f}"
    if known:
        speaker["known"] = known

    return omni.RequestContext(
        mode=omni.RequestMode.DIALOGUE,
        npc=npc_block,
        scene=scene,
        speaker=speaker,
        memories=tuple(m.text for m in relevant),
        constraints=constraints or {},
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
        "max_tokens": constants.LLM_REPLY_MAX_TOKENS,
        "temperature": constants.LLM_REPLY_TEMPERATURE,
        # Local models repeat themselves (the same phrase, the same
        # memory) far more readily than a large hosted one -- these push
        # the model off whatever it's already said this request, on top
        # of `history` already showing it its own prior turns.
        "frequency_penalty": constants.LLM_REPLY_FREQUENCY_PENALTY,
        "presence_penalty": constants.LLM_REPLY_PRESENCE_PENALTY,
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


def _length_matched_max_words(message: str) -> int:
    """How long a reply should be allowed to run, scaled off how much the
    message it's answering actually said: `REPLY_LENGTH_RATIO` words of
    reply per word of message, clamped to `[MIN_WORDS_REPLY,
    MAX_WORDS_REPLY]`. A "hey" gets a short answer, a few sentences of
    news get real room to respond -- the reply tracks the speaker instead
    of sitting at the same flat cap regardless of what was just said."""
    word_count = len(message.split())
    target = round(word_count * constants.REPLY_LENGTH_RATIO)
    return max(constants.MIN_WORDS_REPLY, min(constants.MAX_WORDS_REPLY, target))


def build_npc_to_npc_context(
    *, npc: Npc, other_npc: Npc, district: District, location: Location, message: str = ""
) -> omni.RequestContext:
    """The NPC-to-NPC counterpart of `build_request_context`: `speaker`
    is another NPC rather than a player `Character` (no reputation/job
    block one of those would carry), and there's no stance/memories
    lookup -- unprompted ambient chatter (`panem_sim.systems.
    npc_chatter`) is pure world flavor, not a tracked relationship the
    way `/talk`'s stance and memories are."""
    tone = (npc.speech_style or {}).get("tone", "plain")
    constraints = {"max_words": str(_length_matched_max_words(message))} if message else {}
    return omni.RequestContext(
        mode=omni.RequestMode.DIALOGUE,
        npc={"name": npc.name, "stance": "neutral", "tone": tone},
        scene={"location": location.name, "district": district.name},
        speaker={"name": other_npc.name},
        constraints=constraints,
    )


async def generate_npc_to_npc_reply(
    *,
    npc: Npc,
    other_npc: Npc,
    district: District,
    location: Location,
    message: str,
    settings: Settings,
    history: Sequence[dict[str, str]] = (),
) -> str:
    """`npc` is who's about to speak next; `other_npc` is who they're
    replying to. `message` is the other NPC's last line -- or, for the
    very first line of the exchange, an OOC stage direction (`(( ... ))`,
    the same convention `lemonade/system_prompt.md` already documents
    for out-of-character instructions) rather than anything literally
    said, since nothing prompted this conversation but proximity."""
    provider = resolve_provider(npc, settings)
    if provider == "template":
        return template_reply(npc, "neutral", message)

    ctx = build_npc_to_npc_context(
        npc=npc, other_npc=other_npc, district=district, location=location, message=message
    )
    try:
        return await generate_llm_reply(ctx, message, settings, history=history)
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        logger.warning("dialogue_llm_failed", npc_id=npc.id, error=str(exc))
        return template_reply(npc, "neutral", message)


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
    npc_job_title: str | None = None,
    npc_background: str | None = None,
    district_state: DistrictState | None = None,
    character_job_title: str | None = None,
    character_home_district: District | None = None,
    known: str | None = None,
    district_on_edge: bool = False,
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
        npc_job_title=npc_job_title,
        district_on_edge=district_on_edge,
        npc_background=npc_background,
        district_state=district_state,
        character_job_title=character_job_title,
        character_home_district=character_home_district,
        known=known,
        constraints={"max_words": str(_length_matched_max_words(message))},
    )
    try:
        return await generate_llm_reply(ctx, message, settings, history=history)
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        logger.warning("dialogue_llm_failed", npc_id=npc.id, error=str(exc))
        return template_reply(npc, stance, message)


async def summarize_engagement(
    *,
    npc: Npc,
    transcript: Sequence[str],
    previous_summary: str | None,
    settings: Settings,
) -> str | None:
    """Folds a just-closed engagement's transcript (`"Name: line"` strings,
    oldest first -- player lines and every joined NPC's own replies alike)
    into an updated `RelationshipRow.summary` for `npc`, from `npc`'s own
    perspective. `previous_summary` is whatever was already stored for
    this pair; the request hands the model both so it merges rather than
    only describing what's new, keeping the result a bounded, running
    recap instead of an ever-growing transcript (`lemonade/system_prompt.
    md`'s `summarize` mode documents this contract).

    No template fallback -- there's no sensible canned summary of an
    actual conversation. `provider == "template"`, an empty transcript, or
    any LLM failure all just return `previous_summary` unchanged, exactly
    as if this engagement had never been summarized; the caller persists
    whatever comes back, so a `None` in and a `None` out both mean 'still
    nothing known'."""
    if not transcript:
        return previous_summary
    provider = resolve_provider(npc, settings)
    if provider == "template":
        return previous_summary

    ctx = omni.RequestContext(
        mode=omni.RequestMode.SUMMARIZE,
        npc={"name": npc.name},
        constraints={"max_words": str(constants.RELATIONSHIP_SUMMARY_MAX_WORDS)},
    )
    prompt = (
        f"What you already knew before this conversation: "
        f"{previous_summary or 'nothing yet -- this is the first time.'}\n\n"
        "The conversation that just happened:\n" + "\n".join(transcript)
    )
    try:
        summary = await generate_llm_reply(ctx, prompt, settings)
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        logger.warning("dialogue_summarize_failed", npc_id=npc.id, error=str(exc))
        return previous_summary

    words = summary.split()
    if len(words) > constants.RELATIONSHIP_SUMMARY_MAX_WORDS:
        summary = " ".join(words[: constants.RELATIONSHIP_SUMMARY_MAX_WORDS]) + "…"
    return summary
