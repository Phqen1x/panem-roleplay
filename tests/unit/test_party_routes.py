"""Tests for the Panem Party Pack's real-time party room (`party_routes.
py`) -- the WebSocket-driven shared room `tabs/party.js` renders, plus its
three actually-playable games (Quickfire Trivia, Capitol Says, Pass the
Parcel). Exercises the pure `PartyRoom` state machine directly (no need
for a live event loop/websocket for most of it) alongside a couple of
`TestClient` websocket round-trips for the wire protocol itself.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from panem_api.app import create_app
from panem_api.party_routes import CAPITOL_ACTIONS, STATIONS, TRIVIA_QUESTIONS, PartyRoom
from panem_shared.content.loader import ContentBundle
from panem_shared.content.schemas import District, DistrictCulture, DistrictMap, Location


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
        districts={0: make_district(0, "The Capitol"), 1: make_district(1, "District 1")},
        goods={},
        jobs={},
        routes=[],
    )


class FakeRedis:
    async def aclose(self) -> None:
        pass


class TestPartyRoomJoinAndMove:
    def test_join_spawns_a_player_near_room_center(self) -> None:
        room = PartyRoom()
        player, old_ws = room.handle_join(websocket=object(), discord_id="1", name="Katniss")
        assert old_ws is None
        assert player.name == "Katniss"
        assert 0 <= player.x <= 960
        assert 0 <= player.y <= 640

    def test_rejoining_the_same_discord_id_keeps_position_and_score(self) -> None:
        room = PartyRoom()
        first_ws = object()
        player, _ = room.handle_join(websocket=first_ws, discord_id="1", name="Katniss")
        player.x, player.y = 500.0, 300.0
        player.trivia_score = 4

        second_ws = object()
        rejoined, old_ws = room.handle_join(websocket=second_ws, discord_id="1", name="Katniss")
        assert old_ws is first_ws
        assert rejoined.x == 500.0
        assert rejoined.trivia_score == 4
        assert room.connections["1"] is second_ws

    def test_move_advances_position_towards_the_given_direction(self) -> None:
        room = PartyRoom()
        player, _ = room.handle_join(websocket=object(), discord_id="1", name="Katniss")
        player.x, player.y = 480.0, 320.0
        player.last_move_at -= 0.5  # pretend half a second has passed since spawn

        room.handle_move("1", dx=1.0, dy=0.0)

        assert player.x > 480.0
        assert player.moving is True
        assert player.facing == "right"

    def test_move_clamps_to_room_bounds(self) -> None:
        room = PartyRoom()
        player, _ = room.handle_join(websocket=object(), discord_id="1", name="Katniss")
        player.x, player.y = 5.0, 5.0
        player.last_move_at -= 10.0  # a huge elapsed time shouldn't teleport past the wall

        room.handle_move("1", dx=-1.0, dy=-1.0)

        assert player.x >= 20.0 - 1e-6  # PLAYER_RADIUS
        assert player.y >= 20.0 - 1e-6

    def test_zero_vector_stops_the_player_without_moving_it(self) -> None:
        room = PartyRoom()
        player, _ = room.handle_join(websocket=object(), discord_id="1", name="Katniss")
        player.moving = True
        x_before, y_before = player.x, player.y

        room.handle_move("1", dx=0.0, dy=0.0)

        assert player.moving is False
        assert (player.x, player.y) == (x_before, y_before)


class TestTrivia:
    def test_first_correct_answer_wins_the_round_and_scores_two(self) -> None:
        room = PartyRoom()
        katniss, _ = room.handle_join(websocket=object(), discord_id="1", name="Katniss")
        katniss.x, katniss.y = STATIONS["trivia"]["x"], STATIONS["trivia"]["y"]
        peeta, _ = room.handle_join(websocket=object(), discord_id="2", name="Peeta")
        peeta.x, peeta.y = STATIONS["trivia"]["x"], STATIONS["trivia"]["y"]
        room._advance_trivia()  # starts the first round
        correct_index = TRIVIA_QUESTIONS[room.trivia.question_index]["correct"]

        room.handle_trivia_answer("1", correct_index)

        assert room.trivia.reveal is not None
        assert room.trivia.reveal["winner_name"] == "Katniss"
        assert katniss.trivia_score == 2
        assert peeta.trivia_score == 0

    def test_a_second_answer_after_the_round_is_won_does_nothing(self) -> None:
        room = PartyRoom()
        katniss, _ = room.handle_join(websocket=object(), discord_id="1", name="Katniss")
        katniss.x, katniss.y = STATIONS["trivia"]["x"], STATIONS["trivia"]["y"]
        peeta, _ = room.handle_join(websocket=object(), discord_id="2", name="Peeta")
        peeta.x, peeta.y = STATIONS["trivia"]["x"], STATIONS["trivia"]["y"]
        room._advance_trivia()
        correct_index = TRIVIA_QUESTIONS[room.trivia.question_index]["correct"]
        room.handle_trivia_answer("1", correct_index)

        room.handle_trivia_answer("2", correct_index)

        assert peeta.trivia_score == 0

    def test_wrong_answer_does_not_score_and_blocks_a_retry(self) -> None:
        room = PartyRoom()
        katniss, _ = room.handle_join(websocket=object(), discord_id="1", name="Katniss")
        katniss.x, katniss.y = STATIONS["trivia"]["x"], STATIONS["trivia"]["y"]
        room._advance_trivia()
        correct_index = TRIVIA_QUESTIONS[room.trivia.question_index]["correct"]
        wrong_index = next(i for i in range(4) if i != correct_index)

        room.handle_trivia_answer("1", wrong_index)
        assert katniss.trivia_score == 0
        assert room.trivia.reveal is None  # round stays open for someone else

        room.handle_trivia_answer("1", correct_index)  # same player can't retry
        assert katniss.trivia_score == 0

    def test_answering_from_across_the_room_does_nothing(self) -> None:
        room = PartyRoom()
        katniss, _ = room.handle_join(websocket=object(), discord_id="1", name="Katniss")
        katniss.x, katniss.y = STATIONS["capitol"]["x"], STATIONS["capitol"]["y"]  # nowhere near the trivia booth
        room._advance_trivia()
        correct_index = TRIVIA_QUESTIONS[room.trivia.question_index]["correct"]

        room.handle_trivia_answer("1", correct_index)

        assert katniss.trivia_score == 0
        assert room.trivia.reveal is None


class TestCapitolSays:
    def test_correct_action_scores_a_point(self) -> None:
        room = PartyRoom()
        player, _ = room.handle_join(websocket=object(), discord_id="1", name="Effie")
        player.x, player.y = STATIONS["capitol"]["x"], STATIONS["capitol"]["y"]
        room._advance_capitol()
        correct_key = CAPITOL_ACTIONS[room.capitol.action_index]["key"]

        room.handle_capitol_action("1", correct_key)

        assert player.capitol_score == 1
        assert room.capitol.reveal["winner_name"] == "Effie"

    def test_wrong_action_does_not_score(self) -> None:
        room = PartyRoom()
        player, _ = room.handle_join(websocket=object(), discord_id="1", name="Effie")
        player.x, player.y = STATIONS["capitol"]["x"], STATIONS["capitol"]["y"]
        room._advance_capitol()
        correct_key = CAPITOL_ACTIONS[room.capitol.action_index]["key"]
        wrong_key = next(a["key"] for a in CAPITOL_ACTIONS if a["key"] != correct_key)

        room.handle_capitol_action("1", wrong_key)

        assert player.capitol_score == 0
        assert room.capitol.reveal is None

    def test_acting_from_across_the_room_does_nothing(self) -> None:
        room = PartyRoom()
        player, _ = room.handle_join(websocket=object(), discord_id="1", name="Effie")
        player.x, player.y = STATIONS["trivia"]["x"], STATIONS["trivia"]["y"]  # nowhere near the stage
        room._advance_capitol()
        correct_key = CAPITOL_ACTIONS[room.capitol.action_index]["key"]

        room.handle_capitol_action("1", correct_key)

        assert player.capitol_score == 0
        assert room.capitol.reveal is None


class TestPassTheParcel:
    def test_grab_requires_being_near_the_station(self) -> None:
        room = PartyRoom()
        player, _ = room.handle_join(websocket=object(), discord_id="1", name="Haymitch")
        player.x, player.y = 0.0, 0.0  # far from the parcel station

        room.handle_parcel_grab("1")

        assert room.parcel.holder_id is None

    def test_grab_near_the_station_starts_the_music(self) -> None:
        room = PartyRoom()
        player, _ = room.handle_join(websocket=object(), discord_id="1", name="Haymitch")
        player.x, player.y = STATIONS["parcel"]["x"], STATIONS["parcel"]["y"]

        room.handle_parcel_grab("1")

        assert room.parcel.holder_id == "1"
        assert room.parcel.music_playing is True
        assert room.parcel.stop_at > 0

    def test_pass_transfers_to_the_nearest_other_player(self) -> None:
        room = PartyRoom()
        holder, _ = room.handle_join(websocket=object(), discord_id="1", name="Haymitch")
        holder.x, holder.y = STATIONS["parcel"]["x"], STATIONS["parcel"]["y"]
        room.handle_parcel_grab("1")
        nearby, _ = room.handle_join(websocket=object(), discord_id="2", name="Effie")
        nearby.x, nearby.y = holder.x + 10, holder.y
        far, _ = room.handle_join(websocket=object(), discord_id="3", name="Plutarch")
        far.x, far.y = holder.x + 500, holder.y  # far enough that the pass must skip them

        room.handle_parcel_pass("1")

        assert room.parcel.holder_id == "2"

    def test_pass_with_nobody_nearby_fails_and_keeps_the_holder(self) -> None:
        room = PartyRoom()
        holder, _ = room.handle_join(websocket=object(), discord_id="1", name="Haymitch")
        holder.x, holder.y = STATIONS["parcel"]["x"], STATIONS["parcel"]["y"]
        room.handle_parcel_grab("1")

        room.handle_parcel_pass("1")

        assert room.parcel.holder_id == "1"
        assert "No one" in (room.parcel.last_event or "")

    def test_only_the_holder_can_pass(self) -> None:
        room = PartyRoom()
        holder, _ = room.handle_join(websocket=object(), discord_id="1", name="Haymitch")
        holder.x, holder.y = STATIONS["parcel"]["x"], STATIONS["parcel"]["y"]
        room.handle_parcel_grab("1")
        room.handle_join(websocket=object(), discord_id="2", name="Effie")

        room.handle_parcel_pass("2")

        assert room.parcel.holder_id == "1"

    def test_the_parcel_follows_its_holder(self) -> None:
        room = PartyRoom()
        holder, _ = room.handle_join(websocket=object(), discord_id="1", name="Haymitch")
        holder.x, holder.y = STATIONS["parcel"]["x"], STATIONS["parcel"]["y"]
        room.handle_parcel_grab("1")
        holder.x, holder.y = 100.0, 200.0

        room._advance_parcel()

        assert (room.parcel.x, room.parcel.y) == (100.0, 200.0)

    def test_holder_disconnecting_drops_the_parcel_back_at_the_station(self) -> None:
        room = PartyRoom()
        holder, _ = room.handle_join(websocket=object(), discord_id="1", name="Haymitch")
        holder.x, holder.y = STATIONS["parcel"]["x"], STATIONS["parcel"]["y"]
        room.handle_parcel_grab("1")

        room.drop("1")

        assert room.parcel.holder_id is None
        assert room.parcel.music_playing is False
        assert room.parcel.x == STATIONS["parcel"]["x"]
        assert "left" in (room.parcel.last_event or "")

    def test_music_stopping_reveals_an_outcome_and_resets(self) -> None:
        room = PartyRoom()
        holder, _ = room.handle_join(websocket=object(), discord_id="1", name="Haymitch")
        holder.x, holder.y = STATIONS["parcel"]["x"], STATIONS["parcel"]["y"]
        room.handle_parcel_grab("1")
        room.parcel.stop_at = 0.0  # force it to be due immediately

        room._advance_parcel()

        assert room.parcel.holder_id is None
        assert room.parcel.music_playing is False
        assert room.parcel.reveal is not None
        assert room.parcel.reveal["holder_name"] == "Haymitch"


class TestSnapshot:
    def test_snapshot_reports_room_stations_and_players(self) -> None:
        room = PartyRoom()
        room.handle_join(websocket=object(), discord_id="1", name="Katniss")

        snapshot = room.snapshot()

        assert snapshot["type"] == "state"
        assert snapshot["room"]["width"] == 960
        assert "trivia" in snapshot["stations"]
        assert len(snapshot["players"]) == 1
        assert snapshot["players"][0]["name"] == "Katniss"


class TestPartyRoomWebsocket:
    def test_join_then_move_round_trips_over_the_websocket(self) -> None:
        content = make_content()
        app = create_app(content=content, redis_client=FakeRedis())
        with TestClient(app) as client, client.websocket_connect("/ws/party/room") as ws:
            ws.send_json({"type": "join", "discord_id": "1", "name": "Katniss"})
            state = ws.receive_json()
            assert state["type"] == "state"
            assert state["players"][0]["name"] == "Katniss"

            ws.send_json({"type": "move", "dx": 1.0, "dy": 0.0})
            # The background loop rebroadcasts on its own tick; the client
            # doesn't need to wait on a reply to a single "move" message,
            # but the connection should stay open and keep accepting input.
            ws.send_json({"type": "parcel_grab"})

    def test_a_join_without_a_discord_id_is_refused(self) -> None:
        content = make_content()
        app = create_app(content=content, redis_client=FakeRedis())
        with TestClient(app) as client, client.websocket_connect("/ws/party/room") as ws:
            ws.send_json({"type": "join"})
            # The server closes the socket rather than accepting a
            # nameless/idless player -- reading past that should raise.
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
