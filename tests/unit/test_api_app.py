from __future__ import annotations

import datetime as dt
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from panem_api import discord_staff
from panem_api.app import create_app
from panem_shared import constants, simtime
from panem_shared.constants import TRANSIT_TICKS
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    Good,
    Job,
    JobOption,
    Location,
    NpcContent,
)
from panem_shared.db.models import (
    AfflictionType,
    ApartmentLease,
    Character,
    DistrictState,
    EngagementSettings,
    Inventory,
    LayerCategory,
    LayerOption,
    MarketPrice,
    Npc,
    Property,
    PropertyAuction,
    RelationshipRow,
    Scene,
    Shift,
    StaffAction,
    Trade,
    User,
)
from panem_shared.enums import (
    CharacterStatus,
    DayPhase,
    JobLevel,
    OwnerKind,
    Position,
    PropertyKind,
    SceneKind,
    SceneStatus,
    Stance,
)
from panem_shared.redis_keys import (
    CHARACTER_PENDING_CHANNEL,
    crime_attempt_key,
    crime_interaction_key,
    work_interaction_key,
    work_pending_key,
)
from panem_shared.relationships import relationship_key


class FakeRedis:
    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = store or {}
        self.published: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 0

    async def aclose(self) -> None:
        pass


def make_district(district_id: int, name: str) -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
    ]
    coords = {"square": (0, 0), "station": (10, 10)}
    return District(
        id=district_id,
        name=name,
        industry="x",
        produces=[],
        imports=[],
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=100, height=100, location_coords=coords),
    )


def make_content() -> ContentBundle:
    return ContentBundle(
        districts={
            0: make_district(0, "The Capitol"),
            1: make_district(1, "District 1"),
        },
        goods={},
        jobs={},
        routes=[],
    )


def make_job() -> Job:
    return Job(
        id="miner",
        district=1,
        title="Miner",
        workplace="mine",
        wage=10.0,
        produces={"coal": 5.0},
        shift_phase="morning",
        slots=5,
        options=[JobOption(label="a"), JobOption(label="b"), JobOption(label="c")],
    )


def make_stolen_goods() -> dict[str, Good]:
    """Every id `STEAL_LOOT_GOOD_IDS`/`BURGLE_LOOT_GOOD_IDS` can pick --
    `apply_steal_outcome`/`apply_burgle_outcome` look these up by id on a
    successful attempt, so any content fixture a steal/burgle test uses
    needs them present (mirrors `test_stealing_service.py`'s own
    `make_goods()`)."""
    good_ids = set(constants.STEAL_LOOT_GOOD_IDS) | set(constants.BURGLE_LOOT_GOOD_IDS)
    return {
        good_id: Good(id=good_id, name=good_id.replace("_", " ").title(), base_price=10.0, category="stolen")
        for good_id in good_ids
    }


def make_content_with_job() -> ContentBundle:
    return ContentBundle(
        districts={0: make_district(0, "The Capitol"), 1: make_district(1, "District 1")},
        goods=make_stolen_goods(),
        jobs={"miner": make_job()},
        routes=[],
    )


def make_content_with_outskirts() -> ContentBundle:
    """Same shape as `make_content_with_job`, plus a district-1 `outskirts`
    location and a `food`-category good, for `/activity/dashboard/crime/
    poach` and `/activity/crime/*` poach tests (`check_can_poach`/
    `apply_poach_outcome` need both to find anything to yield)."""
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
        Location(id="outskirts", name="The Outskirts", kind="outskirts"),
    ]
    coords = {loc.id: (0, 0) for loc in locations}
    district_1 = District(
        id=1,
        name="District 1",
        industry="x",
        produces=["grain"],
        imports=[],
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=100, height=100, location_coords=coords),
    )
    return ContentBundle(
        districts={0: make_district(0, "The Capitol"), 1: district_1},
        goods={
            "grain": Good(id="grain", name="Grain", base_price=1.0, category="food"),
            constants.POACH_GOOD_ID: Good(
                id=constants.POACH_GOOD_ID,
                name="Wild Game",
                base_price=20.0,
                category="food",
                hunger_value=35.0,
            ),
        },
        jobs={"miner": make_job()},
        routes=[],
    )


def make_content_with_residence() -> ContentBundle:
    """Same shape as `make_content_with_outskirts`, plus a district-1
    `home` location of kind `residential` -- for the Residents directory's
    "sleeping" status inference (`dashboard_routes._character_status`),
    which reads a character's current location's `kind` rather than any
    dedicated "is asleep" flag (`/sleep` has none; see that function's own
    docstring)."""
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
        Location(id="home", name="Victors' Village", kind="residential"),
    ]
    coords = {loc.id: (0, 0) for loc in locations}
    district_1 = District(
        id=1,
        name="District 1",
        industry="x",
        produces=[],
        imports=[],
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=100, height=100, location_coords=coords),
    )
    return ContentBundle(
        districts={0: make_district(0, "The Capitol"), 1: district_1},
        goods={},
        jobs={},
        routes=[],
    )


async def seed_shift(
    session_factory,
    *,
    character_overrides: dict[str, object] | None = None,
    **shift_overrides: object,
) -> int:
    async with session_factory() as session, session.begin():
        user = User(discord_id=42)
        session.add(user)
        await session.flush()
        character_kwargs: dict[str, object] = dict(
            user_id=user.id,
            district_id=1,
            current_district_id=1,
            name="Wren",
            age=20,
            status=CharacterStatus.APPROVED.value,
            money=0,
            job_title="Miner",
            shift_phase="morning",
        )
        character_kwargs.update(character_overrides or {})
        character = Character(**character_kwargs)  # type: ignore[arg-type]
        session.add(character)
        await session.flush()
        shift = Shift(
            character_id=character.id,
            job_id="miner",
            tick_opened=0,
            tick_due=6,
            **shift_overrides,
        )
        session.add(shift)
        await session.flush()
        return shift.id


async def seed_character(
    session_factory, *, discord_id: int = 42, character_overrides: dict[str, object] | None = None
) -> int:
    async with session_factory() as session, session.begin():
        existing = await session.execute(select(User).where(User.discord_id == discord_id))
        user = existing.scalar_one_or_none()
        if user is None:
            user = User(discord_id=discord_id)
            session.add(user)
            await session.flush()
        character_kwargs: dict[str, object] = dict(
            user_id=user.id,
            district_id=1,
            current_district_id=1,
            name="Wren",
            age=20,
            status=CharacterStatus.APPROVED.value,
            money=100,
            location_id="square",
        )
        character_kwargs.update(character_overrides or {})
        character = Character(**character_kwargs)  # type: ignore[arg-type]
        session.add(character)
        await session.flush()
        return character.id


async def seed_layer_category(
    session_factory, *, name: str = "Hair", z_index: int = 0, option_names: list[str] | None = None
) -> tuple[int, list[int]]:
    async with session_factory() as session, session.begin():
        category = LayerCategory(name=name, z_index=z_index)
        session.add(category)
        await session.flush()
        option_ids = []
        for option_name in option_names or []:
            option = LayerOption(
                category_id=category.id, name=option_name, image_path=f"uploads/layers/{option_name}.png"
            )
            session.add(option)
            await session.flush()
            option_ids.append(option.id)
        return category.id, option_ids


async def seed_npc(session_factory, **overrides: object) -> str:
    async with session_factory() as session, session.begin():
        npc_kwargs: dict[str, object] = dict(
            id="d1_npc_1", district_id=1, name="Mark", age=30, money=50.0, location_id="square"
        )
        npc_kwargs.update(overrides)
        session.add(Npc(**npc_kwargs))  # type: ignore[arg-type]
        return npc_kwargs["id"]  # type: ignore[return-value]


async def seed_house(session_factory, *, owner_id: int, **overrides: object) -> int:
    async with session_factory() as session, session.begin():
        house_kwargs: dict[str, object] = dict(
            district_id=1,
            kind=PropertyKind.HOUSE.value,
            tier="apprentice",
            owner_kind=OwnerKind.CHARACTER.value,
            owner_id=owner_id,
            for_sale=False,
            suggested_price=1000.0,
        )
        house_kwargs.update(overrides)
        house = Property(**house_kwargs)  # type: ignore[arg-type]
        session.add(house)
        await session.flush()
        return house.id


async def seed_property(session_factory, **overrides: object) -> int:
    """An NPC-owned, for-sale house by default -- unlike `seed_house`
    (a character's own property), this is what a dashboard housing test
    buys/rents from scratch."""
    async with session_factory() as session, session.begin():
        kwargs: dict[str, object] = dict(
            district_id=1,
            kind=PropertyKind.HOUSE.value,
            tier="apprentice",
            owner_kind=OwnerKind.NPC.value,
            owner_id=None,
            for_sale=True,
            suggested_price=1000.0,
        )
        kwargs.update(overrides)
        property_ = Property(**kwargs)  # type: ignore[arg-type]
        session.add(property_)
        await session.flush()
        return property_.id


@pytest.fixture
def client() -> TestClient:
    content = make_content()
    redis_client = FakeRedis()
    app = create_app(content=content, redis_client=redis_client)
    app.state.fake_redis = redis_client  # type: ignore[attr-defined]
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def oauth_client() -> TestClient:
    content = make_content()
    redis_client = FakeRedis()
    app = create_app(
        content=content,
        redis_client=redis_client,
        discord_client_id="test-client-id",
        discord_client_secret="test-client-secret",
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def work_app(db_session_factory):
    content = make_content_with_job()
    redis_client = FakeRedis()
    app = create_app(content=content, redis_client=redis_client, session_factory=db_session_factory)
    app.state.fake_redis = redis_client  # type: ignore[attr-defined]
    return app


@pytest.fixture
def work_app_with_avatar_uploads(db_session_factory, tmp_path):
    """Same as `work_app`, but with `static_dir`/`activity_public_url` set
    up the same way `staff_app_with_uploads` is for the layer-catalog
    uploads -- the avatar-upload endpoint writes a real file to disk and
    needs a public base URL to build the stored `avatar_url` from."""
    content = make_content_with_job()
    redis_client = FakeRedis()
    app = create_app(
        content=content,
        redis_client=redis_client,
        session_factory=db_session_factory,
        static_dir=tmp_path,
        activity_public_url="https://example.com",
    )
    app.state.fake_redis = redis_client  # type: ignore[attr-defined]
    return app


@pytest.fixture
def poach_app(db_session_factory):
    content = make_content_with_outskirts()
    redis_client = FakeRedis()
    app = create_app(content=content, redis_client=redis_client, session_factory=db_session_factory)
    app.state.fake_redis = redis_client  # type: ignore[attr-defined]
    return app


def make_content_with_market() -> ContentBundle:
    """District 1 trades `grain` (imported) legally and `contraband` on
    the black market, with `fence` as its fence NPC -- for `/activity/
    dashboard/market` and `.../blackmarket` tests. `legal_market` (not
    `illicit`) is for the legal-market tests; `market` (`illicit=True`)
    stays only so `market.py`'s own illicit-detection-at-a-flagged-legal-
    location tests have somewhere separate from `legal_market` to use
    (sharing one location between them would expose the legal tests to
    `buy`/`sell`'s own illicit-detection roll -- `market.py::_roll_
    illicit_detection` fires for *any* `illicit` location, not just
    illicit goods -- flaking a fine onto an otherwise-deterministic legal
    trade). The dedicated black-market tests below use `outskirts`
    instead -- `resolve_black_market_location` requires it, and it's the
    only place `/blackmarket` can be reached from at all now."""
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
        Location(id="legal_market", name="The Market", kind="market"),
        Location(id="market", name="The Underground Market", kind="market", illicit=True),
        Location(id="outskirts", name="The Outskirts", kind="outskirts"),
    ]
    coords = {loc.id: (0, 0) for loc in locations}
    district_1 = District(
        id=1,
        name="District 1",
        industry="x",
        produces=[],
        imports=["grain"],
        illicit_produces=["contraband"],
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=100, height=100, location_coords=coords),
    )
    return ContentBundle(
        districts={0: make_district(0, "The Capitol"), 1: district_1},
        goods={
            "grain": Good(
                id="grain",
                name="Grain",
                base_price=2.0,
                category="food",
                hunger_value=15.0,
                cook_method="oven",
            ),
            "contraband": Good(
                id="contraband", name="Contraband", base_price=10.0, category="illicit"
            ),
        },
        jobs={},
        routes=[],
        npcs={
            "fence": NpcContent(
                id="fence",
                district=1,
                name="Sal",
                age=40,
                home_location_id="square",
                backstory="",
                black_market_contact=True,
            )
        },
    )


@pytest.fixture
def market_app(db_session_factory):
    content = make_content_with_market()
    redis_client = FakeRedis()
    app = create_app(content=content, redis_client=redis_client, session_factory=db_session_factory)
    app.state.fake_redis = redis_client  # type: ignore[attr-defined]
    return app


def make_content_with_vitals() -> ContentBundle:
    """`grain` (cookable/bakeable, `hunger_value=15`, `cook_method="oven"`)
    and `produce` (drinkable, `thirst_value=25`) -- for `/activity/
    dashboard/vitals` tests. `coal` stays a pure ingredient (no consumption
    value) so an "owned but not edible/drinkable" good is available too."""
    return ContentBundle(
        districts={0: make_district(0, "The Capitol"), 1: make_district(1, "District 1")},
        goods={
            "grain": Good(
                id="grain",
                name="Grain",
                base_price=2.0,
                category="food",
                hunger_value=15.0,
                cook_method="oven",
            ),
            "produce": Good(
                id="produce",
                name="Fruits/Drinks",
                base_price=3.0,
                category="food",
                thirst_value=25.0,
            ),
            "coal": Good(id="coal", name="Coal", base_price=1.0, category="fuel"),
        },
        jobs={},
        routes=[],
    )


@pytest.fixture
def vitals_app(db_session_factory):
    content = make_content_with_vitals()
    redis_client = FakeRedis()
    app = create_app(content=content, redis_client=redis_client, session_factory=db_session_factory)
    app.state.fake_redis = redis_client  # type: ignore[attr-defined]
    return app


@pytest.fixture
def travel_app(db_session_factory):
    """`make_content()`'s two districts (0, 1), each with a `square` and a
    `station` location, is already exactly what the Travel tab's tests
    need -- no dedicated content fixture required."""
    content = make_content()
    redis_client = FakeRedis()
    app = create_app(content=content, redis_client=redis_client, session_factory=db_session_factory)
    app.state.fake_redis = redis_client  # type: ignore[attr-defined]
    return app


@pytest.fixture
def social_app(db_session_factory):
    content = make_content()
    redis_client = FakeRedis()
    app = create_app(
        content=content,
        redis_client=redis_client,
        session_factory=db_session_factory,
        discord_guild_id=999,
    )
    app.state.fake_redis = redis_client  # type: ignore[attr-defined]
    return app


async def seed_scene(session_factory, **overrides: object) -> int:
    async with session_factory() as session, session.begin():
        scene_kwargs: dict[str, object] = dict(
            district_id=1,
            location_id="square",
            thread_id=555,
            forum_channel_id=1,
            kind=SceneKind.ENGAGEMENT.value,
            title="A Chat",
            status=SceneStatus.OPEN.value,
            participants={"characters": [], "pending_characters": [], "npcs": []},
        )
        scene_kwargs.update(overrides)
        scene = Scene(**scene_kwargs)  # type: ignore[arg-type]
        session.add(scene)
        await session.flush()
        return scene.id


@pytest.fixture
def housing_app(db_session_factory):
    content = make_content()
    redis_client = FakeRedis()
    app = create_app(content=content, redis_client=redis_client, session_factory=db_session_factory)
    app.state.fake_redis = redis_client  # type: ignore[attr-defined]
    return app


@pytest.fixture
def staff_app(db_session_factory):
    """`log_channel_id=0` deliberately leaves the best-effort Discord log
    post disabled by default (`post_staff_log` short-circuits on a falsy
    channel id) -- most tests below only care about the jail action itself
    and would otherwise need every one of them to mock `httpx.AsyncClient.
    post` just to avoid a real (sandboxed, so failing) network call. The
    one test that cares about the log post configures its own app."""
    content = make_content()
    redis_client = FakeRedis()
    app = create_app(
        content=content,
        redis_client=redis_client,
        session_factory=db_session_factory,
        discord_guild_id=999,
        discord_token="test-bot-token",
        staff_role_id=777,
        log_channel_id=0,
    )
    app.state.fake_redis = redis_client  # type: ignore[attr-defined]
    return app


@pytest.fixture
def staff_market_app(db_session_factory):
    """Same shape as `staff_app`, but with `make_content_with_market()`'s
    goods/districts -- for staff endpoints (`add-stock`) that need a real
    good/district to act on, which `staff_app`'s own empty-goods content
    doesn't provide."""
    content = make_content_with_market()
    redis_client = FakeRedis()
    app = create_app(
        content=content,
        redis_client=redis_client,
        session_factory=db_session_factory,
        discord_guild_id=999,
        discord_token="test-bot-token",
        staff_role_id=777,
        log_channel_id=0,
    )
    app.state.fake_redis = redis_client  # type: ignore[attr-defined]
    return app


def _fake_member_response(role_ids: list[int]) -> httpx.Response:
    return httpx.Response(200, json={"roles": [str(r) for r in role_ids]})


@pytest.fixture
def donor_app(db_session_factory):
    """Same shape as `staff_app`, but configured with `donor_role_ids`
    instead of `staff_role_id` -- for the dashboard theme (color-wheel)
    tests. `888` is this fixture's donor role id, mirroring `staff_app`'s
    `777` for the staff role."""
    content = make_content()
    redis_client = FakeRedis()
    app = create_app(
        content=content,
        redis_client=redis_client,
        session_factory=db_session_factory,
        discord_guild_id=999,
        discord_token="test-bot-token",
        donor_role_ids=frozenset({888}),
    )
    app.state.fake_redis = redis_client  # type: ignore[attr-defined]
    return app


@pytest.fixture
def staff_app_with_uploads(db_session_factory, tmp_path):
    """Same as `staff_app`, but `static_dir` points at `tmp_path` -- the
    layer-catalog upload tests write real files (`panem_shared.layers`
    saves to disk, not just the DB), and doing that under the checked-out
    `static/` tree would leave test artifacts behind in the real repo."""
    content = make_content()
    redis_client = FakeRedis()
    app = create_app(
        content=content,
        redis_client=redis_client,
        session_factory=db_session_factory,
        discord_guild_id=999,
        discord_token="test-bot-token",
        staff_role_id=777,
        log_channel_id=0,
        static_dir=tmp_path,
    )
    app.state.fake_redis = redis_client  # type: ignore[attr-defined]
    return app


class TestHealth:
    def test_returns_ok(self, client: TestClient):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestListDistricts:
    def test_returns_every_district_sorted_by_id(self, client: TestClient):
        response = client.get("/districts")
        assert response.status_code == 200
        body = response.json()
        assert [d["id"] for d in body] == [0, 1]
        assert [d["name"] for d in body] == ["The Capitol", "District 1"]
        assert body[0]["map_width"] == 100
        assert body[0]["map_height"] == 100
        assert {loc["id"]: (loc["x"], loc["y"]) for loc in body[0]["locations"]} == {
            "square": (0, 0),
            "station": (10, 10),
        }


