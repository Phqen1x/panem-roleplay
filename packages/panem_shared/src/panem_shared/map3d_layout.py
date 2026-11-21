"""Generates a dense, grid-based 3D city for a district -- built from the
vendored City Kit - Commercial/Roads assets (`static/assets3d/city/`,
`.../roads/`). A district's own `DistrictMap.location_coords` (the same
coordinates the 2D schematic `static/tabs/map.js` canvas has always drawn
circles from) still decides *where in the city* each real location ends
up, but the city itself is a proper Manhattan grid of building plots and
streets, not just a building dropped at each location's raw coordinate --
a handful of named locations scattered across empty ground read as "a
circle of buildings," not a city, so every other plot in the grid is
filled with a deterministically-picked building (or left as a small open
gap) purely for density and skyline variety.

Deliberately generic, not Capitol-specific -- see this module's own
history (`git log`) for why; only district 0 is wired up to a 3D view
today (`static/tabs/map.js`'s district check), but this generator works
for any district the moment the frontend is told to render one.

No DB/content-loading here -- takes an already-loaded `District` and
returns a plain, hashable, deterministic result. Determinism matters: two
requests for the same district must produce the exact same layout (same
building picks, same road tiles) so a reload doesn't shuffle the city
around under a player mid-walk.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from panem_shared.content.schemas import District
from panem_shared.enums import LocationKind

WORLD_SCALE = 0.05
"""Content map coordinates run up to ~1600x1000 (`DistrictMap.width`/
`.height`); this compresses that into the same coordinate space the city
grid (`BLOCK_SPACING`-spaced plots) is laid out in before each named
location gets snapped to its nearest plot. Cosmetic tuning constant, not
a correctness one."""

GRID_RADIUS = 3
"""The city grid spans `-GRID_RADIUS..GRID_RADIUS` in both directions --
`GRID_RADIUS=3` is a 7x7 plot grid (49 plots), big enough to read as a
real city block-by-block without asking the client to load an unreasonable
number of models."""

BLOCK_SPACING = 9.0
"""World units between adjacent building plot centers -- generous enough
that a skyscraper-sized building and its street frontage both fit inside
one plot without the model from the next plot over visibly clipping into
it (there's no bounding-box data for the vendored models to size this
from precisely; picked generously and left as a single tuning constant)."""

ROAD_TILE_LENGTH = 2.0
"""The straight road piece's approximate footprint along its own length,
in world units -- how far apart consecutive tiles are placed along a
street. Matches the roughly 2m grid Kenney's City Kit packs are built on."""

EMPTY_PLOT_CHANCE = 0.15
"""Fraction of non-hub, non-named plots left as open ground rather than
given a filler building -- a city that's 100% wall-to-wall buildings reads
as a maze, not a place; a few gaps read as plazas/lots."""

_BUILDING_MODELS_BY_KIND: dict[LocationKind, tuple[str, ...]] = {
    LocationKind.WORKPLACE: ("building-a", "building-b", "building-c", "building-d"),
    LocationKind.GOVERNMENT: (
        "building-skyscraper-a",
        "building-skyscraper-b",
        "building-skyscraper-c",
        "building-skyscraper-d",
        "building-skyscraper-e",
    ),
    LocationKind.RESIDENTIAL: ("building-e", "building-f", "building-g", "building-h"),
    LocationKind.MARKET: ("building-i", "building-j"),
    LocationKind.STATION: ("building-k",),
    LocationKind.JAIL: ("building-l",),
    LocationKind.OUTSKIRTS: ("building-m", "building-n"),
}
"""`LocationKind.PUBLIC` has no entry on purpose: a public location (e.g.
the Capitol's "Avenue of the Tributes") renders as open plaza with no
building at all -- just a marker, same as every other location, that a
player can walk up to and (later) interact with."""

_FILLER_BUILDING_POOL: tuple[str, ...] = (
    *(f"building-{c}" for c in "abcdefghijklmn"),
    *(f"low-detail-building-{c}" for c in "abcdefghijklmn"),
    "low-detail-building-wide-a",
    "low-detail-building-wide-b",
)
"""The background city -- every plot not assigned to a real location --
draws mostly from `low-detail-building-*` (cheaper-looking filler, meant
for exactly this) with the same full-detail buildings mixed in, plus a
small separate chance of a skyscraper for skyline variety (see
`_filler_model`)."""

