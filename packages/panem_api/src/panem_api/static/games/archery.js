// Archery target-practice minigame for `/poach` -- aim with the mouse and
// click to loose an arrow at a moving target; 5 arrows, 30 seconds, land
// at least 3 hits to bring the hunt home. An original canvas sketch (a
// bouncing-target aim-and-click game), same IP-safety posture as every
// other minigame module here.
export const label = "Archery";
export function instructions() {
  return (
    "Aim with your mouse and click to loose an arrow at the moving target. " +
    "You have 5 arrows and 30 seconds -- land at least 3 hits to bring the " +
    "hunt home."
  );
}

const WIDTH = 360;
const HEIGHT = 240;
const TOTAL_ARROWS = 5;
const HITS_NEEDED = 3;
const TIME_LIMIT_MS = 30000;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function mount(boardEl, { onFinish, difficulty = 0.5 }) {
  const clamped = clamp(difficulty, 0, 0.95);
  // A harder attempt reads as a smaller, faster-moving target -- the same
  // shape `games/pickpocket.js`'s zone width/needle speed scale with its
  // own `difficulty`.
  const targetRadius = Math.max(14, Math.round(34 - clamped * 16));
  const speed = 60 + clamped * 90; // px/sec

  let done = false;
  boardEl.className = "board-archery";
  boardEl.innerHTML = `
    <canvas id="archery-canvas" width="${WIDTH}" height="${HEIGHT}"></canvas>
    <p class="archery-meta">
      <span id="archery-arrows">Arrows: ${TOTAL_ARROWS}</span>
      <span id="archery-hits">Hits: 0/${HITS_NEEDED}</span>
      <span id="archery-time">Time: ${Math.ceil(TIME_LIMIT_MS / 1000)}s</span>
    </p>
  `;
  const canvas = boardEl.querySelector("#archery-canvas");
  const ctx = canvas.getContext("2d");
  const arrowsEl = boardEl.querySelector("#archery-arrows");
  const hitsEl = boardEl.querySelector("#archery-hits");
  const timeEl = boardEl.querySelector("#archery-time");

  let targetX = WIDTH / 2;
  let targetY = HEIGHT / 2;
  let vx = speed * (Math.random() < 0.5 ? 1 : -1);
  let vy = speed * (Math.random() < 0.5 ? 1 : -1);
  let mouseX = WIDTH / 2;
  let mouseY = HEIGHT / 2;
  let arrowsLeft = TOTAL_ARROWS;
  let hits = 0;
  const shots = [];
  const startedAt = performance.now();
  let lastTs = startedAt;
  let rafId = null;

  function timeLeftMs() {
    return Math.max(0, TIME_LIMIT_MS - (performance.now() - startedAt));
  }

  function render() {
    ctx.clearRect(0, 0, WIDTH, HEIGHT);
    ctx.fillStyle = "#16321c";
    ctx.fillRect(0, 0, WIDTH, HEIGHT);

    for (const [radius, color] of [
      [targetRadius, "#e0736b"],
      [targetRadius * 0.6, "#f5d76e"],
      [targetRadius * 0.25, "#7cd992"],
    ]) {
      ctx.beginPath();
      ctx.fillStyle = color;
      ctx.arc(targetX, targetY, radius, 0, Math.PI * 2);
      ctx.fill();
    }

    for (const shot of shots) {
      ctx.beginPath();
      ctx.strokeStyle = shot.hit ? "#7cd992" : "#888";
      ctx.lineWidth = 2;
      const r = 6;
      ctx.moveTo(shot.x - r, shot.y - r);
      ctx.lineTo(shot.x + r, shot.y + r);
      ctx.moveTo(shot.x + r, shot.y - r);
      ctx.lineTo(shot.x - r, shot.y + r);
      ctx.stroke();
    }

    ctx.beginPath();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1.5;
    ctx.arc(mouseX, mouseY, 10, 0, Math.PI * 2);
    ctx.moveTo(mouseX - 14, mouseY);
    ctx.lineTo(mouseX + 14, mouseY);
    ctx.moveTo(mouseX, mouseY - 14);
    ctx.lineTo(mouseX, mouseY + 14);
    ctx.stroke();
  }

  function finish() {
    if (done) return;
    done = true;
    if (rafId !== null) cancelAnimationFrame(rafId);
    canvas.removeEventListener("mousemove", onMouseMove);
    canvas.removeEventListener("click", onClick);
    onFinish(hits >= HITS_NEEDED);
  }

  function tick(ts) {
    if (done) return;
    const dtSec = Math.min(0.05, (ts - lastTs) / 1000);
    lastTs = ts;
    targetX += vx * dtSec;
    targetY += vy * dtSec;
    if (targetX <= targetRadius || targetX >= WIDTH - targetRadius) {
      vx *= -1;
      targetX = clamp(targetX, targetRadius, WIDTH - targetRadius);
    }
    if (targetY <= targetRadius || targetY >= HEIGHT - targetRadius) {
      vy *= -1;
      targetY = clamp(targetY, targetRadius, HEIGHT - targetRadius);
    }
    const msLeft = timeLeftMs();
    timeEl.textContent = `Time: ${Math.ceil(msLeft / 1000)}s`;
    render();
    if (msLeft <= 0) {
      finish();
      return;
    }
    rafId = requestAnimationFrame(tick);
  }

  function canvasPoint(event) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * WIDTH,
      y: ((event.clientY - rect.top) / rect.height) * HEIGHT,
    };
  }

  function onMouseMove(event) {
    const point = canvasPoint(event);
    mouseX = point.x;
    mouseY = point.y;
  }

  function onClick(event) {
    if (done || arrowsLeft <= 0) return;
    const { x, y } = canvasPoint(event);
    const hit = Math.hypot(x - targetX, y - targetY) <= targetRadius;
    if (hit) hits += 1;
    shots.push({ x, y, hit });
    arrowsLeft -= 1;
    arrowsEl.textContent = `Arrows: ${arrowsLeft}`;
    hitsEl.textContent = `Hits: ${hits}/${HITS_NEEDED}`;
    if (arrowsLeft <= 0) {
      render();
      finish();
      return;
    }
    render();
  }

  canvas.addEventListener("mousemove", onMouseMove);
  canvas.addEventListener("click", onClick);

  render();
  rafId = requestAnimationFrame(tick);
}