class TestWorldTime:
    def test_defaults_to_tick_zero_when_no_db_configured(self, client: TestClient):
        response = client.get("/world/time")
        assert response.status_code == 200
        assert response.json() == {
            "day": 1,
            "month": 1,
            "year": 1,
            "time": simtime.clock_string(0),
            "phase": DayPhase.NIGHT.value,
        }

    async def test_reflects_the_persisted_world_clock(self, work_app, db_session_factory):
        from panem_shared.db.models import WorldClock

        async with db_session_factory() as session, session.begin():
            session.add(WorldClock(id=1, tick=1000))
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/world/time")
        assert response.status_code == 200
        _tick, phase, day, month = simtime.current(1000)
        assert response.json() == {
            "day": day,
            "month": month,
            "year": simtime.year_for(1000),
            "time": simtime.clock_string(1000),
            "phase": phase.value,
        }


class TestDistrictPositions:
    def test_returns_empty_when_nothing_published_yet(self, client: TestClient):
        response = client.get("/districts/1/positions")
        assert response.status_code == 200
        assert response.json() == {"npcs": [], "characters": []}

    def test_returns_published_data(self, client: TestClient):
        client.app.state.fake_redis.store["pos:1"] = json.dumps(
            {
                "npcs": [
                    {"id": "d1_npc_001", "name": "Ada", "x": 1.0, "y": 2.0, "location_id": "square"}
                ],
                "characters": [],
            }
        )

        response = client.get("/districts/1/positions")

        assert response.status_code == 200
        body = response.json()
        assert body["npcs"][0]["id"] == "d1_npc_001"

    def test_404s_for_an_unknown_district(self, client: TestClient):
        response = client.get("/districts/99/positions")
        assert response.status_code == 404


class TestActivityDebug:
    def test_accepts_and_acknowledges_a_report(self, client: TestClient):
        response = client.post(
            "/activity/debug",
            json={"step": "commands.authorize()", "message": "boom", "stack": "trace"},
        )
        assert response.status_code == 200
        assert response.json() == {"logged": True}

    def test_stack_is_optional(self, client: TestClient):
        response = client.post(
            "/activity/debug", json={"step": "discordSdk.ready()", "message": "timed out"}
        )
        assert response.status_code == 200


class TestActivityConfig:
    def test_returns_empty_client_id_when_unconfigured(self, client: TestClient):
        response = client.get("/activity/config")
        assert response.status_code == 200
        assert response.json() == {"client_id": ""}

    def test_returns_configured_client_id(self, oauth_client: TestClient):
        response = oauth_client.get("/activity/config")
        assert response.status_code == 200
        assert response.json() == {"client_id": "test-client-id"}


class TestActivityToken:
    def test_503s_when_oauth_not_configured(self, client: TestClient):
        response = client.post("/activity/token", json={"code": "abc"})
        assert response.status_code == 503

    def test_exchanges_code_for_access_token(self, oauth_client: TestClient):
        fake_response = httpx.Response(200, json={"access_token": "the-token"})
        with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=fake_response)):
            response = oauth_client.post("/activity/token", json={"code": "abc"})
        assert response.status_code == 200
        assert response.json() == {"access_token": "the-token"}

    def test_502s_when_discord_rejects_the_code(self, oauth_client: TestClient):
        fake_response = httpx.Response(400, json={"error": "invalid_grant"})
        with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=fake_response)):
            response = oauth_client.post("/activity/token", json={"code": "bad"})
        assert response.status_code == 502

    def test_502s_when_discord_is_unreachable(self, oauth_client: TestClient):
        with patch.object(
            httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ConnectError("boom"))
        ):
            response = oauth_client.post("/activity/token", json={"code": "abc"})
        assert response.status_code == 502


class TestActivityFrontend:
    def test_serves_the_frontend_at_root(self, client: TestClient):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_serves_app_js(self, client: TestClient):
        response = client.get("/app.js")
        assert response.status_code == 200

    def test_serves_the_vendored_discord_sdk_not_a_cdn_url(self, client: TestClient):
        """The Activity frontend must not depend on a CDN being reachable
        at runtime (see the README's Activity-frontend notes) -- app.js
        imports the SDK from this same-origin path."""
        app_js = client.get("/app.js").text
        assert 'DISCORD_SDK_URL = "/vendor/discord-embedded-app-sdk.js"' in app_js
        response = client.get("/vendor/discord-embedded-app-sdk.js")
        assert response.status_code == 200
        assert "DiscordSDK" in response.text

    def test_api_routes_still_take_priority_over_the_static_mount(self, client: TestClient):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestDistrictPositionsWebSocket:
    def test_sends_an_initial_snapshot(self, client: TestClient):
        client.app.state.fake_redis.store["pos:1"] = json.dumps(
            {"npcs": [{"id": "d1_npc_001"}], "characters": []}
        )

        with client.websocket_connect("/ws/districts/1/positions") as websocket:
            payload = websocket.receive_json()

        assert payload["npcs"][0]["id"] == "d1_npc_001"

    def test_closes_for_an_unknown_district(self, client: TestClient):
        with (
            pytest.raises(Exception),  # noqa: B017 -- starlette raises on the 4004 close
            client.websocket_connect("/ws/districts/99/positions") as websocket,
        ):
            websocket.receive_json()


class TestWorkPendingShift:
    async def test_404_when_nothing_pending_for_the_channel(self):
        app = create_app(content=make_content_with_job(), redis_client=FakeRedis())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/work/for-channel/999")
        assert response.status_code == 404

    async def test_returns_the_pending_shift_id(self):
        redis_client = FakeRedis({work_pending_key(42): "7"})
        app = create_app(content=make_content_with_job(), redis_client=redis_client)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/work/for-channel/42")
        assert response.status_code == 200
        assert response.json() == {"shift_id": 7}


