"""Panem's custom Lemonade OmniModel (`recipe: "collection.omni"`).

Lemonade (https://github.com/lemonade-sdk/lemonade) can bundle an LLM plus
image, speech-to-text, text-to-speech and any other regular model into one
virtual "omni" model. A `/v1/chat/completions` request addressed to the
collection goes through Lemonade's OmniRouter: the planner LLM is given the
collection's own `system_prompt` (with `{tool_list}` / `{tool_guidance}`
expanded from whichever components are present) and the server executes the
tool calls it makes.

This module is the single source of truth for Panem's collection:

* `PROFILES` picks the component models per hardware class.
* `render_system_prompt` turns `lemonade/system_prompt.md` into the prompt the
  collection stores, splicing the world atlas (from `data/*.yaml`) and the
  sim's rules (from `panem_shared.constants`) in at build time so the prompt
  can never drift from the content the sim actually runs on.
* `build_collection` produces an import-ready `/v1/pull` body -- the same
  format `lemonade export` writes -- with every component definition embedded
  so the file also imports on machines whose Lemonade catalog lacks a name.
* `RequestContext` / `render_request_header` define the per-request contract
  the sim uses when it calls the collection (the prompt teaches the model the
  same contract, so the two must stay in sync).

`scripts/lemonade_omni.py` is the CLI over this module.
"""

from __future__ import annotations

import enum
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from panem_shared import constants
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import District, Good, Job, Location
from panem_shared.enums import LocationKind

COLLECTION_RECIPE = "collection.omni"
ALIAS = "panem-omni"

# Expanded by lemond at request time (see Lemonade's collection_orchestrator).
TOOL_LIST_PLACEHOLDER = "{tool_list}"
TOOL_GUIDANCE_PLACEHOLDER = "{tool_guidance}"
# Expanded here at build time.
WORLD_ATLAS_MARKER = "<<WORLD_ATLAS>>"
WORLD_RULES_MARKER = "<<WORLD_RULES>>"

# Qwen3.x plans tool calls fine without its thinking phase, and an NPC line
# capped at MAX_WORDS_REPLY must come back inside the sim's per-turn timeout.
PLANNER_NO_THINKING_ARGS = "--chat-template-kwargs '{\"enable_thinking\": false}'"

# Which OmniRouter role a component fills, keyed by the label that unlocks it
# (docs/dev/lemonade-omni.md "Available Tools" + the desktop app's role table).
ROLE_LABELS: dict[str, tuple[str, ...]] = {
    "planner": ("chat",),
    "vision": ("vision",),
    "transcription": ("transcription", "audio"),
    "speech": ("tts", "speech"),
    "embeddings": ("embeddings",),
}


@dataclass(frozen=True, slots=True)
class OmniProfile:
    key: str
    model_name: str
    summary: str
    # Planner LLM first: lemond picks the first component labelled `chat`.
    components: tuple[str, ...]
    planner_options: Mapping[str, object]

    @property
    def planner(self) -> str:
        return self.components[0]


PROFILES: dict[str, OmniProfile] = {
    "lite": OmniProfile(
        key="lite",
        model_name="user.Panem-Omni-Lite",
        summary=(
            "~4.2 GB. Fits a 16 GB machine / 8 GB GPU. Qwen3.5-4B (vision + tool "
            "calling, MTP draft decoding) voices NPCs; Whisper-Base transcribes voice "
            "messages; nomic-embed powers NPC memory recall. No text-to-speech "
            "component -- see the README's Profiles section."
        ),
        components=(
            "Qwen3.5-4B-MTP-GGUF",
            "Whisper-Base",
            "nomic-embed-text-v1-GGUF",
        ),
        planner_options={"ctx_size": 16384, "llamacpp_args": PLANNER_NO_THINKING_ARGS},
    ),
    "halo": OmniProfile(
        key="halo",
        model_name="user.Panem-Omni-Halo",
        summary=(
            "~26 GB. For Strix Halo / 32 GB+ GPUs. Qwen3.6-35B-A3B (MoE, vision + tool "
            "calling, MTP) as the planner; Whisper-Large-v3-Turbo transcribes; "
            "Qwen3-Embedding-0.6B powers NPC memory recall. No text-to-speech "
            "component -- see the README's Profiles section."
        ),
        components=(
            "Qwen3.6-35B-A3B-MTP-GGUF",
            "Whisper-Large-v3-Turbo",
            "Qwen3-Embedding-0.6B-GGUF",
        ),
        planner_options={"ctx_size": 32768, "llamacpp_args": PLANNER_NO_THINKING_ARGS},
    ),
}

DEFAULT_PROFILE = "lite"


# --------------------------------------------------------------------------- #
# Component catalog (vendored from Lemonade's server_models.json)
# --------------------------------------------------------------------------- #


