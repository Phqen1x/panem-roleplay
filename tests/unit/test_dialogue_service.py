from __future__ import annotations

import json

import httpx
import pytest

from panem_bot.errors import NotAllowed
from panem_bot.services import dialogue
from panem_shared import constants
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    Location,
)
from panem_shared.db.models import Character, Memory, Npc
from panem_shared.enums import CharacterStatus
from panem_shared.lemonade import omni
from panem_shared.settings import Settings


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expiries: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.expiries[key] = ttl


def make_npc(**overrides: object) -> Npc:
    defaults: dict[str, object] = dict(
        id="npc1",
        district_id=1,
        name="Old Ferro",
        age=60,
        speech_style={"tone": "blunt"},
        traits=["stern"],
    )
    defaults.update(overrides)
    return Npc(**defaults)  # type: ignore[arg-type]


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Kat",
        age=17,
        status=CharacterStatus.APPROVED.value,
    )
    defaults.update(overrides)
    return Character(**defaults)  # type: ignore[arg-type]


def make_district() -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
        Location(id="hob", name="The Hob", kind="market"),
    ]
    coords = {loc.id: (0, 0) for loc in locations}
    return District(
        id=1,
        name="District 12",
        industry="coal",
        produces=["coal"],
        population_base=8000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=100, height=100, location_coords=coords),
    )


def make_memory(**overrides: object) -> Memory:
    defaults: dict[str, object] = dict(
        owner_kind="npc",
        owner_id="npc1",
        tick=1,
        kind="misc",
        importance=1,
        text="Paid her debt early.",
        tags=[],
    )
    defaults.update(overrides)
    return Memory(**defaults)  # type: ignore[arg-type]


def make_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        dialogue_provider="template",
        llm_base_url="http://lemonade.local/v1",
        llm_model="panem-omni",
        llm_timeout_ms=8000,
    )
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


class TestCheckCanTalk:
    def test_rejects_unapproved_character(self):
        char = make_character(status=CharacterStatus.PENDING.value)
        with pytest.raises(NotAllowed):
            dialogue.check_can_talk(char)

    def test_allows_approved_character(self):
        dialogue.check_can_talk(make_character())


class TestResolveProvider:
    def test_settings_default_when_no_override(self):
        assert dialogue.resolve_provider(make_npc(), make_settings()) == "template"

    def test_npc_override_wins(self):
        npc = make_npc(provider_override="llm")
        assert dialogue.resolve_provider(npc, make_settings()) == "llm"


class TestStamina:
    async def test_allows_up_to_the_cap(self):
        redis_client = FakeRedis()
        npc = make_npc()
        for _ in range(constants.TALK_STAMINA_PER_HOUR):
            await dialogue.check_and_spend_stamina(
                redis_client,
                npc=npc,
                tick=1,
                ttl_seconds=1200,  # type: ignore[arg-type]
            )

    async def test_raises_once_the_cap_is_exceeded(self):
        redis_client = FakeRedis()
        npc = make_npc()
        for _ in range(constants.TALK_STAMINA_PER_HOUR):
            await dialogue.check_and_spend_stamina(
                redis_client,
                npc=npc,
                tick=1,
                ttl_seconds=1200,  # type: ignore[arg-type]
            )
        with pytest.raises(NotAllowed):
            await dialogue.check_and_spend_stamina(
                redis_client,
                npc=npc,
                tick=1,
                ttl_seconds=1200,  # type: ignore[arg-type]
            )

    async def test_a_new_tick_resets_the_counter(self):
        redis_client = FakeRedis()
        npc = make_npc()
        for _ in range(constants.TALK_STAMINA_PER_HOUR):
            await dialogue.check_and_spend_stamina(
                redis_client,
                npc=npc,
                tick=1,
                ttl_seconds=1200,  # type: ignore[arg-type]
            )
        await dialogue.check_and_spend_stamina(
            redis_client,
            npc=npc,
            tick=2,
            ttl_seconds=1200,  # type: ignore[arg-type]
        )

    async def test_sets_an_expiry_on_first_spend(self):
        redis_client = FakeRedis()
        await dialogue.check_and_spend_stamina(
            redis_client,
            npc=make_npc(),
            tick=1,
            ttl_seconds=1200,  # type: ignore[arg-type]
        )
        assert redis_client.expiries == {"talk:stamina:npc1:1": 1200}


