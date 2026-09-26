"""Character/NPC avatar image uploads -- persists an uploaded image's bytes
to disk instead of only storing a URL. Mirrors `panem_shared.layers`'s
`create_option` blueprint (validate content type/size, write under
`static_dir`, record a path) almost exactly, with one difference: an
avatar's stored value has to be an absolute, publicly reachable URL
(Discord's webhook `avatar_url`/embed image fields, and `Character.
avatar_url`/`Npc.avatar_url` generally, always have been a plain URL
string, fetched directly by Discord's own CDN) rather than a path relative
to `panem_api`'s own static mount the way a layer image is -- so
`save_avatar_image` only returns the path relative to `static_dir`, and
the caller (which knows the public base URL) builds the absolute one.

This exists because the only "upload" path before it (`/character
avatar`'s `image:` attachment option) just took the Discord CDN's own URL
for the uploaded attachment, which Discord signs with a ~24h expiry
regardless of which message holds it -- there's no way to host a
permanent link through Discord itself. Saving the bytes here instead
gives a URL that keeps working indefinitely, the same way a real
externally-hosted image URL always did.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from panem_shared.errors import ValidationFailed

# Generous for a single avatar image, not for arbitrary uploads.
MAX_AVATAR_BYTES = 5 * 1024 * 1024

# Matches the extension set `/character avatar`'s own URL validation
# (`characters.validate_avatar_url`) already accepts.
ALLOWED_AVATAR_CONTENT_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

UPLOAD_SUBDIR = "uploads/avatars"


def save_avatar_image(*, content_type: str, data: bytes, static_dir: Path) -> str:
    """Validates and writes the image under `static_dir/uploads/avatars/`
    (created if needed), returning its path relative to `static_dir`
    ("uploads/avatars/<uuid>.png")."""
    if not data:
        raise ValidationFailed("invalid_avatar_image")
    if len(data) > MAX_AVATAR_BYTES:
        raise ValidationFailed("avatar_image_too_large")
    ext = ALLOWED_AVATAR_CONTENT_TYPES.get(content_type)
    if ext is None:
        raise ValidationFailed("invalid_avatar_image")

    upload_dir = static_dir / UPLOAD_SUBDIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{ext}"
    (upload_dir / filename).write_bytes(data)
    return f"{UPLOAD_SUBDIR}/{filename}"
