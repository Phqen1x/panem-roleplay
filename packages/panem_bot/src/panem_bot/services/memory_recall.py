"""Bot-side semantic recall of an NPC's memories (`panem_shared.embeddings`).

`recall` is what `ProxyCog.post_engagement_replies` calls in place of the
old "fetch every memory row, let `build_request_context` take the top few by
importance". It fetches the NPC's memories, lazily embeds any that don't yet
have a vector from the current embedding model (one batched call, persisted
on the row so each memory is only ever embedded once), embeds the line that
was just spoken, and returns the memories closest in meaning to it.

Every failure mode -- embeddings turned off, the template provider, no
memories, the embedding server down or slow, a malformed response -- returns
the exact result the pre-embedding code would have: the top `RETRIEVAL_K` by
importance then recency. A reply is never held up by, or fails because of,
this module.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from panem_shared import constants, embeddings
from panem_shared.db.models import Memory
from panem_shared.memory import retrieve as retrieve_by_importance
from panem_shared.settings import Settings

logger = structlog.get_logger()


async def recall(
    session: AsyncSession,
    *,
    npc_id: str,
    message: str,
    settings: Settings,
    provider: str,
    k: int = constants.RETRIEVAL_K,
) -> list[Memory]:
    """The up-to-`k` memories `npc_id` should have in mind when replying to
    `message`. `provider` is the NPC's resolved dialogue provider
    (`dialogue.resolve_provider`) -- the template provider never reaches the
    network, so it gets the plain importance/recency ordering."""
    stmt = select(Memory).where(Memory.owner_kind == "npc", Memory.owner_id == npc_id)
    if embeddings.embeddings_active(settings, provider=provider) and message.strip():
        # Undefer only when we'll actually read the vectors; the plain path
        # never touches the (large) column.
        stmt = stmt.options(undefer(Memory.embedding))
        rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            return []
        try:
            return await _recall_semantic(session, rows, npc_id, message, settings, k)
        except embeddings.EmbeddingError as exc:
            logger.warning("memory_recall_fallback", npc_id=npc_id, error=str(exc))
            return retrieve_by_importance(rows, "npc", npc_id, k)
    rows = list((await session.execute(stmt)).scalars().all())
    return retrieve_by_importance(rows, "npc", npc_id, k)


async def _recall_semantic(
    session: AsyncSession,
    rows: list[Memory],
    npc_id: str,
    message: str,
    settings: Settings,
    k: int,
) -> list[Memory]:
    model = embeddings.resolve_embedding_model(settings)
    stale = [m for m in rows if embeddings.needs_embedding(m, model)]

    # One request carries the query plus every memory still missing a vector,
    # in two calls only because nomic wants different prefixes for the two.
    query_vector = (await embeddings.embed_texts([message], settings, query=True))[0]
    if stale:
        vectors = await embeddings.embed_texts([m.text for m in stale], settings)
        for memory, vector in zip(stale, vectors, strict=True):
            memory.embedding = vector
            memory.embedding_model = model
        # The caller owns the transaction (`bot.db()` commits on exit); a
        # flush here just makes the cache visible to anything querying the
        # same session before then.
        await session.flush()
    return embeddings.rank_memories(rows, "npc", npc_id, query_vector, k)
