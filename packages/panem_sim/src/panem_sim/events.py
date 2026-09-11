"""Typed world events (Spec §5.2) published to Redis `world:events`.

Every event carries `id` (uuid), `tick`, and `schema_version: 1` (Spec
§2.2) so subscribers (the bot's narrator, later the API) can evolve
independently of the sim's internal event shape. Only the event kinds
Milestone A/B need (`NarrationLine`, `Bulletin`) are defined here; later
milestones add more kinds to the `WorldEvent` union as their systems need
them, without touching what's already here.
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


WorldEvent = Annotated[NarrationLine | Bulletin, Field(discriminator="kind")]
AnyWorldEvent = NarrationLine | Bulletin

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