class TestWorkShiftStatus:
    async def test_503s_when_not_configured(self):
        app = create_app(content=make_content_with_job(), redis_client=FakeRedis())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/work/1")
        assert response.status_code == 503

    async def test_404s_for_an_unknown_shift(self, work_app):
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/work/999999")
        assert response.status_code == 404

    async def test_returns_job_and_character_info(self, work_app, db_session_factory):
        shift_id = await seed_shift(db_session_factory)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/activity/work/{shift_id}")
        assert response.status_code == 200
        assert response.json() == {
            "job_title": "Miner",
            "character_name": "Wren",
            "already_resolved": False,
            "already_worked_this_tick": False,
            "level": "apprentice",
        }

    async def test_shift_stays_open_once_worked_since_it_spans_many_ticks(
        self, work_app, db_session_factory
    ):
        shift_id = await seed_shift(db_session_factory)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(f"/activity/work/{shift_id}/result", json={"won": True})
            response = await client.get(f"/activity/work/{shift_id}")
        assert response.json()["already_resolved"] is False

    async def test_already_worked_this_tick_is_true_right_after_working(
        self, work_app, db_session_factory
    ):
        shift_id = await seed_shift(db_session_factory)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(f"/activity/work/{shift_id}/result", json={"won": True})
            response = await client.get(f"/activity/work/{shift_id}")
        assert response.json()["already_worked_this_tick"] is True

    async def test_level_reflects_the_characters_shifts_completed(
        self, work_app, db_session_factory
    ):
        shift_id = await seed_shift(
            db_session_factory, character_overrides={"shifts_completed": 84}
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/activity/work/{shift_id}")
        assert response.json()["level"] == "journeyman"


class TestWorkShiftResult:
    async def test_503s_when_not_configured(self):
        app = create_app(content=make_content_with_job(), redis_client=FakeRedis())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/activity/work/1/result", json={"won": True})
        assert response.status_code == 503

    async def test_win_pays_the_win_multiplier(self, work_app, db_session_factory):
        shift_id = await seed_shift(db_session_factory)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/activity/work/{shift_id}/result", json={"won": True})
        assert response.status_code == 200
        assert response.json() == {
            "wage": 7,
            "won": True,
            "character_name": "Wren",
            "leveled_up": False,
            "level": "apprentice",
            "arrested": False,
        }

    async def test_refuses_while_away_from_home_district(self, work_app, db_session_factory):
        shift_id = await seed_shift(
            db_session_factory,
            character_overrides={"district_id": 1, "current_district_id": 2},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/activity/work/{shift_id}/result", json={"won": True})
        assert response.status_code == 400

    async def test_allows_a_gamemaker_resolving_a_shift_away_from_home(
        self, work_app, db_session_factory
    ):
        shift_id = await seed_shift(
            db_session_factory,
            character_overrides={
                "district_id": 1,
                "current_district_id": 2,
                "positions": ["gamemaker"],
            },
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/activity/work/{shift_id}/result", json={"won": True})
        assert response.status_code == 200

    async def test_does_not_call_discord_when_no_launch_message_was_stashed(
        self, work_app, db_session_factory
    ):
        shift_id = await seed_shift(db_session_factory)
        transport = httpx.ASGITransport(app=work_app)
        with patch.object(httpx.AsyncClient, "patch", AsyncMock()) as mock_patch:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/activity/work/{shift_id}/result", json={"won": True}
                )
        assert response.status_code == 200
        mock_patch.assert_not_called()

    async def test_edits_the_launch_message_and_forgets_it(self, work_app, db_session_factory):
        shift_id = await seed_shift(db_session_factory)
        redis_client = work_app.state.fake_redis
        redis_client.store[work_interaction_key(shift_id)] = json.dumps(
            {"application_id": "111", "token": "tok"}
        )
        transport = httpx.ASGITransport(app=work_app)
        fake_response = httpx.Response(200, json={})
        with patch.object(
            httpx.AsyncClient, "patch", AsyncMock(return_value=fake_response)
        ) as mock_patch:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/activity/work/{shift_id}/result", json={"won": True}
                )
        assert response.status_code == 200
        mock_patch.assert_called_once()
        url, kwargs = mock_patch.call_args.args, mock_patch.call_args.kwargs
        assert "111" in url[0] and "tok" in url[0]
        assert kwargs["json"]["components"] == []
        assert work_interaction_key(shift_id) not in redis_client.store

    async def test_a_failed_discord_edit_does_not_fail_the_response(
        self, work_app, db_session_factory
    ):
        shift_id = await seed_shift(db_session_factory)
        redis_client = work_app.state.fake_redis
        redis_client.store[work_interaction_key(shift_id)] = json.dumps(
            {"application_id": "111", "token": "expired"}
        )
        transport = httpx.ASGITransport(app=work_app)
        with patch.object(
            httpx.AsyncClient, "patch", AsyncMock(side_effect=httpx.ConnectError("boom"))
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/activity/work/{shift_id}/result", json={"won": True}
                )
        assert response.status_code == 200

    async def test_loss_pays_the_loss_multiplier(self, work_app, db_session_factory):
        shift_id = await seed_shift(db_session_factory)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/activity/work/{shift_id}/result", json={"won": False})
        assert response.status_code == 200
        assert response.json()["wage"] == 2

    async def test_reports_leveling_up(self, work_app, db_session_factory):
        from panem_shared import constants

        shift_id = await seed_shift(
            db_session_factory,
            character_overrides={
                "shifts_completed": constants.JOB_LEVEL_SHIFT_THRESHOLDS["novice"] - 1
            },
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/activity/work/{shift_id}/result", json={"won": True})
        assert response.status_code == 200
        assert response.json()["leveled_up"] is True
        assert response.json()["level"] == "novice"

    async def test_409s_if_already_worked_this_tick(self, work_app, db_session_factory):
        shift_id = await seed_shift(db_session_factory)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(f"/activity/work/{shift_id}/result", json={"won": True})
            response = await client.post(f"/activity/work/{shift_id}/result", json={"won": True})
        assert response.status_code == 409
        assert "tick" in response.json()["detail"]

    async def test_404s_for_an_unknown_shift(self, work_app):
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/activity/work/999999/result", json={"won": True})
        assert response.status_code == 404

    async def test_can_work_the_same_shift_again_on_a_later_tick(
        self, work_app, db_session_factory
    ):
        from panem_shared.db.models import WorldClock

        shift_id = await seed_shift(db_session_factory)
        async with db_session_factory() as session, session.begin():
            session.add(WorldClock(id=1, tick=0))
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(f"/activity/work/{shift_id}/result", json={"won": True})
            async with db_session_factory() as session, session.begin():
                clock = await session.get(WorldClock, 1)
                clock.tick = 1
            second = await client.post(f"/activity/work/{shift_id}/result", json={"won": True})
        assert first.status_code == 200
        assert second.status_code == 200

    async def test_shifts_completed_only_counts_once_across_multiple_ticks_worked(
        self, work_app, db_session_factory
    ):
        from panem_shared.db.models import WorldClock

        shift_id = await seed_shift(db_session_factory)
        async with db_session_factory() as session, session.begin():
            session.add(WorldClock(id=1, tick=0))
            shift = await session.get(Shift, shift_id)
            character_id = shift.character_id
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(f"/activity/work/{shift_id}/result", json={"won": True})
            async with db_session_factory() as session, session.begin():
                clock = await session.get(WorldClock, 1)
                clock.tick = 1
            await client.post(f"/activity/work/{shift_id}/result", json={"won": True})
        async with db_session_factory() as session:
            character = await session.get(Character, character_id)
            assert character.shifts_completed == 1


class TestCrimeAttemptStatus:
    async def test_503s_when_not_configured(self):
        app = create_app(content=make_content_with_job(), redis_client=FakeRedis())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/crime/nope")
        assert response.status_code == 503

    async def test_404s_for_an_unknown_attempt(self, work_app):
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/crime/nope")
        assert response.status_code == 404

    async def test_lockpick_status(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory)
        redis_client = work_app.state.fake_redis
        redis_client.store[crime_attempt_key("a1")] = json.dumps(
            {"kind": "lockpick", "character_id": char_id}
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/crime/a1")
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "lockpick"
        assert body["character_name"] == "Wren"
        assert body["target_name"] is None
        assert 0.0 <= body["difficulty"] <= 1.0

    async def test_steal_status_names_the_target(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory)
        npc_id = await seed_npc(db_session_factory, name="Mark")
        redis_client = work_app.state.fake_redis
        redis_client.store[crime_attempt_key("a1")] = json.dumps(
            {
                "kind": "steal",
                "character_id": char_id,
                "district_id": 1,
                "current_tick": 0,
                "victim_kind": "npc",
                "victim_id": npc_id,
            }
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/crime/a1")
        assert response.status_code == 200
        assert response.json()["target_name"] == "Mark"

    async def test_burgle_status(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory)
        other_id = await seed_character(
            db_session_factory, discord_id=99, character_overrides={"name": "Owner"}
        )
        house_id = await seed_house(db_session_factory, owner_id=other_id)
        redis_client = work_app.state.fake_redis
        redis_client.store[crime_attempt_key("a1")] = json.dumps(
            {
                "kind": "burgle",
                "character_id": char_id,
                "property_id": house_id,
                "district_id": 1,
                "current_tick": 0,
            }
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/crime/a1")
        assert response.status_code == 200
        body = response.json()
        assert body["character_name"] == "Wren"
        assert body["target_name"] is None

    async def test_poach_status(self, poach_app, db_session_factory):
        char_id = await seed_character(db_session_factory)
        redis_client = poach_app.state.fake_redis
        redis_client.store[crime_attempt_key("a1")] = json.dumps(
            {
                "kind": "poach",
                "character_id": char_id,
                "district_id": 1,
                "good_id": "grain",
                "current_tick": 0,
            }
        )
        transport = httpx.ASGITransport(app=poach_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/crime/a1")
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "poach"
        assert body["character_name"] == "Wren"
        assert body["target_name"] is None
        assert 0.0 <= body["difficulty"] <= 1.0


class TestCrimeAttemptResult:
    async def test_503s_when_not_configured(self):
        app = create_app(content=make_content_with_job(), redis_client=FakeRedis())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/activity/crime/nope/result", json={"won": True})
        assert response.status_code == 503

    async def test_404s_for_an_unknown_or_already_resolved_attempt(self, work_app):
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/activity/crime/nope/result", json={"won": True})
        assert response.status_code == 404

    async def test_lockpick_win_releases_the_character(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            character_overrides={"jailed_until_tick": 50, "jail_sentence_ticks": 10},
        )
        redis_client = work_app.state.fake_redis
        redis_client.store[crime_attempt_key("a1")] = json.dumps(
            {"kind": "lockpick", "character_id": char_id}
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/activity/crime/a1/result", json={"won": True})
        assert response.status_code == 200
        assert response.json()["success"] is True
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.jailed_until_tick is None
        assert crime_attempt_key("a1") not in redis_client.store

    async def test_lockpick_loss_consumes_a_try(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            character_overrides={
                "jailed_until_tick": 50,
                "jail_sentence_ticks": 10,
                "jail_lockpick_tries_used": 0,
            },
        )
        redis_client = work_app.state.fake_redis
        redis_client.store[crime_attempt_key("a1")] = json.dumps(
            {"kind": "lockpick", "character_id": char_id}
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/activity/crime/a1/result", json={"won": False})
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["tries_left"] == 2
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.jailed_until_tick == 50

    async def test_steal_win_grants_loot_not_money(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, character_overrides={"money": 0})
        npc_id = await seed_npc(db_session_factory, money=50.0)
        redis_client = work_app.state.fake_redis
        redis_client.store[crime_attempt_key("a1")] = json.dumps(
            {
                "kind": "steal",
                "character_id": char_id,
                "district_id": 1,
                "current_tick": 0,
                "victim_kind": "npc",
                "victim_id": npc_id,
            }
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/activity/crime/a1/result", json={"won": True})
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["good_name"] is not None
        assert body["qty"] == constants.STEAL_LOOT_QTY
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.money == 0  # never touched -- the payout is a good, not cash

    async def test_burgle_win_grants_loot_not_money(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, character_overrides={"money": 0})
        other_id = await seed_character(
            db_session_factory, discord_id=99, character_overrides={"name": "Owner"}
        )
        house_id = await seed_house(db_session_factory, owner_id=other_id, suggested_price=1000.0)
        redis_client = work_app.state.fake_redis
        redis_client.store[crime_attempt_key("a1")] = json.dumps(
            {
                "kind": "burgle",
                "character_id": char_id,
                "property_id": house_id,
                "district_id": 1,
                "current_tick": 0,
            }
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/activity/crime/a1/result", json={"won": True})
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["good_name"] is not None
        assert body["qty"] in range(
            constants.BURGLE_LOOT_QTY_RANGE[0], constants.BURGLE_LOOT_QTY_RANGE[1] + 1
        )
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.money == 0

    async def test_poach_win_grants_the_good(self, poach_app, db_session_factory):
        char_id = await seed_character(db_session_factory, character_overrides={"money": 100})
        redis_client = poach_app.state.fake_redis
        redis_client.store[crime_attempt_key("a1")] = json.dumps(
            {
                "kind": "poach",
                "character_id": char_id,
                "district_id": 1,
                "good_id": "grain",
                "current_tick": 0,
            }
        )
        transport = httpx.ASGITransport(app=poach_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("random.Random.random", return_value=0.99):  # not caught
                response = await client.post("/activity/crime/a1/result", json={"won": True})
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["caught"] is False
        assert body["good_name"] == "Grain"
        from panem_shared import constants

        assert body["qty"] == constants.POACH_YIELD_QTY

    async def test_poach_loss_grants_nothing_but_does_not_jail(self, poach_app, db_session_factory):
        char_id = await seed_character(db_session_factory, character_overrides={"money": 100})
        redis_client = poach_app.state.fake_redis
        redis_client.store[crime_attempt_key("a1")] = json.dumps(
            {
                "kind": "poach",
                "character_id": char_id,
                "district_id": 1,
                "good_id": "grain",
                "current_tick": 0,
            }
        )
        transport = httpx.ASGITransport(app=poach_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("random.Random.random", return_value=0.99):  # not caught
                response = await client.post("/activity/crime/a1/result", json={"won": False})
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["caught"] is False
        assert body["good_name"] is None
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.jailed_until_tick is None

    async def test_poach_caught_reports_the_fine_regardless_of_the_shot(
        self, poach_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, character_overrides={"money": 100})
        redis_client = poach_app.state.fake_redis
        redis_client.store[crime_attempt_key("a1")] = json.dumps(
            {
                "kind": "poach",
                "character_id": char_id,
                "district_id": 1,
                "good_id": "grain",
                "current_tick": 0,
            }
        )
        transport = httpx.ASGITransport(app=poach_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("random.Random.random", return_value=0.0):  # caught
                response = await client.post("/activity/crime/a1/result", json={"won": True})
        assert response.status_code == 200
        body = response.json()
        assert body["caught"] is True
        assert body["success"] is False
        assert body["good_name"] is None
        assert body["fine"] > 0

    async def test_one_shot_a_second_post_404s(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            character_overrides={"jailed_until_tick": 50, "jail_sentence_ticks": 10},
        )
        redis_client = work_app.state.fake_redis
        redis_client.store[crime_attempt_key("a1")] = json.dumps(
            {"kind": "lockpick", "character_id": char_id}
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post("/activity/crime/a1/result", json={"won": True})
            second = await client.post("/activity/crime/a1/result", json={"won": True})
        assert first.status_code == 200
        assert second.status_code == 404

    async def test_edits_the_launch_message_and_forgets_it(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            character_overrides={"jailed_until_tick": 50, "jail_sentence_ticks": 10},
        )
        redis_client = work_app.state.fake_redis
        redis_client.store[crime_attempt_key("a1")] = json.dumps(
            {"kind": "lockpick", "character_id": char_id}
        )
        redis_client.store[crime_interaction_key("a1")] = json.dumps(
            {"application_id": "111", "token": "tok"}
        )
        transport = httpx.ASGITransport(app=work_app)
        fake_response = httpx.Response(200, json={})
        with patch.object(
            httpx.AsyncClient, "patch", AsyncMock(return_value=fake_response)
        ) as mock_patch:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post("/activity/crime/a1/result", json={"won": True})
        assert response.status_code == 200
        mock_patch.assert_called_once()
        assert crime_interaction_key("a1") not in redis_client.store


class TestDashboardIdentify:
    async def test_503s_when_not_configured(self, client: TestClient):
        response = client.post("/activity/dashboard/identify", json={"discord_id": 42})
        assert response.status_code == 503

    async def test_unknown_discord_id_returns_no_characters(self, work_app):
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/activity/dashboard/identify", json={"discord_id": 999999}
            )
        assert response.status_code == 200
        assert response.json() == {
            "characters": [],
            "is_staff": False,
            "is_donor": False,
            "theme": {
                "background_hex": "#0a0c10",
                "accent_hex": "#c5a059",
                "panel_hex": "#12161f",
                "text_hex": "#f1f3f7",
                "profile_id": None,
            },
            "theme_profiles": [],
        }

    async def test_is_staff_true_when_discord_reports_the_staff_role(
        self, staff_app, db_session_factory
    ):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/identify", json={"discord_id": 42}
                )
        assert response.status_code == 200
        assert response.json()["is_staff"] is True

    async def test_is_staff_false_without_the_staff_role(self, staff_app, db_session_factory):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1, 2]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/identify", json={"discord_id": 42}
                )
        assert response.status_code == 200
        assert response.json()["is_staff"] is False

    async def test_returns_only_approved_characters_for_that_discord_id(
        self, work_app, db_session_factory
    ):
        approved_id = await seed_character(
            db_session_factory,
            discord_id=42,
            character_overrides={"name": "Wren", "status": CharacterStatus.APPROVED.value},
        )
        await seed_character(
            db_session_factory,
            discord_id=42,
            character_overrides={"name": "Pending One", "status": CharacterStatus.PENDING.value},
        )
        await seed_character(
            db_session_factory,
            discord_id=43,
            character_overrides={"name": "Someone Else", "status": CharacterStatus.APPROVED.value},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/activity/dashboard/identify", json={"discord_id": 42})
        assert response.status_code == 200
        body = response.json()
        assert [c["name"] for c in body["characters"]] == ["Wren"]
        assert body["characters"][0]["id"] == approved_id

    async def test_stale_pre_picrew_appearance_data_does_not_500_the_whole_list(
        self, work_app, db_session_factory
    ):
        # The layer-tables migration renamed appearance_traits ->
        # appearance_layers without touching existing rows' data -- a
        # character customized under the old fixed-palette system still
        # has string-valued junk like {"hair_style": "mohawk"} sitting
        # there, which isn't a valid {category_id: option_id} mapping.
        # Building the dict[str, int] response straight from that used to
        # raise a validation error and 500 this endpoint for every
        # character this discord_id owns, not just the dirty one.
        dirty_id = await seed_character(
            db_session_factory,
            discord_id=42,
            character_overrides={
                "name": "OldCustomized",
                "status": CharacterStatus.APPROVED.value,
                "appearance_layers": {"hair_style": "mohawk", "skin_tone": "tan"},
            },
        )
        clean_id = await seed_character(
            db_session_factory,
            discord_id=42,
            character_overrides={"name": "Fresh", "status": CharacterStatus.APPROVED.value},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/activity/dashboard/identify", json={"discord_id": 42})
        assert response.status_code == 200
        by_id = {c["id"]: c for c in response.json()["characters"]}
        assert by_id[dirty_id]["appearance_layers"] == {}
        assert by_id[clean_id]["appearance_layers"] == {}

    async def test_accepts_a_real_snowflake_sent_as_a_json_string(
        self, work_app, db_session_factory
    ):
        # Real Discord snowflakes are 18-19 digit integers, well past
        # Number.MAX_SAFE_INTEGER (2**53 == 9007199254740992, 16 digits) --
        # app.js used to send `discord_id: Number(rawId)` in every JSON
        # body, which silently rounds a snowflake to the nearest
        # representable double and corrupts its low digits. The fix sends
        # the id as-is (a numeric string); FastAPI/Pydantic parses a
        # numeric JSON string into `int` exactly, no float involved, so
        # this must resolve the same as if the field were sent as a JSON
        # number that happened to be small enough to round-trip safely.
        snowflake = 824399825380032612
        assert snowflake > 2**53
        await seed_character(
            db_session_factory,
            discord_id=snowflake,
            character_overrides={"name": "Wren", "status": CharacterStatus.APPROVED.value},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/activity/dashboard/identify", json={"discord_id": str(snowflake)}
            )
        assert response.status_code == 200
        assert [c["name"] for c in response.json()["characters"]] == ["Wren"]

    async def test_surfaces_jailed_until_tick_for_the_jail_tab(self, work_app, db_session_factory):
        await seed_character(
            db_session_factory,
            discord_id=42,
            character_overrides={"jailed_until_tick": 500},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/activity/dashboard/identify", json={"discord_id": 42})
        character = response.json()["characters"][0]
        assert character["jailed_until_tick"] == 500
        assert character["jailed"] is True

    async def test_jailed_flag_is_false_once_the_stale_sentence_has_lapsed(
        self, work_app, db_session_factory
    ):
        """Regression test for the character selector/character tab showing
        "(jailed)" for a character whose `jailed_until_tick` is set but
        already in the past relative to the current world tick -- the same
        `jailed_until_tick > current_tick` comparison `jail_status` already
        used, just missing from every other place that reads jail state.
        `jailed_until_tick` truthiness alone isn't "currently jailed"."""
        from panem_shared.db.models import WorldClock

        await seed_character(
            db_session_factory,
            discord_id=42,
            character_overrides={"jailed_until_tick": 500},
        )
        async with db_session_factory() as session, session.begin():
            session.add(WorldClock(id=1, tick=1000))
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/activity/dashboard/identify", json={"discord_id": 42})
        character = response.json()["characters"][0]
        assert character["jailed_until_tick"] == 500
        assert character["jailed"] is False

    async def test_is_donor_true_when_discord_reports_a_donor_role(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/identify", json={"discord_id": 42}
                )
        assert response.status_code == 200
        body = response.json()
        assert body["is_donor"] is True
        assert body["theme"] == {
            "background_hex": "#0a0c10",
            "accent_hex": "#c5a059",
            "panel_hex": "#12161f",
            "text_hex": "#f1f3f7",
            "profile_id": None,
        }
        assert body["theme_profiles"] == []

    async def test_is_donor_false_without_a_donor_role(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1, 2]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/identify", json={"discord_id": 42}
                )
        assert response.status_code == 200
        assert response.json()["is_donor"] is False


def _profile_payload(
    *,
    discord_id: int = 42,
    name: str = "Midnight",
    background_hex: str = "#112233",
    accent_hex: str = "#445566",
    panel_hex: str = "#223344",
    text_hex: str = "#eeeeee",
) -> dict[str, object]:
    return {
        "discord_id": discord_id,
        "name": name,
        "background_hex": background_hex,
        "accent_hex": accent_hex,
        "panel_hex": panel_hex,
        "text_hex": text_hex,
    }


DEFAULT_THEME_JSON = {
    "background_hex": "#0a0c10",
    "accent_hex": "#c5a059",
    "panel_hex": "#12161f",
    "text_hex": "#f1f3f7",
    "profile_id": None,
}


class TestDashboardThemeProfiles:
    """The named-profile system (`POST/GET/PATCH .../theme/profiles`,
    activate/assign/unassign, and the character-aware reset/resolve
    endpoints) that replaced the single-slot `POST /activity/dashboard/
    theme` this class used to cover."""

    async def test_create_refuses_a_non_donor(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1, 2]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/theme/profiles", json=_profile_payload()
                )
        assert response.status_code == 403

    async def test_create_rejects_an_invalid_hex_color(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/theme/profiles",
                    json=_profile_payload(background_hex="not-a-color"),
                )
        assert response.status_code == 400

    async def test_create_rejects_a_blank_name(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/theme/profiles", json=_profile_payload(name="   ")
                )
        assert response.status_code == 400

    async def test_create_normalizes_uppercase_hex_to_lowercase(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/theme/profiles",
                    json=_profile_payload(background_hex="#ABCDEF", accent_hex="#123ABC"),
                )
        assert response.status_code == 200
        body = response.json()
        assert body["background_hex"] == "#abcdef"
        assert body["accent_hex"] == "#123abc"

    async def test_create_then_list_returns_the_saved_profile(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                created = (
                    await client.post("/activity/dashboard/theme/profiles", json=_profile_payload())
                ).json()
                list_response = await client.post(
                    "/activity/dashboard/theme/profiles/list", json={"discord_id": 42}
                )
        assert list_response.status_code == 200
        body = list_response.json()
        assert body["active_profile_id"] is None
        assert body["profiles"] == [created]

    async def test_list_refuses_a_non_donor(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1, 2]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/theme/profiles/list", json={"discord_id": 42}
                )
        assert response.status_code == 403

    async def test_update_changes_only_the_given_fields(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                created = (
                    await client.post("/activity/dashboard/theme/profiles", json=_profile_payload())
                ).json()
                updated = await client.patch(
                    f"/activity/dashboard/theme/profiles/{created['id']}",
                    json={"discord_id": 42, "name": "Renamed"},
                )
        assert updated.status_code == 200
        body = updated.json()
        assert body["name"] == "Renamed"
        assert body["background_hex"] == created["background_hex"]

    async def test_update_404s_for_another_accounts_profile(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                created = (
                    await client.post(
                        "/activity/dashboard/theme/profiles", json=_profile_payload(discord_id=42)
                    )
                ).json()
                response = await client.patch(
                    f"/activity/dashboard/theme/profiles/{created['id']}",
                    json={"discord_id": 999999, "name": "Stolen"},
                )
        assert response.status_code == 404

    async def test_activate_sets_the_general_default(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                created = (
                    await client.post("/activity/dashboard/theme/profiles", json=_profile_payload())
                ).json()
                activated = await client.post(
                    f"/activity/dashboard/theme/profiles/{created['id']}/activate",
                    json={"discord_id": 42},
                )
                identify_response = await client.post(
                    "/activity/dashboard/identify", json={"discord_id": 42}
                )
        assert activated.status_code == 200
        assert activated.json()["profile_id"] == created["id"]
        assert activated.json()["background_hex"] == created["background_hex"]
        identify_body = identify_response.json()
        assert identify_body["theme"]["profile_id"] == created["id"]
        assert identify_body["theme"]["background_hex"] == created["background_hex"]

    async def test_activate_refuses_a_non_donor(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                created = (
                    await client.post("/activity/dashboard/theme/profiles", json=_profile_payload())
                ).json()
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1, 2]))
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/activity/dashboard/theme/profiles/{created['id']}/activate",
                    json={"discord_id": 42},
                )
        assert response.status_code == 403

    async def test_delete_clears_the_general_default(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                created = (
                    await client.post("/activity/dashboard/theme/profiles", json=_profile_payload())
                ).json()
                await client.post(
                    f"/activity/dashboard/theme/profiles/{created['id']}/activate",
                    json={"discord_id": 42},
                )
                deleted = await client.post(
                    f"/activity/dashboard/theme/profiles/{created['id']}/delete",
                    json={"discord_id": 42},
                )
                identify_response = await client.post(
                    "/activity/dashboard/identify", json={"discord_id": 42}
                )
        assert deleted.status_code == 200
        assert deleted.json() == {"profiles": [], "active_profile_id": None}
        assert identify_response.json()["theme"] == DEFAULT_THEME_JSON

    async def test_delete_404s_for_an_unknown_profile(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/theme/profiles/999999/delete",
                    json={"discord_id": 42},
                )
        assert response.status_code == 404

    async def test_profile_limit_is_enforced(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                for i in range(20):
                    response = await client.post(
                        "/activity/dashboard/theme/profiles",
                        json=_profile_payload(name=f"Preset {i}"),
                    )
                    assert response.status_code == 200
                over_limit = await client.post(
                    "/activity/dashboard/theme/profiles", json=_profile_payload(name="One Too Many")
                )
        assert over_limit.status_code == 400

    async def test_a_lapsed_donor_stops_seeing_their_active_profile(self, donor_app):
        """A saved profile is only ever *surfaced* while the account
        currently holds the donor role -- the row/assignment isn't
        cleared (in case the role comes back), but `/identify` renders the
        plain default the moment `is_donor` reads false, honoring "only
        donors get custom backgrounds" even for someone who customized
        once and later lost the role."""
        transport = httpx.ASGITransport(app=donor_app)
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                created = (
                    await client.post("/activity/dashboard/theme/profiles", json=_profile_payload())
                ).json()
                await client.post(
                    f"/activity/dashboard/theme/profiles/{created['id']}/activate",
                    json={"discord_id": 42},
                )
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1, 2]))
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                identify_response = await client.post(
                    "/activity/dashboard/identify", json={"discord_id": 42}
                )
        assert identify_response.json()["theme"] == DEFAULT_THEME_JSON
        assert identify_response.json()["theme_profiles"] == []


class TestDashboardThemeAssignment:
    """Per-character theme overrides (`.../profiles/{id}/assign`,
    `.../unassign`) and the character-aware `.../reset`/`.../resolve`
    endpoints -- "assign to different characters" and "save in general to
    go back to" from the feature request."""

    async def test_resolve_is_the_plain_default_for_a_non_donor(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1, 2]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/theme/resolve", json={"discord_id": 42}
                )
        assert response.status_code == 200
        assert response.json() == DEFAULT_THEME_JSON

    async def test_assign_overrides_the_general_default_for_one_character(
        self, donor_app, db_session_factory
    ):
        character_id = await seed_character(db_session_factory, discord_id=42)
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                general = (
                    await client.post(
                        "/activity/dashboard/theme/profiles",
                        json=_profile_payload(name="General", background_hex="#111111"),
                    )
                ).json()
                per_character = (
                    await client.post(
                        "/activity/dashboard/theme/profiles",
                        json=_profile_payload(name="For Wren", background_hex="#222222"),
                    )
                ).json()
                await client.post(
                    f"/activity/dashboard/theme/profiles/{general['id']}/activate",
                    json={"discord_id": 42},
                )
                await client.post(
                    f"/activity/dashboard/theme/profiles/{per_character['id']}/assign",
                    json={"discord_id": 42, "character_id": character_id},
                )
                for_character = await client.post(
                    "/activity/dashboard/theme/resolve",
                    json={"discord_id": 42, "character_id": character_id},
                )
                without_character = await client.post(
                    "/activity/dashboard/theme/resolve", json={"discord_id": 42}
                )
                identify_response = await client.post(
                    "/activity/dashboard/identify", json={"discord_id": 42}
                )
        assert for_character.json()["background_hex"] == "#222222"
        assert for_character.json()["profile_id"] == per_character["id"]
        assert without_character.json()["background_hex"] == "#111111"
        identify_character = identify_response.json()["characters"][0]
        assert identify_character["theme_profile_id"] == per_character["id"]

    async def test_unassign_falls_back_to_the_general_default(self, donor_app, db_session_factory):
        character_id = await seed_character(db_session_factory, discord_id=42)
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                general = (
                    await client.post(
                        "/activity/dashboard/theme/profiles",
                        json=_profile_payload(name="General", background_hex="#111111"),
                    )
                ).json()
                per_character = (
                    await client.post(
                        "/activity/dashboard/theme/profiles",
                        json=_profile_payload(name="For Wren", background_hex="#222222"),
                    )
                ).json()
                await client.post(
                    f"/activity/dashboard/theme/profiles/{general['id']}/activate",
                    json={"discord_id": 42},
                )
                await client.post(
                    f"/activity/dashboard/theme/profiles/{per_character['id']}/assign",
                    json={"discord_id": 42, "character_id": character_id},
                )
                unassigned = await client.post(
                    "/activity/dashboard/theme/unassign",
                    json={"discord_id": 42, "character_id": character_id},
                )
        assert unassigned.status_code == 200
        assert unassigned.json()["background_hex"] == "#111111"
        assert unassigned.json()["profile_id"] == general["id"]

    async def test_assign_refuses_a_character_owned_by_someone_else(
        self, donor_app, db_session_factory
    ):
        other_characters_id = await seed_character(db_session_factory, discord_id=999999)
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                profile = (
                    await client.post("/activity/dashboard/theme/profiles", json=_profile_payload())
                ).json()
                response = await client.post(
                    f"/activity/dashboard/theme/profiles/{profile['id']}/assign",
                    json={"discord_id": 42, "character_id": other_characters_id},
                )
        assert response.status_code == 404

    async def test_reset_with_a_character_id_clears_only_that_characters_assignment(
        self, donor_app, db_session_factory
    ):
        character_id = await seed_character(db_session_factory, discord_id=42)
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                general = (
                    await client.post(
                        "/activity/dashboard/theme/profiles",
                        json=_profile_payload(name="General", background_hex="#111111"),
                    )
                ).json()
                per_character = (
                    await client.post(
                        "/activity/dashboard/theme/profiles",
                        json=_profile_payload(name="For Wren", background_hex="#222222"),
                    )
                ).json()
                await client.post(
                    f"/activity/dashboard/theme/profiles/{general['id']}/activate",
                    json={"discord_id": 42},
                )
                await client.post(
                    f"/activity/dashboard/theme/profiles/{per_character['id']}/assign",
                    json={"discord_id": 42, "character_id": character_id},
                )
                reset_response = await client.post(
                    "/activity/dashboard/theme/reset",
                    json={"discord_id": 42, "character_id": character_id},
                )
        # Falls back to the still-active general profile, not the plain default.
        assert reset_response.json()["background_hex"] == "#111111"
        assert reset_response.json()["profile_id"] == general["id"]

    async def test_reset_without_a_character_id_clears_the_general_default(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([888]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                created = (
                    await client.post("/activity/dashboard/theme/profiles", json=_profile_payload())
                ).json()
                await client.post(
                    f"/activity/dashboard/theme/profiles/{created['id']}/activate",
                    json={"discord_id": 42},
                )
                reset_response = await client.post(
                    "/activity/dashboard/theme/reset", json={"discord_id": 42}
                )
        assert reset_response.json() == DEFAULT_THEME_JSON

    async def test_reset_refuses_a_non_donor(self, donor_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1, 2]))
        ):
            transport = httpx.ASGITransport(app=donor_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/theme/reset", json={"discord_id": 42}
                )
        assert response.status_code == 403


class TestDashboardCharacters:
    async def test_create_503s_when_not_configured(self):
        app = create_app(content=make_content_with_job(), redis_client=FakeRedis())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/activity/dashboard/characters",
                json={
                    "discord_id": 1,
                    "district_id": 1,
                    "name": "Wren",
                    "age": 15,
                    "job_title": "Miner",
                    "shift_phase": "morning",
                },
            )
        assert response.status_code == 503

    async def test_create_rejects_an_unknown_district(self, work_app):
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/activity/dashboard/characters",
                json={
                    "discord_id": 1,
                    "district_id": 999,
                    "name": "Wren",
                    "age": 15,
                    "job_title": "Miner",
                    "shift_phase": "morning",
                },
            )
        assert response.status_code == 400

    async def test_create_rejects_an_invalid_shift_phase(self, work_app):
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/activity/dashboard/characters",
                json={
                    "discord_id": 1,
                    "district_id": 1,
                    "name": "Wren",
                    "age": 15,
                    "job_title": "Miner",
                    "shift_phase": "midnight",
                },
            )
        assert response.status_code == 400

    async def test_create_rejects_an_unknown_rp_mode(self, work_app):
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/activity/dashboard/characters",
                json={
                    "discord_id": 1,
                    "district_id": 1,
                    "name": "Wren",
                    "age": 15,
                    "job_title": "Miner",
                    "shift_phase": "morning",
                    "rp_mode": "not-a-real-mode",
                },
            )
        assert response.status_code == 400

    async def test_create_a_story_mode_character_needs_no_job_fields(self, work_app):
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/activity/dashboard/characters",
                json={
                    "discord_id": 1,
                    "district_id": 1,
                    "name": "Wren",
                    "age": 15,
                    "rp_mode": "story",
                },
            )
        assert response.status_code == 200
        body = response.json()
        assert body["rp_mode"] == "story"
        assert body["job_title"] is None
        assert body["shift_phase"] is None

    async def test_create_happy_path_is_pending_with_no_approval_notified_yet(
        self, work_app, db_session_factory
    ):
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/activity/dashboard/characters",
                json={
                    "discord_id": 7,
                    "district_id": 1,
                    "name": "Wren",
                    "age": 15,
                    "job_title": "Miner",
                    "shift_phase": "morning",
                },
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == CharacterStatus.PENDING.value
        async with db_session_factory() as session:
            character = await session.get(Character, body["id"])
            assert character.approval_notified_at is None
        assert work_app.state.fake_redis.published == [(CHARACTER_PENDING_CHANNEL, str(body["id"]))]

    async def test_create_rejects_a_duplicate_name(self, work_app, db_session_factory):
        await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/activity/dashboard/characters",
                json={
                    "discord_id": 99,
                    "district_id": 1,
                    "name": "Wren",
                    "age": 15,
                    "job_title": "Miner",
                    "shift_phase": "morning",
                },
            )
        assert response.status_code == 400

    async def test_list_excludes_rejected_and_dead_and_sorts_approved_then_retired_then_pending(
        self, work_app, db_session_factory
    ):
        await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"name": "Wren", "status": CharacterStatus.APPROVED.value},
        )
        await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"name": "Applicant", "status": CharacterStatus.PENDING.value},
        )
        await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"name": "Retiree", "status": CharacterStatus.RETIRED.value},
        )
        await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"name": "Denied", "status": CharacterStatus.REJECTED.value},
        )
        await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"name": "Deceased", "status": CharacterStatus.DEAD.value},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/dashboard/characters", params={"discord_id": 5})
        assert response.status_code == 200
        names = [c["name"] for c in response.json()["characters"]]
        assert set(names) == {"Wren", "Applicant", "Retiree"}
        # Approved characters sort first, then retired, then pending --
        # regardless of creation order (Applicant was created before
        # Retiree above) or of the surrogate id order that would otherwise
        # fall out of a plain `.order_by(Character.id)`.
        assert names == ["Wren", "Retiree", "Applicant"]

    async def test_list_jailed_flag_reflects_the_current_world_tick(
        self, work_app, db_session_factory
    ):
        """Regression test: the Character tab used to show "-- jailed" off
        `jailed_until_tick` truthiness alone, so a character whose sentence
        had already lapsed (`jailed_until_tick <= current_tick`) still read
        as jailed forever. The list response's `jailed` field must be
        computed against the world clock, same as `jail_status`."""
        from panem_shared.db.models import WorldClock

        await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"name": "StillIn", "jailed_until_tick": 2000},
        )
        await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"name": "AlreadyOut", "jailed_until_tick": 10},
        )
        async with db_session_factory() as session, session.begin():
            session.add(WorldClock(id=1, tick=1000))
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/dashboard/characters", params={"discord_id": 5})
        by_name = {c["name"]: c["jailed"] for c in response.json()["characters"]}
        assert by_name == {"StillIn": True, "AlreadyOut": False}

    async def test_update_rejects_a_non_owner(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/activity/dashboard/characters/{char_id}",
                json={"discord_id": 6, "avatar_url": "https://example.com/pic.png"},
            )
        assert response.status_code == 404

    async def test_update_sets_avatar_and_tag(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/activity/dashboard/characters/{char_id}",
                json={
                    "discord_id": 5,
                    "avatar_url": "https://example.com/pic.png",
                    "proxy_tag": "wren::",
                },
            )
        assert response.status_code == 200
        body = response.json()
        assert body["avatar_url"] == "https://example.com/pic.png"
        assert body["proxy_tag"] == "wren::"

    async def test_update_rejects_an_invalid_avatar_url(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/activity/dashboard/characters/{char_id}",
                json={"discord_id": 5, "avatar_url": "not-a-url"},
            )
        assert response.status_code == 400

    async def test_avatar_upload_saves_the_file_and_sets_an_absolute_url(
        self, work_app_with_avatar_uploads, db_session_factory, tmp_path
    ):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app_with_avatar_uploads)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/characters/{char_id}/avatar-upload",
                data={"discord_id": "5"},
                files={"file": ("portrait.png", b"fake-png-bytes", "image/png")},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["avatar_url"].startswith("https://example.com/uploads/avatars/")
        saved_path = tmp_path / body["avatar_url"].removeprefix("https://example.com/")
        assert saved_path.exists()
        assert saved_path.read_bytes() == b"fake-png-bytes"

    async def test_avatar_upload_rejects_a_non_owner(
        self, work_app_with_avatar_uploads, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app_with_avatar_uploads)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/characters/{char_id}/avatar-upload",
                data={"discord_id": "6"},
                files={"file": ("portrait.png", b"fake-png-bytes", "image/png")},
            )
        assert response.status_code == 404

    async def test_avatar_upload_rejects_a_disallowed_content_type(
        self, work_app_with_avatar_uploads, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app_with_avatar_uploads)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/characters/{char_id}/avatar-upload",
                data={"discord_id": "5"},
                files={"file": ("portrait.svg", b"<svg></svg>", "image/svg+xml")},
            )
        assert response.status_code == 400

    async def test_avatar_upload_refused_when_no_public_url_is_configured(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/characters/{char_id}/avatar-upload",
                data={"discord_id": "5"},
                files={"file": ("portrait.png", b"fake-png-bytes", "image/png")},
            )
        assert response.status_code == 400

    async def test_create_stores_a_valid_appearance_layers_submission(
        self, work_app, db_session_factory
    ):
        category_id, option_ids = await seed_layer_category(db_session_factory, option_names=["Braid"])
        option_id = option_ids[0]
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/activity/dashboard/characters",
                json={
                    "discord_id": 7,
                    "district_id": 1,
                    "name": "Wren",
                    "age": 15,
                    "job_title": "Miner",
                    "shift_phase": "morning",
                    "appearance_layers": {str(category_id): option_id},
                },
            )
        assert response.status_code == 200
        assert response.json()["appearance_layers"] == {str(category_id): option_id}

    async def test_create_with_no_appearance_layers_stores_nothing(
        self, work_app, db_session_factory
    ):
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/activity/dashboard/characters",
                json={
                    "discord_id": 8,
                    "district_id": 1,
                    "name": "Sabel",
                    "age": 15,
                    "job_title": "Baker",
                    "shift_phase": "morning",
                },
            )
        assert response.status_code == 200
        assert response.json()["appearance_layers"] == {}

    async def test_create_rejects_an_option_id_that_does_not_exist(self, work_app):
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/activity/dashboard/characters",
                json={
                    "discord_id": 9,
                    "district_id": 1,
                    "name": "Bramble",
                    "age": 15,
                    "job_title": "Baker",
                    "shift_phase": "morning",
                    "appearance_layers": {"999": 999},
                },
            )
        assert response.status_code == 400

    async def test_list_returns_empty_appearance_layers_for_a_never_customized_row(
        self, work_app, db_session_factory
    ):
        await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"name": "OldRow", "status": CharacterStatus.APPROVED.value},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/dashboard/characters", params={"discord_id": 5})
        assert response.status_code == 200
        (character,) = response.json()["characters"]
        assert character["appearance_layers"] == {}

    async def test_list_sanitizes_stale_pre_picrew_appearance_data(
        self, work_app, db_session_factory
    ):
        await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={
                "name": "OldRow",
                "status": CharacterStatus.APPROVED.value,
                "appearance_layers": {"hair_style": "mohawk"},
            },
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/dashboard/characters", params={"discord_id": 5})
        assert response.status_code == 200
        (character,) = response.json()["characters"]
        assert character["appearance_layers"] == {}

    async def test_update_sets_appearance_layers(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        category_id, option_ids = await seed_layer_category(db_session_factory, option_names=["Braid"])
        option_id = option_ids[0]
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/activity/dashboard/characters/{char_id}",
                json={"discord_id": 5, "appearance_layers": {str(category_id): option_id}},
            )
        assert response.status_code == 200
        assert response.json()["appearance_layers"] == {str(category_id): option_id}

    async def test_update_rejects_an_option_that_belongs_to_a_different_category(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=5)
        category_id, option_ids = await seed_layer_category(db_session_factory, option_names=["Braid"])
        option_id = option_ids[0]
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/activity/dashboard/characters/{char_id}",
                # option_id is real, but doesn't belong to category_id + 1.
                json={"discord_id": 5, "appearance_layers": {str(category_id + 1): option_id}},
            )
        assert response.status_code == 400

    async def test_layer_catalog_starts_empty(self, work_app):
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/dashboard/layers")
        assert response.status_code == 200
        assert response.json() == {"categories": []}

    async def test_layer_catalog_lists_categories_in_z_index_order(
        self, work_app, db_session_factory
    ):
        await seed_layer_category(db_session_factory, name="Hair", z_index=5, option_names=["Braid"])
        await seed_layer_category(db_session_factory, name="Base", z_index=0, option_names=["Tan"])
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/dashboard/layers")
        assert response.status_code == 200
        names = [c["name"] for c in response.json()["categories"]]
        assert names == ["Base", "Hair"]

    async def test_retire_happy_path(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"status": CharacterStatus.APPROVED.value},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/characters/{char_id}/retire", json={"discord_id": 5}
            )
        assert response.status_code == 200
        assert response.json()["status"] == CharacterStatus.RETIRED.value

    async def test_retire_rejects_a_non_owner(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"status": CharacterStatus.APPROVED.value},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/characters/{char_id}/retire", json={"discord_id": 6}
            )
        assert response.status_code == 404


