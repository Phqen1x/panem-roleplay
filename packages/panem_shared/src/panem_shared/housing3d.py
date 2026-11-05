"""Generates a default interior layout for a `Property` the first time
its 3D interior is viewed (`panem_api.map3d_routes`'s `GET .../housing3d/
{property_id}` -- see `PropertyInteriorItem`'s own docstring for why this
is lazy rather than a purchase-time hook). Built entirely from the
vendored Furniture Kit catalog (`data/furniture.yaml`, `static/assets3d/
furniture/`): a rectangular room shell (walls/floor/one doorway) plus a
starter furniture set, sized by `PropertyKind`/tier so a bigger/pricier
property visibly gets more room and more furniture, then the player
customizes freely from there (`PUT .../housing3d/{property_id}`).
"""

from __future__ import annotations

from dataclasses import dataclass

from panem_shared.enums import OwnerKind, PropertyKind

WALL_SPAN = 2.0
"""World units one `wall`/`floorFull` grid cell covers -- matches the
footprint `data/furniture.yaml` gives those two pieces."""


@dataclass(frozen=True, slots=True)
class PlacedItem:
    item_key: str
    pos_x: float
    pos_z: float
    rotation_y: float = 0.0


def _rectangular_shell(*, width_cells: int, depth_cells: int) -> list[PlacedItem]:
    """A closed rectangle of `wall`/`wallCorner` cells (one grid cell =
    `WALL_SPAN` world units) with a single `wallDoorway` entrance centered
    on the south wall, floored with `floorFull` tiles. `width_cells`/
    `depth_cells` must each be at least 2 (a 1-wide room has no interior
    floor to stand on)."""
    items: list[PlacedItem] = []
    entrance_col = width_cells // 2

    def wall_center(col: float, row: float) -> tuple[float, float]:
        return (
            (col - width_cells / 2 + 0.5) * WALL_SPAN,
            (row - depth_cells / 2 + 0.5) * WALL_SPAN,
        )

    for col in range(width_cells):
        for row in range(depth_cells):
            on_edge = col in (0, width_cells - 1) or row in (0, depth_cells - 1)
            if not on_edge:
                continue
            x, z = wall_center(col, row)
            is_corner = col in (0, width_cells - 1) and row in (0, depth_cells - 1)
            if is_corner:
                rotation = {(0, 0): 0.0, (1, 0): 90.0, (1, 1): 180.0, (0, 1): 270.0}[
                    (0 if col == 0 else 1, 0 if row == 0 else 1)
                ]
                items.append(PlacedItem("wallCorner", x, z, rotation))
                continue
            if row == depth_cells - 1 and col == entrance_col:
                items.append(PlacedItem("wallDoorway", x, z, 0.0))
                continue
            if row == 0:
                items.append(PlacedItem("wall", x, z, 180.0))
            elif row == depth_cells - 1:
                items.append(PlacedItem("wall", x, z, 0.0))
            elif col == 0:
                items.append(PlacedItem("wall", x, z, 90.0))
            else:
                items.append(PlacedItem("wall", x, z, 270.0))

    for col in range(1, width_cells - 1):
        for row in range(1, depth_cells - 1):
            x, z = wall_center(col, row)
            items.append(PlacedItem("floorFull", x, z, 0.0))

    return items


