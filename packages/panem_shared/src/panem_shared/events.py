"""Typed world events (Spec §5.2) published to Redis `world:events`.

Every event carries `id` (uuid), `tick`, and `schema_version: 1` (Spec
§2.2) so subscribers (the bot's narrator, later the API) can evolve
independently of the sim's internal event shape. Only the event kinds
Milestone A/B need (`NarrationLine`, `Bulletin`) are defined here; later
milestones add more kinds to the `WorldEvent` union as their systems need
them, without touching what's already here.

Lives in `panem_shared`, not `panem_sim`, because this is a cross-process
wire contract: `panem_sim` produces these, `panem_bot`'s narrator
consumes them, and neither should have to depend on the other's package
just to agree on an event's shape.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

import redis.asyncio as redis
from pydantic import BaseModel, Field, TypeAdapter

WORLD_EVENTS_CHANNEL = "world:events"


class _EventBase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tick: int
    schema_version: Literal[1] = 1


class NarrationLine(_EventBase):
    """Ambient narration for a district/location, batched at most once per
    location per tick (FR-NPC-3). Consumed by the bot's narrator, posted
    into that location's ambient post."""

    kind: Literal["NarrationLine"] = "NarrationLine"
    district_id: int
    location_id: str
    text: str


class Bulletin(_EventBase):
    """District-wide notice (crisis changes, daily bulletin, ...).
    Consumed by the bot's narrator, posted to `#board`."""

    kind: Literal["Bulletin"] = "Bulletin"
    district_id: int
    text: str


class CharacterArrived(_EventBase):
    """A character's cross-district transit finished (FR-LOC-9). Consumed
    by the bot to grant the destination district's "visitor" role and
    drop the origin's, if either isn't the character's home district
    (`Character.district_id`, never touched here). Arrival narration
    itself is a separate `NarrationLine` at the destination's station,
    the same as any other arrival."""

    kind: Literal["CharacterArrived"] = "CharacterArrived"
    character_id: int
    district_id: int
    """Destination district -- where the character just arrived."""
    origin_district_id: int


class NpcChatter(_EventBase):
    """Two co-located, unengaged NPCs strike up a short conversation on
    their own (`panem_sim.systems.npc_chatter`), purely as ambient world
    flavor -- never involving a player. Unlike `NarrationLine`, the sim
    doesn't write the actual lines here (it never calls the LLM anywhere
    in this codebase); it just decides *that* and *who*, and the bot's
    narrator generates and posts the exchange into the location's pinned
    ambient thread, each line as that NPC (name + avatar), not "The
    Narrator"."""

    kind: Literal["NpcChatter"] = "NpcChatter"
    district_id: int
    location_id: str
    npc_ids: tuple[str, str]


WorldEvent = Annotated[
    NarrationLine | Bulletin | CharacterArrived | NpcChatter, Field(discriminator="kind")
]
AnyWorldEvent = NarrationLine | Bulletin | CharacterArrived | NpcChatter

_event_adapter: TypeAdapter[AnyWorldEvent] = TypeAdapter(WorldEvent)


async def publish(redis_client: redis.Redis, event: AnyWorldEvent) -> None:
    await redis_client.publish(WORLD_EVENTS_CHANNEL, event.model_dump_json())


def parse_event(kind: str, payload: dict[str, Any]) -> AnyWorldEvent:
    """Reconstruct a typed event from a persisted `world_events.payload`
    (used on restart to re-publish events a crash left `announced=False`,
    NFR-7). `kind` is redundant with `payload["kind"]` but is the DB row's
    own indexed column, so callers pass it straight from the row rather
    than digging into `payload` twice."""
    return _event_adapter.validate_python({**payload, "kind": kind})


def parse_message(raw: str | bytes) -> AnyWorldEvent:
    """Decode one `WORLD_EVENTS_CHANNEL` pubsub payload -- the full JSON
    `publish()` sent, `kind` included -- into its typed event. Used by
    subscribers (the bot's narrator); unlike `parse_event`, there's no
    separate `kind` column to pull from here."""
    return _event_adapter.validate_json(raw)
