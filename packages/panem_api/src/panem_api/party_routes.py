"""Panem Party Pack's real-time multiplayer party room: a small top-down,
Animal-Crossing-style shared space (`static/tabs/party.js`) three of the
"Panem Party Pack" catalog's games (`static/tabs/games.js`) actually run
in -- Quickfire Trivia, Capitol Says, and Pass the Parcel. The rest of the
catalog stays a pitch for now; see `tabs/games.js`'s own docstring.

Deliberately NOT wired through Redis or the database: `main.py` runs a
single `uvicorn.run(app, ...)` with no `workers=` (one process), so a
plain in-memory `PartyRoom` singleton is exactly as durable as this needs
to be -- a shared party room is ephemeral fun, not world state, and
losing it on a restart is no worse than an in-progress `/work` minigame
being interrupted. One room, shared by everyone connected at once (no
per-district instancing -- there's no expected scale here that would need
it). No auth beyond trusting whatever discord_id/display name a client
sends at `join`, same documented gap as the rest of `app.py`.

All three games are proximity-gated by design: a player only sees a
station's controls once their character's `x`/`y` (server-authoritative,
moved by `move` messages) is within that station's `radius` of it (see
`snapshot()`'s per-player `near_*` flags) -- the point of putting these
in a room you walk around in rather than a plain modal.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import math
import random
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from panem_shared.logging import get_logger

logger = get_logger(component="api")

ROOM_WIDTH = 960.0
ROOM_HEIGHT = 640.0
PLAYER_RADIUS = 20.0
PLAYER_SPEED = 200.0  # px/second, in room units (see ROOM_WIDTH/HEIGHT)

STATIONS: dict[str, dict[str, float]] = {
    "trivia": {"x": 160.0, "y": 140.0, "radius": 90.0},
    "capitol": {"x": 800.0, "y": 140.0, "radius": 90.0},
    "parcel": {"x": 480.0, "y": 520.0, "radius": 100.0},
}

PLAYER_COLORS = [
    "#e0a72e",
    "#5fb3d9",
    "#d9635f",
    "#7fbf6a",
    "#b07fd9",
    "#d98ecf",
    "#e0d15f",
    "#7fd9c1",
]

TICK_SECONDS = 0.1
TRIVIA_ROUND_SECONDS = 15.0
TRIVIA_REVEAL_SECONDS = 4.0
CAPITOL_ROUND_SECONDS = 6.0
CAPITOL_REVEAL_SECONDS = 3.0
PARCEL_MIN_SECONDS = 8.0
PARCEL_MAX_SECONDS = 22.0
PARCEL_REVEAL_SECONDS = 5.0
PARCEL_PASS_RADIUS = 110.0

# Answers spelled out ("District Four") to match `app.js`'s own
# `DISTRICT_NAMES` labels -- district/industry pairs mirror `data/
# districts/*.yaml`'s `industry` fields, not invented lore.
TRIVIA_QUESTIONS: list[dict[str, object]] = [
    {
        "prompt": "Which district's industry is luxury goods?",
        "options": ["District One", "District Two", "District Eight", "District Eleven"],
        "correct": 0,
    },
    {
        "prompt": "Which district hauls in Panem's fish?",
        "options": ["District Six", "District Four", "District Nine", "District Ten"],
        "correct": 1,
    },
    {
        "prompt": "Which district keeps the nation's power grid running?",
        "options": ["District Three", "District Seven", "District Five", "District Twelve"],
        "correct": 2,
    },
    {
        "prompt": "Which district mines the coal Panem runs on?",
        "options": ["District Twelve", "District Two", "District Six", "District One"],
        "correct": 0,
    },
    {
        "prompt": "Which district's fields grow the nation's grain?",
        "options": ["District Eleven", "District Nine", "District Four", "District Three"],
        "correct": 1,
    },
    {
        "prompt": "Which district raises Panem's livestock?",
        "options": ["District Eight", "District Five", "District Ten", "District Seven"],
        "correct": 2,
    },
    {
        "prompt": "Which district builds Panem's electronics?",
        "options": ["District Three", "District One", "District Nine", "District Six"],
        "correct": 0,
    },
    {
        "prompt": "Which district quarries stone and masonry?",
        "options": ["District Four", "District Two", "District Eleven", "District Eight"],
        "correct": 1,
    },
    {
        "prompt": "Which district weaves Panem's textiles?",
        "options": ["District Ten", "District Twelve", "District Eight", "District Five"],
        "correct": 2,
    },
    {
        "prompt": "Which district moves goods and people by rail across Panem?",
        "options": ["District Six", "District Seven", "District Three", "District Four"],
        "correct": 0,
    },
    {
        "prompt": "Which district's motto is \"From the Deep, We Rise\"?",
        "options": ["District Nine", "District Two", "District Four", "District Twelve"],
        "correct": 2,
    },
    {
        "prompt": "Which district fells timber for the whole nation?",
        "options": ["District Seven", "District One", "District Ten", "District Five"],
        "correct": 0,
    },
    {
        "prompt": "Which district's orchards and fields feed Panem's produce quota?",
        "options": ["District Eleven", "District Three", "District Six", "District Eight"],
        "correct": 0,
    },
]

CAPITOL_ACTIONS: list[dict[str, str]] = [
    {"key": "salute", "label": "Salute"},
    {"key": "bow", "label": "Bow"},
    {"key": "wave", "label": "Wave"},
    {"key": "freeze", "label": "Freeze"},
]

PARCEL_OUTCOMES: list[dict[str, str]] = [
    {"kind": "prize", "text": "A tin of Capitol chocolates!"},
    {"kind": "prize", "text": "A handful of shiny sponsor tokens!"},
    {"kind": "prize", "text": "A badge that reads \"Games Champion\"!"},
    {"kind": "prank", "text": "A sneeze-powder puff -- everyone nearby achoos!"},
    {"kind": "prank", "text": "It's empty, except for a note: \"Better luck next round!\""},
    {"kind": "challenge", "text": "Do your best Capitol accent until the next round starts!"},
    {"kind": "challenge", "text": "Sing your district's anthem, badly, right now!"},
    {"kind": "challenge", "text": "Walk backwards until the next round starts!"},
]


def _distance(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def _near(player: Player, station: str) -> bool:
    """Whether `player` is standing close enough to `STATIONS[station]` to
    interact with it -- the server-side half of "walk up to a station to
    play" (the client only *shows* a station's controls once it thinks
    it's near enough; this is what actually gates the action)."""
    spot = STATIONS[station]
    return _distance(player.x, player.y, spot["x"], spot["y"]) <= spot["radius"]


