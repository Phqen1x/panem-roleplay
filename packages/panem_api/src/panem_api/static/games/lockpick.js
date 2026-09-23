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
    "The pick keeps sinking on its own -- hold W or click and hold to rise " +
    "against it. Keep it hovering inside the green pressure zone as it " +
    "bounces up and down the track -- the meter fills while you're on " +
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
// A one-button "hover" control, not a two-key up/down one: GRAVITY always
// pulls the pick down, and holding the one control key adds THRUST on top
// of it. THRUST must exceed GRAVITY by a real margin -- not just barely --
// or holding the key can't actually make the pick rise at all (the same
// class of bug as an earlier version of this file's two-key model, where
// ACCEL and DRAG briefly ended up equal and canceled each other out every
// frame). Net rising accel is THRUST - GRAVITY; net falling accel while
// idle is just -GRAVITY.
const GRAVITY = 1.2; // track-heights/sec^2, always applied downward
const THRUST = 2.75; // track-heights/sec^2, applied upward only while held
// MAX_SPEED chosen so a full-speed stop (v^2 / (2*GRAVITY)) travels well
// under the smallest zone height (0.16): here that's ~0.09. A faster cap
// means rising at full speed and releasing "at" the zone overshoots
// straight past it -- the same overshoot bug the earlier two-key model
// had, measured directly there (a scripted controller topped out around
// 25%-30% on-target against this physics's first, faster pass at these
// numbers) before the cap was brought down to fix it.
const MAX_SPEED = 0.47; // track-heights/sec, clamped in both directions

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
  // Slightly slower than the pick's own top speed (65%-75% of MAX_SPEED)
  // so a player who's actually tracking it can out-pace it, rather than
  // the zone being able to outrun the pick outright.
  const zoneSpeed = MAX_SPEED * (0.6 + clamped * 0.07); // track-heights/sec
  const reversalChance = 0.12; // per-second chance of an early random reversal

  // An earlier version of this file derived breakeven from zoneHeight /
  // (1 - zoneHeight) -- how much of the run a constant-speed bounce
  // spends on any *interior* fixed point, since that model's idle pick
  // sat frozen at the track's center. This one-button "hover" model is
  // different: an idle pick (never held) falls all the way to the floor
  // (position 0) and stays pinned there, but the zone's own *center*
  // never goes below `zoneHeight / 2` -- so its covered interval only
  // ever touches position 0 for a single instant, right at the bottom of
  // each bounce, not the sustained dwell time an interior point gets.
  // Measured directly: an idle pick's on-target fraction is ~0.00-0.03 at
  // every difficulty here, a rounding error next to the old model's
  // ~0.18-0.43. Idle winning is a non-issue now, so breakeven only needs
  // enough margin over that ~0.03 idle rate to keep idle a guaranteed
  // loss -- it doesn't need to climb anywhere near "genuinely hard to
  // track," which a first pass at this curve (0.3 + clamped*0.15, topping
  // out at 0.4425) did: a scripted controller with excellent (30-60ms)
  // reaction time still only reached ~0.43 on-target at the hardest
  // difficulty, i.e. a strong player was losing at max difficulty
  // regardless of skill. This flatter curve keeps easy/medium about where
  // they were while giving the hardest difficulty real headroom above
  // measured tracked play (~0.5-0.55 on-target at 140ms reaction).
  const breakeven = 0.25 + clamped * 0.08; // 0.25 (easy) to 0.326 (hardest)
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
  let started = false; // physics/timer stay frozen until the player's first input
  let timeLeft = timeLimitS;
  let progress = 0.5;
  let pickPos = 0.5;
  let pickVelocity = 0;
  let holdingUp = false; // the one control: W or a held click/tap
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

    // The pick is always sinking -- GRAVITY applies every frame whether or
    // not the control is held -- and holding it adds THRUST on top, the
    // same "hover" feel Stardew's own fishing bar has: let go and it falls
    // on its own, hold to climb back against it.
    const accel = (holdingUp ? THRUST : 0) - GRAVITY;
    pickVelocity = clamp(pickVelocity + accel * dt, -MAX_SPEED, MAX_SPEED);
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

  // The pick keeps sinking and the clock keeps running the instant `loop`
  // starts -- gated behind the player's first input (not started at mount)
  // so a player who takes a moment to read the instructions first doesn't
  // lose real time/meter progress to a lock that was already ticking
  // before they'd even looked at the board.
  function beginIfNeeded() {
    if (started || done) return;
    started = true;
    if (setStatus) setStatus("");
    rafId = requestAnimationFrame(loop);
  }

  function onKeyDown(event) {
    if (done) return;
    if (event.code === "KeyW" || event.code === "ArrowUp") {
      holdingUp = true;
      beginIfNeeded();
      event.preventDefault();
    }
  }

  function onKeyUp(event) {
    if (event.code === "KeyW" || event.code === "ArrowUp") holdingUp = false;
  }

  // Touch/pointer fallback for Activities opened without a keyboard: held
  // anywhere on the canvas, same one control W is -- there's only one
  // direction to aim now, so there's no top/bottom split to read.
  function onPointerDown(event) {
    if (done) return;
    // This game is normally embedded in an <iframe> (the dashboard's Jail/
    // Crime tabs), which never has keyboard focus by default -- nothing
    // auto-focuses a newly inserted iframe, and this handler's own
    // `preventDefault()` below (needed to stop touch-scroll/selection on
    // tap) also suppresses the click's *default* focus-the-clicked-frame
    // behavior. Without an explicit focus() call here, W's `keydown`
    // listener (on `window`) never actually fires -- only this pointer
    // handler, which doesn't need frame focus to receive events on
    // `canvas` directly -- which looked like "W does nothing, only
    // clicking works" from the outside.
    canvas.focus();
    holdingUp = true;
    beginIfNeeded();
    event.preventDefault();
  }

  function onPointerUp() {
    holdingUp = false;
  }

  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", onKeyUp);
  canvas.addEventListener("pointerdown", onPointerDown);
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("pointercancel", onPointerUp);

  if (setStatus) setStatus("Ready when you are -- hold W or click to start.");
  render();
  // Best-effort: some contexts (a direct, non-iframed load) allow this to
  // actually grab focus immediately, letting W work with no click first.
  // Where it doesn't (an iframe with no prior user gesture in it), this is
  // a silent no-op and onPointerDown's own focus() call above covers it on
  // first interaction instead.
  canvas.focus();
}
