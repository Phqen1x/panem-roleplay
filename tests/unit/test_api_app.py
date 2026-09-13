from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from panem_api.app import create_app
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import District, DistrictCulture, DistrictMap, Location


class FakeRedis:
    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = store or {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str) -> bool:
        self.store[key] = value
        return True

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