_STARTER_FURNITURE: dict[str, list[PlacedItem]] = {
    PropertyKind.APARTMENT.value: [
        PlacedItem("bedSingle", -1.5, -1.5, 0.0),
        PlacedItem("sideTable", -0.3, -1.5, 0.0),
        PlacedItem("loungeChair", 1.5, 1.0, 180.0),
        PlacedItem("tableCoffee", 1.5, -0.2, 0.0),
        PlacedItem("kitchenCabinet", -1.5, 1.5, 90.0),
        PlacedItem("kitchenSink", -0.3, 1.5, 90.0),
        PlacedItem("toilet", 1.5, -1.8, 0.0),
        PlacedItem("rugRound", 0.5, 0.5, 0.0),
        PlacedItem("pottedPlant", 1.8, 1.8, 0.0),
    ],
    PropertyKind.HOUSE.value: [
        PlacedItem("bedDouble", -2.5, -2.5, 0.0),
        PlacedItem("cabinetBedDrawer", -0.8, -2.5, 0.0),
        PlacedItem("lampRoundTable", -0.8, -3.2, 0.0),
        PlacedItem("loungeSofaLong", 2.0, 2.5, 180.0),
        PlacedItem("loungeChair", 3.2, 1.2, 270.0),
        PlacedItem("tableCoffee", 2.0, 1.2, 0.0),
        PlacedItem("televisionModern", 2.0, 3.6, 180.0),
        PlacedItem("bookcaseClosedWide", 3.5, -2.5, 270.0),
        PlacedItem("kitchenCabinet", -3.5, 1.0, 90.0),
        PlacedItem("kitchenCabinetUpper", -3.5, 0.0, 90.0),
        PlacedItem("kitchenFridge", -3.5, 2.2, 90.0),
        PlacedItem("kitchenStove", -2.2, 1.0, 180.0),
        PlacedItem("kitchenSink", -1.0, 1.0, 180.0),
        PlacedItem("kitchenBar", 0.0, 2.2, 0.0),
        PlacedItem("bathtub", -3.5, -0.8, 90.0),
        PlacedItem("toilet", -2.4, -3.5, 0.0),
        PlacedItem("bathroomSink", -1.2, -3.5, 0.0),
        PlacedItem("rugRectangle", 2.0, 1.8, 0.0),
        PlacedItem("pottedPlant", 3.8, 3.8, 0.0),
        PlacedItem("pottedPlant", -3.8, 3.8, 0.0),
    ],
}

_ROOM_SIZE_CELLS: dict[str, tuple[int, int]] = {
    PropertyKind.APARTMENT.value: (4, 4),
    PropertyKind.HOUSE.value: (6, 6),
}


def room_bounds(property_kind: str) -> tuple[float, float]:
    """The interior's usable half-width/half-depth in world units -- e.g.
    a 4-cell room has a 2-cell-wide interior (one cell of wall on each
    side), so a half-extent of `(4 - 2) / 2 * WALL_SPAN`. Used by
    `panem_api.map3d_routes` to reject a placed item that falls outside
    the room it's supposedly furnishing."""
    width_cells, depth_cells = _ROOM_SIZE_CELLS.get(property_kind, (4, 4))
    return (
        (width_cells - 2) / 2 * WALL_SPAN,
        (depth_cells - 2) / 2 * WALL_SPAN,
    )


def is_resident(
    *,
    property_kind: str,
    owner_kind: str,
    owner_id: int | None,
    character_id: int,
    lease_tenant_id: int | None,
) -> bool:
    """Whether `character_id` is the one who gets to decorate this
    property's interior -- the house's owning character, or the
    apartment's current leaseholder. Takes plain scalars rather than ORM
    rows so it stays usable without a DB session (and without importing
    `panem_shared.db.models`, which this otherwise-pure module avoids);
    `panem_api.map3d_routes` resolves `owner_kind`/`owner_id`/
    `lease_tenant_id` from `Property`/`ApartmentLease` before calling it."""
    if property_kind == PropertyKind.HOUSE.value:
        return owner_kind == OwnerKind.CHARACTER.value and owner_id == character_id
    if property_kind == PropertyKind.APARTMENT.value:
        return lease_tenant_id == character_id
    return False


def default_layout(property_kind: str) -> list[PlacedItem]:
    """`property_kind` is a `PropertyKind.value` (`"house"`/`"apartment"`;
    an inn has no interior view -- it's a one-off nightly transaction, not
    a home to decorate, so `panem_api.map3d_routes` never calls this for
    one). Every property of the same kind gets the same shell size and
    starter furniture today -- `tier` doesn't yet vary the layout, only
    the housing system's own buy-gating (`panem_bot.services.housing`);
    future work could scale the room up for higher tiers."""
    width_cells, depth_cells = _ROOM_SIZE_CELLS.get(property_kind, (4, 4))
    shell = _rectangular_shell(width_cells=width_cells, depth_cells=depth_cells)
    starter = _STARTER_FURNITURE.get(
        property_kind, _STARTER_FURNITURE[PropertyKind.APARTMENT.value]
    )
    return [*shell, *starter]