def load_components_catalog(path: Path) -> dict[str, dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a JSON object keyed by model name")
    return {name: entry for name, entry in raw.items() if not name.startswith("_")}


def role_for(labels: list[str]) -> str | None:
    for role, wanted in ROLE_LABELS.items():
        if any(label in wanted for label in labels):
            return role
    return None


def roles_covered(profile: OmniProfile, catalog: Mapping[str, Mapping[str, Any]]) -> set[str]:
    covered: set[str] = set()
    for name in profile.components:
        labels = list(catalog[name].get("labels", []))
        for role, wanted in ROLE_LABELS.items():
            if any(label in wanted for label in labels):
                covered.add(role)
    return covered


# --------------------------------------------------------------------------- #
# Prompt rendering
# --------------------------------------------------------------------------- #


def _humanize(token: str) -> str:
    text = token.replace("_", " ")
    for word, proper in (("capitol", "Capitol"), ("the games", "the Games"), ("hob", "Hob")):
        text = text.replace(word, proper)
    return text


def _level(value: float) -> str:
    if value < 0.15:
        return "very low"
    if value < 0.3:
        return "low"
    if value < 0.45:
        return "moderate"
    if value < 0.6:
        return "high"
    return "very high"


def _district_heading(district: District) -> str:
    label = "" if district.id == 0 else f" (D{district.id})"
    return f"### {district.name}{label} -- {_humanize(district.industry)}"


def _location_line(loc: Location, jobs_by_id: Mapping[str, Job]) -> str:
    bits = [loc.kind.value]
    if loc.kind == LocationKind.PUBLIC:
        bits = ["public gathering place"]
    if loc.illicit:
        bits.append("black market")
    if loc.restricted:
        who = [jobs_by_id[j].title.lower() + "s" for j in loc.access_jobs if j in jobs_by_id]
        items = [_humanize(i) for i in loc.access_items]
        gate = ", ".join(who + [f"holders of a {i}" for i in items]) or "staff-cleared characters"
        bits.append(f"restricted: {gate} only")
    if loc.job_ids:
        titles = [jobs_by_id[j].title for j in loc.job_ids if j in jobs_by_id]
        if titles:
            bits.append("work: " + ", ".join(titles))
    return f"{loc.name} ({'; '.join(bits)})"


def _job_line(job: Job, jobs_by_id: Mapping[str, Job]) -> str:
    parts = [job.title]
    if job.legal:
        parts.append(f"{job.wage:g}/shift")
    else:
        parts.append("illegal, unpaid but profitable")
    parts.append(f"{job.shift_phase.value}s")
    if job.ladder_next and job.ladder_next in jobs_by_id:
        req = job.ladder_requirement or {}
        needs = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in req.items())
        parts.append(
            f"promotes to {jobs_by_id[job.ladder_next].title}"
            + (f" after {needs}" if needs else "")
        )
    if job.min_reputation is not None:
        parts.append(f"needs reputation {job.min_reputation:g}")
    if job.peacekeeper_attention:
        parts.append(f"draws Peacekeeper attention ({_level(job.peacekeeper_attention)})")
    return ", ".join(parts)


def _good_label(good_id: str, goods: Mapping[str, Good]) -> str:
    """A good's name plus its `category` in parentheses (e.g. "coal
    (fuel)") -- the atlas is the only place a small planner model sees
    what a good actually *is*, and without that tag it has no way to
    tell a district's mined/manufactured quota good (fuel, materials,
    industrial) apart from something a person could eat (food)."""
    if good_id not in goods:
        return _humanize(good_id)
    good = goods[good_id]
    return f"{_humanize(good.name.lower())} ({_humanize(good.category)})"


def _district_block(district: District, bundle: ContentBundle) -> str:
    jobs = sorted(bundle.jobs_for_district(district.id), key=lambda j: (not j.legal, -j.wage))
    goods = bundle.goods
    lines = [_district_heading(district)]
    facts = [f"Population about {district.population_base:,}."]
    if district.produces:
        facts.append(
            "Produces: "
            + ", ".join(_good_label(g, goods) for g in district.produces)
            + " -- shipped to the Capitol; not necessarily what anyone here eats."
        )
    if district.quota is not None:
        good_label = _good_label(district.quota.good, goods)
        facts.append(f"Monthly Capitol quota: {district.quota.amount:,.0f} {good_label}.")
    if district.imports:
        facts.append("Imports: " + ", ".join(_good_label(g, goods) for g in district.imports) + ".")
    lines.append(" ".join(facts))
    culture = district.culture
    voice = [f"Voice: {', '.join(_humanize(t) for t in culture.tone)}."]
    if culture.idioms:
        voice.append("Local words: " + ", ".join(f'"{_humanize(i)}"' for i in culture.idioms) + ".")
    if culture.taboos:
        voice.append(
            "Taboo (never done openly): " + "; ".join(_humanize(t) for t in culture.taboos) + "."
        )
    voice.append(
        f"Peacekeeper pressure: {_level(culture.peacekeeper_pressure)}. "
        f"Capitol loyalty: {_level(culture.capitol_loyalty)}."
    )
    lines.append(" ".join(voice))
    lines.append(
        "Places: " + " | ".join(_location_line(loc, bundle.jobs) for loc in district.locations)
    )
    if jobs:
        lines.append("Work: " + " | ".join(_job_line(j, bundle.jobs) for j in jobs))
    return "\n".join(lines)


