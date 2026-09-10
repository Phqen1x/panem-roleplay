"""ORM models for the full data model (Plan §2, Spec §2.4).

Districts, goods, jobs, and locations are content data (YAML, validated by
`panem_shared.content`), not DB tables — DB columns that reference them
(`district_id`, `good_id`, `job_id`, `location_id`) are plain scalars, not
foreign keys, since the content they point at lives outside Postgres.

Tables whose logic belongs to Phase 1+ (relationships, memories, market,
district_state, shifts, jobs_history, world_events, dialogue_log, npcs,
npc_schedule) are still defined here now: Phase 0's `characters` and
`scenes` tables reference or will be referenced by them, and having the
whole schema in one Alembic baseline avoids a churn of follow-up migrations
as later phases land.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from panem_shared.db.base import Base, TimestampMixin
from panem_shared.enums import (
    CharacterStatus,
    SceneKind,
    SceneStatus,
    Stance,
)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    tos_accepted_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    banned_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

    characters: Mapped[list[Character]] = relationship(back_populates="user")


class Character(TimestampMixin, Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    district_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    current_district_id: Mapped[int] = mapped_column(Integer, nullable=False)
    """Where the character physically is now; `district_id` is home (FR-LOC-8)."""

    name: Mapped[str] = mapped_column(String(32), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    appearance: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    backstory: Mapped[str] = mapped_column(String(1500), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CharacterStatus.PENDING.value, index=True
    )

    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_started_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consecutive_missed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    reputation: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    money: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)

    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    proxy_tag: Mapped[str | None] = mapped_column(String(12), nullable=True)

    loyalty: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    fear: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    health: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    hunger: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    hospitalized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tesserae_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    jailed_until_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    in_games: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_victor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    in_transit_until_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transit_destination_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    user: Mapped[User] = relationship(back_populates="characters")

    __table_args__ = (UniqueConstraint("user_id", "proxy_tag", name="uq_character_user_proxy_tag"),)


class Npc(TimestampMixin, Base):
    __tablename__ = "npcs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    district_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    traits: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    speech_style: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    faction_leanings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)
    home_location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    money: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    float_target: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    """`npcs.float` in the spec; renamed to avoid the Python builtin (FR-ECO-9)."""
    shop_goods: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    hunger: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    health: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    fear: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loyalty: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    morale: Mapped[float] = mapped_column(Float, nullable=False, default=60.0)

    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    importance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    provider_override: Mapped[str | None] = mapped_column(String(16), nullable=True)


class NpcSchedule(Base):
    __tablename__ = "npc_schedule"

    npc_id: Mapped[str] = mapped_column(ForeignKey("npcs.id"), primary_key=True)
    phase: Mapped[str] = mapped_column(String(16), primary_key=True)
    location_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False)


class RelationshipRow(TimestampMixin, Base):
    """`relationships` (named to avoid clashing with `sqlalchemy.orm.relationship`)."""

    __tablename__ = "relationships"

    subject_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    object_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    object_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    affinity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trust: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_interaction_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)

    stance: Mapped[str] = mapped_column(String(16), nullable=False, default=Stance.NEUTRAL.value)
    stance_updated_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tick: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    importance: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    expires_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Inventory(Base):
    __tablename__ = "inventories"

    owner_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    good_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MarketPrice(Base):
    __tablename__ = "market_prices"

    district_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    good_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    supply: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    demand: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tick: Mapped[int] = mapped_column(Integer, nullable=False)


class MarketOrder(Base):
    __tablename__ = "market_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    district_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    good_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    tick: Mapped[int] = mapped_column(Integer, nullable=False)


class DistrictState(Base):
    __tablename__ = "district_state"

    district_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tick: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quota_target: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    morale: Mapped[float] = mapped_column(Float, nullable=False, default=60.0)
    unrest: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    peacekeeper_pressure: Mapped[float] = mapped_column(Float, nullable=False, default=0.3)
    crisis_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    crisis_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    capitol_favor: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    victor_bonus_until: Mapped[int | None] = mapped_column(Integer, nullable=True)
    treasury: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class Shift(Base):
    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tick_opened: Mapped[int] = mapped_column(Integer, nullable=False)
    tick_due: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class JobHistory(Base):
    __tablename__ = "jobs_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    started_tick: Mapped[int] = mapped_column(Integer, nullable=False)
    ended_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)


class WorldEvent(TimestampMixin, Base):
    __tablename__ = "world_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tick: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    district_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    announced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)


class DiscordChannel(TimestampMixin, Base):
    __tablename__ = "discord_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    district_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    webhook_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    webhook_token: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint("district_id", "kind", name="uq_discord_channels_district_kind"),
    )


class Scene(TimestampMixin, Base):
    __tablename__ = "scenes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    district_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    location_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    forum_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default=SceneKind.PLAYER.value)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by_character_id: Mapped[int | None] = mapped_column(
        ForeignKey("characters.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SceneStatus.OPEN.value, index=True
    )
    pins_location: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_message_at: Mapped[dt.datetime | None] = mapped_column(nullable=True, index=True)
    participants: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class SceneMessage(Base):
    __tablename__ = "scene_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("scenes.id"), nullable=False, index=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    district_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    discord_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    author_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    author_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    author_name: Mapped[str] = mapped_column(String(64), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[dt.datetime] = mapped_column(nullable=False, index=True)
    edited_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


class DialogueLog(TimestampMixin, Base):
    __tablename__ = "dialogue_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick: Mapped[int] = mapped_column(Integer, nullable=False)
    npc_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    character_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"), nullable=True)
    scene_id: Mapped[int | None] = mapped_column(ForeignKey("scenes.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class StaffAction(TimestampMixin, Base):
    __tablename__ = "staff_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    staff_discord_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
