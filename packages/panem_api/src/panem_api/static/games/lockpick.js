// An original lockpicking minigame -- a single pressure zone that bounces
// back and forth across the track, which the player has to track with a
// hand-controlled pick, filling a catch meter while on target and
// draining it while off, the general shape of Stardew Valley's fishing
// minigame (a moving target + a player-steered indicator + a fill/drain
// progress meter that wins at full and loses at empty).
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
    "green pressure zone as it bounces back and forth -- the meter fills " +
    "while you're on target and drains when you're off. Fill it to turn " +
    "the lock; let it drain empty and the pick slips free."
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
// ACCEL must exceed DRAG, not just be close to it: each frame first adds
// accel*dt to the velocity, then subtracts a drag magnitude capped at
// DRAG*dt in the same direction as that new velocity. Set ACCEL === DRAG
// (as a previous pass here briefly did) and that cap exactly equals what
// accel just added, so drag cancels it in full on every single frame --
// the pick can never move at all no matter how long a key is held. That
// shipped once and made every "held W/S" test indistinguishable from
// doing nothing, which is what a wave of confusing playtest numbers
// turned out to actually be.
const ACCEL = 5.6; // track-heights/sec^2 applied while a direction is held
const DRAG = 2.6; // track-heights/sec^2 of always-on resistance
// MAX_SPEED chosen so a full-speed stop (v^2 / (2*DRAG)) travels less
// than the smallest zone height (0.16): here that's ~0.11. Any faster and
// releasing the key "at" the zone overshoots straight through it no
// matter how good the reaction.
const MAX_SPEED = 0.75; // track-heights/sec, clamped

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function mount(boardEl, { onFinish, setStatus, difficulty = 0.5 }) {
  const clamped = clamp(difficulty, 0, 0.95);
  const zoneHeight = Math.max(0.16, 0.3 - clamped * 0.14); // fraction of track height
  // The zone bounces back and forth at a steady speed like a reflecting
  // ball, with an occasional early random reversal so it isn't a
  // perfectly predictable metronome -- not the "pick a random new
  // waypoint on arrival" version this replaced. That approach (and the
  // damped-random-walk version before it) both suffer the same
  // well-known bias: repeatedly retargeting toward independent random
  // points concentrates dwell time near the middle of the range (the
  // "random waypoint mobility model" border effect), which is exactly
  // where the pick sits when the player does nothing at all -- measured,
  // that let a completely idle player win close to half the time purely
  // from the zone drifting back over dead center. A constant-speed
  // bounce has a close-to-uniform space-time distribution instead, so
  // "did nothing" and "actively tracked" produce genuinely different
  // on-target rates.
  const zoneSpeed = 0.16 + clamped * 0.22; // track-heights/sec
  const reversalChance = 0.12; // per-second chance of an early random reversal

  // A constant-speed bounce spends time on any fixed point roughly
  // proportional to zoneHeight / (1 - zoneHeight) of the whole run --
  // confirmed by direct measurement (~0.42/0.27/0.18 at difficulty
  // 0/0.5/0.95, matching this formula closely). That's exactly how often
  // a pick that never moves at all ends up "on target" by pure chance, so
  // the win condition has to clear it with real margin -- not eyeballed,
  // derived from it directly, with a fixed 1.2x safety factor. (An
  // earlier 1.6x factor was tuned against a version of this file with a
  // real physics bug -- ACCEL exactly equalling DRAG below, which pinned
  // the pick's velocity at zero no matter what was held, so every
  // "tracking" test silently measured the same thing as idle. With that
  // fixed, a predictive scripted controller clears a ~0.70/0.51/0.27
  // on-target rate at the three difficulties above, comfortably above
  // this factor's ~0.51/0.36/0.24 breakeven with real margin to spare.)
  const passiveFraction = zoneHeight / (1 - zoneHeight);
  const breakeven = clamp(passiveFraction * 1.2, 0.24, 0.6);
  const totalRate = 0.24; // meter units/sec, fillRate+drainRate -- overall pace
  const fillRate = (1 - breakeven) * totalRate; // meter units/sec while on target
  const drainRate = breakeven * totalRate; // meter units/sec while off target
  const timeLimitS = 34 - clamped * 6; // generous safety cap -- the meter is the real clock

  const zoneHalfHeightStart = zoneHeight / 2;

  // The pick always starts centered (0.5) -- if the zone did too, it'd
  // start already caught, and a difficulty-0.95 attempt could win from a
  // second of doing nothing before the bounce ever got a chance to test
  // anything. Force a real starting gap (at least 1.5 zone-heights from
  // center) so every attempt opens with an actual find-it moment, the
  // same as a fish rarely biting right where your line already sits.
  const startGap = zoneHeight * 1.5;
  let zoneCenter = zoneHalfHeightStart + Math.random() * (1 - zoneHeight);
  if (Math.abs(zoneCenter - 0.5) < startGap) {
    zoneCenter =
      zoneCenter < 0.5
        ? Math.max(zoneHalfHeightStart, 0.5 - startGap)
        : Math.min(1 - zoneHalfHeightStart, 0.5 + startGap);
  }
  // Genuinely random initial direction -- always heading back toward
  // center first guaranteed an early free crossing over the (still
  // stationary) pick on literally every attempt, which is exactly the
  // free win the starting gap above exists to prevent. A coin flip means
  // roughly half of attempts open moving away instead, so an early catch
  // is possible but never guaranteed.
  let zoneDir = Math.random() < 0.5 ? 1 : -1;

  let done = false;
  let timeLeft = timeLimitS;
  let progress = 0.5;
  let pickPos = 0.5;
  let pickVelocity = 0;
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

    // The pressure zone bounces back and forth at a steady pace, the same
    // "keep checking back on it" shape a fish's wandering has -- there's
    // no fixed pattern to memorize, only a direction and pace to react
    // to. It reflects off both ends and occasionally reverses early at
    // random so a sharp player can't just count out the full period.
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

    // Pressure zone (the "green spot"), bouncing within the track.
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