class TestDashboardJail:
    async def test_status_when_not_jailed(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/jail/{char_id}", params={"discord_id": 5}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["jailed"] is False
        assert body["bail_cost"] is None

    async def test_status_when_jailed_includes_bail_cost(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"jailed_until_tick": 500, "jail_sentence_ticks": 100},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/jail/{char_id}", params={"discord_id": 5}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["jailed"] is True
        assert body["bail_cost"] > 0

    async def test_status_rejects_a_non_owner(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/jail/{char_id}", params={"discord_id": 6}
            )
        assert response.status_code == 404

    async def test_bail_happy_path_clears_the_sentence(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={
                "jailed_until_tick": 500,
                "jail_sentence_ticks": 100,
                "money": 100_000,
            },
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/jail/{char_id}/bail", json={"discord_id": 5}
            )
        assert response.status_code == 200
        assert response.json()["cost"] > 0
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.jailed_until_tick is None

    async def test_bail_refuses_when_not_jailed(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/jail/{char_id}/bail", json={"discord_id": 5}
            )
        assert response.status_code == 400

    async def test_bail_refuses_insufficient_funds(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"jailed_until_tick": 500, "jail_sentence_ticks": 100, "money": 0},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/jail/{char_id}/bail", json={"discord_id": 5}
            )
        assert response.status_code == 400

    async def test_lockpick_start_mints_a_crime_attempt(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"jailed_until_tick": 500, "jail_sentence_ticks": 100},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/jail/{char_id}/lockpick/start", json={"discord_id": 5}
            )
        assert response.status_code == 200
        body = response.json()
        redis_client = work_app.state.fake_redis
        raw = redis_client.store[crime_attempt_key(body["attempt_id"])]
        assert json.loads(raw) == {"kind": "lockpick", "character_id": char_id}

    async def test_lockpick_start_refuses_when_out_of_tries(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={
                "jailed_until_tick": 500,
                "jail_sentence_ticks": 100,
                "jail_lockpick_tries_used": 3,
            },
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/jail/{char_id}/lockpick/start", json={"discord_id": 5}
            )
        assert response.status_code == 400

    async def test_lockpick_start_refuses_when_not_jailed(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/jail/{char_id}/lockpick/start", json={"discord_id": 5}
            )
        assert response.status_code == 400