class TestBuildRequestContext:
    def test_only_this_npcs_memories_are_included(self):
        npc = make_npc()
        memories = [
            make_memory(text="about npc1"),
            make_memory(owner_id="npc2", text="not this one"),
        ]
        ctx = dialogue.build_request_context(
            npc=npc,
            district=make_district(),
            location=make_district().locations[-1],
            character=make_character(),
            stance="likes",
            memories=memories,
        )
        assert ctx.memories == ("about npc1",)
        assert ctx.npc == {"name": "Old Ferro", "stance": "likes", "tone": "blunt"}
        assert ctx.scene == {"location": "The Hob", "district": "District 12"}
        assert ctx.speaker == {"name": "Kat"}
        assert ctx.mode is omni.RequestMode.DIALOGUE

    def test_present_is_folded_into_the_scene_block_when_given(self):
        ctx = dialogue.build_request_context(
            npc=make_npc(),
            district=make_district(),
            location=make_district().locations[-1],
            character=make_character(),
            stance="likes",
            memories=[],
            present=["Greasy Sae", "Peeta"],
        )
        assert ctx.scene["present"] == "Greasy Sae, Peeta"

    def test_present_is_omitted_from_scene_when_empty(self):
        ctx = dialogue.build_request_context(
            npc=make_npc(),
            district=make_district(),
            location=make_district().locations[-1],
            character=make_character(),
            stance="likes",
            memories=[],
        )
        assert "present" not in ctx.scene


class TestTemplateReply:
    def test_mentions_the_npc_and_is_non_empty(self):
        reply = dialogue.template_reply(make_npc(), "likes", "hello there")
        assert "Old Ferro" in reply
        assert len(reply) > 0

    def test_is_deterministic_for_the_same_message(self):
        first = dialogue.template_reply(make_npc(), "likes", "hello there")
        second = dialogue.template_reply(make_npc(), "likes", "hello there")
        assert first == second


class TestGenerateReply:
    async def test_template_provider_never_calls_the_llm(self):
        npc = make_npc()
        reply = await dialogue.generate_reply(
            npc=npc,
            district=make_district(),
            location=make_district().locations[-1],
            character=make_character(),
            stance="likes",
            memories=[],
            message="hello",
            settings=make_settings(dialogue_provider="template"),
        )
        assert reply == dialogue.template_reply(npc, "likes", "hello")

    async def test_llm_provider_uses_the_llm_reply(self, monkeypatch):
        async def fake_llm(ctx, message, settings, *, history=()):
            return "The LLM says hello."

        monkeypatch.setattr(dialogue, "generate_llm_reply", fake_llm)
        reply = await dialogue.generate_reply(
            npc=make_npc(provider_override="llm"),
            district=make_district(),
            location=make_district().locations[-1],
            character=make_character(),
            stance="likes",
            memories=[],
            message="hello",
            settings=make_settings(dialogue_provider="template"),
        )
        assert reply == "The LLM says hello."

    async def test_llm_failure_falls_back_to_the_template(self, monkeypatch):
        async def failing_llm(ctx, message, settings, *, history=()):
            raise httpx.ConnectError("no route to host")

        monkeypatch.setattr(dialogue, "generate_llm_reply", failing_llm)
        npc = make_npc()
        reply = await dialogue.generate_reply(
            npc=npc,
            district=make_district(),
            location=make_district().locations[-1],
            character=make_character(),
            stance="likes",
            memories=[],
            message="hello",
            settings=make_settings(dialogue_provider="llm"),
        )
        assert reply == dialogue.template_reply(npc, "likes", "hello")