def _goods_line(goods: Mapping[str, Good]) -> str:
    parts: list[str] = []
    for good in sorted(goods.values(), key=lambda g: g.base_price):
        note = [good.category]
        if good.rationed:
            note.append("rationed")
        if good.perishable:
            note.append("spoils")
        parts.append(f"{good.name.lower()} {good.base_price:g} ({', '.join(note)})")
    line = "Goods and their base prices: " + " | ".join(parts) + "."
    line += (
        " Only the food-category goods here are things a person eats -- fuel, materials, "
        "industrial and utility goods are shipped or used, never consumed; transport is spent "
        "on train travel."
    )
    return line


def render_world_atlas(bundle: ContentBundle) -> str:
    blocks = [
        _district_block(d, bundle) for d in sorted(bundle.districts.values(), key=lambda d: d.id)
    ]
    blocks.append(_goods_line(bundle.goods))
    return "\n\n".join(blocks)


def render_world_rules() -> str:
    c = constants
    stance_lo, stance_dis, stance_like, stance_love = c.STANCE_THRESHOLDS
    crisis = ", ".join(f"{int(t * 100)}%" for t in c.CRISIS_THRESHOLDS)
    return "\n".join(
        [
            f"- Time: a world day has {c.TICKS_PER_DAY} hours split into night, morning, afternoon "
            f"and evening; a month has {c.DAYS_PER_MONTH} days. The sim tells you the current phase.",
            f"- Work: a shift lasts {c.SHIFT_DURATION_TICKS} hours. Missing "
            f"{c.MISSES_TO_MASTERY_PENALTY} shifts in a row costs you skill, not the job itself -- "
            f"new hires get {c.NEW_HIRE_GRACE_DAYS} days of grace. Foremen and shift bosses notice "
            "who shows up.",
            f"- Money: newcomers start with {c.STARTING_MONEY}. Wages are paid per shift. Market prices "
            f"swing between {c.PRICE_CLAMP_MIN:g}x and {c.PRICE_CLAMP_MAX:g}x the base price with "
            f"supply; selling to a market pays {int(c.SELL_DISCOUNT * 100)}% of the price and illicit "
            f"goods fetch {int(c.ILLICIT_PRICE_MULT * 100)}%. Medicine is rationed.",
            f"- Quotas and crises: each district owes the Capitol a monthly quota. Shortfalls escalate "
            f"through four crisis levels (at {crisis} short) and take about {c.CRISIS_RECOVERY_DAYS} "
            "days to recover from; crises mean hunger, blackouts, layoffs and more Peacekeepers.",
            f"- Travel: a round trip costs {c.TRANSPORT_UNITS_PER_TRIP} units of transport, bought at "
            f"a district market like any other good, and the journey takes {c.TRANSIT_TICKS} hours; a "
            f"job tolerates {c.AWAY_GRACE_DAYS} days away before counting you absent. A poor district "
            "can run short of transport the same way it runs short of anything else; most district "
            "folk never leave.",
            f"- Stances: every NPC holds an affinity toward each character. Below {stance_lo} they hate, "
            f"below {stance_dis} they dislike, above {stance_like} they like, above {stance_love} they "
            f"love; anything else is neutral, and 'stranger' means you have never met. Love and hate "
            f"only form after at least {c.STANCE_MIN_INTERACTIONS_EXTREME} interactions. Someone who "
            "loves you sells at a discount; someone who hates you refuses to trade at all.",
            f"- Memory: an NPC keeps up to {c.MEMORY_CAP_PER_NPC} memories and the sim hands you the "
            f"{c.RETRIEVAL_K} most relevant ones per conversation. Treat them as what the NPC "
            "genuinely remembers; do not invent shared history that is not there.",
            f"- Patience: NPCs have limited energy for talk each hour ({c.TALK_STAMINA_PER_HOUR} "
            "stamina); a tired NPC gets shorter and wants to get back to work or home.",
            f"- Treasury: each district's Justice Building holds a treasury (about {c.TREASURY_BASE:,} "
            "at baseline) that pays wages and relief; when it runs dry, so do the jobs.",
            f"- Characters: player characters are {c.CHARACTER_AGE_MIN} to {c.CHARACTER_AGE_MAX} years "
            "old, belong to one home district, and hold one job at a time. Victors have access to "
            "places ordinary citizens do not.",
            f"- Replies: an NPC line is at most {c.MAX_WORDS_REPLY} words unless the request says otherwise.",
        ]
    )