class TestDashboardCrime:
    async def test_steal_targets_lists_players_and_npcs_at_the_same_spot(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"location_id": "square"}
        )
        await seed_character(
            db_session_factory,
            discord_id=6,
            character_overrides={"name": "Mark", "location_id": "square"},
        )
        await seed_npc(db_session_factory, name="Effie", location_id="square")
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/crime/{char_id}/steal-targets",
                params={"discord_id": 5},
            )
        assert response.status_code == 200
        targets = {(t["name"], t["kind"]) for t in response.json()["targets"]}
        assert targets == {("Mark", "player"), ("Effie", "npc")}

    async def test_steal_targets_excludes_story_mode_characters(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"location_id": "square"}
        )
        await seed_character(
            db_session_factory,
            discord_id=6,
            character_overrides={"name": "Mark", "location_id": "square", "rp_mode": "story"},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/crime/{char_id}/steal-targets",
                params={"discord_id": 5},
            )
        assert response.status_code == 200
        assert response.json()["targets"] == []

    async def test_burgle_targets_lists_house_owners_in_district(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=5)
        owner_id = await seed_character(
            db_session_factory, discord_id=6, character_overrides={"name": "Owner"}
        )
        await seed_house(db_session_factory, owner_id=owner_id)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/crime/{char_id}/burgle-targets",
                params={"discord_id": 5},
            )
        assert response.status_code == 200
        assert response.json()["owners"] == ["Owner"]

    async def test_burgle_targets_is_empty_for_a_life_mode_character(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"rp_mode": "life"}
        )
        owner_id = await seed_character(
            db_session_factory, discord_id=6, character_overrides={"name": "Owner"}
        )
        await seed_house(db_session_factory, owner_id=owner_id)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/crime/{char_id}/burgle-targets",
                params={"discord_id": 5},
            )
        assert response.status_code == 200
        assert response.json()["owners"] == []

    async def test_steal_start_mints_an_attempt(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"location_id": "square"}
        )
        victim_id = await seed_character(
            db_session_factory,
            discord_id=6,
            character_overrides={"name": "Mark", "location_id": "square"},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/crime/{char_id}/steal/start",
                json={"discord_id": 5, "target": "Mark"},
            )
        assert response.status_code == 200
        body = response.json()
        redis_client = work_app.state.fake_redis
        raw = json.loads(redis_client.store[crime_attempt_key(body["attempt_id"])])
        assert raw == {
            "kind": "steal",
            "character_id": char_id,
            "district_id": 1,
            "current_tick": 0,
            "victim_kind": "character",
            "victim_id": str(victim_id),
        }

    async def test_steal_start_404s_for_an_unknown_target(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/crime/{char_id}/steal/start",
                json={"discord_id": 5, "target": "Nobody"},
            )
        assert response.status_code == 404

    async def test_steal_start_refuses_on_cooldown(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"location_id": "square", "last_steal_tick": 0},
        )
        await seed_character(
            db_session_factory,
            discord_id=6,
            character_overrides={"name": "Mark", "location_id": "square"},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/crime/{char_id}/steal/start",
                json={"discord_id": 5, "target": "Mark"},
            )
        assert response.status_code == 400

    async def test_burgle_start_mints_an_attempt(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        owner_id = await seed_character(
            db_session_factory, discord_id=6, character_overrides={"name": "Owner"}
        )
        house_id = await seed_house(db_session_factory, owner_id=owner_id)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/crime/{char_id}/burgle/start",
                json={"discord_id": 5, "owner": "Owner"},
            )
        assert response.status_code == 200
        body = response.json()
        redis_client = work_app.state.fake_redis
        raw = json.loads(redis_client.store[crime_attempt_key(body["attempt_id"])])
        assert raw["kind"] == "burgle"
        assert raw["property_id"] == house_id

    async def test_burgle_start_with_two_houses_in_the_same_district_does_not_500(
        self, work_app, db_session_factory
    ):
        """Nothing in the housing system stops one character from owning two
        houses in the same district, so the owner-house lookup can legitimately
        match more than one `Property` row -- previously that raised an
        unhandled `MultipleResultsFound` (a 500) from `.scalar_one_or_none()`
        rather than just picking one."""
        char_id = await seed_character(db_session_factory, discord_id=5)
        owner_id = await seed_character(
            db_session_factory, discord_id=6, character_overrides={"name": "Owner"}
        )
        await seed_house(db_session_factory, owner_id=owner_id)
        await seed_house(db_session_factory, owner_id=owner_id)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/crime/{char_id}/burgle/start",
                json={"discord_id": 5, "owner": "Owner"},
            )
        assert response.status_code == 200

    async def test_burgle_start_404s_for_an_unknown_owner(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/crime/{char_id}/burgle/start",
                json={"discord_id": 5, "owner": "Nobody"},
            )
        assert response.status_code == 404

    async def test_burgle_start_refuses_own_house(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"name": "Self"}
        )
        await seed_house(db_session_factory, owner_id=char_id)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/crime/{char_id}/burgle/start",
                json={"discord_id": 5, "owner": "Self"},
            )
        assert response.status_code == 400

    async def test_poach_start_mints_an_attempt(self, poach_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"location_id": "outskirts"}
        )
        transport = httpx.ASGITransport(app=poach_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/crime/{char_id}/poach/start",
                json={"discord_id": 5},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["target_name"] is None
        redis_client = poach_app.state.fake_redis
        raw = json.loads(redis_client.store[crime_attempt_key(body["attempt_id"])])
        assert raw == {
            "kind": "poach",
            "character_id": char_id,
            "district_id": 1,
            "good_id": constants.POACH_GOOD_ID,
            "current_tick": 0,
        }

    async def test_poach_start_refuses_when_not_at_outskirts(self, poach_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=poach_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/crime/{char_id}/poach/start",
                json={"discord_id": 5},
            )
        assert response.status_code == 400

    async def test_poach_start_refuses_on_cooldown(self, poach_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"location_id": "outskirts", "last_poach_tick": 0},
        )
        transport = httpx.ASGITransport(app=poach_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/crime/{char_id}/poach/start",
                json={"discord_id": 5},
            )
        assert response.status_code == 400

    async def test_log_returns_recent_entries_most_recent_first(
        self, poach_app, db_session_factory
    ):
        from panem_shared.db.models import CrimeLog

        char_id = await seed_character(db_session_factory, discord_id=5)
        async with db_session_factory() as session, session.begin():
            session.add(
                CrimeLog(
                    character_id=char_id,
                    kind="steal",
                    tick=1,
                    success=True,
                    caught=False,
                    target_name="Mark",
                    amount=15,
                )
            )
            session.add(
                CrimeLog(
                    character_id=char_id,
                    kind="poach",
                    tick=2,
                    success=True,
                    caught=False,
                    good_name="Grain",
                    amount=1,
                )
            )
        transport = httpx.ASGITransport(app=poach_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/crime/{char_id}/log", params={"discord_id": 5}
            )
        assert response.status_code == 200
        entries = response.json()["entries"]
        assert [e["kind"] for e in entries] == ["poach", "steal"]
        assert entries[0]["good_name"] == "Grain"
        assert entries[1]["target_name"] == "Mark"
        assert entries[1]["amount"] == 15

    async def test_log_empty_for_a_character_with_no_history(self, poach_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=poach_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/crime/{char_id}/log", params={"discord_id": 5}
            )
        assert response.status_code == 200
        assert response.json()["entries"] == []

    async def test_log_refuses_a_non_owner(self, poach_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=poach_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/crime/{char_id}/log", params={"discord_id": 999}
            )
        assert response.status_code == 404


class TestDashboardWork:
    async def test_status_reports_job_and_level(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"job_title": "Miner", "shift_phase": "morning"},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/work/{char_id}", params={"discord_id": 5}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["has_job"] is True
        assert body["job_title"] == "Miner"
        assert body["level"] == "apprentice"

    async def test_status_reports_no_job(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/work/{char_id}", params={"discord_id": 5}
            )
        assert response.status_code == 200
        assert response.json()["has_job"] is False

    async def test_start_finds_the_open_shift(self, work_app, db_session_factory):
        shift_id = await seed_shift(db_session_factory)
        async with db_session_factory() as session:
            rows = await session.execute(select(Character).where(Character.name == "Wren"))
            char_id = rows.scalar_one().id
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/work/{char_id}/start", json={"discord_id": 42}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["shift_id"] == shift_id
        assert body["already_worked_this_tick"] is False
        async with db_session_factory() as session:
            shift = await session.get(Shift, shift_id)
            assert shift.started_at_tick == 0

    async def test_start_refuses_without_a_job(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/work/{char_id}/start", json={"discord_id": 5}
            )
        assert response.status_code == 400

    async def test_start_refuses_while_jailed(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={
                "job_title": "Miner",
                "shift_phase": "morning",
                "jailed_until_tick": 1_000_000,
            },
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/work/{char_id}/start", json={"discord_id": 5}
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "work_jailed"

    async def test_start_refuses_with_no_open_shift_and_no_gamemaker_position(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"job_title": "Miner", "shift_phase": "morning"},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/work/{char_id}/start", json={"discord_id": 5}
            )
        assert response.status_code == 400

    async def test_start_refuses_while_away_from_home_district(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={
                "job_title": "Miner",
                "shift_phase": "morning",
                "district_id": 1,
                "current_district_id": 2,
            },
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/work/{char_id}/start", json={"discord_id": 5}
            )
        assert response.status_code == 400

    async def test_start_allows_a_gamemaker_working_away_from_home(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={
                "job_title": "Miner",
                "shift_phase": "morning",
                "district_id": 1,
                "current_district_id": 2,
                "positions": ["gamemaker"],
            },
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/work/{char_id}/start", json={"discord_id": 5}
            )
        assert response.status_code == 200

    async def test_start_opens_an_adhoc_shift_for_a_gamemaker(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={
                "job_title": "Miner",
                "shift_phase": "morning",
                "positions": ["gamemaker"],
            },
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/work/{char_id}/start", json={"discord_id": 5}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["job_title"] == "Miner"
        async with db_session_factory() as session:
            shift = await session.get(Shift, body["shift_id"])
            assert shift is not None
            assert shift.character_id == char_id


class TestDashboardMarket:
    async def test_status_lists_traded_goods_and_inventory(self, market_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"location_id": "legal_market"}
        )
        transport = httpx.ASGITransport(app=market_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/market/{char_id}", params={"discord_id": 5}
            )
        assert response.status_code == 200
        body = response.json()
        assert [p["good_id"] for p in body["prices"]] == ["grain"]
        assert body["prices"][0]["hunger_value"] == 15.0
        assert body["prices"][0]["cook_method"] == "oven"
        assert body["prices"][0]["stock"] is None
        assert body["inventory"] == []

    async def test_status_reports_current_stock(self, market_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"location_id": "legal_market"}
        )
        async with db_session_factory() as session, session.begin():
            session.add(MarketPrice(district_id=1, good_id="grain", price=2.0, supply=42.0, tick=0))
        transport = httpx.ASGITransport(app=market_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/market/{char_id}", params={"discord_id": 5}
            )
        assert response.json()["prices"][0]["stock"] == 42.0

    async def test_buy_happy_path(self, market_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"location_id": "legal_market", "money": 1000},
        )
        transport = httpx.ASGITransport(app=market_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/market/{char_id}/buy",
                json={"discord_id": 5, "good_id": "grain", "qty": 3},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["qty"] == 3
        assert body["good_name"] == "Grain"
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.money == 1000 - round(3 * 2.0)

    async def test_buy_refuses_not_at_a_market(self, market_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"location_id": "square"}
        )
        transport = httpx.ASGITransport(app=market_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/market/{char_id}/buy",
                json={"discord_id": 5, "good_id": "grain", "qty": 1},
            )
        assert response.status_code == 400

    async def test_buy_refuses_a_non_positive_qty(self, market_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"location_id": "legal_market"}
        )
        transport = httpx.ASGITransport(app=market_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/market/{char_id}/buy",
                json={"discord_id": 5, "good_id": "grain", "qty": 0},
            )
        assert response.status_code == 400

    async def test_sell_happy_path(self, market_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"location_id": "legal_market", "money": 0},
        )
        async with db_session_factory() as session, session.begin():
            session.add(
                Inventory(
                    owner_kind=OwnerKind.CHARACTER.value,
                    owner_id=str(char_id),
                    good_id="grain",
                    qty=5,
                )
            )
        transport = httpx.ASGITransport(app=market_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/market/{char_id}/sell",
                json={"discord_id": 5, "good_id": "grain", "qty": 2},
            )
        assert response.status_code == 200
        assert response.json()["qty"] == 2


