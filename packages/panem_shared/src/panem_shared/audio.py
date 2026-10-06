"""Speech for the bot: Whisper speech-to-text and Kokoro text-to-speech,
both through Lemonade's OpenAI-compatible audio endpoints.

* `transcribe` -- a Discord voice message in a scene becomes text, which the
  proxy then treats exactly like something the player typed (the NPCs reply
  to it, the hostile-action and RP-credit checks read it).
* `synthesize` -- an NPC's reply becomes audio, in a voice that stays the
  same for that NPC every time (`voice_for_npc`).

Both models are called *directly by name* (not routed through the omni
collection's planner): the planner only ever calls tools for the request
modes that ask for them, and routing speech through it would add a model
round-trip and a failure mode for no gain. Kokoro is therefore not a
component of the collection at all (`omni.TTS_MODEL`) -- a bad Kokoro
download costs the audio and nothing else.

Pure helpers (`spoken_text`, `voice_for_npc`) are separate from the two HTTP
calls so they can be tested without a server. Every failure surfaces as
`AudioError`; callers degrade (a notice for STT, silence for TTS) and never
let it take down a reply.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil

import httpx
import structlog

from panem_shared.lemonade import omni
from panem_shared.settings import Settings

logger = structlog.get_logger()

# Discord caps non-boosted uploads at 25 MB; a voice message is far smaller,
# this just refuses to ship a pathological file to the model.
MAX_AUDIO_BYTES = 25 * 1024 * 1024


class AudioError(Exception):
    """The speech server errored, timed out, or returned nothing usable."""


# --------------------------------------------------------------------------- #
# Speech-to-text
# --------------------------------------------------------------------------- #


def resolve_stt_model(settings: Settings) -> str:
    if settings.stt_model:
        return settings.stt_model
    profile = omni.PROFILES.get(settings.lemonade_profile) or omni.PROFILES[omni.DEFAULT_PROFILE]
    return profile.transcription_model


def _headers(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}


async def _to_wav(audio: bytes) -> bytes | None:
    """16 kHz mono WAV via `ffmpeg`, for the whisper.cpp builds that only
    accept WAV. `None` when ffmpeg isn't installed or fails -- the caller
    then reports the original server error, so this is strictly a retry aid."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-loglevel", "error", "-i", "pipe:0", "-ar", "16000", "-ac", "1", "-f", "wav",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    out, _ = await proc.communicate(audio)
    return out if proc.returncode == 0 and out else None


async def _post_transcription(
    client: httpx.AsyncClient, url: str, settings: Settings, audio: bytes, filename: str, mime: str
) -> str:
    response = await client.post(
        url,
        headers=_headers(settings),
        data={"model": resolve_stt_model(settings), "response_format": "json"},
        files={"file": (filename, audio, mime)},
    )
    response.raise_for_status()
    return str(response.json().get("text", "")).strip()


async def transcribe(
    audio: bytes,
    settings: Settings,
    *,
    filename: str = "voice-message.ogg",
    mime: str = "audio/ogg",
) -> str:
    """The words spoken in `audio`. Raises `AudioError` on any failure and
    when nothing intelligible comes back (an empty transcript is a failure:
    there is nothing for the NPC to reply to)."""
    if not audio:
        raise AudioError("empty audio")
    if len(audio) > MAX_AUDIO_BYTES:
        raise AudioError("audio too large")
    url = f"{(settings.llm_base_url or omni.DEFAULT_BASE_URL).rstrip('/')}/audio/transcriptions"
    try:
        async with httpx.AsyncClient(timeout=settings.stt_timeout_ms / 1000) as client:
            try:
                text = await _post_transcription(client, url, settings, audio, filename, mime)
            except httpx.HTTPStatusError:
                # Some whisper.cpp builds reject Opus/OGG outright; one retry
                # as WAV (when ffmpeg is around) before giving up.
                wav = await _to_wav(audio)
                if wav is None:
                    raise
                text = await _post_transcription(
                    client, url, settings, wav, "voice-message.wav", "audio/wav"
                )
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise AudioError(str(exc) or type(exc).__name__) from exc
    # Whisper emits bracketed sound tags for non-speech ("[BLANK_AUDIO]",
    # "(music)"); a transcript that is only those is silence.
    if not re.sub(r"[\[\(][^\]\)]*[\]\)]", "", text).strip():
        raise AudioError("no speech detected")
    return text


