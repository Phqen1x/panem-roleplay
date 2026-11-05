"""Turns a district's existing 2D map data (`DistrictMap.location_coords`,
same coordinates the schematic `static/tabs/map.js` canvas has always drawn
circles from) into a 3D city layout: one building per location plus a
spoke-and-hub road network, built from the vendored City Kit - Commercial/
Roads assets (`static/assets3d/city/`, `.../roads/`).

Deliberately generic, not Capitol-specific -- the feature this ships for
only wires district 0 up to a 3D view (`static/tabs/map.js`'s district
check), but the request that asked for it ("we'll do this sort of thing
for each district later") means the generator itself shouldn't assume
anything about *which* district it's given. Every other district still
gets the plain 2D schematic for now; this function works for any of them
the moment the frontend is told to render one in 3D.

No DB/content-loading here -- takes an already-loaded `District` and
returns a plain, hashable, deterministic result. Determinism matters: two
requests for the same district must produce the exact same layout (same
building model choices, same road tiles) so a reload doesn't shuffle the
city around under a player mid-walk.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from panem_shared.content.schemas import District
from panem_shared.enums import LocationKind

WORLD_SCALE = 0.05
"""Content map coordinates run up to ~1600x1000 (`DistrictMap.width`/
`.height`); this compresses that into a walkable ~80x50 world-unit scene.
Chosen to roughly match the vendored City Kit tiles' own ~2-unit grid once
scaled, not derived from anything -- a cosmetic tuning constant, not a
correctness one."""

ROAD_TILE_LENGTH = 2.0
"""The straight road piece's approximate footprint along its own length,
in world units -- how far apart consecutive tiles are placed along a
spoke. Matches the roughly 2m grid Kenney's City Kit packs are built on."""

BUILDING_RADIUS = 3.0
"""How close a spoke road is allowed to approach a building's own center
before it's considered "arrived" and stops laying tiles -- keeps the road
from driving straight into the building model."""

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


def _stable_pick(seed: str, options: tuple[str, ...]) -> str:
    """Deterministically picks one of `options` from `seed` -- same seed,
    same pick, every time and on every process, unlike `random.choice`
    (which would need a seeded `Random` threaded through everywhere to get
    the same guarantee)."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return options[digest[0] % len(options)]


@dataclass(frozen=True, slots=True)
class CityBuilding:
    location_id: str
    name: str
    kind: str
    model: str | None
    """`None` for a `PUBLIC` location -- an open plaza marker, no mesh."""
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
    return (
        (raw[0] - center[0]) * WORLD_SCALE,
        (raw[1] - center[1]) * WORLD_SCALE,
    )


def _facing_degrees(dx: float, dz: float) -> float:
    """Degrees around the vertical (Y) axis a model should be rotated so
    its forward axis points from the origin toward `(dx, dz)` -- Three.js'
    convention of measuring rotation from +Z, matched on the frontend side
    when these are applied."""
    return math.degrees(math.atan2(dx, dz))


def _spoke_roads(*, from_xz: tuple[float, float], to_xz: tuple[float, float]) -> list[RoadTile]:
    dx, dz = to_xz[0] - from_xz[0], to_xz[1] - from_xz[1]
    distance = math.hypot(dx, dz)
    usable = max(distance - BUILDING_RADIUS, 0.0)
    if usable <= 0:
        return []
    tile_count = max(1, round(usable / ROAD_TILE_LENGTH))
    rotation = _facing_degrees(dx, dz)
    ux, uz = dx / distance, dz / distance
    tiles = []
    for i in range(tile_count):
        step = (i + 0.5) * (usable / tile_count)
        tiles.append(
            RoadTile(
                model="road-straight",
                x=from_xz[0] + ux * step,
                z=from_xz[1] + uz * step,
                rotation_deg=rotation,
            )
        )
    return tiles


def build_city_layout(district: District) -> CityLayout:
    coords = district.map.location_coords
    center = (district.map.width / 2, district.map.height / 2)
    placed: dict[str, tuple[float, float]] = {
        loc.id: _to_world(coords[loc.id], center=center)
        for loc in district.locations
        if loc.id in coords
    }
    if not placed:
        return CityLayout(
            district_id=district.id,
            scale=WORLD_SCALE,
            hub_location_id=None,
            spawn_x=0.0,
            spawn_z=0.0,
        )

    # The hub is whichever placed location sits closest to the map's own
    # center -- generically finding the "town square" rather than assuming
    # any particular location id is always it (today, for the Capitol,
    # this lands on "Avenue of the Tributes", which the content data
    # already places exactly at its map's center).
    hub_id = min(placed, key=lambda loc_id: math.hypot(*placed[loc_id]))

    buildings: list[CityBuilding] = []
    roads: list[RoadTile] = []
    hub_xz = placed[hub_id]
    for loc in district.locations:
        if loc.id not in placed:
            continue
        x, z = placed[loc.id]
        rotation = 0.0 if loc.id == hub_id else _facing_degrees(hub_xz[0] - x, hub_xz[1] - z)
        models = _BUILDING_MODELS_BY_KIND.get(loc.kind)
        model = _stable_pick(loc.id, models) if models else None
        buildings.append(
            CityBuilding(
                location_id=loc.id,
                name=loc.name,
                kind=loc.kind.value,
                model=model,
                x=x,
                z=z,
                rotation_deg=rotation,
            )
        )
        if loc.id != hub_id:
            roads.extend(_spoke_roads(from_xz=hub_xz, to_xz=(x, z)))

    return CityLayout(
        district_id=district.id,
        scale=WORLD_SCALE,
        hub_location_id=hub_id,
        spawn_x=hub_xz[0],
        spawn_z=hub_xz[1],
        buildings=buildings,
        roads=roads,
    )