class TestDashboardBlackMarket:
    async def test_status_reports_untrusted_by_default(self, market_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=market_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/blackmarket/{char_id}", params={"discord_id": 5}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["trusted"] is False
        assert [p["good_id"] for p in body["prices"]] == ["contraband"]

    async def test_status_reports_trusted_with_good_relations(self, market_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        async with db_session_factory() as session, session.begin():
            key = relationship_key(
                (OwnerKind.CHARACTER.value, str(char_id)), (OwnerKind.NPC.value, "fence")
            )
            session.add(
                RelationshipRow(
                    subject_kind=key[0],
                    subject_id=key[1],
                    object_kind=key[2],
                    object_id=key[3],
                    affinity=0,
                    trust=0.0,
                    stance=Stance.LIKES.value,
                )
            )
        transport = httpx.ASGITransport(app=market_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/blackmarket/{char_id}", params={"discord_id": 5}
            )
        assert response.status_code == 200
        assert response.json()["trusted"] is True

    async def test_buy_refuses_when_not_trusted(self, market_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"location_id": "outskirts", "money": 1000},
        )
        transport = httpx.ASGITransport(app=market_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/blackmarket/{char_id}/buy",
                json={"discord_id": 5, "good_id": "contraband", "qty": 1},
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "blackmarket_not_trusted"

    async def test_buy_refuses_away_from_the_outskirts(self, market_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"location_id": "market", "money": 1000},
        )
        async with db_session_factory() as session, session.begin():
            key = relationship_key(
                (OwnerKind.CHARACTER.value, str(char_id)), (OwnerKind.NPC.value, "fence")
            )
            session.add(
                RelationshipRow(
                    subject_kind=key[0],
                    subject_id=key[1],
                    object_kind=key[2],
                    object_id=key[3],
                    affinity=0,
                    trust=0.0,
                    stance=Stance.LOVES.value,
                )
            )
        transport = httpx.ASGITransport(app=market_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/blackmarket/{char_id}/buy",
                json={"discord_id": 5, "good_id": "contraband", "qty": 1},
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "blackmarket_not_at_market"

    async def test_buy_succeeds_when_trusted(self, market_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={"location_id": "outskirts", "money": 1000},
        )
        async with db_session_factory() as session, session.begin():
            key = relationship_key(
                (OwnerKind.CHARACTER.value, str(char_id)), (OwnerKind.NPC.value, "fence")
            )
            session.add(
                RelationshipRow(
                    subject_kind=key[0],
                    subject_id=key[1],
                    object_kind=key[2],
                    object_id=key[3],
                    affinity=0,
                    trust=0.0,
                    stance=Stance.LOVES.value,
                )
            )
            session.add(
                MarketPrice(district_id=1, good_id="contraband", price=10.0, supply=100, tick=0)
            )
        transport = httpx.ASGITransport(app=market_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/blackmarket/{char_id}/buy",
                json={"discord_id": 5, "good_id": "contraband", "qty": 1},
            )
        assert response.status_code == 200
        assert response.json()["good_name"] == "Contraband"


class TestDashboardTravel:
    async def test_status_lists_locations_and_other_districts(self, travel_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        transport = httpx.ASGITransport(app=travel_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/travel/{char_id}", params={"discord_id": 42}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["current_district_id"] == 1
        assert body["location_id"] == "square"
        assert {loc["id"] for loc in body["locations"]} == {"square", "station"}
        assert body["districts"] == [{"id": 0, "name": "The Capitol"}]
        assert body["in_transit"] is False

    async def test_location_travel_happy_path(self, travel_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        transport = httpx.ASGITransport(app=travel_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/travel/{char_id}/location",
                json={"discord_id": 42, "location_id": "station"},
            )
        assert response.status_code == 200
        assert response.json()["location_name"] == "Rail Station"
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.location_id == "station"

    async def test_location_travel_refuses_unknown_location(self, travel_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        transport = httpx.ASGITransport(app=travel_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/travel/{char_id}/location",
                json={"discord_id": 42, "location_id": "nowhere"},
            )
        assert response.status_code == 404

    async def test_location_travel_refuses_while_jailed(self, travel_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=42,
            character_overrides={"jailed_until_tick": 1_000_000},
        )
        transport = httpx.ASGITransport(app=travel_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/travel/{char_id}/location",
                json={"discord_id": 42, "location_id": "station"},
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "travel_jailed"

    async def test_district_travel_home_is_free(self, travel_app, db_session_factory):
        """Traveling back to a character's home district never needs
        banked `transport` stock (`is_free_route`) -- this character is
        away in the Capitol and heads back to District 1, their home."""
        char_id = await seed_character(
            db_session_factory,
            discord_id=42,
            character_overrides={
                "district_id": 1,
                "current_district_id": 0,
                "location_id": "station",
            },
        )
        transport = httpx.ASGITransport(app=travel_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/travel/{char_id}/district",
                json={"discord_id": 42, "destination_id": 1},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["destination_district_name"] == "District 1"
        assert body["transit_ticks"] == TRANSIT_TICKS
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.transit_destination_id == 1

    async def test_district_travel_refuses_insufficient_transport(
        self, travel_app, db_session_factory
    ):
        char_id = await seed_character(
            db_session_factory,
            discord_id=42,
            character_overrides={
                "district_id": 1,
                "current_district_id": 1,
                "location_id": "station",
            },
        )
        transport = httpx.ASGITransport(app=travel_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/travel/{char_id}/district",
                json={"discord_id": 42, "destination_id": 0},
            )
        assert response.status_code == 400

    async def test_district_travel_refuses_not_at_station(self, travel_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=42,
            character_overrides={"location_id": "square"},
        )
        transport = httpx.ASGITransport(app=travel_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/travel/{char_id}/district",
                json={"discord_id": 42, "destination_id": 0},
            )
        assert response.status_code == 400


class TestDashboardResidents:
    async def test_list_returns_residents_with_job_and_location(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        await seed_npc(db_session_factory, job_id="miner")
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/residents/{char_id}", params={"discord_id": 42}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["district_name"] == "District 1"
        assert body["residents"] == [
            {
                "name": "Mark",
                "job_title": "Miner",
                "location_id": "square",
                "location_name": "The Square",
                "kind": "npc",
                "status": None,
                "opinion_label": "stranger",
                "opinion_score": 0,
            }
        ]

    async def test_list_reports_the_viewing_characters_relationship_with_each_npc(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=42)
        await seed_npc(db_session_factory, job_id="miner")
        async with db_session_factory() as session, session.begin():
            session.add(
                RelationshipRow(
                    subject_kind=OwnerKind.CHARACTER.value,
                    subject_id=str(char_id),
                    object_kind=OwnerKind.NPC.value,
                    object_id="d1_npc_1",
                    affinity=12,
                    stance="likes",
                )
            )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/residents/{char_id}", params={"discord_id": 42}
            )
        resident = response.json()["residents"][0]
        assert resident["opinion_label"] == "likes"
        assert resident["opinion_score"] == 12

    async def test_list_leaves_opinion_unset_for_other_characters(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=42)
        await seed_character(
            db_session_factory,
            discord_id=43,
            character_overrides={"name": "Other", "location_id": "station"},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/residents/{char_id}", params={"discord_id": 42}
            )
        other = next(r for r in response.json()["residents"] if r["name"] == "Other")
        assert other["opinion_label"] is None
        assert other["opinion_score"] is None

    async def test_list_includes_other_characters_labeled_user_and_idle(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=42)
        await seed_character(
            db_session_factory,
            discord_id=43,
            character_overrides={"name": "Other", "location_id": "station"},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/residents/{char_id}", params={"discord_id": 42}
            )
        assert response.status_code == 200
        residents = response.json()["residents"]
        other = next(r for r in residents if r["name"] == "Other")
        assert other["kind"] == "user"
        assert other["status"] == "idle"
        assert other["location_name"] == "Rail Station"
        # The viewer's own character never lists itself.
        assert all(r["name"] != "Wren" for r in residents)

    async def test_list_excludes_other_characters_not_approved(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        await seed_character(
            db_session_factory,
            discord_id=43,
            character_overrides={"name": "Pending", "status": CharacterStatus.PENDING.value},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/residents/{char_id}", params={"discord_id": 42}
            )
        residents = response.json()["residents"]
        assert all(r["name"] != "Pending" for r in residents)

    async def test_list_reports_engaged_over_location_for_a_character_in_a_scene(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=42)
        other_id = await seed_character(
            db_session_factory,
            discord_id=43,
            character_overrides={"name": "Other"},
        )
        await seed_scene(db_session_factory, participants={"characters": [other_id], "npcs": []})
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/residents/{char_id}", params={"discord_id": 42}
            )
        residents = response.json()["residents"]
        other = next(r for r in residents if r["name"] == "Other")
        assert other["status"] == "engaged"

    async def test_list_reports_sleeping_for_a_character_in_a_residential_location(
        self, db_session_factory
    ):
        content = make_content_with_residence()
        redis_client = FakeRedis()
        app = create_app(
            content=content, redis_client=redis_client, session_factory=db_session_factory
        )
        char_id = await seed_character(db_session_factory, discord_id=42)
        await seed_character(
            db_session_factory,
            discord_id=43,
            character_overrides={"name": "Other", "location_id": "home"},
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/residents/{char_id}", params={"discord_id": 42}
            )
        residents = response.json()["residents"]
        other = next(r for r in residents if r["name"] == "Other")
        assert other["status"] == "sleeping"

    async def test_profile_returns_details_for_a_stranger(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        await seed_npc(
            db_session_factory,
            job_id="miner",
            traits=["gruff", "loyal"],
            speech_style={"tone": "blunt"},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/residents/{char_id}/Mark", params={"discord_id": 42}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["job_title"] == "Miner"
        assert body["location_name"] == "The Square"
        assert body["traits"] == ["gruff", "loyal"]
        assert body["tone"] == "blunt"
        assert body["stance"] == "stranger"

    async def test_profile_404s_for_unknown_resident(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/residents/{char_id}/Nobody", params={"discord_id": 42}
            )
        assert response.status_code == 404

    async def test_character_profile_returns_what_they_submitted_at_creation(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=42)
        await seed_character(
            db_session_factory,
            discord_id=43,
            character_overrides={
                "name": "Other",
                "age": 22,
                "gender": "female",
                "appearance": "Tall, dark-haired.",
                "backstory": "Grew up in the Seam.",
                "avatar_url": "https://example.com/a.png",
                "appearance_layers": {"1": 5},
                "job_title": "Baker",
                "shift_phase": "morning",
                "location_id": "station",
            },
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/residents/{char_id}/character/Other",
                params={"discord_id": 42},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Other"
        assert body["age"] == 22
        assert body["gender"] == "female"
        assert body["appearance"] == "Tall, dark-haired."
        assert body["backstory"] == "Grew up in the Seam."
        assert body["avatar_url"] == "https://example.com/a.png"
        assert body["appearance_layers"] == {"1": 5}
        assert body["job_title"] == "Baker"
        assert body["shift_phase"] == "morning"
        assert body["district_name"] == "District 1"
        assert body["location_name"] == "Rail Station"

    async def test_character_profile_404s_for_an_unapproved_character(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=42)
        await seed_character(
            db_session_factory,
            discord_id=43,
            character_overrides={"name": "Pending", "status": CharacterStatus.PENDING.value},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/residents/{char_id}/character/Pending",
                params={"discord_id": 42},
            )
        assert response.status_code == 404

    async def test_character_profile_404s_for_an_unknown_character(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=42)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/residents/{char_id}/character/Nobody",
                params={"discord_id": 42},
            )
        assert response.status_code == 404


class TestDashboardSocial:
    async def test_status_reports_not_in_a_scene(self, social_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        transport = httpx.ASGITransport(app=social_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/social/{char_id}", params={"discord_id": 42}
            )
        assert response.status_code == 200
        assert response.json() == {
            "in_scene": False,
            "scene_kind": None,
            "scene_title": None,
            "location_name": None,
            "participant_character_names": [],
            "participant_npc_names": [],
            "discord_thread_url": None,
        }

    async def test_status_reports_the_open_scene_with_a_thread_link(
        self, social_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=42)
        await seed_npc(db_session_factory)
        await seed_scene(
            db_session_factory,
            thread_id=777,
            title="A Quiet Word",
            participants={"characters": [char_id], "pending_characters": [], "npcs": ["d1_npc_1"]},
        )
        transport = httpx.ASGITransport(app=social_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/social/{char_id}", params={"discord_id": 42}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["in_scene"] is True
        assert body["scene_title"] == "A Quiet Word"
        assert body["scene_kind"] == SceneKind.ENGAGEMENT.value
        assert body["location_name"] == "The Square"
        assert body["participant_character_names"] == ["Wren"]
        assert body["participant_npc_names"] == ["Mark"]
        assert body["discord_thread_url"] == "https://discord.com/channels/999/777"

    async def test_status_omits_thread_link_without_a_configured_guild(
        self, travel_app, db_session_factory
    ):
        """`travel_app` (unlike `social_app`) has no `discord_guild_id`
        configured -- the default for a dev/preview server that hasn't
        set `DISCORD_GUILD_ID` -- so the link is omitted rather than
        pointing at a bogus `channels/0/...` URL."""
        char_id = await seed_character(db_session_factory, discord_id=42)
        await seed_scene(db_session_factory, participants={"characters": [char_id], "npcs": []})
        transport = httpx.ASGITransport(app=travel_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/social/{char_id}", params={"discord_id": 42}
            )
        assert response.status_code == 200
        assert response.json()["discord_thread_url"] is None


class TestDashboardRpMode:
    async def test_status_reports_mode_meters_and_no_afflictions(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/mode/{char_id}/status", params={"discord_id": 5}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "simulation"
        assert body["dead"] is False
        assert body["crime_enabled"] is True
        assert body["next_mode_switch_eligible_at"] is None
        assert body["next_crime_toggle_eligible_at"] is None
        assert body["afflictions"] == []

    async def test_switch_moves_to_the_new_mode(self, work_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/mode/{char_id}/switch",
                json={"discord_id": 5, "new_mode": "life"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "life"
        assert body["next_mode_switch_eligible_at"] is not None

    async def test_switch_refuses_within_the_cooldown_window(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={
                "rp_mode_changed_at": dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
            },
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/mode/{char_id}/switch",
                json={"discord_id": 5, "new_mode": "life"},
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "mode_switch_on_cooldown"

    async def test_crime_toggle_flips_and_stamps(self, work_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"rp_mode": "life"}
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/mode/{char_id}/crime-toggle",
                json={"discord_id": 5, "enabled": False},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["crime_enabled"] is False
        assert body["next_crime_toggle_eligible_at"] is not None

    async def test_crime_toggle_refuses_for_a_non_life_mode_character(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/mode/{char_id}/crime-toggle",
                json={"discord_id": 5, "enabled": False},
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "crime_toggle_wrong_mode"

    async def test_crime_toggle_refuses_within_the_cooldown_window(
        self, work_app, db_session_factory
    ):
        char_id = await seed_character(
            db_session_factory,
            discord_id=5,
            character_overrides={
                "rp_mode": "life",
                "crime_toggle_changed_at": dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
            },
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/mode/{char_id}/crime-toggle",
                json={"discord_id": 5, "enabled": False},
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "crime_toggle_on_cooldown"


class TestDashboardHousing:
    async def test_status_lists_own_home_and_district_listings(
        self, housing_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=42)
        prop_id = await seed_property(db_session_factory)
        transport = httpx.ASGITransport(app=housing_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/housing/{char_id}", params={"discord_id": 42}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["home_property_id"] is None
        assert [item["id"] for item in body["listings"]] == [prop_id]
        assert body["listings"][0]["price_label"] == "sale"

    async def test_buy_house_happy_path(self, housing_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=42, character_overrides={"money": 2000}
        )
        prop_id = await seed_property(db_session_factory, suggested_price=1000.0)
        transport = httpx.ASGITransport(app=housing_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/housing/{char_id}/{prop_id}/buy",
                json={"discord_id": 42},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["price"] == 1000
        assert body["financed"] is False
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            property_ = await session.get(Property, prop_id)
            assert character.money == 1000
            assert character.housing_property_id == prop_id
            assert property_.owner_kind == OwnerKind.CHARACTER.value
            assert property_.owner_id == char_id

    async def test_buy_refuses_wrong_district(self, housing_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=42, character_overrides={"district_id": 1}
        )
        prop_id = await seed_property(db_session_factory, district_id=0)
        transport = httpx.ASGITransport(app=housing_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/housing/{char_id}/{prop_id}/buy",
                json={"discord_id": 42},
            )
        assert response.status_code == 400

    async def test_buy_refuses_insufficient_funds(self, housing_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=42, character_overrides={"money": 0}
        )
        prop_id = await seed_property(db_session_factory, suggested_price=1000.0)
        transport = httpx.ASGITransport(app=housing_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/housing/{char_id}/{prop_id}/buy",
                json={"discord_id": 42},
            )
        assert response.status_code == 400

    async def test_rent_apartment_happy_path(self, housing_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        prop_id = await seed_property(
            db_session_factory, kind=PropertyKind.APARTMENT.value, suggested_price=50.0
        )
        transport = httpx.ASGITransport(app=housing_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/housing/{char_id}/{prop_id}/rent",
                json={"discord_id": 42},
            )
        assert response.status_code == 200
        assert response.json()["price"] == 50
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.housing_property_id == prop_id
            lease = (
                await session.execute(
                    select(ApartmentLease).where(ApartmentLease.property_id == prop_id)
                )
            ).scalar_one_or_none()
            assert lease is not None
            assert lease.tenant_character_id == char_id

    async def test_move_out_happy_path(self, housing_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        prop_id = await seed_property(
            db_session_factory, kind=PropertyKind.APARTMENT.value, suggested_price=50.0
        )
        transport = httpx.ASGITransport(app=housing_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                f"/activity/dashboard/housing/{char_id}/{prop_id}/rent",
                json={"discord_id": 42},
            )
            response = await client.post(
                f"/activity/dashboard/housing/{char_id}/move-out", json={"discord_id": 42}
            )
        assert response.status_code == 200
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.housing_property_id is None
            lease = (
                await session.execute(
                    select(ApartmentLease).where(ApartmentLease.property_id == prop_id)
                )
            ).scalar_one_or_none()
            assert lease is None

    async def test_move_out_refuses_without_a_lease(self, housing_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        transport = httpx.ASGITransport(app=housing_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/housing/{char_id}/move-out", json={"discord_id": 42}
            )
        assert response.status_code == 400

    async def test_refinance_happy_path(self, housing_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=42, character_overrides={"money": 0}
        )
        prop_id = await seed_house(db_session_factory, owner_id=char_id, suggested_price=1000.0)
        transport = httpx.ASGITransport(app=housing_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/housing/{char_id}/{prop_id}/refinance",
                json={"discord_id": 42, "amount": 200},
            )
        assert response.status_code == 200
        assert response.json()["amount"] == 200
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            property_ = await session.get(Property, prop_id)
            assert character.money == 200
            assert property_.mortgage_principal == 200

    async def test_refinance_refuses_someone_elses_property(self, housing_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        other_id = await seed_character(
            db_session_factory, discord_id=99, character_overrides={"name": "Owner"}
        )
        prop_id = await seed_house(db_session_factory, owner_id=other_id)
        transport = httpx.ASGITransport(app=housing_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/housing/{char_id}/{prop_id}/refinance",
                json={"discord_id": 42, "amount": 50},
            )
        assert response.status_code == 400

    async def test_sell_lists_and_delists(self, housing_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        prop_id = await seed_house(db_session_factory, owner_id=char_id)
        transport = httpx.ASGITransport(app=housing_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/housing/{char_id}/{prop_id}/sell",
                json={"discord_id": 42, "price": 1500},
            )
            assert response.json()["for_sale"] is True
            response = await client.post(
                f"/activity/dashboard/housing/{char_id}/{prop_id}/sell",
                json={"discord_id": 42, "price": None},
            )
        assert response.status_code == 200
        assert response.json()["for_sale"] is False

    async def test_auction_start_and_bid(self, housing_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        bidder_id = await seed_character(
            db_session_factory,
            discord_id=99,
            character_overrides={"name": "Bidder", "money": 5000},
        )
        prop_id = await seed_house(db_session_factory, owner_id=char_id)
        transport = httpx.ASGITransport(app=housing_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            start_response = await client.post(
                f"/activity/dashboard/housing/{char_id}/{prop_id}/auction-start",
                json={"discord_id": 42, "minimum_bid": 500},
            )
            assert start_response.status_code == 200
            bid_response = await client.post(
                f"/activity/dashboard/housing/{bidder_id}/{prop_id}/auction-bid",
                json={"discord_id": 99, "amount": 600},
            )
        assert bid_response.status_code == 200
        assert bid_response.json()["amount"] == 600
        async with db_session_factory() as session:
            auction = (
                await session.execute(
                    select(PropertyAuction).where(PropertyAuction.property_id == prop_id)
                )
            ).scalar_one_or_none()
            assert auction is not None
            assert auction.current_bid == 600
            assert auction.current_bidder_id == bidder_id

    async def test_auction_bid_refuses_below_minimum(self, housing_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=42)
        bidder_id = await seed_character(
            db_session_factory, discord_id=99, character_overrides={"name": "Bidder"}
        )
        prop_id = await seed_house(db_session_factory, owner_id=char_id)
        transport = httpx.ASGITransport(app=housing_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                f"/activity/dashboard/housing/{char_id}/{prop_id}/auction-start",
                json={"discord_id": 42, "minimum_bid": 500},
            )
            response = await client.post(
                f"/activity/dashboard/housing/{bidder_id}/{prop_id}/auction-bid",
                json={"discord_id": 99, "amount": 100},
            )
        assert response.status_code == 400

    async def test_sleep_restores_fatigue_at_night(self, housing_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=42, character_overrides={"fatigue": 0.0}
        )
        transport = httpx.ASGITransport(app=housing_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/housing/{char_id}/sleep", json={"discord_id": 42}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["restored"] > 0
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.fatigue == body["fatigue"]

    async def test_inn_stay_happy_path(self, housing_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=42, character_overrides={"money": 200}
        )
        inn_id = await seed_property(
            db_session_factory, kind=PropertyKind.INN.value, suggested_price=20.0
        )
        transport = httpx.ASGITransport(app=housing_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/housing/{char_id}/{inn_id}/inn-stay",
                json={"discord_id": 42},
            )
        assert response.status_code == 200
        assert response.json()["price"] == 20
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.money == 180


async def give_inventory(session_factory, character_id: int, good_id: str, qty: int) -> None:
    async with session_factory() as session, session.begin():
        session.add(
            Inventory(
                owner_kind=OwnerKind.CHARACTER.value,
                owner_id=str(character_id),
                good_id=good_id,
                qty=qty,
            )
        )


class TestDashboardVitals:
    async def test_status_lists_owned_edible_and_drinkable_goods(
        self, vitals_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, discord_id=5)
        await give_inventory(db_session_factory, char_id, "grain", 2)
        await give_inventory(db_session_factory, char_id, "produce", 1)
        await give_inventory(db_session_factory, char_id, "coal", 3)
        transport = httpx.ASGITransport(app=vitals_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/activity/dashboard/vitals/{char_id}/status", params={"discord_id": 5}
            )
        assert response.status_code == 200
        body = response.json()
        assert [g["good_id"] for g in body["edible"]] == ["grain"]
        assert body["edible"][0]["cook_method"] == "oven"
        assert [g["good_id"] for g in body["drinkable"]] == ["produce"]
        assert body["has_bed"] is False
        assert len(body["entertainment"]) == 6
        assert {g["game_id"] for g in body["entertainment"]} == {
            "minesweeper",
            "snake",
            "connect4",
            "coinflip",
            "poison",
            "solitaire",
        }

    async def test_eat_consumes_inventory_and_relieves_hunger(
        self, vitals_app, db_session_factory
    ):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"hunger": 50.0}
        )
        await give_inventory(db_session_factory, char_id, "grain", 2)
        transport = httpx.ASGITransport(app=vitals_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/vitals/{char_id}/eat",
                json={"discord_id": 5, "good_id": "grain"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["hunger"] == 35.0
        async with db_session_factory() as session:
            row = await session.get(Inventory, (OwnerKind.CHARACTER.value, str(char_id), "grain"))
            assert row.qty == 1

    async def test_eat_with_bonus_doubles_a_cookable_goods_relief(
        self, vitals_app, db_session_factory
    ):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"hunger": 50.0}
        )
        await give_inventory(db_session_factory, char_id, "grain", 1)
        transport = httpx.ASGITransport(app=vitals_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/vitals/{char_id}/eat",
                json={"discord_id": 5, "good_id": "grain", "bonus": True},
            )
        assert response.status_code == 200
        assert response.json()["hunger"] == 20.0

    async def test_eat_refuses_without_inventory(self, vitals_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=vitals_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/vitals/{char_id}/eat",
                json={"discord_id": 5, "good_id": "grain"},
            )
        assert response.status_code == 400

    async def test_drink_consumes_inventory_and_relieves_thirst(
        self, vitals_app, db_session_factory
    ):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"thirst": 50.0}
        )
        await give_inventory(db_session_factory, char_id, "produce", 1)
        transport = httpx.ASGITransport(app=vitals_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/vitals/{char_id}/drink",
                json={"discord_id": 5, "good_id": "produce"},
            )
        assert response.status_code == 200
        assert response.json()["thirst"] == 25.0

    async def test_entertain_credits_the_games_sanity_value(self, vitals_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"sanity": 50.0}
        )
        transport = httpx.ASGITransport(app=vitals_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/vitals/{char_id}/entertain",
                json={"discord_id": 5, "game_id": "solitaire"},
            )
        assert response.status_code == 200
        assert response.json()["sanity"] == 65.0

    async def test_entertain_refuses_an_unknown_game(self, vitals_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=vitals_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/vitals/{char_id}/entertain",
                json={"discord_id": 5, "game_id": "chess"},
            )
        assert response.status_code == 400


class TestDashboardStaff:
    async def test_jail_refuses_without_the_staff_role(self, staff_app, db_session_factory):
        await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1, 2]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/jail",
                    json={"discord_id": 42, "character_name": "Wren", "ticks": 50},
                )
        assert response.status_code == 403
        assert response.json()["detail"] == "staff_only"

    async def test_jail_fails_closed_when_the_discord_lookup_errors(
        self, staff_app, db_session_factory
    ):
        await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(side_effect=httpx.ConnectError("boom"))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/jail",
                    json={"discord_id": 42, "character_name": "Wren", "ticks": 50},
                )
        assert response.status_code == 403

    async def test_jail_happy_path_with_the_staff_role(self, staff_app, db_session_factory):
        char_id = await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/jail",
                    json={
                        "discord_id": 42,
                        "character_name": "Wren",
                        "ticks": 50,
                        "reason": "brawling",
                    },
                )
        assert response.status_code == 200
        body = response.json()
        assert body["character_name"] == "Wren"
        assert body["base_ticks"] == 50
        assert body["prior_bonus_ticks"] == 0
        assert body["applied_ticks"] == 50
        assert body["jailed_until_tick"] == 50
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.jailed_until_tick == 50
            assert character.jail_count == 1
            staff_actions = (
                (await session.execute(select(StaffAction).where(StaffAction.action == "jail")))
                .scalars()
                .all()
            )
        assert len(staff_actions) == 1
        assert staff_actions[0].staff_discord_id == 42
        assert staff_actions[0].target == str(char_id)
        assert staff_actions[0].payload == {
            "ticks": 50,
            "applied_ticks": 50,
            "reason": "brawling",
        }

    async def test_jail_applied_ticks_includes_the_prior_bonus(
        self, staff_app, db_session_factory
    ):
        # Regression test: a staff member entered ticks=25 and the response
        # said "jailed for 43 ticks", which read like a bug but is
        # `commit_to_jail`'s existing priors scaling (`jail_count *
        # JAIL_PRIOR_TICKS_PER_COUNT`) doing exactly what it's meant to --
        # the dashboard just didn't surface the breakdown, so it looked
        # broken. `base_ticks`/`prior_bonus_ticks` exist so the response
        # (and the Staff tab's result line) can show both halves.
        char_id = await seed_character(
            db_session_factory, character_overrides={"name": "Wren", "jail_count": 3}
        )
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/jail",
                    json={"discord_id": 42, "character_name": "Wren", "ticks": 25},
                )
        assert response.status_code == 200
        body = response.json()
        assert body["base_ticks"] == 25
        assert body["prior_bonus_ticks"] == 18  # 3 priors * JAIL_PRIOR_TICKS_PER_COUNT(6)
        assert body["applied_ticks"] == 43
        assert body["jailed_until_tick"] == 43
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.jail_count == 4

    async def test_jail_refuses_an_unknown_character(self, staff_app, db_session_factory):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/jail",
                    json={"discord_id": 42, "character_name": "Nobody", "ticks": 50},
                )
        assert response.status_code == 404
        assert response.json()["detail"] == "character_not_found"

    async def test_jail_refuses_a_non_positive_tick_count(self, staff_app, db_session_factory):
        await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/jail",
                    json={"discord_id": 42, "character_name": "Wren", "ticks": 0},
                )
        assert response.status_code == 400
        assert response.json()["detail"] == "invalid_ticks"

    async def test_post_staff_log_posts_to_the_configured_channel(self):
        # Exercised directly rather than through the `/staff/jail` route:
        # both the route's outer test request and this best-effort call
        # are `httpx.AsyncClient.post`, so patching that method globally
        # while also driving the route through an `httpx.AsyncClient`-
        # backed ASGI transport (this module's usual pattern for an async
        # test) would intercept the outer request too. `discord_staff.
        # post_staff_log`'s own behavior -- what URL/headers/body it sends,
        # and that a failure is swallowed -- is what's worth covering here.
        fake_response = httpx.Response(200, json={})
        with patch.object(
            httpx.AsyncClient, "post", AsyncMock(return_value=fake_response)
        ) as mock_post:
            await discord_staff.post_staff_log(
                channel_id=555, bot_token="test-bot-token", content="**Staff action:** ... `Wren`"
            )
        mock_post.assert_awaited_once()
        args, kwargs = mock_post.call_args
        assert args[0] == f"{discord_staff.DISCORD_API_BASE}/channels/555/messages"
        assert kwargs["headers"]["Authorization"] == "Bot test-bot-token"
        assert "Wren" in kwargs["json"]["content"]

    async def test_post_staff_log_is_a_noop_without_a_channel_or_token(self):
        with patch.object(httpx.AsyncClient, "post", AsyncMock()) as mock_post:
            await discord_staff.post_staff_log(channel_id=0, bot_token="", content="ignored")
        mock_post.assert_not_awaited()

    async def test_post_staff_log_swallows_a_failed_request(self):
        with patch.object(
            httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ConnectError("boom"))
        ):
            await discord_staff.post_staff_log(
                channel_id=555, bot_token="test-bot-token", content="ignored"
            )  # no raise


class TestDashboardStaffExtras:
    """The rest of `/staff ...`'s subcommands, consolidated into the same
    Staff tab (see `dashboard_routes.build_staff_router`'s own comment for
    which two stayed bot-only). One 403-without-the-role check plus one
    happy path per endpoint is enough here -- the role check itself
    (`_require_staff`) is already covered exhaustively by `TestDashboardStaff`
    above, and it's the exact same helper on every route in this class."""

    async def test_give_money_refuses_without_the_staff_role(self, staff_app, db_session_factory):
        await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/give/money",
                    json={"discord_id": 42, "character_name": "Wren", "amount": 50},
                )
        assert response.status_code == 403

    async def test_give_money_happy_path(self, staff_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, character_overrides={"name": "Wren", "money": 100}
        )
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/give/money",
                    json={"discord_id": 42, "character_name": "Wren", "amount": 50},
                )
        assert response.status_code == 200
        assert response.json()["new_balance"] == 150
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.money == 150
            actions = (
                await session.execute(
                    select(StaffAction).where(StaffAction.action == "give_money")
                )
            ).scalars().all()
        assert len(actions) == 1 and actions[0].payload == {"amount": 50}

    async def test_give_money_never_takes_a_character_below_zero(
        self, staff_app, db_session_factory
    ):
        char_id = await seed_character(
            db_session_factory, character_overrides={"name": "Wren", "money": 20}
        )
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/give/money",
                    json={"discord_id": 42, "character_name": "Wren", "amount": -1000},
                )
        assert response.status_code == 200
        assert response.json()["new_balance"] == 0
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.money == 0

    async def test_give_item_happy_path(self, staff_market_app, db_session_factory):
        char_id = await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_market_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/give/item",
                    json={
                        "discord_id": 42,
                        "character_name": "Wren",
                        "good_id": "grain",
                        "qty": 3,
                    },
                )
        assert response.status_code == 200
        assert response.json() == {
            "character_name": "Wren",
            "good_id": "grain",
            "good_name": "Grain",
            "new_qty": 3,
        }
        async with db_session_factory() as session:
            inv = await session.get(Inventory, (OwnerKind.CHARACTER.value, str(char_id), "grain"))
            assert inv is not None and inv.qty == 3

    async def test_give_item_refuses_an_unknown_good(self, staff_market_app, db_session_factory):
        await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_market_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/give/item",
                    json={
                        "discord_id": 42,
                        "character_name": "Wren",
                        "good_id": "nonexistent",
                        "qty": 1,
                    },
                )
        assert response.status_code == 404

    async def test_give_position_grants_then_revokes(self, staff_app, db_session_factory):
        char_id = await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                grant = await client.post(
                    "/activity/dashboard/staff/give/position",
                    json={
                        "discord_id": 42,
                        "character_name": "Wren",
                        "position": Position.VICTOR.value,
                        "grant": True,
                    },
                )
                revoke = await client.post(
                    "/activity/dashboard/staff/give/position",
                    json={
                        "discord_id": 42,
                        "character_name": "Wren",
                        "position": Position.VICTOR.value,
                        "grant": False,
                    },
                )
        assert grant.status_code == 200
        assert grant.json()["positions"] == [Position.VICTOR.value]
        assert revoke.status_code == 200
        assert revoke.json()["positions"] == []
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.positions == []

    async def test_give_job_sets_title_and_shift(self, staff_app, db_session_factory):
        char_id = await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/give/job",
                    json={
                        "discord_id": 42,
                        "character_name": "Wren",
                        "job_title": "Baker",
                        "shift_phase": DayPhase.MORNING.value,
                        "illicit": False,
                    },
                )
        assert response.status_code == 200
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.job_title == "Baker"
            assert character.shift_phase == DayPhase.MORNING.value
            assert character.job_is_illicit is False

    async def test_give_mastery_sets_shifts_completed_directly(
        self, staff_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/give/mastery",
                    json={"discord_id": 42, "character_name": "Wren", "shifts_completed": 40},
                )
        assert response.status_code == 200
        body = response.json()
        assert body["shifts_completed"] == 40
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.shifts_completed == 40

    async def test_give_mastery_jumps_to_a_levels_threshold(self, staff_app, db_session_factory):
        char_id = await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/give/mastery",
                    json={"discord_id": 42, "character_name": "Wren", "level": JobLevel.EXPERT.value},
                )
        assert response.status_code == 200
        assert response.json()["level"] == JobLevel.EXPERT.value
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.shifts_completed == constants.JOB_LEVEL_SHIFT_THRESHOLDS["expert"]

    async def test_give_mastery_needs_a_value(self, staff_app, db_session_factory):
        await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/give/mastery",
                    json={"discord_id": 42, "character_name": "Wren"},
                )
        assert response.status_code == 400

    async def test_kill_character_sets_status_and_cause(self, staff_app, db_session_factory):
        char_id = await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/character/kill",
                    json={"discord_id": 42, "character_name": "Wren", "reason": "Fell in the arena"},
                )
        assert response.status_code == 200
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.status == CharacterStatus.DEAD.value
            assert character.death_cause == "Fell in the arena"

    async def test_note_logs_a_staff_action_without_changing_the_character(
        self, staff_app, db_session_factory
    ):
        char_id = await seed_character(db_session_factory, character_overrides={"name": "Wren"})
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/character/note",
                    json={"discord_id": 42, "character_name": "Wren", "text": "Watching this one."},
                )
        assert response.status_code == 200
        async with db_session_factory() as session:
            actions = (
                await session.execute(select(StaffAction).where(StaffAction.action == "note"))
            ).scalars().all()
        assert len(actions) == 1
        assert actions[0].target == str(char_id)
        assert actions[0].payload == {"text": "Watching this one."}

    async def test_delete_pending_removes_a_pending_application(
        self, staff_app, db_session_factory
    ):
        char_id = await seed_character(
            db_session_factory,
            character_overrides={"name": "Hopeful", "status": CharacterStatus.PENDING.value},
        )
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/character/delete-pending",
                    json={"discord_id": 42, "character_name": "Hopeful"},
                )
        assert response.status_code == 200
        async with db_session_factory() as session:
            assert await session.get(Character, char_id) is None

    async def test_delete_pending_refuses_an_approved_character(
        self, staff_app, db_session_factory
    ):
        await seed_character(
            db_session_factory,
            character_overrides={"name": "Wren", "status": CharacterStatus.APPROVED.value},
        )
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/character/delete-pending",
                    json={"discord_id": 42, "character_name": "Wren"},
                )
        assert response.status_code == 400

    async def test_character_limit_override_and_reset(self, staff_app, db_session_factory):
        await seed_character(db_session_factory, discord_id=99)
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                set_resp = await client.post(
                    "/activity/dashboard/staff/character/limit",
                    json={"discord_id": 42, "target_discord_id": 99, "limit": 7},
                )
                reset_resp = await client.post(
                    "/activity/dashboard/staff/character/limit",
                    json={"discord_id": 42, "target_discord_id": 99, "limit": None},
                )
        assert set_resp.status_code == 200 and set_resp.json()["limit"] == 7
        assert reset_resp.status_code == 200 and reset_resp.json()["limit"] is None
        async with db_session_factory() as session:
            user = (
                await session.execute(select(User).where(User.discord_id == 99))
            ).scalar_one()
            assert user.max_characters_override is None

    async def test_ban_sets_banned_at(self, staff_app, db_session_factory):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/ban",
                    json={"discord_id": 42, "target_discord_id": 12345},
                )
        assert response.status_code == 200
        async with db_session_factory() as session:
            user = (
                await session.execute(select(User).where(User.discord_id == 12345))
            ).scalar_one()
            assert user.banned_at is not None

    async def test_housing_set_price_overrides_and_clears(self, staff_app, db_session_factory):
        property_id = await seed_property(db_session_factory, suggested_price=1000.0)
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                set_resp = await client.post(
                    "/activity/dashboard/staff/housing/set-price",
                    json={"discord_id": 42, "property_id": property_id, "price": 2500.0},
                )
                clear_resp = await client.post(
                    "/activity/dashboard/staff/housing/set-price",
                    json={"discord_id": 42, "property_id": property_id, "price": None},
                )
        assert set_resp.status_code == 200 and set_resp.json()["price"] == 2500.0
        assert clear_resp.status_code == 200 and clear_resp.json()["price"] is None
        async with db_session_factory() as session:
            property_ = await session.get(Property, property_id)
            assert property_.asking_price is None

    async def test_engagement_timeout_creates_then_updates_the_singleton_row(
        self, staff_app, db_session_factory
    ):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                first = await client.post(
                    "/activity/dashboard/staff/engagement/timeout",
                    json={"discord_id": 42, "minutes": 15},
                )
                second = await client.post(
                    "/activity/dashboard/staff/engagement/timeout",
                    json={"discord_id": 42, "minutes": 30},
                )
        assert first.status_code == 200 and first.json()["minutes"] == 15
        assert second.status_code == 200 and second.json()["minutes"] == 30
        async with db_session_factory() as session:
            settings_row = await session.get(EngagementSettings, 1)
            assert settings_row.idle_timeout_minutes == 30

    async def test_district_state_view_and_crackdown(self, staff_app, db_session_factory):
        async with db_session_factory() as session, session.begin():
            session.add(
                DistrictState(
                    district_id=1,
                    crisis_level=2,
                    crisis_kind="unrest",
                    unrest=0.5,
                    peacekeeper_pressure=0.3,
                )
            )
        # `fetch_is_staff` (not `httpx.AsyncClient.get`) is what's patched
        # here -- this test issues its own GET through the same client
        # class the app uses to reach Discord's API, and patching that
        # globally would intercept the outer test request too (see
        # `post_staff_log`'s own tests below for the identical trap on the
        # POST side).
        with patch.object(discord_staff, "fetch_is_staff", AsyncMock(return_value=True)):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                view = await client.get(
                    "/activity/dashboard/staff/district/1", params={"discord_id": 42}
                )
                crackdown = await client.post(
                    "/activity/dashboard/staff/district/crackdown",
                    json={"discord_id": 42, "district_id": 1, "duration_ticks": 10},
                )
        assert view.status_code == 200
        assert view.json()["crisis_level"] == 2
        assert crackdown.status_code == 200
        assert crackdown.json()["until_tick"] == 10
        async with db_session_factory() as session:
            row = await session.get(DistrictState, 1)
            assert row.crackdown_until_tick == 10
            assert row.peacekeeper_pressure > 0.3

    async def test_npc_add_then_rename_background_appearance_traits_speech(
        self, staff_app, db_session_factory
    ):
        # Same reasoning as the district-state test above: this test issues
        # its own GET (the NPC listing), so `fetch_is_staff` is patched
        # directly rather than `httpx.AsyncClient.get`.
        with patch.object(discord_staff, "fetch_is_staff", AsyncMock(return_value=True)):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                add_resp = await client.post(
                    "/activity/dashboard/staff/npcs/add",
                    json={
                        "discord_id": 42,
                        "name": "Cato",
                        "district_id": 1,
                        "age": 18,
                        "home_location_id": "square",
                        "traits": "brash, proud",
                        "gender": "male",
                    },
                )
                assert add_resp.status_code == 200
                npc_id = add_resp.json()["id"]

                listing = await client.get(
                    "/activity/dashboard/staff/npcs",
                    params={"district_id": 1, "discord_id": 42},
                )
                assert any(row["id"] == npc_id for row in listing.json())

                rename = await client.post(
                    "/activity/dashboard/staff/npcs/rename",
                    json={"discord_id": 42, "npc_id": npc_id, "new_name": "Cato Ludara"},
                )
                background = await client.post(
                    "/activity/dashboard/staff/npcs/background",
                    json={"discord_id": 42, "npc_id": npc_id, "backstory": "A career from D2."},
                )
                appearance = await client.post(
                    "/activity/dashboard/staff/npcs/appearance",
                    json={"discord_id": 42, "npc_id": npc_id, "appearance": "Tall, scarred."},
                )
                traits = await client.post(
                    "/activity/dashboard/staff/npcs/traits",
                    json={"discord_id": 42, "npc_id": npc_id, "traits": "ruthless, loyal"},
                )
                speech = await client.post(
                    "/activity/dashboard/staff/npcs/speech",
                    json={"discord_id": 42, "npc_id": npc_id, "tone": "blunt"},
                )
        assert rename.status_code == 200 and rename.json()["name"] == "Cato Ludara"
        assert background.status_code == 200
        assert appearance.status_code == 200
        assert traits.status_code == 200
        assert speech.status_code == 200
        async with db_session_factory() as session:
            npc = await session.get(Npc, npc_id)
            assert npc.name == "Cato Ludara"
            assert npc.backstory_override == "A career from D2."
            assert npc.appearance_override == "Tall, scarred."
            assert npc.traits == ["ruthless", "loyal"]
            assert npc.speech_style["tone"] == "blunt"

    async def test_npc_add_refuses_an_unknown_location(self, staff_app, db_session_factory):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/npcs/add",
                    json={
                        "discord_id": 42,
                        "name": "Cato",
                        "district_id": 1,
                        "age": 18,
                        "home_location_id": "nonexistent",
                        "traits": "brash",
                    },
                )
        assert response.status_code == 404


class TestDashboardStaffMarket:
    async def test_add_stock_refuses_without_the_staff_role(self, staff_market_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1, 2]))
        ):
            transport = httpx.ASGITransport(app=staff_market_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/market/add-stock",
                    json={"discord_id": 42, "district_id": 1, "good_id": "grain", "qty": 10},
                )
        assert response.status_code == 403

    async def test_add_stock_creates_a_row_when_none_exists(self, staff_market_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_market_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/market/add-stock",
                    json={"discord_id": 42, "district_id": 1, "good_id": "grain", "qty": 10},
                )
        assert response.status_code == 200
        body = response.json()
        assert body["good_name"] == "Grain"
        assert body["new_supply"] == 10.0

    async def test_add_stock_tops_up_existing_supply(
        self, staff_market_app, db_session_factory
    ):
        async with db_session_factory() as session, session.begin():
            session.add(MarketPrice(district_id=1, good_id="grain", price=2.0, supply=5.0, tick=0))
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_market_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/market/add-stock",
                    json={"discord_id": 42, "district_id": 1, "good_id": "grain", "qty": 3},
                )
        assert response.status_code == 200
        assert response.json()["new_supply"] == 8.0

    async def test_add_stock_404s_for_an_unknown_good(self, staff_market_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_market_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/market/add-stock",
                    json={"discord_id": 42, "district_id": 1, "good_id": "nonexistent", "qty": 1},
                )
        assert response.status_code == 404
        assert response.json()["detail"] == "staff_good_not_found"

    async def test_add_stock_rejects_a_non_positive_qty(self, staff_market_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_market_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/market/add-stock",
                    json={"discord_id": 42, "district_id": 1, "good_id": "grain", "qty": 0},
                )
        assert response.status_code == 400
        assert response.json()["detail"] == "market_stock_qty_must_be_positive"