# --------------------------------------------------------------------------- #
# Text-to-speech
# --------------------------------------------------------------------------- #

# Kokoro's bundled voices. The prefix is language+gender: `a`/`b` =
# American/British English, `f`/`m` = female/male.
_FEMALE_US = ("af_bella", "af_nicole", "af_sarah", "af_sky", "af_heart", "af_nova", "af_alloy")
_MALE_US = ("am_adam", "am_michael", "am_eric", "am_liam", "am_onyx", "am_fenrir")
_FEMALE_UK = ("bf_emma", "bf_isabella", "bf_alice", "bf_lily")
_MALE_UK = ("bm_george", "bm_lewis", "bm_daniel", "bm_fable")

_ASTERISK_ACTION = re.compile(r"\*[^*]*\*")
_STAGE_DIRECTION = re.compile(r"\(\([^)]*\)\)")
_MARKDOWN_NOISE = re.compile(r"[_`~#>|]+")
_WHITESPACE = re.compile(r"\s+")


def voice_for_npc(npc_id: str, gender: str | None, *, capitol: bool = False) -> str:
    """A voice that is stable for one NPC across every reply and restart
    (SHA-256, not `hash()`, which is salted per process): gender picks the
    pool, the Capitol gets the British voices for its affected accent, and
    the NPC's id picks within the pool. Unset/non-binary gender draws from
    both pools so those NPCs aren't all pushed into one register."""
    if gender == "female":
        pool: tuple[str, ...] = _FEMALE_UK if capitol else _FEMALE_US
    elif gender == "male":
        pool = _MALE_UK if capitol else _MALE_US
    else:
        pool = (_FEMALE_UK + _MALE_UK) if capitol else (_FEMALE_US + _MALE_US)
    digest = hashlib.sha256(npc_id.encode("utf-8")).digest()
    return pool[int.from_bytes(digest[:4], "big") % len(pool)]


def spoken_text(reply: str, *, max_chars: int = 500) -> str:
    """Just the words an NPC says aloud. Replies mix spoken lines with
    `*asterisk actions*` (`lemonade/system_prompt.md`'s voicing rules) and
    occasional markdown; a speech engine reading "asterisk nods asterisk"
    would be absurd, so actions, stage directions and markup are dropped.
    Empty when the reply was all action. Truncated at a sentence boundary
    when it runs past `max_chars`, so a long reply doesn't become a
    minute-long audio file."""
    text = _STAGE_DIRECTION.sub(" ", reply)
    text = _ASTERISK_ACTION.sub(" ", text)
    text = text.replace("*", " ")
    text = _MARKDOWN_NOISE.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip(" \"'“”")
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars]
    cut = max(clipped.rfind(". "), clipped.rfind("! "), clipped.rfind("? "))
    return clipped[: cut + 1] if cut > max_chars // 3 else clipped.rstrip() + "…"


async def synthesize(text: str, voice: str, settings: Settings) -> bytes:
    """Audio of `text` in Kokoro voice `voice`, in `settings.tts_format`."""
    if not text.strip():
        raise AudioError("nothing to say")
    url = f"{(settings.llm_base_url or omni.DEFAULT_BASE_URL).rstrip('/')}/audio/speech"
    body = {
        "model": settings.tts_model or omni.TTS_MODEL,
        "input": text,
        "voice": voice,
        "response_format": settings.tts_format,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.tts_timeout_ms / 1000) as client:
            response = await client.post(url, json=body, headers=_headers(settings))
            response.raise_for_status()
            audio = response.content
    except httpx.HTTPError as exc:
        raise AudioError(str(exc) or type(exc).__name__) from exc
    if not audio:
        raise AudioError("empty audio returned")
    return audio
