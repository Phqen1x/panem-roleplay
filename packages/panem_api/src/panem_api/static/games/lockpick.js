// An original pin-tumbler lockpicking minigame -- replaces this game's
// earlier version (a sweeping-needle timing hit, mechanically identical
// to pickpocket.js's own timing game) with something that actually feels
// like fiddling with a lock: juggle a drifting tension wrench and push
// each pin up to its own hidden shear line by feel, rather than reacting
// to one moving target. Shared by `/lockpick` (escaping jail) and
// `/burgle` (breaking into a house): picking a lock is picking a lock
// either way, just against a cell door or a house door. Not a port of
// any third-party project -- the requested reference project turned out
// to be a Unity/C# GPLv3 game, not browser-embeddable, and porting its
// code in would pull GPLv3 into this repo -- this is a fresh
// implementation of the general pin-tumbler mechanic, not a copy of any
// particular game's code or assets.
export const label = "Lockpick";
export function instructions() {
  return (
    "Drag the tension bar into the marked zone, then hold a pin (click, or " +
    "select with ←/→ and hold Space) to work it up until it catches. " +
    "Push too far and it strikes back -- three strikes and the pick snaps."
  );
}

const WIDTH = 320;
const HEIGHT = 220;
const TENSION_TRACK_X = 24;
const TENSION_TRACK_Y = 20;
const TENSION_TRACK_H = 160;
const TENSION_TRACK_W = 26;
const TENSION_SAFE_MIN = 0.4;
const TENSION_SAFE_MAX = 0.6;
const PIN_AREA_X = 76;
const PIN_AREA_W = WIDTH - PIN_AREA_X - 16;
const PIN_TRACK_Y = 20;
const PIN_TRACK_H = 160;
const MAX_STRIKES = 3;
const TIME_LIMIT_S = 26;
const PUSH_SPEED = 0.55; // height units/sec while held
const FALL_SPEED = 1.4; // height units/sec once released, unset
const SET_LOSS_GRACE_S = 0.6; // how long a set pin tolerates bad tension before springing back

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function mount(boardEl, { onFinish, setStatus, difficulty = 0.5 }) {
  const clamped = clamp(difficulty, 0, 0.95);
  const pinCount = 3 + Math.round(clamped * 3); // 3..6
  const catchTolerance = Math.max(0.028, 0.075 - clamped * 0.045);
  const driftRate = 0.35 + clamped * 0.85; // random-walk magnitude, per second

  let done = false;
  let strikes = 0;
  let timeLeft = TIME_LIMIT_S;
  let tension = 0.5;
  let tensionVelocity = 0;
  let draggingTension = false;
  let selectedPin = 0;
  let pushing = false;
  let flashUntil = 0;
  let rafId = null;
  let lastTs = null;

  const pins = Array.from({ length: pinCount }, () => ({
    shear: 0.5 + Math.random() * 0.4, // 0.5..0.9, never trivially at the very top
    height: 0,
    set: false,
    badTensionFor: 0,
  }));

  boardEl.className = "board-lockpick";
  boardEl.innerHTML = `
    <canvas id="lockpick-canvas" width="${WIDTH}" height="${HEIGHT}"></canvas>
    <p class="lockpick-meta">
      <span id="lockpick-strikes">Strikes: 0/${MAX_STRIKES}</span>
      <span id="lockpick-timer">${TIME_LIMIT_S}s</span>
    </p>
  `;
  const canvas = boardEl.querySelector("#lockpick-canvas");
  const ctx = canvas.getContext("2d");
  const strikesEl = boardEl.querySelector("#lockpick-strikes");
  const timerEl = boardEl.querySelector("#lockpick-timer");

  function pinColumnX(index) {
    const slot = PIN_AREA_W / pinCount;
    return PIN_AREA_X + slot * index + slot / 2;
  }

  function pinIndexAt(x) {
    if (x < PIN_AREA_X) return -1;
    const slot = PIN_AREA_W / pinCount;
    const index = Math.floor((x - PIN_AREA_X) / slot);
    return index >= 0 && index < pinCount ? index : -1;
  }

  function tensionInSafeZone() {
    return tension >= TENSION_SAFE_MIN && tension <= TENSION_SAFE_MAX;
  }

  function finish(won) {
    if (done) return;
    done = true;
    cancelAnimationFrame(rafId);
    canvas.removeEventListener("pointerdown", onPointerDown);
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
    window.removeEventListener("keydown", onKeyDown);
    window.removeEventListener("keyup", onKeyUp);
    onFinish(won);
  }

  function strike() {
    strikes += 1;
    strikesEl.textContent = `Strikes: ${strikes}/${MAX_STRIKES}`;
    if (setStatus) setStatus("The pick binds and springs back -- careful.");
    if (strikes >= MAX_STRIKES) {
      if (setStatus) setStatus("The pick snaps.");
      finish(false);
    }
  }

  function update(dt) {
    if (done) return;

    // Tension free-drifts (a random walk) whenever the player isn't
    // actively dragging the wrench -- this is the "keep checking back on
    // it" half of the juggling act. Dragging sets tension directly (see
    // onPointerMove) and pins the velocity to zero so it doesn't lurch
    // the instant the pointer is released.
    if (!draggingTension) {
      tensionVelocity += (Math.random() - 0.5) * driftRate * dt;
      tensionVelocity *= 0.92;
      tension = clamp(tension + tensionVelocity * dt, 0, 1);
    }

    const safe = tensionInSafeZone();
    const active = pins[selectedPin];

    for (const pin of pins) {
      if (pin.set) {
        // A set pin only stays set while tension holds it against the
        // shear line -- losing the zone for longer than the grace
        // period lets it spring back down, undoing that pin's progress.
        pin.badTensionFor = safe ? 0 : pin.badTensionFor + dt;
        if (pin.badTensionFor > SET_LOSS_GRACE_S) {
          pin.set = false;
          pin.height = 0;
          pin.badTensionFor = 0;
        }
        continue;
      }
      if (pin === active && pushing) {
        pin.height += PUSH_SPEED * dt;
        if (pin.height >= pin.shear - catchTolerance && pin.height <= pin.shear + catchTolerance) {
          if (safe) {
            pin.set = true;
            pin.height = pin.shear;
            flashUntil = performance.now() + 220;
          }
        } else if (pin.height > pin.shear + catchTolerance) {
          pin.height = 0;
          strike();
        }
      } else {
        pin.height = Math.max(0, pin.height - FALL_SPEED * dt);
      }
    }

    if (pins.every((pin) => pin.set)) {
      if (setStatus) setStatus("The last pin sets -- the lock turns.");
      finish(true);
      return;
    }

    timeLeft -= dt;
    timerEl.textContent = `${Math.max(0, Math.ceil(timeLeft))}s`;
    if (timeLeft <= 0) {
      if (setStatus) setStatus("Time's up.");
      finish(false);
    }
  }

  function render() {
    ctx.clearRect(0, 0, WIDTH, HEIGHT);

    // Tension track + safe zone + current level.
    ctx.fillStyle = "#222";
    ctx.fillRect(TENSION_TRACK_X, TENSION_TRACK_Y, TENSION_TRACK_W, TENSION_TRACK_H);
    const safeTopY = TENSION_TRACK_Y + TENSION_TRACK_H * (1 - TENSION_SAFE_MAX);
    const safeH = TENSION_TRACK_H * (TENSION_SAFE_MAX - TENSION_SAFE_MIN);
    ctx.fillStyle = tensionInSafeZone() ? "#2ed573" : "#3a4150";
    ctx.globalAlpha = 0.35;
    ctx.fillRect(TENSION_TRACK_X, safeTopY, TENSION_TRACK_W, safeH);
    ctx.globalAlpha = 1;
    const levelY = TENSION_TRACK_Y + TENSION_TRACK_H * (1 - tension);
    ctx.fillStyle = tensionInSafeZone() ? "#2ed573" : "#e0736b";
    ctx.fillRect(TENSION_TRACK_X - 4, levelY - 4, TENSION_TRACK_W + 8, 8);
    ctx.fillStyle = "#8b93a3";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("wrench", TENSION_TRACK_X + TENSION_TRACK_W / 2, TENSION_TRACK_Y + TENSION_TRACK_H + 16);

    // Pins.
    pins.forEach((pin, index) => {
      const x = pinColumnX(index);
      const colW = Math.max(18, PIN_AREA_W / pinCount - 10);
      ctx.fillStyle = "#222";
      ctx.fillRect(x - colW / 2, PIN_TRACK_Y, colW, PIN_TRACK_H);
      const barH = PIN_TRACK_H * pin.height;
      const flashing = pin.set && performance.now() < flashUntil;
      ctx.fillStyle = pin.set ? (flashing ? "#ffffff" : "#2ed573") : "#e0a72e";
      ctx.fillRect(x - colW / 2, PIN_TRACK_Y + PIN_TRACK_H - barH, colW, Math.max(2, barH));
      if (index === selectedPin && !pin.set) {
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        ctx.strokeRect(x - colW / 2 - 2, PIN_TRACK_Y - 2, colW + 4, PIN_TRACK_H + 4);
      }
    });
    ctx.fillStyle = "#8b93a3";
    ctx.fillText("pins", PIN_AREA_X + PIN_AREA_W / 2, PIN_TRACK_Y + PIN_TRACK_H + 16);
  }

  function loop(ts) {
    if (done) return;
    const dt = lastTs === null ? 0 : Math.min(0.05, (ts - lastTs) / 1000);
    lastTs = ts;
    update(dt);
    if (!done) {
      render();
      rafId = requestAnimationFrame(loop);
    }
  }

  function canvasPoint(event) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * WIDTH,
      y: ((event.clientY - rect.top) / rect.height) * HEIGHT,
    };
  }

  function onPointerDown(event) {
    if (done) return;
    const { x, y } = canvasPoint(event);
    if (x >= TENSION_TRACK_X - 10 && x <= TENSION_TRACK_X + TENSION_TRACK_W + 10) {
      draggingTension = true;
      tensionVelocity = 0;
      tension = clamp(1 - (y - TENSION_TRACK_Y) / TENSION_TRACK_H, 0, 1);
      event.preventDefault();
      return;
    }
    const pinIndex = pinIndexAt(x);
    if (pinIndex >= 0) {
      selectedPin = pinIndex;
      pushing = true;
      event.preventDefault();
    }
  }

  function onPointerMove(event) {
    if (!draggingTension) return;
    const { y } = canvasPoint(event);
    tension = clamp(1 - (y - TENSION_TRACK_Y) / TENSION_TRACK_H, 0, 1);
  }

  function onPointerUp() {
    draggingTension = false;
    pushing = false;
  }

  function onKeyDown(event) {
    if (done) return;
    if (event.code === "ArrowLeft") {
      selectedPin = (selectedPin - 1 + pinCount) % pinCount;
      event.preventDefault();
    } else if (event.code === "ArrowRight") {
      selectedPin = (selectedPin + 1) % pinCount;
      event.preventDefault();
    } else if (event.code === "Space") {
      pushing = true;
      event.preventDefault();
    } else if (event.code === "ArrowUp") {
      draggingTension = false;
      tensionVelocity = 0;
      tension = clamp(tension + 0.4 * (1 / 60), 0, 1);
      event.preventDefault();
    } else if (event.code === "ArrowDown") {
      draggingTension = false;
      tensionVelocity = 0;
      tension = clamp(tension - 0.4 * (1 / 60), 0, 1);
      event.preventDefault();
    }
  }

  function onKeyUp(event) {
    if (event.code === "Space") pushing = false;
  }

  canvas.addEventListener("pointerdown", onPointerDown);
  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", onKeyUp);

  render();
  rafId = requestAnimationFrame(loop);
}
