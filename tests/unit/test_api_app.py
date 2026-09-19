from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from panem_api.app import create_app
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
    ApartmentLease,
    Character,
    Inventory,
    MarketPrice,
    Npc,
    Property,
    PropertyAuction,
    RelationshipRow,
    Scene,
    Shift,
    User,
)
from panem_shared.enums import (
    CharacterStatus,
    OwnerKind,
    PropertyKind,
    SceneKind,
    SceneStatus,
    Stance,
)
from panem_shared.redis_keys import (
    crime_attempt_key,
    crime_interaction_key,
    work_interaction_key,
    work_pending_key,
)
from panem_shared.relationships import relationship_key


class FakeRedis:
    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = store or {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

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


def make_content_with_job() -> ContentBundle:
    return ContentBundle(
        districts={0: make_district(0, "The Capitol"), 1: make_district(1, "District 1")},
        goods={},
        jobs={"miner": make_job()},
        routes=[],
    )


def make_content_with_outskirts() -> ContentBundle:
    """Same shape as `make_content_with_job`, plus a district-1 `outskirts`
    location and a `food`-category good, for `/activity/dashboard/crime/
    poach` tests (`resolve_poach` needs both to find anything to yield)."""
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
        goods={"grain": Good(id="grain", name="Grain", base_price=1.0, category="food")},
        jobs={"miner": make_job()},
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
def poach_app(db_session_factory):
    content = make_content_with_outskirts()
    redis_client = FakeRedis()
    app = create_app(content=content, redis_client=redis_client, session_factory=db_session_factory)
    app.state.fake_redis = redis_client  # type: ignore[attr-defined]
    return app


def make_content_with_market() -> ContentBundle:
    """District 1 trades `grain` (imported) legally and `contraband` on
    the black market, with `fence` as its fence NPC -- for `/activity/
    dashboard/market` and `.../blackmarket` tests. Two separate
    `kind="market"` locations: `legal_market` (not `illicit`) for the
    legal-market tests, and `market` (`illicit=True`, required by
    `resolve_black_market_location`) for the black-market ones -- sharing
    one location between them would expose the legal tests to `buy`/
    `sell`'s own illicit-detection roll (`market.py::_roll_illicit_
    detection` fires for *any* `illicit` location, not just illicit
    goods), flaking a fine onto an otherwise-deterministic legal trade."""
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
        Location(id="legal_market", name="The Market", kind="market"),
        Location(id="market", name="The Underground Market", kind="market", illicit=True),
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
            "grain": Good(id="grain", name="Grain", base_price=2.0, category="food"),
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

    async def test_steal_win_moves_money(self, work_app, db_session_factory):
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
        assert body["amount"] > 0
        async with db_session_factory() as session:
            character = await session.get(Character, char_id)
            assert character.money == body["amount"]

    async def test_burgle_win_pays_a_fraction_of_the_house_value(
        self, work_app, db_session_factory
    ):
        from panem_shared import constants

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
        assert body["amount"] == round(1000.0 * constants.BURGLE_YIELD_FRACTION)

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
        assert response.json() == {"characters": []}

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

    async def test_surfaces_jailed_until_tick_for_the_jail_tab(self, work_app, db_session_factory):
        await seed_character(
            db_session_factory,
            discord_id=42,
            character_overrides={"jailed_until_tick": 500},
        )
        transport = httpx.ASGITransport(app=work_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/activity/dashboard/identify", json={"discord_id": 42})
        assert response.json()["characters"][0]["jailed_until_tick"] == 500


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

    async def test_list_excludes_rejected_and_dead_but_keeps_pending_approved_retired(
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
        names = {c["name"] for c in response.json()["characters"]}
        assert names == {"Wren", "Applicant", "Retiree"}

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

    async def test_poach_success_grants_the_good(self, poach_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"location_id": "outskirts"}
        )
        transport = httpx.ASGITransport(app=poach_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("random.Random.random", return_value=0.99):
                response = await client.post(
                    f"/activity/dashboard/crime/{char_id}/poach",
                    json={"discord_id": 5},
                )
        assert response.status_code == 200
        body = response.json()
        assert body["caught"] is False
        assert body["good_name"] == "Grain"

    async def test_poach_caught_reports_the_fine(self, poach_app, db_session_factory):
        char_id = await seed_character(
            db_session_factory, discord_id=5, character_overrides={"location_id": "outskirts"}
        )
        transport = httpx.ASGITransport(app=poach_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("random.Random.random", return_value=0.0):
                response = await client.post(
                    f"/activity/dashboard/crime/{char_id}/poach",
                    json={"discord_id": 5},
                )
        assert response.status_code == 200
        body = response.json()
        assert body["caught"] is True
        assert body["fine"] > 0

    async def test_poach_refuses_when_not_at_outskirts(self, poach_app, db_session_factory):
        char_id = await seed_character(db_session_factory, discord_id=5)
        transport = httpx.ASGITransport(app=poach_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/crime/{char_id}/poach",
                json={"discord_id": 5},
            )
        assert response.status_code == 400


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
        assert body["inventory"] == []

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
            character_overrides={"location_id": "market", "money": 1000},
        )
        transport = httpx.ASGITransport(app=market_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/activity/dashboard/blackmarket/{char_id}/buy",
                json={"discord_id": 5, "good_id": "contraband", "qty": 1},
            )
        assert response.status_code == 400

    async def test_buy_succeeds_when_trusted(self, market_app, db_session_factory):
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
            }
        ]

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