@dataclasses.dataclass
class Player:
    discord_id: str
    name: str
    color: str
    x: float
    y: float
    facing: str = "down"
    moving: bool = False
    trivia_score: int = 0
    capitol_score: int = 0
    last_move_at: float = dataclasses.field(default_factory=time.monotonic, repr=False)


@dataclasses.dataclass
class TriviaRound:
    question_index: int = -1
    deadline: float = 0.0
    answered: set[str] = dataclasses.field(default_factory=set)
    reveal: dict[str, object] | None = None
    reveal_until: float = 0.0


@dataclasses.dataclass
class CapitolRound:
    action_index: int = -1
    deadline: float = 0.0
    answered: set[str] = dataclasses.field(default_factory=set)
    reveal: dict[str, object] | None = None
    reveal_until: float = 0.0


@dataclasses.dataclass
class ParcelRound:
    holder_id: str | None = None
    x: float = STATIONS["parcel"]["x"]
    y: float = STATIONS["parcel"]["y"]
    music_playing: bool = False
    stop_at: float = 0.0
    reveal: dict[str, object] | None = None
    reveal_until: float = 0.0
    last_event: str | None = None


class PartyRoom:
    """One shared room's worth of state, plus the background loop that
    advances the three games' timers and broadcasts a fresh snapshot to
    every connected socket at `TICK_SECONDS` cadence. Player movement
    itself isn't tick-driven -- `handle_move` applies it immediately from
    each `move` message's own elapsed time so it feels responsive; the
    tick loop just carries that (and the games' state) out to everyone
    else.
    """

    def __init__(self) -> None:
        self.players: dict[str, Player] = {}
        self.connections: dict[str, WebSocket] = {}
        self.trivia = TriviaRound()
        self.capitol = CapitolRound()
        self.parcel = ParcelRound()
        self._task: asyncio.Task[None] | None = None
        self._rng = random.Random()

    def ensure_loop(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        try:
            while self.connections:
                self._advance_trivia()
                self._advance_capitol()
                self._advance_parcel()
                await self.broadcast()
                await asyncio.sleep(TICK_SECONDS)
        except Exception:
            logger.exception("party_room_loop_crashed")
        finally:
            self._task = None

    # ------------------------------------------------------------- join

    def handle_join(
        self, websocket: WebSocket, discord_id: str, name: str
    ) -> tuple[Player, WebSocket | None]:
        player = self.players.get(discord_id)
        if player is None:
            color = PLAYER_COLORS[len(self.players) % len(PLAYER_COLORS)]
            player = Player(
                discord_id=discord_id,
                name=name,
                color=color,
                x=ROOM_WIDTH / 2 + self._rng.uniform(-80, 80),
                y=ROOM_HEIGHT / 2 + self._rng.uniform(-80, 80),
            )
            self.players[discord_id] = player
        else:
            player.name = name  # reconnect -- keep position/score, refresh the name
        old_ws = self.connections.get(discord_id)
        self.connections[discord_id] = websocket
        return player, (old_ws if old_ws is not websocket else None)

    def drop(self, discord_id: str) -> None:
        self.connections.pop(discord_id, None)
        if self.parcel.holder_id == discord_id:
            player = self.players.get(discord_id)
            self.parcel.holder_id = None
            self.parcel.music_playing = False
            self.parcel.x = STATIONS["parcel"]["x"]
            self.parcel.y = STATIONS["parcel"]["y"]
            self.parcel.last_event = f"{player.name if player else 'Someone'} dropped the parcel and left!"
        self.players.pop(discord_id, None)

    # --------------------------------------------------------- movement

    def handle_move(self, discord_id: str, dx: float, dy: float) -> None:
        player = self.players.get(discord_id)
        if player is None:
            return
        now = time.monotonic()
        elapsed = min(max(now - player.last_move_at, 0.0), 0.5)
        player.last_move_at = now
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            player.moving = False
            return
        nx, ny = dx / length, dy / length
        player.x = min(max(player.x + nx * PLAYER_SPEED * elapsed, PLAYER_RADIUS), ROOM_WIDTH - PLAYER_RADIUS)
        player.y = min(max(player.y + ny * PLAYER_SPEED * elapsed, PLAYER_RADIUS), ROOM_HEIGHT - PLAYER_RADIUS)
        player.moving = True
        player.facing = (
            ("right" if nx > 0 else "left") if abs(nx) > abs(ny) else ("down" if ny > 0 else "up")
        )

    # ----------------------------------------------------------- trivia

    def _start_trivia_round(self, now: float) -> None:
        t = self.trivia
        choices = [i for i in range(len(TRIVIA_QUESTIONS)) if i != t.question_index]
        t.question_index = self._rng.choice(choices or list(range(len(TRIVIA_QUESTIONS))))
        t.deadline = now + TRIVIA_ROUND_SECONDS
        t.answered = set()
        t.reveal = None

    def _advance_trivia(self) -> None:
        now = time.monotonic()
        t = self.trivia
        if t.reveal is not None:
            if now < t.reveal_until:
                return
            t.reveal = None
            t.question_index = -1
        if t.question_index == -1:
            self._start_trivia_round(now)
            return
        if now >= t.deadline:
            question = TRIVIA_QUESTIONS[t.question_index]
            t.reveal = {
                "correct_index": question["correct"],
                "correct_text": question["options"][question["correct"]],
                "winner_name": None,
            }
            t.reveal_until = now + TRIVIA_REVEAL_SECONDS

    def handle_trivia_answer(self, discord_id: str, index: int) -> None:
        t = self.trivia
        now = time.monotonic()
        if t.reveal is not None or t.question_index == -1 or now >= t.deadline:
            return
        if discord_id in t.answered:
            return
        player = self.players.get(discord_id)
        if player is None or not _near(player, "trivia"):
            return
        t.answered.add(discord_id)
        question = TRIVIA_QUESTIONS[t.question_index]
        if index != question["correct"]:
            return
        player.trivia_score += 2
        t.reveal = {
            "correct_index": question["correct"],
            "correct_text": question["options"][question["correct"]],
            "winner_name": player.name,
        }
        t.reveal_until = now + TRIVIA_REVEAL_SECONDS

    # --------------------------------------------------------- capitol

    def _advance_capitol(self) -> None:
        now = time.monotonic()
        c = self.capitol
        if c.reveal is not None:
            if now < c.reveal_until:
                return
            c.reveal = None
            c.action_index = -1
        if c.action_index == -1:
            choices = [i for i in range(len(CAPITOL_ACTIONS)) if i != c.action_index]
            c.action_index = self._rng.choice(choices or list(range(len(CAPITOL_ACTIONS))))
            c.deadline = now + CAPITOL_ROUND_SECONDS
            c.answered = set()
            return
        if now >= c.deadline:
            c.reveal = {"correct_action": CAPITOL_ACTIONS[c.action_index]["key"], "winner_name": None}
            c.reveal_until = now + CAPITOL_REVEAL_SECONDS

    def handle_capitol_action(self, discord_id: str, action_key: str) -> None:
        c = self.capitol
        now = time.monotonic()
        if c.reveal is not None or c.action_index == -1 or now >= c.deadline:
            return
        if discord_id in c.answered:
            return
        player = self.players.get(discord_id)
        if player is None or not _near(player, "capitol"):
            return
        c.answered.add(discord_id)
        correct_key = CAPITOL_ACTIONS[c.action_index]["key"]
        if action_key != correct_key:
            return
        player.capitol_score += 1
        c.reveal = {"correct_action": correct_key, "winner_name": player.name}
        c.reveal_until = now + CAPITOL_REVEAL_SECONDS

    # ----------------------------------------------------------- parcel

    def _advance_parcel(self) -> None:
        now = time.monotonic()
        p = self.parcel
        if p.reveal is not None:
            if now < p.reveal_until:
                return
            p.reveal = None
            p.last_event = None
        if p.music_playing and p.holder_id is not None and now >= p.stop_at:
            holder = self.players.get(p.holder_id)
            outcome = self._rng.choice(PARCEL_OUTCOMES)
            p.reveal = {
                "kind": outcome["kind"],
                "text": outcome["text"],
                "holder_name": holder.name if holder is not None else "Someone",
            }
            p.reveal_until = now + PARCEL_REVEAL_SECONDS
            p.music_playing = False
            p.holder_id = None
            p.x, p.y = STATIONS["parcel"]["x"], STATIONS["parcel"]["y"]
            p.last_event = None
            return
        if p.holder_id is not None:
            holder = self.players.get(p.holder_id)
            if holder is None:
                p.holder_id = None
                p.music_playing = False
                p.x, p.y = STATIONS["parcel"]["x"], STATIONS["parcel"]["y"]
            else:
                p.x, p.y = holder.x, holder.y

    def handle_parcel_grab(self, discord_id: str) -> None:
        p = self.parcel
        if p.reveal is not None or p.holder_id is not None:
            return
        player = self.players.get(discord_id)
        if player is None:
            return
        if _distance(player.x, player.y, p.x, p.y) > STATIONS["parcel"]["radius"]:
            return
        p.holder_id = discord_id
        p.music_playing = True
        p.stop_at = time.monotonic() + self._rng.uniform(PARCEL_MIN_SECONDS, PARCEL_MAX_SECONDS)
        p.last_event = f"{player.name} grabbed the parcel -- the music's playing!"

    def handle_parcel_pass(self, discord_id: str) -> None:
        p = self.parcel
        if p.holder_id != discord_id:
            return
        holder = self.players.get(discord_id)
        if holder is None:
            return
        best_id: str | None = None
        best_dist = PARCEL_PASS_RADIUS
        for other_id, other in self.players.items():
            if other_id == discord_id:
                continue
            dist = _distance(holder.x, holder.y, other.x, other.y)
            if dist <= best_dist:
                best_id, best_dist = other_id, dist
        if best_id is None:
            p.last_event = "No one's close enough to pass to!"
            return
        p.holder_id = best_id
        p.last_event = f"{holder.name} passed the parcel to {self.players[best_id].name}!"

    # --------------------------------------------------------- snapshot

    def snapshot(self) -> dict[str, object]:
        now = time.monotonic()
        players_out = [
            {
                "id": player.discord_id,
                "name": player.name,
                "color": player.color,
                "x": round(player.x, 1),
                "y": round(player.y, 1),
                "facing": player.facing,
                "moving": player.moving,
                "trivia_score": player.trivia_score,
                "capitol_score": player.capitol_score,
            }
            for player in self.players.values()
        ]

        t = self.trivia
        trivia_out: dict[str, object] | None
        if t.reveal is not None:
            trivia_out = {"active": False, "reveal": t.reveal}
        elif t.question_index != -1:
            question = TRIVIA_QUESTIONS[t.question_index]
            trivia_out = {
                "active": True,
                "prompt": question["prompt"],
                "options": question["options"],
                "seconds_left": max(0.0, round(t.deadline - now, 1)),
                "reveal": None,
            }
        else:
            trivia_out = None

        c = self.capitol
        capitol_out: dict[str, object] | None
        if c.reveal is not None:
            capitol_out = {"active": False, "reveal": c.reveal}
        elif c.action_index != -1:
            capitol_out = {
                "active": True,
                "prompt": CAPITOL_ACTIONS[c.action_index]["label"],
                "actions": CAPITOL_ACTIONS,
                "seconds_left": max(0.0, round(c.deadline - now, 1)),
                "reveal": None,
            }
        else:
            capitol_out = None

        p = self.parcel
        parcel_out = {
            "holder_id": p.holder_id,
            "holder_name": self.players[p.holder_id].name if p.holder_id in self.players else None,
            "x": round(p.x, 1),
            "y": round(p.y, 1),
            "music_playing": p.music_playing,
            "reveal": p.reveal,
            "last_event": p.last_event,
        }

        return {
            "type": "state",
            "room": {"width": ROOM_WIDTH, "height": ROOM_HEIGHT, "player_radius": PLAYER_RADIUS},
            "stations": STATIONS,
            "players": players_out,
            "trivia": trivia_out,
            "capitol": capitol_out,
            "parcel": parcel_out,
        }

    async def broadcast(self) -> None:
        if not self.connections:
            return
        payload = self.snapshot()
        stale: list[str] = []
        for discord_id, ws in list(self.connections.items()):
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(discord_id)
        for discord_id in stale:
            self.drop(discord_id)


def build_party_router() -> APIRouter:
    router = APIRouter()
    room = PartyRoom()

    @router.websocket("/ws/party/room")
    async def party_room_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        discord_id: str | None = None
        try:
            first = await websocket.receive_json()
            if not isinstance(first, dict) or first.get("type") != "join" or not first.get("discord_id"):
                await websocket.close(code=4000, reason="Expected a join message")
                return
            discord_id = str(first["discord_id"])
            name = str(first.get("name") or "Tribute")[:32]
            _player, old_ws = room.handle_join(websocket, discord_id, name)
            if old_ws is not None:
                with contextlib.suppress(Exception):
                    await old_ws.close(code=4001, reason="Reconnected elsewhere")
            room.ensure_loop()
            await room.broadcast()

            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict):
                    continue
                kind = message.get("type")
                if kind == "move":
                    room.handle_move(
                        discord_id, float(message.get("dx", 0) or 0), float(message.get("dy", 0) or 0)
                    )
                elif kind == "trivia_answer":
                    room.handle_trivia_answer(discord_id, int(message.get("index", -1)))
                elif kind == "capitol_action":
                    room.handle_capitol_action(discord_id, str(message.get("action", "")))
                elif kind == "parcel_grab":
                    room.handle_parcel_grab(discord_id)
                elif kind == "parcel_pass":
                    room.handle_parcel_pass(discord_id)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("party_room_ws_error")
        finally:
            if discord_id is not None and room.connections.get(discord_id) is websocket:
                room.drop(discord_id)

    return router
