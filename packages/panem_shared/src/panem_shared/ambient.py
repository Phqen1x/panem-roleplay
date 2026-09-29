"""Activity ambient music management (`panem_shared.ambient`).

Staff can upload audio tracks (MP3, OGG, WAV, M4A, FLAC, WebM) via Discord bot
(`/staff music upload`) or the web Activity Staff tab. Tracks can be scoped:
  - "global": Default fallback everywhere
  - "district": Plays in a specific district
  - "location": Plays in a specific in-game location (e.g. "outskirts", "hob", "square")
  - "channel": Plays when connected via a specific Discord channel ID
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared.db.models import AmbientTrack
from panem_shared.errors import NotFound, ValidationFailed

TITLE_MAX_LEN = 128
MAX_AUDIO_BYTES = 50 * 1024 * 1024  # 50 MB cap

ALLOWED_AUDIO_EXTENSIONS: set[str] = {
    ".mp3",
    ".ogg",
    ".wav",
    ".m4a",
    ".flac",
    ".webm",
}

ALLOWED_SCOPES: set[str] = {"global", "location", "channel", "district"}

UPLOAD_SUBDIR = "uploads/ambient"


async def list_ambient_tracks(session: AsyncSession) -> list[AmbientTrack]:
    """Every uploaded ambient music track, newest first."""
    result = await session.execute(
        select(AmbientTrack).order_by(AmbientTrack.id.desc())
    )
    return list(result.scalars().all())


async def get_ambient_track(session: AsyncSession, track_id: int) -> AmbientTrack:
    track = await session.get(AmbientTrack, track_id)
    if track is None:
        raise NotFound("ambient_track_not_found")
    return track


async def get_matching_ambient_tracks(
    session: AsyncSession,
    *,
    district_id: int | None = None,
    location_id: str | None = None,
    channel_id: str | None = None,
) -> list[AmbientTrack]:
    """Fetch ambient tracks relevant to the given context, ordered by specificity."""
    all_tracks = await list_ambient_tracks(session)
    matching: list[AmbientTrack] = []

    # 1. Location match
    if location_id:
        for t in all_tracks:
            if t.scope == "location" and t.location_id == location_id:
                if t.district_id is None or t.district_id == district_id:
                    matching.append(t)

    # 2. Channel match
    if channel_id:
        for t in all_tracks:
            if t.scope == "channel" and t.channel_id == str(channel_id):
                matching.append(t)

    # 3. District match
    if district_id is not None:
        for t in all_tracks:
            if t.scope == "district" and t.district_id == district_id:
                matching.append(t)

    # 4. Global fallback
    for t in all_tracks:
        if t.scope == "global":
            matching.append(t)

    return matching


async def create_ambient_track(
    session: AsyncSession,
    *,
    title: str,
    file_path: str,
    scope: str = "global",
    district_id: int | None = None,
    location_id: str | None = None,
    channel_id: str | None = None,
    created_by_staff_discord_id: int,
) -> AmbientTrack:
    title_clean = title.strip()
    if not title_clean or len(title_clean) > TITLE_MAX_LEN:
        raise ValidationFailed("ambient_title_invalid")
    if scope not in ALLOWED_SCOPES:
        raise ValidationFailed("ambient_scope_invalid")

    track = AmbientTrack(
        title=title_clean,
        file_path=file_path,
        scope=scope,
        district_id=district_id,
        location_id=location_id.strip() if location_id else None,
        channel_id=str(channel_id).strip() if channel_id else None,
        created_by_staff_discord_id=created_by_staff_discord_id,
    )
    session.add(track)
    await session.flush()
    return track


async def delete_ambient_track(session: AsyncSession, track_id: int) -> None:
    track = await get_ambient_track(session, track_id)
    await session.delete(track)


def save_ambient_audio_bytes(file_bytes: bytes, filename: str, uploads_root: Path) -> str:
    """Writes `file_bytes` into `uploads_root / "uploads/ambient/<uuid>_<safe_name>"`."""
    if len(file_bytes) > MAX_AUDIO_BYTES:
        raise ValidationFailed("ambient_file_too_large")

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        ext = ".mp3"

    safe_stem = "".join(c for c in Path(filename).stem if c.isalnum() or c in ("-", "_"))[:32] or "track"
    unique_name = f"{uuid4().hex}_{safe_stem}{ext}"

    dest_dir = uploads_root / UPLOAD_SUBDIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / unique_name
    dest_path.write_bytes(file_bytes)

    return f"{UPLOAD_SUBDIR}/{unique_name}"