class TestDashboardStaffLayers:
    async def test_create_category_refuses_without_the_staff_role(self, staff_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1, 2]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/layers/categories",
                    json={"discord_id": 42, "name": "Hair", "z_index": 1},
                )
        assert response.status_code == 403

    async def test_create_category_happy_path(self, staff_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/layers/categories",
                    json={"discord_id": 42, "name": "Hair", "z_index": 3},
                )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Hair"
        assert body["z_index"] == 3
        assert body["options"] == []

    async def test_create_category_rejects_an_empty_name(self, staff_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/layers/categories",
                    json={"discord_id": 42, "name": "   ", "z_index": 0},
                )
        assert response.status_code == 400

    async def test_update_category_renames_and_reorders(self, staff_app, db_session_factory):
        category_id, _ = await seed_layer_category(db_session_factory, name="Hair", z_index=0)
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.patch(
                    f"/activity/dashboard/staff/layers/categories/{category_id}",
                    json={"discord_id": 42, "name": "Hairstyles", "z_index": 9},
                )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Hairstyles"
        assert body["z_index"] == 9

    async def test_update_category_refuses_an_unknown_id(self, staff_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.patch(
                    "/activity/dashboard/staff/layers/categories/999",
                    json={"discord_id": 42, "name": "Whatever"},
                )
        assert response.status_code == 404

    async def test_delete_category_removes_it_and_its_options(
        self, staff_app_with_uploads, db_session_factory, tmp_path
    ):
        (tmp_path / "uploads" / "layers").mkdir(parents=True)
        image_path = tmp_path / "uploads" / "layers" / "existing.png"
        image_path.write_bytes(b"fake-png-bytes")
        category_id, _ = await seed_layer_category(
            db_session_factory, name="Hair", option_names=["Braid"]
        )
        # Point the seeded option at the file actually on disk so deletion
        # has something real to unlink.
        async with db_session_factory() as session, session.begin():
            option = (
                await session.execute(select(LayerOption).where(LayerOption.category_id == category_id))
            ).scalar_one()
            option.image_path = "uploads/layers/existing.png"

        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app_with_uploads)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/activity/dashboard/staff/layers/categories/{category_id}/delete",
                    json={"discord_id": 42},
                )
        assert response.status_code == 200
        assert not image_path.exists()
        async with db_session_factory() as session:
            assert await session.get(LayerCategory, category_id) is None

    async def test_upload_option_saves_the_file_and_creates_a_row(
        self, staff_app_with_uploads, db_session_factory, tmp_path
    ):
        category_id, _ = await seed_layer_category(db_session_factory, name="Hair")
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app_with_uploads)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/activity/dashboard/staff/layers/categories/{category_id}/options",
                    data={"discord_id": "42", "name": "Braid"},
                    files={"file": ("braid.png", b"fake-png-bytes", "image/png")},
                )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Braid"
        assert body["image_url"].startswith("/uploads/layers/")
        saved_path = tmp_path / body["image_url"].removeprefix("/")
        assert saved_path.exists()
        assert saved_path.read_bytes() == b"fake-png-bytes"

    async def test_upload_option_rejects_a_disallowed_content_type(
        self, staff_app_with_uploads, db_session_factory
    ):
        category_id, _ = await seed_layer_category(db_session_factory, name="Hair")
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app_with_uploads)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/activity/dashboard/staff/layers/categories/{category_id}/options",
                    data={"discord_id": "42", "name": "Braid"},
                    files={"file": ("braid.svg", b"<svg></svg>", "image/svg+xml")},
                )
        assert response.status_code == 400

    async def test_upload_option_refuses_without_the_staff_role(
        self, staff_app_with_uploads, db_session_factory
    ):
        category_id, _ = await seed_layer_category(db_session_factory, name="Hair")
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1, 2]))
        ):
            transport = httpx.ASGITransport(app=staff_app_with_uploads)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/activity/dashboard/staff/layers/categories/{category_id}/options",
                    data={"discord_id": "42", "name": "Braid"},
                    files={"file": ("braid.png", b"fake-png-bytes", "image/png")},
                )
        assert response.status_code == 403

    async def test_delete_option_removes_the_file_and_row(
        self, staff_app_with_uploads, db_session_factory, tmp_path
    ):
        (tmp_path / "uploads" / "layers").mkdir(parents=True)
        image_path = tmp_path / "uploads" / "layers" / "existing.png"
        image_path.write_bytes(b"fake-png-bytes")
        _category_id, (option_id,) = await seed_layer_category(
            db_session_factory, name="Hair", option_names=["Braid"]
        )
        async with db_session_factory() as session, session.begin():
            option = await session.get(LayerOption, option_id)
            option.image_path = "uploads/layers/existing.png"

        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app_with_uploads)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/activity/dashboard/staff/layers/options/{option_id}/delete",
                    json={"discord_id": 42},
                )
        assert response.status_code == 200
        assert not image_path.exists()
        async with db_session_factory() as session:
            assert await session.get(LayerOption, option_id) is None