def validate_prompt_template(template: str) -> None:
    """Both OmniRouter placeholders must appear exactly once (lemond substitutes
    the first occurrence of each; the desktop editor refuses to save without
    them), and nothing else in the prompt may use braces so a future placeholder
    can never be confused with prose."""
    for placeholder in (TOOL_LIST_PLACEHOLDER, TOOL_GUIDANCE_PLACEHOLDER):
        if template.count(placeholder) != 1:
            raise ValueError(f"system prompt must contain {placeholder} exactly once")
    stripped = template.replace(TOOL_LIST_PLACEHOLDER, "").replace(TOOL_GUIDANCE_PLACEHOLDER, "")
    if "{" in stripped or "}" in stripped:
        raise ValueError(
            "system prompt may not contain braces other than the two OmniRouter placeholders"
        )
    for marker in (WORLD_ATLAS_MARKER, WORLD_RULES_MARKER):
        if template.count(marker) != 1:
            raise ValueError(f"system prompt template must contain {marker} exactly once")


def render_system_prompt(template: str, bundle: ContentBundle) -> str:
    validate_prompt_template(template)
    rendered = template.replace(WORLD_ATLAS_MARKER, render_world_atlas(bundle))
    rendered = rendered.replace(WORLD_RULES_MARKER, render_world_rules())
    return rendered.strip() + "\n"


# --------------------------------------------------------------------------- #
# Collection file (import-ready /v1/pull body, same shape as `lemonade export`)
# --------------------------------------------------------------------------- #


def build_collection(
    profile: OmniProfile,
    *,
    system_prompt: str,
    catalog: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    missing = [name for name in profile.components if name not in catalog]
    if missing:
        raise KeyError(f"components missing from lemonade/components.json: {missing}")
    models = [{"model_name": name, **catalog[name]} for name in profile.components]
    size = round(sum(float(entry.get("size", 0.0)) for entry in models), 2)
    return {
        "model_name": profile.model_name,
        "recipe": COLLECTION_RECIPE,
        "checkpoints": {"main": ""},
        "components": list(profile.components),
        "models": models,
        "labels": ["chat"],
        "recipe_options": {},
        "system_prompt": system_prompt,
        "size": size,
    }


def collection_filename(profile: OmniProfile) -> str:
    return profile.model_name.removeprefix("user.") + ".json"


def dump_collection(collection: Mapping[str, Any]) -> str:
    return json.dumps(collection, indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- #
# Per-request contract (what the sim sends; the prompt teaches the same shape)
# --------------------------------------------------------------------------- #


class RequestMode(enum.StrEnum):
    DIALOGUE = "dialogue"
    NARRATE = "narrate"
    BROADCAST = "broadcast"
    SPEAK = "speak"
    DESCRIBE_IMAGE = "describe_image"
    NPC_GENERATE = "npc_generate"
    REVIEW_CHARACTER = "review_character"
    STAFF = "staff"
    SUMMARIZE = "summarize"


@dataclass(frozen=True, slots=True)
class RequestContext:
    mode: RequestMode
    npc: Mapping[str, str] = field(default_factory=dict)
    scene: Mapping[str, str] = field(default_factory=dict)
    speaker: Mapping[str, str] = field(default_factory=dict)
    memories: tuple[str, ...] = ()
    constraints: Mapping[str, str] = field(default_factory=dict)


def render_request_header(ctx: RequestContext) -> str:
    lines = [f"[MODE: {ctx.mode.value}]"]
    for tag, block in (("NPC", ctx.npc), ("SCENE", ctx.scene), ("SPEAKER", ctx.speaker)):
        if block:
            lines.append(f"[{tag}] " + "; ".join(f"{k}: {v}" for k, v in block.items()))
    if ctx.memories:
        lines.append("[MEMORIES]")
        lines.extend(f"- {memory}" for memory in ctx.memories)
    constraints = {"max_words": str(constants.MAX_WORDS_REPLY), **ctx.constraints}
    lines.append("[CONSTRAINTS] " + "; ".join(f"{k}={v}" for k, v in constraints.items()))
    return "\n".join(lines)


def build_messages(ctx: RequestContext, turns: list[dict[str, str]]) -> list[dict[str, str]]:
    """lemond prepends the collection's system prompt to the first system
    message, so the request header rides in that slot and the conversation
    turns (`user` = the player's proxied line, `assistant` = the NPC's earlier
    lines) follow."""
    return [{"role": "system", "content": render_request_header(ctx)}, *turns]
