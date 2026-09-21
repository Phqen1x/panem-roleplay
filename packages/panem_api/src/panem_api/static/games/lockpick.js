// An original lockpicking minigame -- a single drifting pressure zone the
// player has to track with a hand-controlled pick, filling a catch meter
// while on target and draining it while off, the general shape of Stardew
// Valley's fishing minigame (a moving target + a player-steered indicator
// + a fill/drain progress meter that wins at full and loses at empty).
// That's a description of a mechanic, not a copy of anyone's code or
// assets -- this is a fresh canvas implementation, same IP-safety posture
// as this file's earlier pin-tumbler version (replaced here after user
// feedback that fiddling with several pins at once was fussier than fun;
// see the README entry for this rewrite for the full history, including
// the still-unreachable Unity/C# GPLv3 project an even earlier version of
// this file was asked to reference and couldn't). Shared by `/lockpick`
// (escaping jail) and `/burgle` (breaking into a house) -- picking a lock
// is picking a lock either way, just against a cell door or a house door.
export const label = "Lockpick";
export function instructions() {
  return (
    "Hold W to push the pick up, S to ease it down. Keep it inside the " +
    "green pressure zone as it drifts -- the meter fills while you're on " +
    "target and drains when you're off. Fill it to turn the lock; let it " +
    "drain empty and the pick slips free."
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
const PICK_THICKNESS = 8;
const ACCEL = 2.4; // track-heights/sec^2 applied while a direction is held
const DRAG = 2.0; // track-heights/sec^2 of always-on resistance
const MAX_SPEED = 1.15; // track-heights/sec, clamped

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function mount(boardEl, { onFinish, setStatus, difficulty = 0.5 }) {
  const clamped = clamp(difficulty, 0, 0.95);
  const zoneHeight = Math.max(0.15, 0.34 - clamped * 0.2); // fraction of track height
  const driftRate = 0.55 + clamped * 1.35; // zone's random-walk magnitude, per second
  // Deliberately slow: a rate fast enough to decide the game within a
  // fraction of a second (an earlier version of these numbers could drain
  // half the meter before a human even finishes registering the mismatch)
  // isn't testing tracking skill, it's testing reflexes nobody has. A full
  // empty-to-full swing takes several seconds even at max difficulty, so
  // one bad moment is a setback to recover from, not an instant loss.
  const fillRate = 0.22 - clamped * 0.12; // meter units/sec while on target
  const drainRate = 0.1 + clamped * 0.14; // meter units/sec while off target
  const timeLimitS = 34 - clamped * 6; // generous safety cap -- the meter is the real clock

  // The pick always starts centered (0.5) -- if the zone did too, it'd
  // start already caught, and a difficulty-0.95 attempt could win from a
  // second of doing nothing before the drift ever got a chance to test
  // anything. Force a real starting gap (at least 1.5 zone-heights from
  // center) so every attempt opens with an actual find-it moment, the
  // same as a fish rarely biting right where your line already sits.
  const zoneHalfHeightStart = zoneHeight / 2;
  const startGap = zoneHeight * 1.5;
  let zoneCenter = zoneHalfHeightStart + Math.random() * (1 - zoneHeight);
  if (Math.abs(zoneCenter - 0.5) < startGap) {
    zoneCenter =
      zoneCenter < 0.5
        ? Math.max(zoneHalfHeightStart, 0.5 - startGap)
        : Math.min(1 - zoneHalfHeightStart, 0.5 + startGap);
  }

  let done = false;
  let timeLeft = timeLimitS;
  let progress = 0.5;
  let pickPos = 0.5;
  let pickVelocity = 0;
  let zoneVelocity = 0;
  let holdingUp = false;
  let holdingDown = false;
  let rafId = null;
  let lastTs = null;

  boardEl.className = "board-lockpick";
  boardEl.innerHTML = `
    <canvas id="lockpick-canvas" width="${WIDTH}" height="${HEIGHT}" tabindex="0"></canvas>
    <p class="lockpick-meta">
      <span id="lockpick-progress">Progress: 50%</span>
      <span id="lockpick-timer">${Math.ceil(timeLimitS)}s</span>
    </p>
  `;
  const canvas = boardEl.querySelector("#lockpick-canvas");
  const ctx = canvas.getContext("2d");
  const progressEl = boardEl.querySelector("#lockpick-progress");
  const timerEl = boardEl.querySelector("#lockpick-timer");

  function zoneHalfHeight() {
    return zoneHeight / 2;
  }

  function onTarget() {
    const half = zoneHalfHeight();
    return pickPos >= zoneCenter - half && pickPos <= zoneCenter + half;
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

    // The pressure zone free-drifts via a damped random walk, the same
    // "keep checking back on it" shape a fish's wandering has -- there's
    // no fixed pattern to memorize, only a rate of change to react to.
    zoneVelocity += (Math.random() - 0.5) * driftRate * dt;
    zoneVelocity *= 0.92;
    const half = zoneHalfHeight();
    zoneCenter = clamp(zoneCenter + zoneVelocity * dt, half, 1 - half);

    // The pick has momentum: holding a direction accelerates it, and a
    // constant drag always pulls that velocity back toward zero, so
    // letting go doesn't stop it dead but does bleed it off -- the same
    // "juggling," not "snapping to a spot," feel the old tension wrench
    // had.
    let accel = 0;
    if (holdingUp) accel += ACCEL;
    if (holdingDown) accel -= ACCEL;
    pickVelocity += accel * dt;
    const dragMag = Math.min(Math.abs(pickVelocity), DRAG * dt);
    pickVelocity -= Math.sign(pickVelocity) * dragMag;
    pickVelocity = clamp(pickVelocity, -MAX_SPEED, MAX_SPEED);
    pickPos = clamp(pickPos + pickVelocity * dt, 0, 1);

    progress = clamp(progress + (onTarget() ? fillRate : -drainRate) * dt, 0, 1);
    progressEl.textContent = `Progress: ${Math.round(progress * 100)}%`;

    if (progress >= 1) {
      if (setStatus) setStatus("The pins line up -- the lock turns.");
      finish(true);
      return;
    }
    if (progress <= 0) {
      if (setStatus) setStatus("The pick slips free.");
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

    // Track background.
    ctx.fillStyle = "#222";
    ctx.fillRect(TRACK_X, TRACK_Y, TRACK_W, TRACK_H);

    // Pressure zone (the "green spot"), drifting within the track.
    const half = zoneHalfHeight();
    const zoneTopY = TRACK_Y + TRACK_H * (1 - (zoneCenter + half));
    const zoneH = TRACK_H * zoneHeight;
    ctx.fillStyle = "#2ed573";
    ctx.globalAlpha = 0.4;
    ctx.fillRect(TRACK_X, zoneTopY, TRACK_W, zoneH);
    ctx.globalAlpha = 1;

    // The pick itself (the player-controlled line).
    const pickY = TRACK_Y + TRACK_H * (1 - pickPos);
    ctx.fillStyle = onTarget() ? "#ffffff" : "#e0736b";
    ctx.fillRect(TRACK_X - 6, pickY - PICK_THICKNESS / 2, TRACK_W + 12, PICK_THICKNESS);

    ctx.fillStyle = "#8b93a3";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("pick", TRACK_X + TRACK_W / 2, TRACK_Y + TRACK_H + 16);

    // Progress meter.
    ctx.fillStyle = "#222";
    ctx.fillRect(METER_X, TRACK_Y, METER_W, TRACK_H);
    const filledH = TRACK_H * progress;
    ctx.fillStyle = progress > 0.5 ? "#2ed573" : progress > 0.2 ? "#e0a72e" : "#e0736b";
    ctx.fillRect(METER_X, TRACK_Y + TRACK_H - filledH, METER_W, filledH);
    ctx.fillText("meter", METER_X + METER_W / 2, TRACK_Y + TRACK_H + 16);
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

  function onKeyDown(event) {
    if (done) return;
    if (event.code === "KeyW" || event.code === "ArrowUp") {
      holdingUp = true;
      event.preventDefault();
    } else if (event.code === "KeyS" || event.code === "ArrowDown") {
      holdingDown = true;
      event.preventDefault();
    }
  }

  function onKeyUp(event) {
    if (event.code === "KeyW" || event.code === "ArrowUp") holdingUp = false;
    else if (event.code === "KeyS" || event.code === "ArrowDown") holdingDown = false;
  }

  // Touch/pointer fallback for Activities opened without a keyboard: the
  // top half of the track pushes up, the bottom half pushes down, held
  // for as long as the pointer stays down -- same two directions as W/S,
  // just aimed with a thumb instead.
  function onPointerDown(event) {
    if (done) return;
    // This game is normally embedded in an <iframe> (the dashboard's Jail/
    // Crime tabs), which never has keyboard focus by default -- nothing
    // auto-focuses a newly inserted iframe, and this handler's own
    // `preventDefault()` below (needed to stop touch-scroll/selection on
    // tap) also suppresses the click's *default* focus-the-clicked-frame
    // behavior. Without an explicit focus() call here, W/S's `keydown`
    // listener (on `window`) never actually fires -- only this pointer
    // handler, which doesn't need frame focus to receive events on
    // `canvas` directly -- which looked like "W/S do nothing, only
    // clicking works" from the outside.
    canvas.focus();
    const rect = canvas.getBoundingClientRect();
    const y = ((event.clientY - rect.top) / rect.height) * HEIGHT;
    if (y < TRACK_Y + TRACK_H / 2) holdingUp = true;
    else holdingDown = true;
    event.preventDefault();
  }

  function onPointerUp() {
    holdingUp = false;
    holdingDown = false;
  }

  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", onKeyUp);
  canvas.addEventListener("pointerdown", onPointerDown);
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("pointercancel", onPointerUp);

  render();
  rafId = requestAnimationFrame(loop);
  // Best-effort: some contexts (a direct, non-iframed load) allow this to
  // actually grab focus immediately, letting W/S work with no click
  // first. Where it doesn't (an iframe with no prior user gesture in it),
  // this is a silent no-op and onPointerDown's own focus() call above
  // covers it on first interaction instead.
  canvas.focus();
}