_FILLER_SKYSCRAPER_POOL: tuple[str, ...] = tuple(f"building-skyscraper-{c}" for c in "abcde")
_FILLER_SKYSCRAPER_CHANCE = 0.12

_ROTATIONS = (0.0, 90.0, 180.0, 270.0)


def _stable_unit(seed: str) -> float:
    """A deterministic pseudo-random float in [0, 1) from `seed`."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 2**32


def _stable_pick(seed: str, options: tuple[str, ...]) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return options[digest[0] % len(options)]


@dataclass(frozen=True, slots=True)
class CityBuilding:
    location_id: str | None
    """`None` for a filler building/empty plot -- there's no real location
    behind it, so there's nothing for `panem_api.map3d_routes` to attach
    interaction to."""
    name: str | None
    kind: str | None
    model: str | None
    """`None` for a `PUBLIC` location or an empty filler plot -- open
    ground, no mesh."""
    x: float
    z: float
    rotation_deg: float


@dataclass(frozen=True, slots=True)
class RoadTile:
    model: str
    x: float
    z: float
    rotation_deg: float


@dataclass(frozen=True, slots=True)
class CityLayout:
    district_id: int
    scale: float
    hub_location_id: str | None
    spawn_x: float
    spawn_z: float
    buildings: list[CityBuilding] = field(default_factory=list)
    roads: list[RoadTile] = field(default_factory=list)


def _to_world(raw: tuple[int, int], *, center: tuple[float, float]) -> tuple[float, float]:
    return ((raw[0] - center[0]) * WORLD_SCALE, (raw[1] - center[1]) * WORLD_SCALE)


def _facing_degrees(dx: float, dz: float) -> float:
    """Degrees around the vertical (Y) axis a model should be rotated so
    its forward axis points from the origin toward `(dx, dz)` -- Three.js'
    convention of measuring rotation from +Z, matched on the frontend side
    when these are applied."""
    if dx == 0 and dz == 0:
        return 0.0
    return math.degrees(math.atan2(dx, dz))


def _plot_center(col: int, row: int) -> tuple[float, float]:
    return (col * BLOCK_SPACING, row * BLOCK_SPACING)


def _all_plots() -> list[tuple[int, int]]:
    return [
        (col, row)
        for col in range(-GRID_RADIUS, GRID_RADIUS + 1)
        for row in range(-GRID_RADIUS, GRID_RADIUS + 1)
    ]


def _filler_model(col: int, row: int) -> str | None:
    seed = f"filler|{col}|{row}"
    if _stable_unit(f"{seed}|empty") < EMPTY_PLOT_CHANCE:
        return None
    if _stable_unit(f"{seed}|tall") < _FILLER_SKYSCRAPER_CHANCE:
        return _stable_pick(seed, _FILLER_SKYSCRAPER_POOL)
    return _stable_pick(seed, _FILLER_BUILDING_POOL)


def _street_tiles(*, from_xz: tuple[float, float], to_xz: tuple[float, float]) -> list[RoadTile]:
    dx, dz = to_xz[0] - from_xz[0], to_xz[1] - from_xz[1]
    distance = math.hypot(dx, dz)
    if distance <= 0:
        return []
    tile_count = max(1, round(distance / ROAD_TILE_LENGTH))
    rotation = _facing_degrees(dx, dz)
    ux, uz = dx / distance, dz / distance
    tiles = []
    for i in range(tile_count):
        step = (i + 0.5) * (distance / tile_count)
        tiles.append(
            RoadTile(
                model="road-straight",
                x=from_xz[0] + ux * step,
                z=from_xz[1] + uz * step,
                rotation_deg=rotation,
            )
        )
    return tiles


def _build_street_grid() -> list[RoadTile]:
    """A full Manhattan grid: one street running between each pair of
    adjacent plot columns/rows (offset half a plot from the building
    centers, so streets frame each block of plots rather than running
    through their middles), plus a `road-crossroad` tile at every
    intersection."""
    tiles: list[RoadTile] = []
    low = (-GRID_RADIUS - 0.5) * BLOCK_SPACING
    high = (GRID_RADIUS + 0.5) * BLOCK_SPACING
    street_offsets = [(c + 0.5) * BLOCK_SPACING for c in range(-GRID_RADIUS, GRID_RADIUS)]

    for offset in street_offsets:
        # Vertical street at x=offset, running the full z span.
        tiles.extend(_street_tiles(from_xz=(offset, low), to_xz=(offset, high)))
        # Horizontal street at z=offset, running the full x span.
        tiles.extend(_street_tiles(from_xz=(low, offset), to_xz=(high, offset)))

    for x_offset in street_offsets:
        for z_offset in street_offsets:
            tiles.append(RoadTile(model="road-crossroad", x=x_offset, z=z_offset, rotation_deg=0.0))

    return tiles


def build_city_layout(district: District) -> CityLayout:
    coords = district.map.location_coords
    center = (district.map.width / 2, district.map.height / 2)
    desired: dict[str, tuple[float, float]] = {
        loc.id: _to_world(coords[loc.id], center=center)
        for loc in district.locations
        if loc.id in coords
    }
    by_id = {loc.id: loc for loc in district.locations}

    all_plots = _all_plots()
    hub_plot = (0, 0)
    claimed: dict[tuple[int, int], str] = {}

    # The hub is whichever located location sits closest to the map's own
    # center -- if that happens to be the grid's own center plot's worth of
    # distance anyway it just naturally lands there; what matters is the
    # center plot itself is *always* reserved as open plaza (no building),
    # so the city always has a walkable town square to spawn players into.
    hub_location_id: str | None = None
    if desired:
        hub_location_id = min(desired, key=lambda loc_id: math.hypot(*desired[loc_id]))
        claimed[hub_plot] = hub_location_id

    remaining_plots = [p for p in all_plots if p != hub_plot]
    remaining_locations = sorted(
        (loc_id for loc_id in desired if loc_id != hub_location_id),
        key=lambda loc_id: min(
            math.hypot(
                desired[loc_id][0] - _plot_center(*p)[0], desired[loc_id][1] - _plot_center(*p)[1]
            )
            for p in remaining_plots
        ),
    )
    for loc_id in remaining_locations:
        best_plot = min(
            remaining_plots,
            key=lambda p: math.hypot(
                desired[loc_id][0] - _plot_center(*p)[0], desired[loc_id][1] - _plot_center(*p)[1]
            ),
        )
        claimed[best_plot] = loc_id
        remaining_plots.remove(best_plot)

    hub_xz = _plot_center(*hub_plot)
    buildings: list[CityBuilding] = []
    for plot in all_plots:
        x, z = _plot_center(*plot)
        claimed_loc_id = claimed.get(plot)
        if claimed_loc_id is not None:
            loc = by_id[claimed_loc_id]
            rotation = 0.0 if plot == hub_plot else _facing_degrees(hub_xz[0] - x, hub_xz[1] - z)
            models = _BUILDING_MODELS_BY_KIND.get(loc.kind)
            model = _stable_pick(claimed_loc_id, models) if models else None
            buildings.append(
                CityBuilding(
                    location_id=claimed_loc_id,
                    name=loc.name,
                    kind=loc.kind.value,
                    model=model,
                    x=x,
                    z=z,
                    rotation_deg=rotation,
                )
            )
            continue
        if plot == hub_plot:
            continue  # reserved plaza, no filler
        model = _filler_model(*plot)
        if model is None:
            continue  # an open gap -- deliberately no building here
        rotation_index = int(_stable_unit(f"filler-rot|{plot[0]}|{plot[1]}") * len(_ROTATIONS))
        buildings.append(
            CityBuilding(
                location_id=None,
                name=None,
                kind=None,
                model=model,
                x=x,
                z=z,
                rotation_deg=_ROTATIONS[min(rotation_index, len(_ROTATIONS) - 1)],
            )
        )

    return CityLayout(
        district_id=district.id,
        scale=WORLD_SCALE,
        hub_location_id=hub_location_id,
        spawn_x=hub_xz[0],
        spawn_z=hub_xz[1],
        buildings=buildings,
        roads=_build_street_grid(),
    )


def npc_scatter_offset(npc_id: str, *, radius: float = 2.5) -> tuple[float, float]:
    """A small, deterministic offset for an NPC's home position -- many
    NPCs in the same district share a `home_location_id` (e.g. most of the
    Capitol's residents live at "mansions"), and without this every one of
    them renders stacked exactly on top of each other at that single
    building's coordinates. Spreads them in a stable ring around their
    actual home instead, close enough to still read as "lives/works
    here," never overlapping by more than floating point coincidence."""
    angle = _stable_unit(f"{npc_id}|scatter-angle") * 2 * math.pi
    distance = radius * (0.3 + 0.7 * _stable_unit(f"{npc_id}|scatter-radius"))
    return (math.cos(angle) * distance, math.sin(angle) * distance)
