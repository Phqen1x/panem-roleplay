// The "Party Room" -- a small top-down, Animal-Crossing-style shared space
// three of the "Panem Party Pack" catalog's games (`games.js`) actually
// run in: Quickfire Trivia, Capitol Says, and Pass the Parcel. Everyone
// connected controls a simple capsule-and-head avatar with WASD/arrow
// keys, rendered on a single `<canvas>`; the three games are stations you
// walk up to, each with its own HTML overlay panel that only lights up
// once you're close enough (mirrors the server's own proximity check --
// see `party_routes.py`'s `_near()` -- so "walk up to it" isn't just
// cosmetic).
//
// All game state lives server-side (`party_routes.PartyRoom`) and arrives
// over one WebSocket (`/ws/party/room`) as a full-room snapshot roughly
// 10 times a second; this module never decides who won a round, it only
// renders what the server already decided and forwards input. Position
// is the one thing rendered with client-side smoothing (`renderPlayers`'
// exponential ease towards each snapshot's `x`/`y`) so movement doesn't
// look like it's teleporting between 100ms updates -- everything else
// (scores, timers, reveals) is shown exactly as the server sends it.
import { el } from "./_shared.js?v=7";

const MOVE_KEYS = {
  w: "up",
  arrowup: "up",
  s: "down",
  arrowdown: "down",
  a: "left",
  arrowleft: "left",
  d: "right",
  arrowright: "right",
};

const MOVE_SEND_INTERVAL_MS = 80;

const STATION_LABELS = {
  trivia: "Quickfire Trivia",
  capitol: "Capitol Says",
  parcel: "Pass the Parcel",
};

