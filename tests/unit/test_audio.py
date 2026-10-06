from __future__ import annotations

import json

import httpx
import pytest

from panem_bot.services import voice
from panem_shared import audio
from panem_shared.lemonade import omni
from panem_shared.settings import Settings


def make_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(llm_base_url="http://lemonade.local/v1")
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[arg-type]


def route(monkeypatch, handler):
    real = httpx.AsyncClient

    def mock_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(audio.httpx, "AsyncClient", mock_client)


class TestSttModel:
    def test_profile_default_and_override(self):
        assert audio.resolve_stt_model(make_settings(lemonade_profile="lite")) == "Whisper-Base"
        assert (
            audio.resolve_stt_model(make_settings(lemonade_profile="halo"))
            == "Whisper-Large-v3-Turbo"
        )
        assert audio.resolve_stt_model(make_settings(stt_model="custom")) == "custom"

    def test_every_profile_names_components_it_actually_ships(self):
        for profile in omni.PROFILES.values():
            assert profile.transcription_model in profile.components
            assert profile.embedding_model in profile.components


class TestTranscribe:
    async def test_posts_multipart_and_returns_the_text(self, monkeypatch):
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["ctype"] = request.headers["content-type"]
            seen["body"] = request.content
            return httpx.Response(200, json={"text": "  Evening, Ferro.  "})

        route(monkeypatch, handler)
        text = await audio.transcribe(b"OggSfake", make_settings(llm_api_key="k"))

        assert text == "Evening, Ferro."
        assert seen["url"] == "http://lemonade.local/v1/audio/transcriptions"
        assert seen["auth"] == "Bearer k"
        assert str(seen["ctype"]).startswith("multipart/form-data")
        body = seen["body"]
        assert b"Whisper-Base" in body  # type: ignore[operator]
        assert b"OggSfake" in body  # type: ignore[operator]

    @pytest.mark.parametrize("text", ["", "   ", "[BLANK_AUDIO]", "(music) [silence]"])
    async def test_silence_is_an_error(self, monkeypatch, text):
        route(monkeypatch, lambda r: httpx.Response(200, json={"text": text}))
        with pytest.raises(audio.AudioError):
            await audio.transcribe(b"x", make_settings())

    async def test_server_error_is_an_audio_error(self, monkeypatch):
        monkeypatch.setattr(audio.shutil, "which", lambda name: None)
        route(monkeypatch, lambda r: httpx.Response(500, text="model load failed"))
        with pytest.raises(audio.AudioError):
            await audio.transcribe(b"x", make_settings())

    async def test_retries_as_wav_when_the_server_rejects_ogg(self, monkeypatch):
        calls: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.content)
            if b"RIFFwav" in request.content:
                return httpx.Response(200, json={"text": "hello there"})
            return httpx.Response(400, text="unsupported format")

        async def fake_wav(data: bytes) -> bytes:
            return b"RIFFwav"

        route(monkeypatch, handler)
        monkeypatch.setattr(audio, "_to_wav", fake_wav)
        assert await audio.transcribe(b"OggSfake", make_settings()) == "hello there"
        assert len(calls) == 2

    async def test_empty_and_oversized_audio_never_reach_the_server(self, monkeypatch):
        route(monkeypatch, lambda r: pytest.fail("should not be called"))
        with pytest.raises(audio.AudioError):
            await audio.transcribe(b"", make_settings())
        with pytest.raises(audio.AudioError):
            await audio.transcribe(b"x" * (audio.MAX_AUDIO_BYTES + 1), make_settings())


class TestSpokenText:
    def test_drops_asterisk_actions_and_keeps_the_words(self):
        assert (
            audio.spoken_text("*Nash straightens his collar.* It is not often we sit like this.")
            == "It is not often we sit like this."
        )

    def test_all_action_reply_is_silent(self):
        assert audio.spoken_text("*shrugs and turns away*") == ""

    def test_strips_stage_directions_and_markdown_and_whitespace(self):
        assert (
            audio.spoken_text("((OOC note)) **Keep** your `voice`   down.")
            == "Keep your voice down."
        )

    def test_long_replies_are_cut_at_a_sentence_boundary(self):
        text = "First thing happens here. " * 40
        out = audio.spoken_text(text, max_chars=100)
        assert len(out) <= 100
        assert out.endswith(".")

    def test_long_unpunctuated_reply_gets_an_ellipsis(self):
        out = audio.spoken_text("word " * 200, max_chars=50)
        assert out.endswith("…")
        assert len(out) <= 51