class TestGenerateLlmReply:
    async def test_posts_the_expected_body_and_parses_the_reply(self, monkeypatch):
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "  Hello, stranger.  "}}]},
            )

        real_async_client = httpx.AsyncClient

        def mock_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(dialogue.httpx, "AsyncClient", mock_client)

        ctx = omni.RequestContext(mode=omni.RequestMode.DIALOGUE, speaker={"name": "Kat"})
        settings = make_settings(
            llm_base_url="http://lemonade.local/v1", llm_api_key="secret", llm_model="panem-omni"
        )

        reply = await dialogue.generate_llm_reply(ctx, "hello", settings)

        assert reply == "Hello, stranger."
        assert captured["url"] == "http://lemonade.local/v1/chat/completions"
        assert captured["headers"]["authorization"] == "Bearer secret"  # type: ignore[index]
        body = captured["body"]
        assert body["model"] == "panem-omni"  # type: ignore[index]
        assert body["messages"][-1] == {"role": "user", "content": "hello"}  # type: ignore[index]

    async def test_history_turns_come_before_the_new_message(self, monkeypatch):
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": "reply"}}]})

        real_async_client = httpx.AsyncClient

        def mock_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(dialogue.httpx, "AsyncClient", mock_client)

        ctx = omni.RequestContext(mode=omni.RequestMode.DIALOGUE, speaker={"name": "Kat"})
        settings = make_settings(llm_base_url="http://lemonade.local/v1", llm_model="panem-omni")
        history = [
            {"role": "user", "content": "Evening, Ferro."},
            {"role": "assistant", "content": '*nods and says,* "Yeah?"'},
        ]

        await dialogue.generate_llm_reply(ctx, "Got anything hot?", settings, history=history)

        messages = captured["body"]["messages"]  # type: ignore[index]
        assert messages[1:3] == history
        assert messages[-1] == {"role": "user", "content": "Got anything hot?"}


class TestNpcToNpcReply:
    def test_context_uses_the_other_npc_as_speaker(self):
        ferro = make_npc(id="npc1", name="Old Ferro")
        sae = make_npc(id="npc2", name="Greasy Sae", speech_style={"tone": "warm"})
        ctx = dialogue.build_npc_to_npc_context(
            npc=ferro,
            other_npc=sae,
            district=make_district(),
            location=make_district().locations[-1],
        )
        assert ctx.npc == {"name": "Old Ferro", "stance": "neutral", "tone": "blunt"}
        assert ctx.speaker == {"name": "Greasy Sae"}
        assert ctx.memories == ()

    async def test_template_provider_never_calls_the_llm(self):
        ferro = make_npc()
        sae = make_npc(id="npc2", name="Greasy Sae")
        reply = await dialogue.generate_npc_to_npc_reply(
            npc=ferro,
            other_npc=sae,
            district=make_district(),
            location=make_district().locations[-1],
            message="(( You spot each other and strike up a conversation. ))",
            settings=make_settings(dialogue_provider="template"),
        )
        assert "Old Ferro" in reply

    async def test_llm_failure_falls_back_to_the_template(self, monkeypatch):
        async def failing_llm(ctx, message, settings, *, history=()):
            raise httpx.ConnectError("no route to host")

        monkeypatch.setattr(dialogue, "generate_llm_reply", failing_llm)
        ferro = make_npc()
        sae = make_npc(id="npc2", name="Greasy Sae")
        reply = await dialogue.generate_npc_to_npc_reply(
            npc=ferro,
            other_npc=sae,
            district=make_district(),
            location=make_district().locations[-1],
            message="(( opener ))",
            settings=make_settings(dialogue_provider="llm"),
        )
        assert reply == dialogue.template_reply(ferro, "neutral", "(( opener ))")
