// A fishing minigame for the Hobbies tab -- reuses `./lockpick.js`'s
// exact physics (a moving target zone + a player-steered, always-sinking
// indicator + a fill/drain progress meter that wins at full and loses at
// empty), which that file's own comment already describes as "the
// general shape of Stardew Valley's fishing minigame." Lockpicking is
// just one reskin of that mechanic (escaping jail/breaking into a
// house); this is the other one it was always describing, with its own
// flavor text, colors, and labels so a fishing session doesn't read like
// picking a lock. The tuning constants below are copied unchanged from
// `./lockpick.js` -- they're the result of real measured-play tuning
// documented there (idle-play win rate, breakeven curves, etc.), and
// nothing about fishing calls for different numbers.
export const label = "Fishing";
export function instructions() {
  return (
    "The line keeps going slack on its own -- hold W or click and hold to " +
    "reel in against it. Keep the bobber inside the blue bite zone as it " +
    "drifts up and down -- the tension meter fills while you're on target " +
    "and drains when you're off. Fill it to land the catch; let it drain " +
    "empty and the fish gets away."
  );
}

const WIDTH = 220;
const HEIGHT = 260;
const TRACK_X = 40;
const TRACK_Y = 16;
const TRACK_W = 90;
const TRACK_H = 220;
const METER_X = 150;
const METER_W = 30;
const BOBBER_THICKNESS = 8;
const GRAVITY = 1.2; // track-heights/sec^2, always applied downward (the line going slack)
const THRUST = 2.75; // track-heights/sec^2, applied upward only while reeling in
const MAX_SPEED = 0.47; // track-heights/sec, clamped in both directions

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function mount(boardEl, { onFinish, setStatus, difficulty = 0.5 }) {
  const clamped = clamp(difficulty, 0, 0.95);
  const zoneHeight = Math.max(0.16, 0.3 - clamped * 0.14);
  const zoneSpeed = MAX_SPEED * (0.6 + clamped * 0.07);
  const reversalChance = 0.12;
  const breakeven = 0.25 + clamped * 0.08;
  const totalRate = 0.24;
  const fillRate = (1 - breakeven) * totalRate;
  const drainRate = breakeven * totalRate;
  const timeLimitS = 34 - clamped * 6;

  const zoneHalfHeightStart = zoneHeight / 2;
  const startGap = zoneHeight * 1.5;
  let zoneCenter = zoneHalfHeightStart + Math.random() * (1 - zoneHeight);
  if (Math.abs(zoneCenter - 0.5) < startGap) {
    zoneCenter =
      zoneCenter < 0.5
        ? Math.max(zoneHalfHeightStart, 0.5 - startGap)
        : Math.min(1 - zoneHalfHeightStart, 0.5 + startGap);
  }
  let zoneDir = Math.random() < 0.5 ? 1 : -1;

  let done = false;
  let started = false;
  let timeLeft = timeLimitS;
  let progress = 0.5;
  let bobberPos = 0.5;
  let bobberVelocity = 0;
  let reeling = false;
  let rafId = null;
  let lastTs = null;

  boardEl.className = "board-lockpick board-fishing";
  boardEl.innerHTML = `
    <canvas id="fishing-canvas" width="${WIDTH}" height="${HEIGHT}" tabindex="0"></canvas>
    <p class="lockpick-meta">
      <span id="fishing-progress">Tension: 50%</span>
      <span id="fishing-timer">${Math.ceil(timeLimitS)}s</span>
    </p>
  `;
  const canvas = boardEl.querySelector("#fishing-canvas");
  const ctx = canvas.getContext("2d");
  const progressEl = boardEl.querySelector("#fishing-progress");
  const timerEl = boardEl.querySelector("#fishing-timer");

  function zoneHalfHeight() {
    return zoneHeight / 2;
  }

  function onTarget() {
    const half = zoneHalfHeight();
    return bobberPos >= zoneCenter - half && bobberPos <= zoneCenter + half;
  }

  function finish(won) {
    if (done) return;
    done = true;
    cancelAnimationFrame(rafId);
    window.removeEventListener("keydown", onKeyDown);
    window.removeEventListener("keyup", onKeyUp);
    canvas.removeEventListener("pointerdown", onPointerDown);
    window.removeEventListener("pointerup", onPointerUp);
    window.removeEventListener("pointercancel", onPointerUp);
    onFinish(won);
  }

  function update(dt) {
    if (done) return;

    const half = zoneHalfHeight();
    zoneCenter += zoneDir * zoneSpeed * dt;
    if (zoneCenter <= half) {
      zoneCenter = half;
      zoneDir = 1;
    } else if (zoneCenter >= 1 - half) {
      zoneCenter = 1 - half;
      zoneDir = -1;
    } else if (Math.random() < reversalChance * dt) {
      zoneDir *= -1;
    }

    const accel = (reeling ? THRUST : 0) - GRAVITY;
    bobberVelocity = clamp(bobberVelocity + accel * dt, -MAX_SPEED, MAX_SPEED);
    bobberPos = clamp(bobberPos + bobberVelocity * dt, 0, 1);

    progress = clamp(progress + (onTarget() ? fillRate : -drainRate) * dt, 0, 1);
    progressEl.textContent = `Tension: ${Math.round(progress * 100)}%`;

    if (progress >= 1) {
      if (setStatus) setStatus("Got it! The catch is yours.");
      finish(true);
      return;
    }
    if (progress <= 0) {
      if (setStatus) setStatus("The line goes slack -- it got away.");
      finish(false);
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

    // Water column.
    ctx.fillStyle = "#15293a";
    ctx.fillRect(TRACK_X, TRACK_Y, TRACK_W, TRACK_H);

    // Bite zone, drifting within the water column.
    const half = zoneHalfHeight();
    const zoneTopY = TRACK_Y + TRACK_H * (1 - (zoneCenter + half));
    const zoneH = TRACK_H * zoneHeight;
    ctx.fillStyle = "#3aa7e0";
    ctx.globalAlpha = 0.45;
    ctx.fillRect(TRACK_X, zoneTopY, TRACK_W, zoneH);
    ctx.globalAlpha = 1;

    // The bobber (player-controlled line).
    const bobberY = TRACK_Y + TRACK_H * (1 - bobberPos);
    ctx.fillStyle = onTarget() ? "#ffffff" : "#e0736b";
    ctx.fillRect(TRACK_X - 6, bobberY - BOBBER_THICKNESS / 2, TRACK_W + 12, BOBBER_THICKNESS);

    ctx.fillStyle = "#8b93a3";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("bobber", TRACK_X + TRACK_W / 2, TRACK_Y + TRACK_H + 16);

    // Tension meter.
    ctx.fillStyle = "#222";
    ctx.fillRect(METER_X, TRACK_Y, METER_W, TRACK_H);
    const filledH = TRACK_H * progress;
    ctx.fillStyle = progress > 0.5 ? "#3aa7e0" : progress > 0.2 ? "#e0a72e" : "#e0736b";
    ctx.fillRect(METER_X, TRACK_Y + TRACK_H - filledH, METER_W, filledH);
    ctx.fillText("tension", METER_X + METER_W / 2, TRACK_Y + TRACK_H + 16);

    if (!started) {
      ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
      ctx.fillRect(0, 0, WIDTH, HEIGHT);
      ctx.fillStyle = "#ffffff";
      ctx.font = "bold 13px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("Hold W or click", WIDTH / 2, HEIGHT / 2 - 8);
      ctx.fillText("to begin", WIDTH / 2, HEIGHT / 2 + 10);
    }
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

  function beginIfNeeded() {
    if (started || done) return;
    started = true;
    if (setStatus) setStatus("");
    rafId = requestAnimationFrame(loop);
  }

  function onKeyDown(event) {
    if (done) return;
    if (event.code === "KeyW" || event.code === "ArrowUp") {
      reeling = true;
      beginIfNeeded();
      event.preventDefault();
    }
  }

  function onKeyUp(event) {
    if (event.code === "KeyW" || event.code === "ArrowUp") reeling = false;
  }

  function onPointerDown(event) {
    if (done) return;
    canvas.focus();
    reeling = true;
    beginIfNeeded();
    event.preventDefault();
  }

  function onPointerUp() {
    reeling = false;
  }

  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", onKeyUp);
  canvas.addEventListener("pointerdown", onPointerDown);
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("pointercancel", onPointerUp);

  if (setStatus) setStatus("Ready when you are -- hold W or click to start.");
  render();
  canvas.focus();
}