class TestDashboardPanemHistory:
    # `discord_staff.fetch_is_staff` (rather than `httpx.AsyncClient.get`)
    # is what's mocked below -- these routes are read via `client.get`
    # itself, so patching the same method Discord's own role lookup uses
    # would intercept the outer test request too (see `test_post_staff_
    # log_posts_to_the_configured_channel`'s own comment on this exact
    # trap, right above `TestDashboardAfflictionTypes`).
    async def test_list_refuses_without_the_staff_role(self, staff_app):
        with patch.object(discord_staff, "fetch_is_staff", AsyncMock(return_value=False)):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/activity/dashboard/history/panem-history", params={"discord_id": 42}
                )
        assert response.status_code == 403
        assert response.json()["detail"] == "staff_only"

    async def test_create_then_list_round_trips(self, staff_app):
        with patch.object(discord_staff, "fetch_is_staff", AsyncMock(return_value=True)):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                create_response = await client.post(
                    "/activity/dashboard/history/panem-history",
                    json={
                        "discord_id": 42,
                        "keywords": [" Dark Days ", "", "district 13"],
                        "text": "  The Dark Days ended with the Treaty of Treason.  ",
                    },
                )
                assert create_response.status_code == 200
                created = create_response.json()
                assert created["keywords"] == ["Dark Days", "district 13"]
                assert created["text"] == "The Dark Days ended with the Treaty of Treason."
                assert created["created_by_staff_discord_id"] == 42

                list_response = await client.get(
                    "/activity/dashboard/history/panem-history", params={"discord_id": 42}
                )
        assert list_response.status_code == 200
        entries = list_response.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["id"] == created["id"]

    async def test_create_rejects_no_real_keywords(self, staff_app):
        with patch.object(discord_staff, "fetch_is_staff", AsyncMock(return_value=True)):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/history/panem-history",
                    json={"discord_id": 42, "keywords": ["  "], "text": "Some fact."},
                )
        assert response.status_code == 400
        assert response.json()["detail"] == "panem_history_needs_a_keyword"

    async def test_delete_removes_the_entry(self, staff_app):
        with patch.object(discord_staff, "fetch_is_staff", AsyncMock(return_value=True)):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                created = (
                    await client.post(
                        "/activity/dashboard/history/panem-history",
                        json={"discord_id": 42, "keywords": ["a"], "text": "fact"},
                    )
                ).json()
                delete_response = await client.post(
                    f"/activity/dashboard/history/panem-history/{created['id']}/delete",
                    json={"discord_id": 42},
                )
                assert delete_response.status_code == 200
                list_response = await client.get(
                    "/activity/dashboard/history/panem-history", params={"discord_id": 42}
                )
        assert list_response.json()["entries"] == []

    async def test_delete_404s_for_an_unknown_entry(self, staff_app):
        with patch.object(discord_staff, "fetch_is_staff", AsyncMock(return_value=True)):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/history/panem-history/999999/delete",
                    json={"discord_id": 42},
                )
        assert response.status_code == 404


class TestDashboardWorldLore:
    async def test_get_refuses_without_the_staff_role(self, staff_app):
        with patch.object(discord_staff, "fetch_is_staff", AsyncMock(return_value=False)):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/activity/dashboard/history/world-lore", params={"discord_id": 42}
                )
        assert response.status_code == 403

    async def test_get_defaults_to_empty_notes(self, staff_app):
        with patch.object(discord_staff, "fetch_is_staff", AsyncMock(return_value=True)):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/activity/dashboard/history/world-lore", params={"discord_id": 42}
                )
        assert response.status_code == 200
        assert response.json()["alternate_universe_notes"] == ""

    async def test_save_then_get_round_trips(self, staff_app):
        with patch.object(discord_staff, "fetch_is_staff", AsyncMock(return_value=True)):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                save_response = await client.put(
                    "/activity/dashboard/history/world-lore",
                    json={"discord_id": 42, "alternate_universe_notes": "  A new canon note.  "},
                )
                assert save_response.status_code == 200
                assert save_response.json()["alternate_universe_notes"] == "A new canon note."

                get_response = await client.get(
                    "/activity/dashboard/history/world-lore", params={"discord_id": 42}
                )
        assert get_response.json()["alternate_universe_notes"] == "A new canon note."


async def seed_affliction_type(session_factory, **overrides: object) -> int:
    async with session_factory() as session, session.begin():
        defaults: dict[str, object] = dict(
            name="Broken Leg", description="Ouch", is_permanent=False
        )
        defaults.update(overrides)
        row = AfflictionType(**defaults)  # type: ignore[arg-type]
        session.add(row)
        await session.flush()
        return row.id


class TestDashboardAfflictionTypes:
    async def test_catalog_is_empty_with_no_seed_data(self, work_app):
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/dashboard/affliction-types")
        assert response.status_code == 200
        assert response.json()["types"] == []

    async def test_catalog_lists_staff_authored_types(self, work_app, db_session_factory):
        await seed_affliction_type(
            db_session_factory,
            name="Dehydrated",
            description="Needs water",
            cure_stat="thirst",
            cure_threshold=30.0,
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/activity/dashboard/affliction-types")
        assert response.status_code == 200
        types = response.json()["types"]
        assert len(types) == 1
        assert types[0]["name"] == "Dehydrated"
        assert types[0]["cure_stat"] == "thirst"
        assert types[0]["cure_threshold"] == 30.0


class TestDashboardStaffAfflictionTypes:
    async def test_create_refuses_without_the_staff_role(self, staff_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1, 2]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/affliction-types",
                    json={"discord_id": 42, "name": "Broken Leg"},
                )
        assert response.status_code == 403

    async def test_create_happy_path(self, staff_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/affliction-types",
                    json={
                        "discord_id": 42,
                        "name": "Broken Leg",
                        "description": "Took a bad fall",
                        "is_permanent": False,
                        "cure_stat": "health",
                        "cure_threshold": 80.0,
                    },
                )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Broken Leg"
        assert body["cure_stat"] == "health"
        assert body["cure_threshold"] == 80.0
        assert body["is_permanent"] is False

    async def test_create_rejects_an_invalid_cure_stat(self, staff_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/affliction-types",
                    json={
                        "discord_id": 42,
                        "name": "Broken Leg",
                        "cure_stat": "luck",
                        "cure_threshold": 80.0,
                    },
                )
        assert response.status_code == 400

    async def test_create_rejects_a_permanent_type_with_cure_fields(self, staff_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/affliction-types",
                    json={
                        "discord_id": 42,
                        "name": "Missing Finger",
                        "is_permanent": True,
                        "cure_stat": "health",
                        "cure_threshold": 80.0,
                    },
                )
        assert response.status_code == 400

    async def test_create_rejects_an_incomplete_cure_pair(self, staff_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/affliction-types",
                    json={"discord_id": 42, "name": "Broken Leg", "cure_stat": "health"},
                )
        assert response.status_code == 400

    async def test_create_rejects_a_duplicate_name(self, staff_app, db_session_factory):
        await seed_affliction_type(db_session_factory, name="Broken Leg")
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/activity/dashboard/staff/affliction-types",
                    json={"discord_id": 42, "name": "Broken Leg"},
                )
        assert response.status_code == 400

    async def test_update_edits_fields_and_replaces_the_cure_pair(
        self, staff_app, db_session_factory
    ):
        affliction_type_id = await seed_affliction_type(
            db_session_factory, name="Broken Leg", cure_stat="health", cure_threshold=80.0
        )
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.patch(
                    f"/activity/dashboard/staff/affliction-types/{affliction_type_id}",
                    json={
                        "discord_id": 42,
                        "description": "Updated",
                        "cure_stat": "fatigue",
                        "cure_threshold": 50.0,
                    },
                )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Broken Leg"
        assert body["description"] == "Updated"
        assert body["cure_stat"] == "fatigue"
        assert body["cure_threshold"] == 50.0

    async def test_update_refuses_an_unknown_id(self, staff_app):
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.patch(
                    "/activity/dashboard/staff/affliction-types/999",
                    json={"discord_id": 42},
                )
        assert response.status_code == 404

    async def test_delete_removes_the_row(self, staff_app, db_session_factory):
        affliction_type_id = await seed_affliction_type(db_session_factory)
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([777]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/activity/dashboard/staff/affliction-types/{affliction_type_id}/delete",
                    json={"discord_id": 42},
                )
        assert response.status_code == 200
        async with db_session_factory() as session:
            assert await session.get(AfflictionType, affliction_type_id) is None

    async def test_delete_refuses_without_the_staff_role(self, staff_app, db_session_factory):
        affliction_type_id = await seed_affliction_type(db_session_factory)
        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=_fake_member_response([1, 2]))
        ):
            transport = httpx.ASGITransport(app=staff_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/activity/dashboard/staff/affliction-types/{affliction_type_id}/delete",
                    json={"discord_id": 42},
                )
        assert response.status_code == 403


class TestDashboardPay:
    async def test_happy_path(self, work_app, db_session_factory):
        sender_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"money": 100}
        )
        await seed_character(
            db_session_factory,
            discord_id=6,
            character_overrides={"name": "Mark", "money": 50},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/pay/{sender_id}",
                json={"discord_id": 5, "target": "Mark", "amount": 10},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["sender_money"] == 90
        assert body["recipient_name"] == "Mark"
        async with db_session_factory() as session:
            recipient = (
                await session.execute(select(Character).where(Character.name == "Mark"))
            ).scalar_one()
            assert recipient.money == 60

    async def test_refuses_a_story_mode_recipient(self, work_app, db_session_factory):
        sender_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"money": 100}
        )
        await seed_character(
            db_session_factory,
            discord_id=6,
            character_overrides={"name": "Mark", "rp_mode": "story"},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/pay/{sender_id}",
                json={"discord_id": 5, "target": "Mark", "amount": 10},
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "pay_mode_forbidden"

    async def test_refuses_insufficient_money(self, work_app, db_session_factory):
        sender_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"money": 0}
        )
        await seed_character(
            db_session_factory, discord_id=6, character_overrides={"name": "Mark"}
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/pay/{sender_id}",
                json={"discord_id": 5, "target": "Mark", "amount": 10},
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "pay_insufficient_money"


class TestDashboardTrade:
    async def test_offer_then_accept_moves_money_and_goods(self, work_app, db_session_factory):
        initiator_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"money": 100}
        )
        await seed_character(
            db_session_factory,
            discord_id=6,
            character_overrides={"name": "Mark", "money": 100},
        )
        async with db_session_factory() as session, session.begin():
            session.add(
                Inventory(
                    owner_kind=OwnerKind.CHARACTER.value,
                    owner_id=str(initiator_id),
                    good_id="grain",
                    qty=5,
                )
            )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            offer_response = await client.post(
                f"/activity/dashboard/trade/{initiator_id}/offer",
                json={
                    "discord_id": 5,
                    "target": "Mark",
                    "give_good_id": "grain",
                    "give_qty": 3,
                    "want_money": 20,
                },
            )
            assert offer_response.status_code == 200
            trade_id = offer_response.json()["id"]

            outgoing_list = (
                await client.get(
                    f"/activity/dashboard/trade/{initiator_id}/list", params={"discord_id": 5}
                )
            ).json()
            assert outgoing_list["trades"][0]["direction"] == "outgoing"

        async with db_session_factory() as session:
            mark = (
                await session.execute(select(Character).where(Character.name == "Mark"))
            ).scalar_one()
        mark_id = mark.id

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            list_response = await client.get(
                f"/activity/dashboard/trade/{mark_id}/list", params={"discord_id": 6}
            )
            assert list_response.json()["trades"][0]["direction"] == "incoming"

            accept_response = await client.post(
                f"/activity/dashboard/trade/{mark_id}/{trade_id}/accept",
                json={"discord_id": 6},
            )
        assert accept_response.status_code == 200
        assert accept_response.json()["status"] == "accepted"

        async with db_session_factory() as session:
            initiator = await session.get(Character, initiator_id)
            mark = await session.get(Character, mark_id)
            assert initiator.money == 120
            assert mark.money == 80
            initiator_inv = await session.get(
                Inventory, (OwnerKind.CHARACTER.value, str(initiator_id), "grain")
            )
            mark_inv = await session.get(
                Inventory, (OwnerKind.CHARACTER.value, str(mark_id), "grain")
            )
            assert initiator_inv.qty == 2
            assert mark_inv.qty == 3

    async def test_offer_refuses_a_story_mode_party(self, work_app, db_session_factory):
        initiator_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"rp_mode": "story"}
        )
        await seed_character(
            db_session_factory, discord_id=6, character_overrides={"name": "Mark"}
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/trade/{initiator_id}/offer",
                json={"discord_id": 5, "target": "Mark", "give_money": 10},
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "trade_mode_forbidden"

    async def test_decline_leaves_money_and_goods_untouched(self, work_app, db_session_factory):
        initiator_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"money": 100}
        )
        recipient_id = await seed_character(
            db_session_factory,
            discord_id=6,
            character_overrides={"name": "Mark", "money": 100},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            offer_response = await client.post(
                f"/activity/dashboard/trade/{initiator_id}/offer",
                json={"discord_id": 5, "target": "Mark", "give_money": 10, "want_money": 5},
            )
            trade_id = offer_response.json()["id"]
            decline_response = await client.post(
                f"/activity/dashboard/trade/{recipient_id}/{trade_id}/decline",
                json={"discord_id": 6},
            )
        assert decline_response.status_code == 200
        assert decline_response.json()["status"] == "declined"
        async with db_session_factory() as session:
            initiator = await session.get(Character, initiator_id)
            recipient = await session.get(Character, recipient_id)
            assert initiator.money == 100
            assert recipient.money == 100

    async def test_cancel_only_allowed_by_the_initiator(self, work_app, db_session_factory):
        initiator_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"money": 100}
        )
        recipient_id = await seed_character(
            db_session_factory,
            discord_id=6,
            character_overrides={"name": "Mark", "money": 100},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            offer_response = await client.post(
                f"/activity/dashboard/trade/{initiator_id}/offer",
                json={"discord_id": 5, "target": "Mark", "give_money": 10, "want_money": 5},
            )
            trade_id = offer_response.json()["id"]

            wrong_cancel = await client.post(
                f"/activity/dashboard/trade/{recipient_id}/{trade_id}/cancel",
                json={"discord_id": 6},
            )
            assert wrong_cancel.status_code == 404

            cancel_response = await client.post(
                f"/activity/dashboard/trade/{initiator_id}/{trade_id}/cancel",
                json={"discord_id": 5},
            )
        assert cancel_response.status_code == 200
        assert cancel_response.json()["status"] == "cancelled"

    async def test_accept_refuses_a_stale_offer_cleanly(self, work_app, db_session_factory):
        """The offer promised 3 grain, but the initiator's inventory was
        drained to nothing before the recipient got around to accepting --
        `accept_trade` re-validates and refuses rather than going negative."""
        initiator_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"money": 100}
        )
        recipient_id = await seed_character(
            db_session_factory,
            discord_id=6,
            character_overrides={"name": "Mark", "money": 100},
        )
        async with db_session_factory() as session, session.begin():
            session.add(
                Inventory(
                    owner_kind=OwnerKind.CHARACTER.value,
                    owner_id=str(initiator_id),
                    good_id="grain",
                    qty=3,
                )
            )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            offer_response = await client.post(
                f"/activity/dashboard/trade/{initiator_id}/offer",
                json={
                    "discord_id": 5,
                    "target": "Mark",
                    "give_good_id": "grain",
                    "give_qty": 3,
                    "want_money": 10,
                },
            )
            trade_id = offer_response.json()["id"]

            async with db_session_factory() as session:
                inv = await session.get(
                    Inventory, (OwnerKind.CHARACTER.value, str(initiator_id), "grain")
                )
                inv.qty = 0
                await session.commit()

            accept_response = await client.post(
                f"/activity/dashboard/trade/{recipient_id}/{trade_id}/accept",
                json={"discord_id": 6},
            )
        assert accept_response.status_code == 400
        assert accept_response.json()["detail"] == "trade_insufficient_inventory"
        async with db_session_factory() as session:
            trade = (
                await session.execute(select(Trade).where(Trade.id == trade_id))
            ).scalar_one()
            assert trade.status == "pending"
