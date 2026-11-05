"""The 3D Capitol map's REST + WebSocket surface: `static/tabs/map.js`
switches to a Three.js scene (`static/map3d/*.js`) whenever the selected
district is the Capitol (id 0), rendered from the vendored Blocky
Characters/City Kit/Cube Pets/Furniture Kit assets (`static/assets3d/`).
This module is the backend half of that, split into four pieces:

- `/map3d/character/*` -- a player's own block-character look (persisted:
  `Character.block_outfit`/`.block_palette`).
- `/map3d/{district_id}/layout` -- the generated city (buildings + roads,
  `panem_shared.map3d_layout`), plus the NPCs who live there and their own
  assigned look (`panem_shared.block_models`).
- `/ws/map3d/{district_id}/room` -- live player positions and NPC
  wandering, deliberately built the same way `party_routes.PartyRoom` is
  (an in-memory, per-process room with its own broadcast tick loop) rather
  than through Redis/the DB: a character's position *while walking around
  the city* isn't world state worth persisting (see `Character.x`/`.y`'s
  own role, still owned by `panem_sim.systems.time`'s location-snap
  system, which this deliberately never touches) -- losing it on a
  restart is no worse than `PartyRoom` losing an in-progress game.
- `/pets/*` and `/housing3d/*` -- the Capitol Market's pet store and a
  property's decoratable interior, both real persisted state (`Pet`,
  `PropertyInteriorItem`).

No auth beyond the same discord_id/character_id ownership check every
other dashboard route uses (`_resolve_owned_character` below) -- same
documented gap as the rest of `panem_api` (see `app.py`'s module
docstring).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import math
import random
import time

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from panem_shared import block_models, housing3d
from panem_shared.content.loader import ContentBundle
from panem_shared.db.models import (
    ApartmentLease,
    Character,
    Npc,
    Pet,
    Property,
    PropertyInteriorItem,
    User,
    WorldClock,
)
from panem_shared.db.session import session_scope
from panem_shared.enums import PropertyKind
from panem_shared.errors import ServiceError
from panem_shared.logging import get_logger
from panem_shared.map3d_layout import CityLayout, build_city_layout
from panem_shared.pets import purchase_pet

logger = get_logger(component="api")


def _require_session_factory(
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> async_sessionmaker[AsyncSession]:
    if session_factory is None:
        raise HTTPException(status_code=503, detail="The 3D map isn't configured")
    return session_factory


async def _resolve_owned_character(
    session: AsyncSession, *, discord_id: int, character_id: int
) -> Character:
    """Same ownership check `dashboard_routes` uses everywhere -- 404 (not
    403) for a character that exists but belongs to someone else, same as
    an unknown id."""
    character = await session.get(Character, character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="No such character")
    user = await session.get(User, character.user_id)
    if user is None or user.discord_id != discord_id:
        raise HTTPException(status_code=404, detail="No such character")
    return character


async def _current_tick(session: AsyncSession) -> int:
    clock = await session.get(WorldClock, 1)
    return clock.tick if clock is not None else 0


def _http_from_service_error(exc: ServiceError) -> HTTPException:
    return HTTPException(status_code=400, detail=exc.reason_key)


# --------------------------------------------------------------- appearance


class BlockAppearance(BaseModel):
    block_outfit: str
    block_palette: str


class UpdateBlockAppearanceRequest(BaseModel):
    discord_id: int
    block_outfit: str
    block_palette: str


def build_appearance_router(
    *, session_factory: async_sessionmaker[AsyncSession] | None
) -> APIRouter:
    router = APIRouter(prefix="/activity/dashboard/map3d/character", tags=["map3d"])

    @router.get("/{character_id}", response_model=BlockAppearance)
    async def get_appearance(character_id: int, discord_id: int) -> BlockAppearance:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            return BlockAppearance(
                block_outfit=character.block_outfit, block_palette=character.block_palette
            )

    @router.patch("/{character_id}", response_model=BlockAppearance)
    async def update_appearance(
        character_id: int, body: UpdateBlockAppearanceRequest
    ) -> BlockAppearance:
        if not block_models.is_valid_letter(body.block_outfit) or not block_models.is_valid_letter(
            body.block_palette
        ):
            raise HTTPException(status_code=400, detail="block_outfit/block_palette must be a-r")
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            character.block_outfit = body.block_outfit
            character.block_palette = body.block_palette
            return BlockAppearance(
                block_outfit=character.block_outfit, block_palette=character.block_palette
            )

    return router


# ------------------------------------------------------------------- layout


class CityBuildingOut(BaseModel):
    location_id: str
    name: str
    kind: str
    model: str | None
    x: float
    z: float
    rotation_deg: float


class RoadTileOut(BaseModel):
    model: str
    x: float
    z: float
    rotation_deg: float


class NpcOut(BaseModel):
    id: str
    name: str
    job_title: str | None
    home_x: float
    home_z: float
    block_outfit: str
    block_palette: str


class CityLayoutOut(BaseModel):
    district_id: int
    scale: float
    hub_location_id: str | None
    spawn_x: float
    spawn_z: float
    buildings: list[CityBuildingOut]
    roads: list[RoadTileOut]
    npcs: list[NpcOut]


def _layout_out(layout: CityLayout) -> CityLayoutOut:
    return CityLayoutOut(
        district_id=layout.district_id,
        scale=layout.scale,
        hub_location_id=layout.hub_location_id,
        spawn_x=layout.spawn_x,
        spawn_z=layout.spawn_z,
        buildings=[
            CityBuildingOut(
                location_id=b.location_id,
                name=b.name,
                kind=b.kind,
                model=b.model,
                x=b.x,
                z=b.z,
                rotation_deg=b.rotation_deg,
            )
            for b in layout.buildings
        ],
        roads=[
            RoadTileOut(model=r.model, x=r.x, z=r.z, rotation_deg=r.rotation_deg)
            for r in layout.roads
        ],
        npcs=[],
    )


async def _npcs_for_district(
    session: AsyncSession, *, content: ContentBundle, district_id: int, scale: float
) -> list[NpcOut]:
    district = content.district(district_id)
    center = (district.map.width / 2, district.map.height / 2)
    rows = await session.execute(select(Npc).where(Npc.district_id == district_id))
    npcs_out: list[NpcOut] = []
    for npc in rows.scalars().all():
        outfit, palette = block_models.assign_block_model(npc.id, gender=npc.gender)
        raw_x = npc.x if npc.x is not None else center[0]
        raw_y = npc.y if npc.y is not None else center[1]
        job_title = None
        if npc.job_id is not None:
            job = content.jobs.get(npc.job_id)
            job_title = job.title if job is not None else npc.job_id
        npcs_out.append(
            NpcOut(
                id=npc.id,
                name=npc.name,
                job_title=job_title,
                home_x=(raw_x - center[0]) * scale,
                home_z=(raw_y - center[1]) * scale,
                block_outfit=outfit,
                block_palette=palette,
            )
        )
    return npcs_out


def build_layout_router(
    *, content: ContentBundle, session_factory: async_sessionmaker[AsyncSession] | None
) -> APIRouter:
    router = APIRouter(prefix="/activity/dashboard/map3d", tags=["map3d"])

    @router.get("/{district_id}/layout", response_model=CityLayoutOut)
    async def get_layout(district_id: int) -> CityLayoutOut:
        if district_id not in content.districts:
            raise HTTPException(status_code=404, detail="No such district")
        district = content.district(district_id)
        layout = build_city_layout(district)
        out = _layout_out(layout)

        # Same "degrade, don't 503" posture as `/world/time` above (see its
        # own docstring): the building/road layout is pure content, needing
        # no DB at all -- only the NPC roster does, so a fresh/unconfigured
        # deployment with no `session_factory` still gets a city, just with
        # nobody in it yet, rather than refusing the whole endpoint.
        if session_factory is not None:
            async with session_scope(session_factory) as session:
                out.npcs = await _npcs_for_district(
                    session, content=content, district_id=district_id, scale=layout.scale
                )
        return out

    return router


# --------------------------------------------------------------- live room

NPC_WANDER_RADIUS = 2.5
NPC_SPEED = 0.6
PLAYER_SPEED = 3.2
TICK_SECONDS = 0.1
"""Matches `party_routes.PartyRoom`'s own broadcast cadence -- fast enough
that the frontend's position easing (mirroring `tabs/party.js`'s own)
reads as smooth movement rather than a visible step every tick."""
RETARGET_SECONDS = 4.0


@dataclasses.dataclass
class RoomPlayer:
    character_id: int
    name: str
    block_outfit: str
    block_palette: str
    x: float
    z: float
    facing_deg: float = 0.0
    moving: bool = False
    last_move_at: float = dataclasses.field(default_factory=time.monotonic, repr=False)


@dataclasses.dataclass
class RoomNpc:
    npc_id: str
    name: str
    job_title: str | None
    block_outfit: str
    block_palette: str
    home_x: float
    home_z: float
    x: float
    z: float
    target_x: float
    target_z: float
    next_retarget_at: float = 0.0


class Map3DRoom:
    """One district's live Capitol-map room: connected players plus a
    lightweight server-authoritative NPC wander so every client agrees on
    where NPCs currently are, the same reasoning `party_routes.PartyRoom`
    documents for its own tick loop. Scoped per district id so a future
    district getting its own 3D view (see this module's docstring) doesn't
    share a room with the Capitol's."""

    def __init__(self, district_id: int, layout: CityLayout) -> None:
        self.district_id = district_id
        self.layout = layout
        self.bounds = max((abs(b.x) for b in layout.buildings), default=20.0) + 10.0
        self.players: dict[int, RoomPlayer] = {}
        self.connections: dict[int, WebSocket] = {}
        self.npcs: dict[str, RoomNpc] = {}
        self._rng = random.Random()
        self._task: asyncio.Task[None] | None = None

    def seed_npcs(self, npcs: list[NpcOut]) -> None:
        if self.npcs:
            return
        for npc in npcs:
            self.npcs[npc.id] = RoomNpc(
                npc_id=npc.id,
                name=npc.name,
                job_title=npc.job_title,
                block_outfit=npc.block_outfit,
                block_palette=npc.block_palette,
                home_x=npc.home_x,
                home_z=npc.home_z,
                x=npc.home_x,
                z=npc.home_z,
                target_x=npc.home_x,
                target_z=npc.home_z,
            )

    def join(
        self, websocket: WebSocket, *, character_id: int, name: str, outfit: str, palette: str
    ) -> WebSocket | None:
        old_ws = self.connections.get(character_id)
        existing = self.players.get(character_id)
        if existing is None:
            self.players[character_id] = RoomPlayer(
                character_id=character_id,
                name=name,
                block_outfit=outfit,
                block_palette=palette,
                x=self.layout.spawn_x + self._rng.uniform(-2, 2),
                z=self.layout.spawn_z + self._rng.uniform(-2, 2),
            )
        else:
            existing.name, existing.block_outfit, existing.block_palette = name, outfit, palette
        self.connections[character_id] = websocket
        return old_ws if old_ws is not websocket else None

    def drop(self, character_id: int) -> None:
        self.connections.pop(character_id, None)
        self.players.pop(character_id, None)

    def handle_move(self, character_id: int, dx: float, dz: float) -> None:
        player = self.players.get(character_id)
        if player is None:
            return
        now = time.monotonic()
        elapsed = min(max(now - player.last_move_at, 0.0), 0.5)
        player.last_move_at = now
        length = math.hypot(dx, dz)
        if length <= 1e-6:
            player.moving = False
            return
        ux, uz = dx / length, dz / length
        player.x = min(max(player.x + ux * PLAYER_SPEED * elapsed, -self.bounds), self.bounds)
        player.z = min(max(player.z + uz * PLAYER_SPEED * elapsed, -self.bounds), self.bounds)
        player.moving = True
        player.facing_deg = math.degrees(math.atan2(ux, uz))

    def _advance_npcs(self) -> None:
        now = time.monotonic()
        for npc in self.npcs.values():
            if now >= npc.next_retarget_at:
                npc.target_x = npc.home_x + self._rng.uniform(-NPC_WANDER_RADIUS, NPC_WANDER_RADIUS)
                npc.target_z = npc.home_z + self._rng.uniform(-NPC_WANDER_RADIUS, NPC_WANDER_RADIUS)
                npc.next_retarget_at = now + self._rng.uniform(
                    RETARGET_SECONDS, RETARGET_SECONDS * 2
                )
            dx, dz = npc.target_x - npc.x, npc.target_z - npc.z
            distance = math.hypot(dx, dz)
            if distance <= 1e-3:
                continue
            step = min(distance, NPC_SPEED * TICK_SECONDS)
            npc.x += dx / distance * step
            npc.z += dz / distance * step

    def snapshot(self) -> dict[str, object]:
        return {
            "type": "state",
            "players": [
                {
                    "character_id": p.character_id,
                    "name": p.name,
                    "block_outfit": p.block_outfit,
                    "block_palette": p.block_palette,
                    "x": round(p.x, 2),
                    "z": round(p.z, 2),
                    "facing_deg": round(p.facing_deg, 1),
                    "moving": p.moving,
                }
                for p in self.players.values()
            ],
            "npcs": [
                {
                    "id": n.npc_id,
                    "name": n.name,
                    "job_title": n.job_title,
                    "block_outfit": n.block_outfit,
                    "block_palette": n.block_palette,
                    "x": round(n.x, 2),
                    "z": round(n.z, 2),
                }
                for n in self.npcs.values()
            ],
        }

    async def broadcast(self) -> None:
        if not self.connections:
            return
        payload = self.snapshot()
        stale: list[int] = []
        for character_id, ws in list(self.connections.items()):
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(character_id)
        for character_id in stale:
            self.drop(character_id)

    def ensure_loop(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        try:
            while self.connections:
                self._advance_npcs()
                await self.broadcast()
                await asyncio.sleep(TICK_SECONDS)
        except Exception:
            logger.exception("map3d_room_loop_crashed", district_id=self.district_id)
        finally:
            self._task = None


def build_room_router(
    *, content: ContentBundle, session_factory: async_sessionmaker[AsyncSession] | None
) -> APIRouter:
    router = APIRouter()
    rooms: dict[int, Map3DRoom] = {}

    async def _get_or_create_room(district_id: int) -> Map3DRoom:
        room = rooms.get(district_id)
        if room is not None:
            return room
        district = content.district(district_id)
        layout = build_city_layout(district)
        room = Map3DRoom(district_id, layout)
        rooms[district_id] = room
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            npcs_out = await _npcs_for_district(
                session, content=content, district_id=district_id, scale=layout.scale
            )
        room.seed_npcs(npcs_out)
        return room

    @router.websocket("/ws/map3d/{district_id}/room")
    async def room_ws(websocket: WebSocket, district_id: int) -> None:
        if district_id not in content.districts:
            await websocket.close(code=4004, reason="No such district")
            return
        await websocket.accept()
        character_id: int | None = None
        room: Map3DRoom | None = None
        try:
            first = await websocket.receive_json()
            if not isinstance(first, dict) or first.get("type") != "join":
                await websocket.close(code=4000, reason="Expected a join message")
                return
            discord_id = int(first.get("discord_id", 0) or 0)
            character_id = int(first.get("character_id", 0) or 0)

            factory = _require_session_factory(session_factory)
            async with session_scope(factory) as session:
                character = await _resolve_owned_character(
                    session, discord_id=discord_id, character_id=character_id
                )
                name, outfit, palette = (
                    character.name,
                    character.block_outfit,
                    character.block_palette,
                )

            room = await _get_or_create_room(district_id)
            old_ws = room.join(
                websocket, character_id=character_id, name=name, outfit=outfit, palette=palette
            )
            if old_ws is not None:
                with contextlib.suppress(Exception):
                    await old_ws.close(code=4001, reason="Reconnected elsewhere")

            room.ensure_loop()
            await room.broadcast()

            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "move":
                    room.handle_move(
                        character_id,
                        float(message.get("dx", 0) or 0),
                        float(message.get("dz", 0) or 0),
                    )
        except WebSocketDisconnect:
            pass
        except HTTPException:
            raise
        except Exception:
            logger.exception("map3d_room_ws_error", district_id=district_id)
        finally:
            if (
                room is not None
                and character_id is not None
                and room.connections.get(character_id) is websocket
            ):
                room.drop(character_id)

    return router


# ------------------------------------------------------------------- pets


class PetSpeciesOut(BaseModel):
    id: str
    name: str
    model: str
    price: float
    description: str


class PetsCatalogResponse(BaseModel):
    species: list[PetSpeciesOut]


class OwnedPetOut(BaseModel):
    id: int
    species_id: str
    model: str
    name: str


class OwnedPetsResponse(BaseModel):
    pets: list[OwnedPetOut]


class BuyPetRequest(BaseModel):
    discord_id: int
    species_id: str
    name: str


def build_pets_router(
    *, content: ContentBundle, session_factory: async_sessionmaker[AsyncSession] | None
) -> APIRouter:
    router = APIRouter(prefix="/activity/dashboard/pets", tags=["map3d"])

    @router.get("/catalog", response_model=PetsCatalogResponse)
    async def catalog() -> PetsCatalogResponse:
        return PetsCatalogResponse(
            species=[
                PetSpeciesOut(
                    id=p.id, name=p.name, model=p.model, price=p.price, description=p.description
                )
                for p in sorted(content.pets.values(), key=lambda p: p.price)
            ]
        )

    @router.get("/{character_id}", response_model=OwnedPetsResponse)
    async def owned_pets(character_id: int, discord_id: int) -> OwnedPetsResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            await _resolve_owned_character(
                session, discord_id=discord_id, character_id=character_id
            )
            rows = await session.execute(select(Pet).where(Pet.owner_character_id == character_id))
            out: list[OwnedPetOut] = []
            for pet in rows.scalars().all():
                species = content.pets.get(pet.species_id)
                model = species.model if species is not None else pet.species_id
                out.append(
                    OwnedPetOut(id=pet.id, species_id=pet.species_id, model=model, name=pet.name)
                )
            return OwnedPetsResponse(pets=out)

    @router.post("/{character_id}/buy", response_model=OwnedPetOut)
    async def buy_pet(character_id: int, body: BuyPetRequest) -> OwnedPetOut:
        species = content.pets.get(body.species_id)
        if species is None:
            raise HTTPException(status_code=404, detail="No such pet species")
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            character = await _resolve_owned_character(
                session, discord_id=body.discord_id, character_id=character_id
            )
            current_tick = await _current_tick(session)
            try:
                pet = await purchase_pet(
                    session,
                    character=character,
                    species=species,
                    name=body.name,
                    current_tick=current_tick,
                )
            except ServiceError as exc:
                raise _http_from_service_error(exc) from exc
            return OwnedPetOut(
                id=pet.id, species_id=pet.species_id, model=species.model, name=pet.name
            )

    return router


# --------------------------------------------------------------- housing3d


class InteriorItemOut(BaseModel):
    item_key: str
    pos_x: float
    pos_z: float
    rotation_y: float
    structural: bool


class FurnitureCatalogItemOut(BaseModel):
    item_key: str
    name: str
    category: str
    footprint_w: float
    footprint_d: float


class InteriorResponse(BaseModel):
    property_id: int
    can_edit: bool
    half_width: float
    half_depth: float
    items: list[InteriorItemOut]
    catalog: list[FurnitureCatalogItemOut]


class InteriorItemIn(BaseModel):
    item_key: str
    pos_x: float
    pos_z: float
    rotation_y: float = 0.0


class ReplaceInteriorRequest(BaseModel):
    discord_id: int
    items: list[InteriorItemIn]


async def _resolve_property_residency(
    session: AsyncSession, *, character: Character, property_: Property
) -> bool:
    lease_tenant_id: int | None = None
    if property_.kind == PropertyKind.APARTMENT.value:
        lease_row = await session.execute(
            select(ApartmentLease).where(ApartmentLease.property_id == property_.id)
        )
        lease = lease_row.scalar_one_or_none()
        lease_tenant_id = lease.tenant_character_id if lease is not None else None
    return housing3d.is_resident(
        property_kind=property_.kind,
        owner_kind=property_.owner_kind,
        owner_id=property_.owner_id,
        character_id=character.id,
        lease_tenant_id=lease_tenant_id,
    )


async def _get_or_seed_items(
    session: AsyncSession, property_: Property
) -> list[PropertyInteriorItem]:
    rows = await session.execute(
        select(PropertyInteriorItem).where(PropertyInteriorItem.property_id == property_.id)
    )
    items = list(rows.scalars().all())
    if items:
        return items
    for placed in housing3d.default_layout(property_.kind):
        row = PropertyInteriorItem(
            property_id=property_.id,
            item_key=placed.item_key,
            pos_x=placed.pos_x,
            pos_z=placed.pos_z,
            rotation_y=placed.rotation_y,
        )
        session.add(row)
        items.append(row)
    await session.flush()
    return items


def build_housing3d_router(
    *, content: ContentBundle, session_factory: async_sessionmaker[AsyncSession] | None
) -> APIRouter:
    router = APIRouter(prefix="/activity/dashboard/housing3d", tags=["map3d"])

    def _catalog_response() -> list[FurnitureCatalogItemOut]:
        return [
            FurnitureCatalogItemOut(
                item_key=f.item_key,
                name=f.name,
                category=f.category,
                footprint_w=f.footprint_w,
                footprint_d=f.footprint_d,
            )
            for f in content.furniture.values()
            if not f.structural
        ]

    @router.get("/{property_id}", response_model=InteriorResponse)
    async def get_interior(property_id: int, discord_id: int) -> InteriorResponse:
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            property_ = await session.get(Property, property_id)
            if property_ is None or property_.kind == PropertyKind.INN.value:
                raise HTTPException(status_code=404, detail="No interior for this property")
            user_row = await session.execute(select(User).where(User.discord_id == discord_id))
            user = user_row.scalar_one_or_none()
            can_edit = False
            if user is not None:
                char_rows = await session.execute(
                    select(Character).where(Character.user_id == user.id)
                )
                for character in char_rows.scalars().all():
                    if await _resolve_property_residency(
                        session, character=character, property_=property_
                    ):
                        can_edit = True
                        break
            items = await _get_or_seed_items(session, property_)
            half_width, half_depth = housing3d.room_bounds(property_.kind)
            return InteriorResponse(
                property_id=property_.id,
                can_edit=can_edit,
                half_width=half_width,
                half_depth=half_depth,
                items=[
                    InteriorItemOut(
                        item_key=i.item_key,
                        pos_x=i.pos_x,
                        pos_z=i.pos_z,
                        rotation_y=i.rotation_y,
                        structural=content.furniture[i.item_key].structural
                        if i.item_key in content.furniture
                        else True,
                    )
                    for i in items
                ],
                catalog=_catalog_response(),
            )

    @router.put("/{property_id}", response_model=InteriorResponse)
    async def replace_interior(property_id: int, body: ReplaceInteriorRequest) -> InteriorResponse:
        half_width, half_depth = 0.0, 0.0
        factory = _require_session_factory(session_factory)
        async with session_scope(factory) as session:
            property_ = await session.get(Property, property_id)
            if property_ is None or property_.kind == PropertyKind.INN.value:
                raise HTTPException(status_code=404, detail="No interior for this property")
            character = None
            user_row = await session.execute(select(User).where(User.discord_id == body.discord_id))
            user = user_row.scalar_one_or_none()
            if user is not None:
                char_rows = await session.execute(
                    select(Character).where(Character.user_id == user.id)
                )
                for candidate in char_rows.scalars().all():
                    if await _resolve_property_residency(
                        session, character=candidate, property_=property_
                    ):
                        character = candidate
                        break
            if character is None:
                raise HTTPException(status_code=403, detail="You don't live at this property")

            half_width, half_depth = housing3d.room_bounds(property_.kind)
            for item in body.items:
                piece = content.furniture.get(item.item_key)
                if piece is None or piece.structural:
                    raise HTTPException(
                        status_code=400, detail=f"{item.item_key!r} isn't a placeable item"
                    )
                if abs(item.pos_x) > half_width or abs(item.pos_z) > half_depth:
                    raise HTTPException(
                        status_code=400, detail=f"{item.item_key!r} placed outside the room"
                    )

            # Ensure the structural shell exists (first-ever view skipped
            # straight to editing), then replace only the non-structural
            # rows -- the shell itself is never touched by this endpoint.
            existing = await _get_or_seed_items(session, property_)
            for row in existing:
                if (
                    row.item_key not in content.furniture
                    or not content.furniture[row.item_key].structural
                ):
                    await session.delete(row)
            await session.flush()
            for item in body.items:
                session.add(
                    PropertyInteriorItem(
                        property_id=property_.id,
                        item_key=item.item_key,
                        pos_x=item.pos_x,
                        pos_z=item.pos_z,
                        rotation_y=item.rotation_y,
                    )
                )
            await session.flush()
            items = await _get_or_seed_items(session, property_)
            return InteriorResponse(
                property_id=property_.id,
                can_edit=True,
                half_width=half_width,
                half_depth=half_depth,
                items=[
                    InteriorItemOut(
                        item_key=i.item_key,
                        pos_x=i.pos_x,
                        pos_z=i.pos_z,
                        rotation_y=i.rotation_y,
                        structural=content.furniture[i.item_key].structural
                        if i.item_key in content.furniture
                        else True,
                    )
                    for i in items
                ],
                catalog=_catalog_response(),
            )

    return router


def build_map3d_routers(
    *, content: ContentBundle, session_factory: async_sessionmaker[AsyncSession] | None
) -> list[APIRouter]:
    return [
        build_appearance_router(session_factory=session_factory),
        build_layout_router(content=content, session_factory=session_factory),
        build_room_router(content=content, session_factory=session_factory),
        build_pets_router(content=content, session_factory=session_factory),
        build_housing3d_router(content=content, session_factory=session_factory),
    ]