const CAPITOL_ACTION_EMOJI = {
  salute: "✋",
  bow: "🙏",
  wave: "👋",
  freeze: "❄️",
};

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws/party/room`;
}

function distance(ax, ay, bx, by) {
  return Math.hypot(ax - bx, ay - by);
}

function drawFloor(g, width, height) {
  g.fillStyle = "#264a2e";
  g.fillRect(0, 0, width, height);
  const tile = 48;
  for (let y = 0; y < height; y += tile) {
    for (let x = 0; x < width; x += tile) {
      const shade = (Math.floor(x / tile) + Math.floor(y / tile)) % 2 === 0;
      g.fillStyle = shade ? "rgba(255,255,255,0.025)" : "rgba(0,0,0,0.05)";
      g.fillRect(x, y, tile, tile);
    }
  }
}

function drawStation(g, station, key, active) {
  g.save();
  g.beginPath();
  g.setLineDash([6, 8]);
  g.lineWidth = 2;
  g.strokeStyle = active ? "rgba(224,167,46,0.9)" : "rgba(224,167,46,0.35)";
  g.arc(station.x, station.y, station.radius, 0, Math.PI * 2);
  g.stroke();
  g.setLineDash([]);
  g.fillStyle = "rgba(255,255,255,0.85)";
  g.font = "600 13px Inter, sans-serif";
  g.textAlign = "center";
  g.fillText(STATION_LABELS[key] || key, station.x, station.y - station.radius - 10);
  g.restore();
}

function drawParcel(g, parcel) {
  if (!parcel) return;
  g.save();
  const wobble = parcel.music_playing ? Math.sin(performance.now() / 90) * 3 : 0;
  g.translate(parcel.x, parcel.y - 26 + wobble);
  g.fillStyle = "#c5453f";
  g.fillRect(-14, -12, 28, 24);
  g.fillStyle = "#e0d15f";
  g.fillRect(-14, -3, 28, 6);
  g.fillRect(-3, -12, 6, 24);
  if (parcel.music_playing) {
    g.fillStyle = "rgba(255,255,255,0.85)";
    g.font = "14px Inter, sans-serif";
    g.textAlign = "center";
    g.fillText("♪", 0, -20);
  }
  g.restore();
}

function drawPlayer(g, render, isSelf, now) {
  const meta = render.meta;
  if (!meta) return;
  const bob = meta.moving ? Math.abs(Math.sin(now / 130 + render.bobOffset)) * 4 : 0;
  const x = render.dispX;
  const y = render.dispY - bob;

  // Shadow -- the one thing that stays glued to the floor rather than
  // bobbing with the body, which is what sells the little hop as height
  // rather than the sprite just sliding up and down in place.
  g.save();
  g.fillStyle = "rgba(0,0,0,0.35)";
  g.beginPath();
  g.ellipse(x, render.dispY + 4, 14, 6, 0, 0, Math.PI * 2);
  g.fill();
  g.restore();

  if (isSelf) {
    g.save();
    g.strokeStyle = "rgba(255,255,255,0.6)";
    g.lineWidth = 2;
    g.beginPath();
    g.ellipse(x, render.dispY + 4, 19, 9, 0, 0, Math.PI * 2);
    g.stroke();
    g.restore();
  }

  // Body -- a capsule (rounded rect) rather than a plain circle so it
  // reads as a standing figure even at this size, with a simple top-to-
  // bottom gradient standing in for shading.
  g.save();
  const grad = g.createLinearGradient(0, y - 26, 0, y);
  grad.addColorStop(0, meta.color);
  grad.addColorStop(1, "rgba(0,0,0,0.25)");
  g.fillStyle = grad;
  const w = 20;
  const h = 26;
  const r = 9;
  g.beginPath();
  g.moveTo(x - w / 2 + r, y - h);
  g.arcTo(x + w / 2, y - h, x + w / 2, y - h + r, r);
  g.arcTo(x + w / 2, y, x + w / 2 - r, y, r);
  g.arcTo(x - w / 2, y, x - w / 2, y - r, r);
  g.arcTo(x - w / 2, y - h, x - w / 2 + r, y - h, r);
  g.closePath();
  g.fill();
  g.restore();

  // Head + tiny facing-direction eyes.
  g.save();
  g.fillStyle = "#f1d9b8";
  g.beginPath();
  g.arc(x, y - h - 8, 10, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#2a2118";
  const eyeOffsets = {
    down: [-3, 3],
    up: [0, -2],
    left: [-4, 0],
    right: [4, 0],
  }[meta.facing] || [0, 3];
  g.beginPath();
  g.arc(x + eyeOffsets[0] - 2, y - h - 8 + eyeOffsets[1], 1.4, 0, Math.PI * 2);
  g.arc(x + eyeOffsets[0] + 2, y - h - 8 + eyeOffsets[1], 1.4, 0, Math.PI * 2);
  g.fill();
  g.restore();

  // Name label.
  g.save();
  g.font = "600 11px Inter, sans-serif";
  g.textAlign = "center";
  g.lineWidth = 3;
  g.strokeStyle = "rgba(0,0,0,0.75)";
  g.fillStyle = "#fff";
  const label = meta.name + (meta.holdsParcel ? " 🎁" : "");
  g.strokeText(label, x, y - h - 24);
  g.fillText(label, x, y - h - 24);
  g.restore();
}

export function mount(root, ctx) {
  const character = ctx.characters().find((c) => c.id === ctx.characterId());
  const playerName = (character ? character.name : "Tribute").slice(0, 32);
  const discordId = ctx.discordId();

  let latestState = null;
  const renderPlayers = new Map();
  let selfId = discordId != null ? String(discordId) : null;

  const canvas = el("canvas", { width: "960", height: "640", class: "party-canvas" });
  const statusLine = el("p", { class: "tab-status" }, "Connecting to the party room…");

  const triviaBody = el("div", { class: "party-station-body" });
  const triviaPanel = el(
    "div",
    { class: "panel party-station-panel" },
    el("h2", { text: "Quickfire Trivia" }),
    triviaBody
  );

  const capitolBody = el("div", { class: "party-station-body" });
  const capitolPanel = el(
    "div",
    { class: "panel party-station-panel" },
    el("h2", { text: "Capitol Says" }),
    capitolBody
  );

  const parcelBody = el("div", { class: "party-station-body" });
  const parcelPanel = el(
    "div",
    { class: "panel party-station-panel" },
    el("h2", { text: "Pass the Parcel" }),
    parcelBody
  );

  const scoreboardBody = el("div", {});
  const scoreboardPanel = el(
    "div",
    { class: "panel" },
    el("h2", { text: "Scoreboard" }),
    scoreboardBody
  );

  const root_ = el(
    "div",
    { class: "party-room" },
    el(
      "div",
      { class: "party-canvas-wrap" },
      canvas,
      statusLine,
      el(
        "p",
        { class: "tab-status" },
        "Move with WASD or the arrow keys. Walk up to a station to play its game."
      )
    ),
    el("div", { class: "party-sidebar" }, triviaPanel, capitolPanel, parcelPanel, scoreboardPanel)
  );
  root.append(root_);

  const g = canvas.getContext("2d");

  const ws = new WebSocket(wsUrl());
  ws.addEventListener("open", () => {
    ws.send(JSON.stringify({ type: "join", discord_id: discordId, name: playerName }));
    statusLine.textContent = "";
  });
  ws.addEventListener("close", () => {
    statusLine.textContent = "Disconnected from the party room -- switch tabs and back to reconnect.";
  });
  ws.addEventListener("error", () => {
    statusLine.textContent = "Couldn't reach the party room.";
  });
  ws.addEventListener("message", (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (payload && payload.type === "state") {
      latestState = payload;
      updateRenderPlayers(payload.players);
      renderSidebar(payload);
    }
  });

  function updateRenderPlayers(players) {
    const seen = new Set();
    for (const p of players) {
      seen.add(p.id);
      let render = renderPlayers.get(p.id);
      if (!render) {
        render = {
          dispX: p.x,
          dispY: p.y,
          bobOffset: Math.random() * Math.PI * 2,
        };
        renderPlayers.set(p.id, render);
      }
      render.targetX = p.x;
      render.targetY = p.y;
      render.meta = {
        name: p.name,
        color: p.color,
        facing: p.facing,
        moving: p.moving,
        holdsParcel: Boolean(latestState && latestState.parcel && latestState.parcel.holder_id === p.id),
      };
    }
    for (const id of Array.from(renderPlayers.keys())) {
      if (!seen.has(id)) renderPlayers.delete(id);
    }
  }

  function selfPlayer() {
    if (!latestState) return null;
    return latestState.players.find((p) => p.id === selfId) || null;
  }

  function nearStation(key) {
    const me = selfPlayer();
    if (!me || !latestState) return false;
    const station = latestState.stations[key];
    return distance(me.x, me.y, station.x, station.y) <= station.radius;
  }

  function renderSidebar(state) {
    renderTrivia(state);
    renderCapitol(state);
    renderParcel(state);
    renderScoreboard(state);
  }

  // The server broadcasts a fresh snapshot roughly 10 times a second, and
  // most of that is just a ticking countdown -- rebuilding a station's
  // whole panel (buttons included) on every single one of those ticks
  // would needlessly nuke hover state and, worse, gives any in-flight
  // click a real chance of landing on a node that's already been torn
  // down. Each station panel below only tears down and rebuilds its DOM
  // when a `key` describing its *structure* (near/active/prompt/reveal --
  // never the live seconds-left number) actually changes, and updates the
  // countdown text node in place the rest of the time.
  const triviaRenderCache = { key: null, secondsEl: null };
  const capitolRenderCache = { key: null, secondsEl: null };
  const parcelRenderCache = { key: null };

  function renderTrivia(state) {
    const near = nearStation("trivia");
    const t = state.trivia;
    const active = Boolean(t && t.active);
    const key = JSON.stringify({
      near,
      hasQuestion: Boolean(t),
      active,
      prompt: active ? t.prompt : null,
      reveal: !active && t ? t.reveal : null,
    });
    if (key !== triviaRenderCache.key) {
      triviaRenderCache.key = key;
      triviaRenderCache.secondsEl = null;
      triviaBody.innerHTML = "";
      if (!near) {
        triviaBody.append(el("p", { class: "tab-status" }, "Walk up to the Trivia Booth to play."));
      } else if (!t) {
        triviaBody.append(el("p", { class: "tab-status" }, "Loading a question…"));
      } else if (!active && t.reveal) {
        triviaBody.append(
          el(
            "p",
            { class: t.reveal.winner_name ? "result-line win" : "result-line" },
            t.reveal.winner_name
              ? `${t.reveal.winner_name} got it! (+2)`
              : "Time's up -- nobody answered in time."
          ),
          el("p", { class: "tab-status" }, `Correct answer: ${t.reveal.correct_text}`)
        );
      } else {
        triviaRenderCache.secondsEl = el("p", { class: "tab-status" });
        triviaBody.append(
          el("p", {}, t.prompt),
          triviaRenderCache.secondsEl,
          el(
            "div",
            { class: "party-answer-grid" },
            ...t.options.map((option, index) =>
              el(
                "button",
                {
                  class: "btn secondary",
                  type: "button",
                  onclick: () => ws.send(JSON.stringify({ type: "trivia_answer", index })),
                },
                option
              )
            )
          )
        );
      }
    }
    if (triviaRenderCache.secondsEl && active) {
      triviaRenderCache.secondsEl.textContent = `${t.seconds_left.toFixed(0)}s left`;
    }
  }

  function renderCapitol(state) {
    const near = nearStation("capitol");
    const c = state.capitol;
    const active = Boolean(c && c.active);
    const key = JSON.stringify({
      near,
      hasCue: Boolean(c),
      active,
      prompt: active ? c.prompt : null,
      reveal: !active && c ? c.reveal : null,
    });
    if (key !== capitolRenderCache.key) {
      capitolRenderCache.key = key;
      capitolRenderCache.secondsEl = null;
      capitolBody.innerHTML = "";
      if (!near) {
        capitolBody.append(el("p", { class: "tab-status" }, "Walk up to the Capitol Stage to play."));
      } else if (!c) {
        capitolBody.append(el("p", { class: "tab-status" }, "Waiting for the next cue…"));
      } else if (!active && c.reveal) {
        capitolBody.append(
          el(
            "p",
            { class: c.reveal.winner_name ? "result-line win" : "result-line" },
            c.reveal.winner_name ? `${c.reveal.winner_name} nailed it! (+1)` : "Too slow -- nobody scored."
          ),
          el(
            "p",
            { class: "tab-status" },
            `Capitol said: ${CAPITOL_ACTION_EMOJI[c.reveal.correct_action] || ""} ${c.reveal.correct_action}`
          )
        );
      } else {
        capitolRenderCache.secondsEl = el("p", { class: "tab-status" });
        capitolBody.append(
          el("p", {}, `Capitol says: ${c.prompt}!`),
          capitolRenderCache.secondsEl,
          el(
            "div",
            { class: "party-answer-grid" },
            ...c.actions.map((action) =>
              el(
                "button",
                {
                  class: "btn secondary",
                  type: "button",
                  onclick: () => ws.send(JSON.stringify({ type: "capitol_action", action: action.key })),
                },
                `${CAPITOL_ACTION_EMOJI[action.key] || ""} ${action.label}`
              )
            )
          )
        );
      }
    }
    if (capitolRenderCache.secondsEl && active) {
      capitolRenderCache.secondsEl.textContent = `${c.seconds_left.toFixed(0)}s left`;
    }
  }

  function renderParcel(state) {
    const p = state.parcel;
    const near = nearStation("parcel");
    const key = JSON.stringify({
      near,
      reveal: p.reveal,
      holderId: p.holder_id,
      holderName: p.holder_name,
      musicPlaying: p.music_playing,
      lastEvent: p.last_event,
      isSelf: p.holder_id === selfId,
    });
    if (key === parcelRenderCache.key) return;
    parcelRenderCache.key = key;
    parcelBody.innerHTML = "";
    if (p.reveal) {
      parcelBody.append(
        el(
          "p",
          { class: p.reveal.kind === "prank" ? "result-line lose" : "result-line win" },
          `${p.reveal.holder_name} opened it: ${p.reveal.text}`
        )
      );
      return;
    }
    if (!near) {
      parcelBody.append(
        el(
          "p",
          { class: "tab-status" },
          p.music_playing ? "The music's playing somewhere in the room…" : "Walk up to the Parcel Circle to play."
        )
      );
      return;
    }
    if (!p.holder_id) {
      parcelBody.append(
        el("p", { class: "tab-status" }, "The parcel is sitting here, unclaimed."),
        el(
          "button",
          {
            class: "btn",
            type: "button",
            onclick: () => ws.send(JSON.stringify({ type: "parcel_grab" })),
          },
          "Grab it!"
        )
      );
      return;
    }
    const holdingIsMe = p.holder_id === selfId;
    parcelBody.append(
      el("p", { class: "tab-status" }, `${p.holder_name} is holding it -- the music's playing!`)
    );
    if (holdingIsMe) {
      parcelBody.append(
        el(
          "button",
          {
            class: "btn",
            type: "button",
            onclick: () => ws.send(JSON.stringify({ type: "parcel_pass" })),
          },
          "Pass it to someone nearby!"
        )
      );
    }
    if (p.last_event) {
      parcelBody.append(el("p", { class: "tab-status" }, p.last_event));
    }
  }

  function renderScoreboard(state) {
    scoreboardBody.innerHTML = "";
    const rows = [...state.players].sort(
      (a, b) => b.trivia_score + b.capitol_score - (a.trivia_score + a.capitol_score)
    );
    if (rows.length === 0) {
      scoreboardBody.append(el("p", { class: "tab-status" }, "Nobody's here yet."));
      return;
    }
    for (const player of rows) {
      scoreboardBody.append(
        el(
          "p",
          { class: "tab-status" },
          `${player.name} -- Trivia ${player.trivia_score} · Capitol Says ${player.capitol_score}`
        )
      );
    }
  }

  // -------------------------------------------------------------- input

  const heldDirections = new Set();
  function onKeyDown(event) {
    const dir = MOVE_KEYS[event.key.toLowerCase()];
    if (!dir) return;
    heldDirections.add(dir);
    event.preventDefault();
  }
  function onKeyUp(event) {
    const dir = MOVE_KEYS[event.key.toLowerCase()];
    if (!dir) return;
    heldDirections.delete(dir);
  }
  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", onKeyUp);

  function currentDirection() {
    let dx = 0;
    let dy = 0;
    if (heldDirections.has("left")) dx -= 1;
    if (heldDirections.has("right")) dx += 1;
    if (heldDirections.has("up")) dy -= 1;
    if (heldDirections.has("down")) dy += 1;
    return { dx, dy };
  }

  const sendTimer = setInterval(() => {
    if (ws.readyState !== WebSocket.OPEN) return;
    const { dx, dy } = currentDirection();
    ws.send(JSON.stringify({ type: "move", dx, dy }));
  }, MOVE_SEND_INTERVAL_MS);

  // ------------------------------------------------------------- render

  let lastFrameAt = performance.now();
  let rafHandle = 0;
  function frame(now) {
    rafHandle = requestAnimationFrame(frame);
    const dt = Math.min((now - lastFrameAt) / 1000, 0.25);
    lastFrameAt = now;
    const ease = 1 - Math.exp(-dt * 12);
    for (const render of renderPlayers.values()) {
      render.dispX += ((render.targetX ?? render.dispX) - render.dispX) * ease;
      render.dispY += ((render.targetY ?? render.dispY) - render.dispY) * ease;
    }

    const width = latestState ? latestState.room.width : 960;
    const height = latestState ? latestState.room.height : 640;
    drawFloor(g, width, height);
    if (latestState) {
      for (const [key, station] of Object.entries(latestState.stations)) {
        drawStation(g, station, key, nearStation(key));
      }
      drawParcel(g, latestState.parcel);
      for (const [id, render] of renderPlayers) {
        drawPlayer(g, render, id === selfId, now);
      }
    }
  }
  rafHandle = requestAnimationFrame(frame);

  return {
    unmount() {
      cancelAnimationFrame(rafHandle);
      clearInterval(sendTimer);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      try {
        ws.close();
      } catch {
        // Already closed -- nothing else to clean up.
      }
    },
  };
}
