from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from panem_bot.services import memory_recall
from panem_shared import constants, embeddings
from panem_shared.db.models import Memory, Npc
from panem_shared.settings import Settings

AXES = {"sister": 0, "debt": 1, "winter": 2}


def vector_for(text: str) -> list[float]:
    vec = [0.0, 0.0, 0.0]
    for word, axis in AXES.items():
        if word in text.lower():
            vec[axis] = 1.0
    return vec if any(vec) else [0.1, 0.1, 0.1]


def make_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        llm_base_url="http://lemonade.local/v1",
        lemonade_profile="lite",
        dialogue_provider="llm",
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[arg-type]


def make_memory(**overrides: object) -> Memory:
    defaults: dict[str, object] = dict(
        owner_kind="npc",
        owner_id="npc1",
        tick=1,
        kind="misc",
        importance=2,
        text="Paid her debt early.",
        tags=[],
    )
    defaults.update(overrides)
    return Memory(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def fake_embedder(monkeypatch):
    """Routes every `httpx.AsyncClient` in `panem_shared.embeddings` at an
    in-process handler that embeds by keyword and records each request."""
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append({"url": str(request.url), "body": body, "headers": dict(request.headers)})
        data = [{"index": i, "embedding": vector_for(t)} for i, t in enumerate(body["input"])]
        # Real servers may answer out of order; the client must re-sort.
        return httpx.Response(200, json={"data": list(reversed(data))})

    real = httpx.AsyncClient

    def mock_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(embeddings.httpx, "AsyncClient", mock_client)
    return calls


class TestCosine:
    def test_identical_and_orthogonal(self):
        assert embeddings.cosine([1, 0], [1, 0]) == pytest.approx(1.0)
        assert embeddings.cosine([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_zero_vector_and_dimension_mismatch_score_zero(self):
        assert embeddings.cosine([0, 0], [1, 1]) == 0.0
        assert embeddings.cosine([1, 0, 0], [1, 0]) == 0.0
        assert embeddings.cosine([], []) == 0.0


class TestModelResolution:
    def test_defaults_to_the_profiles_embedder(self):
        assert (
            embeddings.resolve_embedding_model(make_settings(lemonade_profile="lite"))
            == "nomic-embed-text-v1-GGUF"
        )
        assert (
            embeddings.resolve_embedding_model(make_settings(lemonade_profile="halo"))
            == "Qwen3-Embedding-0.6B-GGUF"
        )

    def test_explicit_setting_wins(self):
        assert embeddings.resolve_embedding_model(make_settings(embedding_model="x")) == "x"

    def test_never_active_for_the_template_provider(self):
        settings = make_settings()
        assert embeddings.embeddings_active(settings, provider="llm")
        assert not embeddings.embeddings_active(settings, provider="template")
        assert not embeddings.embeddings_active(
            make_settings(embeddings_enabled=False), provider="llm"
        )


class TestEmbedTexts:
    async def test_posts_batch_sorts_by_index_and_prefixes_nomic(self, fake_embedder):
        settings = make_settings(llm_api_key="secret")
        out = await embeddings.embed_texts(["my sister", "the debt"], settings)

        assert out == [vector_for("my sister"), vector_for("the debt")]
        call = fake_embedder[0]
        assert call["url"] == "http://lemonade.local/v1/embeddings"
        assert call["headers"]["authorization"] == "Bearer secret"  # type: ignore[index]
        assert call["body"]["model"] == "nomic-embed-text-v1-GGUF"  # type: ignore[index]
        assert call["body"]["input"] == [  # type: ignore[index]
            "search_document: my sister",
            "search_document: the debt",
        ]

    async def test_query_gets_the_query_prefix(self, fake_embedder):
        await embeddings.embed_texts(["where is she"], make_settings(), query=True)
        assert fake_embedder[0]["body"]["input"] == ["search_query: where is she"]  # type: ignore[index]

    async def test_non_nomic_model_gets_no_prefix(self, fake_embedder):
        await embeddings.embed_texts(["hi"], make_settings(lemonade_profile="halo"))
        assert fake_embedder[0]["body"]["input"] == ["hi"]  # type: ignore[index]

    async def test_empty_input_makes_no_request(self, fake_embedder):
        assert await embeddings.embed_texts([], make_settings()) == []
        assert fake_embedder == []

    async def test_server_error_becomes_embedding_error(self, monkeypatch):
        real = httpx.AsyncClient
        monkeypatch.setattr(
            embeddings.httpx,
            "AsyncClient",
            lambda *a, **k: real(
                *a, **{**k, "transport": httpx.MockTransport(lambda r: httpx.Response(500))}
            ),
        )
        with pytest.raises(embeddings.EmbeddingError):
            await embeddings.embed_texts(["x"], make_settings())

    async def test_wrong_vector_count_is_an_error(self, monkeypatch):
        real = httpx.AsyncClient
        monkeypatch.setattr(
            embeddings.httpx,
            "AsyncClient",
            lambda *a, **k: real(
                *a,
                **{
                    **k,
                    "transport": httpx.MockTransport(
                        lambda r: httpx.Response(200, json={"data": []})
                    ),
                },
            ),
        )
        with pytest.raises(embeddings.EmbeddingError):
            await embeddings.embed_texts(["x"], make_settings())


class TestRankMemories:
    def test_on_topic_memory_beats_a_more_important_off_topic_one(self):
        sister = make_memory(text="Her sister left for the Capitol.", importance=2, tick=1)
        sister.embedding = vector_for(sister.text)
        winter = make_memory(text="The winter of the bad harvest.", importance=4, tick=2)
        winter.embedding = vector_for(winter.text)

        ranked = embeddings.rank_memories(
            [winter, sister], "npc", "npc1", vector_for("how is your sister?"), k=2
        )
        assert ranked[0] is sister

    def test_no_query_vector_matches_importance_then_recency(self):
        low = make_memory(importance=1, tick=9)
        high_old = make_memory(importance=4, tick=1)
        high_new = make_memory(importance=4, tick=5)
        ranked = embeddings.rank_memories([low, high_old, high_new], "npc", "npc1", None, k=3)
        assert ranked == [high_new, high_old, low]

    def test_importance_still_breaks_a_similarity_tie(self):
        a = make_memory(text="about the sister", importance=1)
        b = make_memory(text="also the sister", importance=5)
        a.embedding = b.embedding = [1.0, 0.0, 0.0]
        assert embeddings.rank_memories([a, b], "npc", "npc1", [1.0, 0.0, 0.0], k=1) == [b]

    def test_only_the_owners_memories_and_capped_at_k(self):
        mine = [make_memory(tick=t) for t in range(10)]
        other = make_memory(owner_id="someone-else")
        ranked = embeddings.rank_memories([*mine, other], "npc", "npc1", None)
        assert len(ranked) == constants.RETRIEVAL_K
        assert other not in ranked

    def test_memory_without_a_vector_still_ranks(self):
        plain = make_memory(importance=5)
        assert embeddings.rank_memories([plain], "npc", "npc1", [1.0, 0.0, 0.0]) == [plain]

    def test_weights_sum_to_one(self):
        total = (
            constants.MEMORY_RECALL_WEIGHT_SIMILARITY
            + constants.MEMORY_RECALL_WEIGHT_IMPORTANCE
            + constants.MEMORY_RECALL_WEIGHT_RECENCY
        )
        assert total == pytest.approx(1.0)


class TestRecall:
    async def _seed(self, db_session):
        db_session.add(Npc(id="npc1", district_id=1, name="Ferro", age=50))
        rows = [
            make_memory(text="Her sister left for the Capitol.", importance=2, tick=1),
            make_memory(text="The winter of the bad harvest.", importance=4, tick=2),
            make_memory(text="Paid her debt early.", importance=3, tick=3),
        ]
        db_session.add_all(rows)
        await db_session.flush()
        return rows

    async def test_returns_the_semantically_closest_memory_first(self, db_session, fake_embedder):
        await self._seed(db_session)
        out = await memory_recall.recall(
            db_session,
            npc_id="npc1",
            message="how is your sister?",
            settings=make_settings(),
            provider="llm",
            k=1,
        )
        assert [m.text for m in out] == ["Her sister left for the Capitol."]

    async def test_vectors_are_cached_so_a_second_recall_only_embeds_the_query(
        self, db_session, fake_embedder
    ):
        await self._seed(db_session)
        settings = make_settings()
        await memory_recall.recall(
            db_session, npc_id="npc1", message="sister", settings=settings, provider="llm"
        )
        first_calls = len(fake_embedder)
        assert first_calls == 2  # query + the three stale memories, one batch each

        await memory_recall.recall(
            db_session, npc_id="npc1", message="the debt", settings=settings, provider="llm"
        )
        assert len(fake_embedder) == first_calls + 1  # query only

        stored = (await db_session.execute(select(Memory))).scalars().all()
        assert all(m.embedding_model == "nomic-embed-text-v1-GGUF" for m in stored)

    async def test_a_changed_embedding_model_re_embeds(self, db_session, fake_embedder):
        await self._seed(db_session)
        await memory_recall.recall(
            db_session,
            npc_id="npc1",
            message="sister",
            settings=make_settings(),
            provider="llm",
        )
        before = len(fake_embedder)
        await memory_recall.recall(
            db_session,
            npc_id="npc1",
            message="sister",
            settings=make_settings(embedding_model="other-embedder"),
            provider="llm",
        )
        assert len(fake_embedder) == before + 2

    async def test_template_provider_never_touches_the_network(self, db_session, fake_embedder):
        await self._seed(db_session)
        out = await memory_recall.recall(
            db_session,
            npc_id="npc1",
            message="sister",
            settings=make_settings(),
            provider="template",
        )
        assert fake_embedder == []
        assert [m.importance for m in out] == [4, 3, 2]  # importance order, as before

    async def test_embedding_failure_falls_back_to_importance_order(self, db_session, monkeypatch):
        await self._seed(db_session)

        async def boom(*args: object, **kwargs: object) -> list[list[float]]:
            raise embeddings.EmbeddingError("server down")

        monkeypatch.setattr(embeddings, "embed_texts", boom)
        out = await memory_recall.recall(
            db_session,
            npc_id="npc1",
            message="sister",
            settings=make_settings(),
            provider="llm",
        )
        assert [m.importance for m in out] == [4, 3, 2]

    async def test_npc_with_no_memories_gets_nothing(self, db_session, fake_embedder):
        out = await memory_recall.recall(
            db_session, npc_id="ghost", message="hi", settings=make_settings(), provider="llm"
        )
        assert out == []
