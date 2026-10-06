"""Semantic NPC memory recall (Spec §6, `RETRIEVAL_K`).

`panem_shared.memory.retrieve` ranks an NPC's memories purely by importance
and recency, so an NPC asked about their sister recalls whatever was most
important lately -- not the memory about their sister. This module is the
meaning-aware half: an embedding model (nomic-embed on the Lite Lemonade
profile, Qwen3-Embedding on Halo) turns each memory and the line just spoken
into a vector, and `rank_memories` blends how close they are in meaning with
the old importance/recency signal.

Lives in `panem_shared` for the same reason `memory.retrieve` does: the bot's
dialogue path needs it and must not depend on `panem_sim`. The sim never
calls this -- a tick is deterministic and offline -- so memory *formation*
stays exactly as it was; vectors are filled in lazily the first time an NPC
actually has a conversation (`panem_bot.services.memory_recall`) and cached on
the row.

Everything here is pure except `embed_texts`, the one HTTP call.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

import httpx

from panem_shared import constants
from panem_shared.db.models import Memory
from panem_shared.lemonade import omni
from panem_shared.settings import Settings

# nomic-embed was trained with a task prefix on every input; without one,
# query-vs-document similarity is noticeably worse. Matched by checkpoint
# name so a profile swap to a different embedder doesn't inherit a prefix it
# wasn't trained with.
_NOMIC_QUERY_PREFIX = "search_query: "
_NOMIC_DOCUMENT_PREFIX = "search_document: "

# Embedding inputs are capped: a memory is a sentence or two, and a runaway
# player message shouldn't be able to blow the embedder's context window.
MAX_EMBED_INPUT_CHARS = 1000


class EmbeddingError(Exception):
    """The embedding server errored, timed out, or returned something that
    isn't a usable vector list. Callers fall back to importance/recency."""


def resolve_embedding_model(settings: Settings) -> str:
    if settings.embedding_model:
        return settings.embedding_model
    profile = omni.PROFILES.get(settings.lemonade_profile) or omni.PROFILES[omni.DEFAULT_PROFILE]
    return profile.embedding_model


def embeddings_active(settings: Settings, *, provider: str) -> bool:
    """Whether a conversation on `provider` (`dialogue.resolve_provider`)
    should use semantic recall at all -- never for the offline template
    provider, which makes no network calls by design."""
    return settings.embeddings_enabled and provider != "template"


def _prefixed(model: str, text: str, *, query: bool) -> str:
    text = text.strip()[:MAX_EMBED_INPUT_CHARS]
    if "nomic" in model.lower():
        return (_NOMIC_QUERY_PREFIX if query else _NOMIC_DOCUMENT_PREFIX) + text
    return text


async def embed_texts(
    texts: Sequence[str], settings: Settings, *, query: bool = False
) -> list[list[float]]:
    """One `/v1/embeddings` call for the whole batch; vectors come back in
    input order. `query=True` marks the inputs as search queries (nomic's
    prefix convention) rather than stored documents."""
    if not texts:
        return []
    model = resolve_embedding_model(settings)
    base_url = settings.llm_base_url or omni.DEFAULT_BASE_URL
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
    body = {"model": model, "input": [_prefixed(model, t, query=query) for t in texts]}
    try:
        async with httpx.AsyncClient(timeout=settings.embedding_timeout_ms / 1000) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/embeddings", json=body, headers=headers
            )
            response.raise_for_status()
            data = response.json()["data"]
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        vectors = [[float(x) for x in item["embedding"]] for item in ordered]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise EmbeddingError(str(exc) or type(exc).__name__) from exc
    if len(vectors) != len(texts) or any(not v for v in vectors):
        raise EmbeddingError(f"expected {len(texts)} vectors, got {len(vectors)}")
    return vectors


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in [-1, 1]; 0.0 for a zero vector or mismatched
    dimensions (a stale vector from a differently-sized model)."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def needs_embedding(memory: Memory, model: str) -> bool:
    return not memory.embedding or memory.embedding_model != model


def score_memory(
    memory: Memory, query_vector: Sequence[float] | None, *, newest_tick: int, oldest_tick: int
) -> float:
    """Weighted blend of meaning, importance and recency, each in [0, 1].

    A memory with no usable vector scores 0 on similarity rather than being
    dropped, so a half-embedded set still ranks sensibly and important/recent
    memories can still surface on their own merits."""
    similarity = 0.0
    if query_vector is not None and memory.embedding:
        similarity = max(0.0, cosine(query_vector, memory.embedding))
    importance = min(max(memory.importance, 0), constants.MEMORY_IMPORTANCE_MAX) / (
        constants.MEMORY_IMPORTANCE_MAX
    )
    span = newest_tick - oldest_tick
    recency = (memory.tick - oldest_tick) / span if span > 0 else 1.0
    return (
        constants.MEMORY_RECALL_WEIGHT_SIMILARITY * similarity
        + constants.MEMORY_RECALL_WEIGHT_IMPORTANCE * importance
        + constants.MEMORY_RECALL_WEIGHT_RECENCY * recency
    )


def rank_memories(
    memories: Iterable[Memory],
    owner_kind: str,
    owner_id: str,
    query_vector: Sequence[float] | None,
    k: int = constants.RETRIEVAL_K,
) -> list[Memory]:
    """The `k` best memories for one owner given the line just spoken.

    Ties (and the no-query case) fall back to importance-then-recency, so
    with `query_vector=None` this degrades to exactly what
    `memory.retrieve` returns."""
    candidates = [m for m in memories if m.owner_kind == owner_kind and m.owner_id == owner_id]
    if not candidates:
        return []
    newest = max(m.tick for m in candidates)
    oldest = min(m.tick for m in candidates)
    candidates.sort(
        key=lambda m: (
            -score_memory(m, query_vector, newest_tick=newest, oldest_tick=oldest),
            -m.importance,
            -m.tick,
        )
    )
    return candidates[:k]
