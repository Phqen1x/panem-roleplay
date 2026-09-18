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
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from panem_shared.db.base import Base, TimestampMixin
from panem_shared.enums import (
    CharacterStatus,
    OwnerKind,
    SceneKind,
    SceneStatus,
    Stance,
)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    tos_accepted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    banned_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_characters_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """`/staff character_limit`; NULL means the guild default applies."""

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

    job_title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    """Free-typed by the player at character creation (Spec rework: no more
    picking from a `jobs.yaml` catalog) -- staff sign off on it (or edit it)
    as part of approval, same as the rest of the application, and can change
    it any time after via `/staff give job`. Purely descriptive; wages and
    market production no longer key off it at all (see `shift_phase` and
    `panem_shared.shifts`)."""
    shift_phase: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """`DayPhase` the player chose to work at creation (staff-editable the
    same way `job_title` is) -- replaces a catalog `Job.shift_phase`."""
    shifts_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """Total shifts ever completed (`/work`, win or lose) -- drives the
    Apprentice->Expert wage-multiplier ladder in `panem_shared.job_levels`,
    which replaced the old per-job ladder (`Job.ladder_next`)."""
    job_is_illicit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Self-declared by the player alongside `job_title`/`shift_phase` at
    character creation (staff-editable via `/staff give job`, same as
    those two) -- whether their free-typed job counts as illicit work for
    the contraband system (`panem_bot.cogs.jobs`'s illicit-production
    branch, heat accumulation, arrest-evasion). Unlike `job_title` there's
    no catalog to validate this against, so it's just what the player
    says it is."""
    job_started_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consecutive_missed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """Separate from `consecutive_missed` (attendance) -- these track the
    `/work` minigame's own win/lose streak, which
    `panem_shared.shifts.resolve_shift_game` reads to decide a reputation
    streak bonus/penalty. A `neutral` resolution (skipping the minigame)
    leaves both alone rather than resetting them, since it's neither a win
    nor a loss."""
    last_active_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Last tick this character did something active (`/work`, a proxied
    message) -- drives the district economy's active-player demand model
    (`panem_sim.systems.economy`), a proxy for "interacted in the past real
    week" since ticks run on a fixed real-time cadence."""

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
    fatigue: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    """100 = fully rested. Drained by working a shift
    (`panem_shared.shifts.apply_shift_outcome`) and by proxied RP
    (`panem_bot.cogs.proxy`); restored by `/sleep` (full rate in a bed --
    an owned house, a leased apartment, or an inn stay -- half on the bare
    ground) or by paying for a night at an inn. `panem_sim.systems.needs`
    docks health on a night it ends too low, mirroring the existing
    hunger->health pattern."""
    hospitalized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    jailed_until_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    jail_sentence_ticks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """The *original* length of the current jailing, set once by
    `panem_bot.services.jail.commit_to_jail` and left alone while
    `jailed_until_tick` counts down -- lock-picking difficulty
    (`/lockpick`) reads this instead, so a longer sentence stays harder to
    pick for its whole duration rather than getting easier near release."""
    jail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """Total times ever jailed -- scales both the next sentence length and
    its bail cost (`panem_bot.services.jail`), so repeat offenders serve
    longer and pay more."""
    jail_lockpick_tries_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """`/lockpick` attempts spent on the *current* jailing -- reset to 0
    whenever `panem_shared.jail.commit_to_jail` starts a fresh sentence;
    capped at `LOCKPICK_MAX_TRIES` (3) before bail/waiting it out are the
    only options left."""
    illicit_heat: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    """Per-character peacekeeper suspicion (0-100ish), separate from a
    district's own `DistrictState.peacekeeper_pressure` -- built up by
    working an illicit job (`Character.job_is_illicit`), decayed back
    toward 0 daily the same way district pressure decays toward its own
    baseline (`panem_sim.systems.crisis`). Crossing `ILLICIT_HEAT_ARREST_
    THRESHOLD` triggers an immediate arrest-evasion roll."""
    last_steal_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """The tick of this character's last `/steal` attempt (success or
    not) -- gates the once-per-day-phase cooldown by comparing `tick //
    simtime.TICKS_PER_PHASE` to this same division of `last_steal_tick`."""
    in_games: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    housing_property_id: Mapped[int | None] = mapped_column(
        ForeignKey("properties.id", use_alter=True, name="fk_characters_housing_property_id"),
        nullable=True,
    )
    """The character's home -- an owned `HOUSE`, or the `Property` behind
    their current `ApartmentLease`. `None` means no fixed home: sleeping
    (`/sleep`) falls back to the ground's reduced fatigue restoration and
    an inn stay is a one-off nightly transaction, not a lasting home.

    `use_alter=True` (+ an explicit `name=`, matching the migration's own
    `op.create_foreign_key` name): `Property.owner_id` points back at
    `characters.id`, so without it `Base.metadata.create_all`/`drop_all`
    (every test's DB fixture) can't topologically sort the two tables --
    this marks the constraint as addable/droppable via a separate `ALTER
    TABLE`, breaking the cycle for DDL ordering purposes only. Postgres
    needs a constraint name to `DROP CONSTRAINT` it, hence the name."""
    positions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    """`Position` enum values (Victor/Gamemaker/Governor), staff-granted via
    `/staff give position` -- not content-authored or applied for like a
    `Job`. Was a single `is_victor` bool; a list since a character can hold
    more than one."""

    in_transit_until_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transit_destination_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_since_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Set when a character first leaves `district_id` (home); cleared on
    return. `panem_sim.systems.jobs` excuses missed shifts for
    `AWAY_GRACE_DAYS` from this tick before they count against
    `consecutive_missed` again (FR-LOC-9)."""

    user: Mapped[User] = relationship(back_populates="characters")

    __table_args__ = (
        UniqueConstraint("user_id", "proxy_tag", name="uq_character_user_proxy_tag"),
        # Case-insensitive, since every by-name lookup in the bot (proxying,
        # `/character edit`, `/staff kill`, ...) assumes at most one match.
        # Rejected applications never became real characters, so excluded --
        # their names are free to reuse.
        Index(
            "uq_characters_name_ci",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("status <> 'rejected'"),
        ),
    )


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
    engagement_id: Mapped[int | None] = mapped_column(ForeignKey("scenes.id"), nullable=True)
    """The `ENGAGEMENT`-kind `Scene` this NPC has been pulled into, if any --
    set when they join (`panem_bot.services.engagements`), cleared when the
    engagement ends. `panem_sim.systems.schedule` skips an NPC entirely
    while this is set, which is what keeps them at the engagement's
    location instead of wandering off on their normal weighted schedule."""

    backstory_override: Mapped[str | None] = mapped_column(String(1500), nullable=True)
    appearance_override: Mapped[str | None] = mapped_column(String(400), nullable=True)
    """`NpcContent.backstory`/`.appearance` (`data/npcs/*.yaml`) are
    display-only flavor text with no DB column of their own -- fine for an
    authored resident, but staff have no way to retroactively correct or
    flesh one out, and a staff-created NPC (`/staff npc add`, not backed by
    any authored content at all) needs somewhere to hold it in the first
    place. `panem_bot.cogs.staff`'s NPC-editing commands set these; every
    read site (`/resident profile`, dialogue's `npc_background`) prefers
    the override when set and falls back to the authored content
    otherwise, mirroring `provider_override`'s own override-a-default
    shape."""


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
    interaction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """Total interactions ever, not just recent ones -- gates the extreme
    stances (`STANCE_MIN_INTERACTIONS_EXTREME`): a handful of run-ins
    can dislike someone, but hating/loving them takes a real history."""

    stance: Mapped[str] = mapped_column(String(16), nullable=False, default=Stance.NEUTRAL.value)
    stance_updated_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)

    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    """A compacted, running recap of every engagement `subject` and `object`
    have had together -- `dialogue.summarize_engagement` folds each closed
    engagement's transcript into an updated version of this on top of
    whatever it already said, so it stays roughly `RELATIONSHIP_SUMMARY_
    MAX_WORDS` long rather than growing without bound. Read back into the
    `[SPEAKER] ... known` field of the next dialogue request between this
    same pair, so the NPC keeps a real memory of them across days, bot
    restarts, and any number of separate conversations -- not just the
    fact-bullet `Memory` rows the sim itself forms from notable events."""


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
    crackdown_until_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """A moderator-triggered peacekeeper crackdown (`/staff district
    crackdown`) -- while set and in the future, every illicit-activity
    probability roll in the district (market/black-market detection,
    illicit-work arrest evasion, stealing, lock-picking) is scaled harder,
    and `dialogue.py` surfaces it as NPC nervousness. Not a new decay
    mechanism of its own: `/staff district crackdown` also spikes
    `peacekeeper_pressure` directly, which `crisis.py` already relaxes
    back toward baseline once the crackdown window passes."""
    crisis_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    crisis_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    capitol_favor: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    victor_bonus_until: Mapped[int | None] = mapped_column(Integer, nullable=True)
    treasury: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class WorldClock(Base):
    """Single-row global tick counter (Plan §4, FR-TCK). Not in the
    original Plan §2 schema list -- `district_state.tick`/`world_events.tick`
    both assume a shared tick numbering scheme, but nothing actually
    persisted *the* current tick until now; this is that source of truth,
    read and incremented once per tick by `panem_sim.systems.time`."""

    __tablename__ = "world_clock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    tick: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )
    """Wall-clock time this row's `tick` was committed; lets a display
    (e.g. `/time`) compute real seconds remaining until the next tick from
    `tick_interval_seconds`, without the bot needing to talk to the sim
    process directly."""


class EngagementSettings(Base):
    """Single-row staff-tunable settings for NPC engagements, mirroring
    `WorldClock`'s singleton shape. A code constant would need a redeploy
    to change; `/staff engagement set-timeout` edits this row directly, and
    `EngagementCog`'s idle-close background task reads it fresh every
    pass, so a change takes effect on the very next check."""

    __tablename__ = "engagement_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    idle_timeout_minutes: Mapped[int] = mapped_column(Integer, nullable=False)


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
    started_at_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Set when `/work` launches this shift's minigame (Plan §5's "paid if
    you started before the shift ended" rule) -- `panem_sim.systems.jobs`
    won't mark a shift missed while this is set and it's still within
    `WORK_GAME_GRACE_TICKS` of `tick_due`, giving the player time to
    actually finish the game after the shift's nominal deadline."""
    last_worked_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """The tick of this shift's most recent resolved `/work` action.
    `panem_shared.shifts.apply_shift_outcome` no longer closes a shift the
    instant it's worked -- a shift stays open (`result IS NULL`) for its
    whole `tick_opened`..`tick_due` window so a player can work it again on
    a later tick, capped at one resolution per tick by comparing the
    current tick against this column. It also marks whether the shift has
    ever been worked at all (`None` = never), which is what
    `apply_shift_outcome` checks to only credit `shifts_completed` once per
    shift no matter how many ticks it was worked, and what
    `panem_sim.systems.jobs._resolve_missed_shifts` checks to close a
    worked shift as COMPLETED rather than MISSED once `tick_due` passes."""


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


class Property(Base):
    """A purchasable/rentable house, apartment unit, or inn (housing
    system). `district_id` is a plain content-referencing column (no FK,
    same convention as `Character.district_id` -- districts live in
    content YAML, not a DB table).

    `tier` is a `JobLevel.value` gating who may *buy* the property --
    meaningful for a house (a character's job level must be at or above
    it, per `panem_bot.services.housing`), a fixed placeholder for an
    apartment or inn, neither of which is tier-gated.

    `complex_id` groups every `APARTMENT`-kind unit in the same building;
    owning every unit sharing one `complex_id` is what makes a character
    that building's landlord (computed on the fly from ownership, not a
    stored flag -- nothing here directly represents "is a landlord").

    `asking_price` is `None` until a seller (or staff) overrides
    `suggested_price` -- `panem_bot.services.housing.quoted_price` reads
    whichever is set. It doubles as more than a sale price depending on
    `kind`: an apartment unit's listed rent when vacant, or an inn's
    price per night -- documented here rather than adding a column per
    kind for what's ultimately one "current listed price" concept.

    `mortgage_principal`/`mortgage_payment`/`mortgage_next_due_tick`/
    `mortgage_missed_payments` track a financed purchase's remaining
    installments. For an inn, the same four fields double as its daily
    maintenance-due tracking (`mortgage_payment` = the daily maintenance
    cost, `mortgage_principal` unused) -- one collection/foreclosure loop
    in `panem_sim.systems.housing` handles both rather than two parallel
    mechanisms for "can't afford the payment".

    `location_id` (contraband system) is only ever set for a `HOUSE` --
    `panem_sim.world.seed_properties` assigns one of the district's
    `residential` (or, failing that, `public`) locations at seeding time.
    `/burgle`'s "nobody's home" check reads whether the owner's own
    `Character.location_id` currently matches it; `None` (every property
    seeded before this column existed, and every non-house kind) just
    skips that check rather than refusing every burglary."""

    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    district_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    complex_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    owner_kind: Mapped[str] = mapped_column(String(16), nullable=False, default=OwnerKind.NPC.value)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"), nullable=True)

    for_sale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    suggested_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    asking_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    mortgage_principal: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    mortgage_payment: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    mortgage_next_due_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mortgage_missed_payments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at_tick: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ApartmentLease(Base):
    """The one active tenancy on an `APARTMENT`-kind `Property` -- vacating
    (voluntary move-out or an eviction past `RENT_MISSES_TO_EVICT`)
    deletes this row rather than keeping lease history, so a unique
    `property_id` is enough to guarantee one active lease per unit."""

    __tablename__ = "apartment_leases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id"), nullable=False, index=True
    )
    tenant_character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id"), nullable=False, index=True
    )
    rent_price: Mapped[float] = mapped_column(Float, nullable=False)
    started_tick: Mapped[int] = mapped_column(Integer, nullable=False)
    next_rent_due_tick: Mapped[int] = mapped_column(Integer, nullable=False)
    missed_payments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("property_id", name="uq_apartment_lease_one_per_unit"),)


class PropertyAuction(Base):
    """A `Property` listed for bidding -- either voluntary (`seller_kind`
    is a real `OwnerKind`) or the automatic foreclosure fallback
    (`seller_kind="bank"`, no `seller_id`) `panem_sim.systems.housing`
    creates when a mortgage/rent/maintenance payment is missed too many
    times."""

    __tablename__ = "property_auctions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id"), nullable=False, index=True
    )
    seller_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    seller_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"), nullable=True)
    minimum_bid: Mapped[float] = mapped_column(Float, nullable=False)
    current_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_bidder_id: Mapped[int | None] = mapped_column(
        ForeignKey("characters.id"), nullable=True
    )
    ends_at_tick: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")


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
    last_message_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
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
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    edited_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
