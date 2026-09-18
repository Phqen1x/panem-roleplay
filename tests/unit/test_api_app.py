from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from panem_api.app import create_app
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    Job,
    JobOption,
    Location,
)
from panem_shared.db.models import Character, Npc, Property, Shift, User
from panem_shared.enums import CharacterStatus, OwnerKind, PropertyKind
from panem_shared.redis_keys import (
    crime_attempt_key,
    crime_interaction_key,
    work_interaction_key,
    work_pending_key,
)


class FakeRedis:
    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = store or {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str) -> bool:
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
