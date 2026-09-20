"""Picrew-style layered character portrait: staff-authored "categories" (a
stack position + a name, e.g. "Hair") each holding staff-uploaded image
"options" a player picks from. This replaces the old fixed-palette trait
system (`panem_shared.appearance`, now removed) -- instead of a hardcoded
enum per field rendered by original-art code, the whole palette is real
artwork uploaded through the dashboard after the fact. There is
deliberately no seed data: every category/option is created by staff via
`panem_api.dashboard_routes.build_layers_router`'s admin endpoints, so the
catalog starts empty and every reader here (the player-facing picker
included) has to render sensibly on zero rows, not just a populated one.

Discord-independent (same reasoning as `panem_shared.jail`/`.stealing`/
`.characters`): this is purely a web-dashboard feature (there's no Discord
slash-command equivalent -- picking from staff-uploaded artwork doesn't
map onto a modal/select the way free text or a fixed enum did), but lives
here rather than only in `panem_api` on the chance panem_bot ever wants to
read it (e.g. to show a character's portrait somewhere), and to match
every other service module's package placement.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from panem_shared.db.models import LayerCategory, LayerOption
from panem_shared.errors import NotFound, ValidationFailed

CATEGORY_NAME_MAX_LEN = 64
OPTION_NAME_MAX_LEN = 64

# Generous for a single character-portrait layer image, not for arbitrary
# uploads -- these are meant to be small transparent sprites, not photos.
MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Only formats a browser can composite as a transparent <img> layer without
# surprises. No SVG (script/XSS surface if ever served with the wrong
# content-type) and no JPEG (no alpha channel, so it'd always paint an
# opaque rectangle over whatever's beneath it).
ALLOWED_IMAGE_CONTENT_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

UPLOAD_SUBDIR = "uploads/layers"


async def list_categories(session: AsyncSession) -> list[LayerCategory]:
    """Every category with its options, in render order (lower `z_index`
    first, i.e. further back) -- the single source of truth for both the
    player-facing picker and the staff admin panel."""
    result = await session.execute(
        select(LayerCategory)
        .options(selectinload(LayerCategory.options))
        .order_by(LayerCategory.z_index, LayerCategory.id)
    )
    return list(result.scalars().all())


def _clean_name(name: str, *, max_len: int, reason_key: str) -> str:
    name = name.strip()
    if not name or len(name) > max_len:
        raise ValidationFailed(reason_key)
    return name


async def create_category(session: AsyncSession, *, name: str, z_index: int) -> LayerCategory:
    category = LayerCategory(
        name=_clean_name(name, max_len=CATEGORY_NAME_MAX_LEN, reason_key="invalid_layer_category"),
        z_index=z_index,
    )
    session.add(category)
    await session.flush()
    return category


async def update_category(
    session: AsyncSession,
    category_id: int,
    *,
    name: str | None = None,
    z_index: int | None = None,
) -> LayerCategory:
    category = await session.get(LayerCategory, category_id)
    if category is None:
        raise NotFound("layer_category_not_found")
    if name is not None:
        category.name = _clean_name(
            name, max_len=CATEGORY_NAME_MAX_LEN, reason_key="invalid_layer_category"
        )
    if z_index is not None:
        category.z_index = z_index
    return category


def _image_file(static_dir: Path, image_path: str) -> Path:
    """Resolves a stored `image_path` ("uploads/layers/<uuid>.png") to a
    real file under `static_dir`, refusing to resolve outside the upload
    subdirectory. `image_path` is always our own `uuid4()`-generated name
    (never taken from user input), but this costs nothing and means a bug
    here fails closed instead of unlinking an arbitrary path."""
    upload_dir = (static_dir / UPLOAD_SUBDIR).resolve()
    target = (static_dir / image_path).resolve()
    if upload_dir not in target.parents:
        raise ValidationFailed("invalid_layer_image")
    return target


async def delete_category(session: AsyncSession, category_id: int, *, static_dir: Path) -> None:
    category = await session.get(
        LayerCategory, category_id, options=[selectinload(LayerCategory.options)]
    )
    if category is None:
        raise NotFound("layer_category_not_found")
    for option in category.options:
        _image_file(static_dir, option.image_path).unlink(missing_ok=True)
    await session.delete(category)  # cascades to options (relationship cascade)


async def create_option(
    session: AsyncSession,
    *,
    category_id: int,
    name: str,
    content_type: str,
    data: bytes,
    static_dir: Path,
) -> LayerOption:
    """Saves the uploaded image under `static_dir/uploads/layers/` (created
    if needed, served by the same static mount as everything else under
    `static/`) and records it. `static_dir` is `panem_api`'s own `static/`
    directory, passed in rather than imported -- this module has no
    business knowing panem_api's directory layout, only where to put
    things relative to it."""
    category = await session.get(LayerCategory, category_id)
    if category is None:
        raise NotFound("layer_category_not_found")
    name = _clean_name(name, max_len=OPTION_NAME_MAX_LEN, reason_key="invalid_layer_option")
    if not data:
        raise ValidationFailed("invalid_layer_image")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValidationFailed("layer_image_too_large")
    ext = ALLOWED_IMAGE_CONTENT_TYPES.get(content_type)
    if ext is None:
        raise ValidationFailed("invalid_layer_image")

    upload_dir = static_dir / UPLOAD_SUBDIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{ext}"
    (upload_dir / filename).write_bytes(data)

    option = LayerOption(
        category_id=category_id, name=name, image_path=f"{UPLOAD_SUBDIR}/{filename}"
    )
    session.add(option)
    await session.flush()
    return option


async def delete_option(session: AsyncSession, option_id: int, *, static_dir: Path) -> None:
    option = await session.get(LayerOption, option_id)
    if option is None:
        raise NotFound("layer_option_not_found")
    _image_file(static_dir, option.image_path).unlink(missing_ok=True)
    await session.delete(option)


async def validate_layer_selection(
    session: AsyncSession, selection: dict[str, int]
) -> dict[str, int]:
    """Raises `ValidationFailed` if `selection` isn't a `{category_id:
    option_id}` mapping, or if any entry names a category/option pair that
    doesn't actually exist -- catches a broken or tampered client rather
    than silently accepting garbage. An option a character already has
    selected being deleted *afterward* (staff cleaning up old art) is a
    different, expected case, handled by the frontend at render time
    (skip a selection with no matching option) rather than here."""
    if not isinstance(selection, dict):
        raise ValidationFailed("invalid_appearance_layers")

    result: dict[str, int] = {}
    for category_id_raw, option_id_raw in selection.items():
        try:
            category_id = int(category_id_raw)
            option_id = int(option_id_raw)
        except (TypeError, ValueError) as exc:
            raise ValidationFailed("invalid_appearance_layers") from exc
        option = await session.get(LayerOption, option_id)
        if option is None or option.category_id != category_id:
            raise ValidationFailed("invalid_appearance_layers")
        result[str(category_id)] = option_id
    return result
