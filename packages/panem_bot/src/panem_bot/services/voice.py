"""Discord-facing glue for `panem_shared.audio`: finding a voice message,
checking it is worth transcribing, and turning an NPC's reply into a
ready-to-attach audio file.

Kept free of any `discord` import (attachments are duck-typed) so it stays
unit-testable like the rest of `panem_bot.services`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from redis.asyncio import Redis

from panem_bot import redis_keys
from panem_shared import audio
from panem_shared.settings import Settings

logger = structlog.get_logger()


class VoiceAttachment(Protocol):
    """The slice of `discord.Attachment` this module reads."""

    filename: str
    content_type: str | None
    duration: float | None

    def is_voice_message(self) -> bool: ...

    async def read(self) -> bytes: ...


class VoiceTooLong(Exception):
    def __init__(self, limit_seconds: int) -> None:
        super().__init__(f"voice message longer than {limit_seconds}s")
        self.limit_seconds = limit_seconds


def find_voice_message(attachments: Iterable[Any]) -> VoiceAttachment | None:
    """The first attachment Discord flagged as a voice message, if any."""
    for attachment in attachments:
        if attachment.is_voice_message():
            return attachment  # type: ignore[no-any-return]
    return None


async def transcribe_attachment(attachment: VoiceAttachment, settings: Settings) -> str:
    """Whisper's transcript of a voice message. Raises `VoiceTooLong` for
    one past `stt_max_audio_seconds` (a minutes-long recording would hold the
    interaction hostage for no RP benefit) and `audio.AudioError` for any
    server failure or silence."""
    if attachment.duration and attachment.duration > settings.stt_max_audio_seconds:
        raise VoiceTooLong(settings.stt_max_audio_seconds)
    data = await attachment.read()
    return await audio.transcribe(
        data,
        settings,
        filename=attachment.filename or "voice-message.ogg",
        mime=attachment.content_type or "audio/ogg",
    )


def format_transcript(transcript: str) -> str:
    """How a transcribed voice message reads when proxied as the character:
    quoted speech with a microphone marker, so it's visibly something said
    aloud (and not an `*action*`, which plain italics would imply)."""
    return f'🎙️ "{transcript.strip()}"'


@dataclass(frozen=True, slots=True)
class SpokenReply:
    audio: bytes
    filename: str


async def scene_voice_enabled(redis_client: Redis, thread_id: int) -> bool:
    return bool(await redis_client.get(redis_keys.scene_voice_key(thread_id)))


async def set_scene_voice(redis_client: Redis, thread_id: int, *, enabled: bool) -> None:
    if enabled:
        await redis_client.set(
            redis_keys.scene_voice_key(thread_id), "1", ex=redis_keys.SCENE_VOICE_TTL_S
        )
    else:
        await redis_client.delete(redis_keys.scene_voice_key(thread_id))


async def speak_reply(
    *,
    npc_id: str,
    npc_name: str,
    gender: str | None,
    district_id: int,
    reply: str,
    settings: Settings,
) -> SpokenReply | None:
    """Kokoro audio of what an NPC just said, or `None` when there is nothing
    to voice (an all-action reply) or the TTS server failed. Never raises:
    a missing voice must never cost the reply its text."""
    text = audio.spoken_text(reply, max_chars=settings.tts_max_chars)
    if not text:
        return None
    voice = audio.voice_for_npc(npc_id, gender, capitol=district_id == 0)
    try:
        data = await audio.synthesize(text, voice, settings)
    except audio.AudioError as exc:
        logger.warning("tts_failed", npc_id=npc_id, voice=voice, error=str(exc))
        return None
    return SpokenReply(audio=data, filename=f"{_safe_name(npc_name)}.{settings.tts_format}")


def _safe_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in name).strip("-")
    return cleaned or "npc"


def participants_of(scene_participants: dict[str, Any]) -> Sequence[int]:
    return [int(c) for c in scene_participants.get("characters", [])]