class TestVoiceForNpc:
    def test_stable_across_calls(self):
        assert audio.voice_for_npc("d7_npc_015", "male") == audio.voice_for_npc(
            "d7_npc_015", "male"
        )

    def test_gender_selects_the_pool(self):
        assert all(audio.voice_for_npc(f"n{i}", "female").startswith("af_") for i in range(20))
        assert all(audio.voice_for_npc(f"n{i}", "male").startswith("am_") for i in range(20))

    def test_capitol_gets_british_voices(self):
        assert audio.voice_for_npc("cap1", "female", capitol=True).startswith("bf_")
        assert audio.voice_for_npc("cap2", "male", capitol=True).startswith("bm_")

    def test_unset_gender_draws_from_both_pools(self):
        prefixes = {audio.voice_for_npc(f"n{i}", None)[:2] for i in range(60)}
        assert prefixes == {"af", "am"}

    def test_different_npcs_do_not_all_share_one_voice(self):
        assert len({audio.voice_for_npc(f"npc{i}", "female") for i in range(30)}) > 1


class TestSynthesize:
    async def test_posts_the_speech_request_and_returns_bytes(self, monkeypatch):
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, content=b"ID3audio")

        route(monkeypatch, handler)
        out = await audio.synthesize("Hello.", "af_sky", make_settings())

        assert out == b"ID3audio"
        assert seen["url"] == "http://lemonade.local/v1/audio/speech"
        assert seen["body"] == {
            "model": "kokoro-v1",
            "input": "Hello.",
            "voice": "af_sky",
            "response_format": "mp3",
        }

    async def test_empty_text_and_empty_response_are_errors(self, monkeypatch):
        with pytest.raises(audio.AudioError):
            await audio.synthesize("   ", "af_sky", make_settings())
        route(monkeypatch, lambda r: httpx.Response(200, content=b""))
        with pytest.raises(audio.AudioError):
            await audio.synthesize("Hi.", "af_sky", make_settings())

    async def test_server_error_is_an_audio_error(self, monkeypatch):
        route(monkeypatch, lambda r: httpx.Response(500))
        with pytest.raises(audio.AudioError):
            await audio.synthesize("Hi.", "af_sky", make_settings())


class FakeAttachment:
    def __init__(
        self, *, voice_msg: bool = True, duration: float | None = 4.0, data: bytes = b"ogg"
    ):
        self.filename = "voice-message.ogg"
        self.content_type = "audio/ogg"
        self.duration = duration
        self._voice = voice_msg
        self._data = data

    def is_voice_message(self) -> bool:
        return self._voice

    async def read(self) -> bytes:
        return self._data


class TestVoiceService:
    def test_finds_only_flagged_voice_messages(self):
        plain = FakeAttachment(voice_msg=False)
        vm = FakeAttachment()
        assert voice.find_voice_message([plain]) is None
        assert voice.find_voice_message([plain, vm]) is vm

    async def test_transcribes_an_attachment(self, monkeypatch):
        route(monkeypatch, lambda r: httpx.Response(200, json={"text": "I have bread."}))
        assert (
            await voice.transcribe_attachment(FakeAttachment(), make_settings()) == "I have bread."
        )

    async def test_too_long_is_refused_before_any_download(self):
        att = FakeAttachment(duration=500)
        att.read = lambda: pytest.fail("must not download")  # type: ignore[assignment]
        with pytest.raises(voice.VoiceTooLong):
            await voice.transcribe_attachment(att, make_settings(stt_max_audio_seconds=120))

    def test_transcript_is_displayed_as_quoted_speech(self):
        assert voice.format_transcript(" Hi there. ") == '🎙️ "Hi there."'

    async def test_speak_reply_returns_audio_and_a_safe_filename(self, monkeypatch):
        route(monkeypatch, lambda r: httpx.Response(200, content=b"mp3bytes"))
        spoken = await voice.speak_reply(
            npc_id="n1",
            npc_name="Old Ferro",
            gender="male",
            district_id=7,
            reply="*nods.* Mind the step.",
            settings=make_settings(),
        )
        assert spoken is not None
        assert spoken.audio == b"mp3bytes"
        assert spoken.filename == "Old-Ferro.mp3"

    async def test_speak_reply_is_none_for_all_action_and_on_failure(self, monkeypatch):
        kwargs = dict(npc_id="n1", npc_name="Ferro", gender=None, district_id=0)
        assert (
            await voice.speak_reply(reply="*shrugs*", settings=make_settings(), **kwargs)  # type: ignore[arg-type]
            is None
        )
        route(monkeypatch, lambda r: httpx.Response(500))
        assert (
            await voice.speak_reply(reply="Hello.", settings=make_settings(), **kwargs)  # type: ignore[arg-type]
            is None
        )


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


class TestSceneVoiceFlag:
    async def test_toggle_round_trip(self):
        redis = FakeRedis()
        assert not await voice.scene_voice_enabled(redis, 42)  # type: ignore[arg-type]
        await voice.set_scene_voice(redis, 42, enabled=True)  # type: ignore[arg-type]
        assert await voice.scene_voice_enabled(redis, 42)  # type: ignore[arg-type]
        assert not await voice.scene_voice_enabled(redis, 43)  # type: ignore[arg-type]
        await voice.set_scene_voice(redis, 42, enabled=False)  # type: ignore[arg-type]
        assert not await voice.scene_voice_enabled(redis, 42)  # type: ignore[arg-type]
