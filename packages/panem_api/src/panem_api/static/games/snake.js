// Snake. Arrow keys, WASD, or a swipe/tap on the board steer; eating
// `WIN_SCORE` food clears the shift, crashing into a wall or yourself
// before then loses it. The target score climbs with job level
// (`levelIndex`, 0 = Apprentice.. 4 = Expert) -- same board, same speed,
// just more to survive for.

const GRID_SIZE = 20;
const CELL_PX = 20;
const TICK_MS = 130;
const BASE_WIN_SCORE = 8;
const WIN_SCORE_GROWTH_PER_LEVEL = 4;

function winScoreForLevel(levelIndex) {
  return BASE_WIN_SCORE + WIN_SCORE_GROWTH_PER_LEVEL * Math.max(0, levelIndex);
}

const SWIPE_THRESHOLD_PX = 16;

export const label = "Snake";
export function instructions(levelIndex = 0) {
  const winScore = winScoreForLevel(levelIndex);
  return `Eat ${winScore} to clear the shift -- arrow keys, WASD, or swipe/tap the board to start, don't hit a wall or yourself.`;
}

const DIRECTIONS = {
  ArrowUp: { x: 0, y: -1 },
  ArrowDown: { x: 0, y: 1 },
  ArrowLeft: { x: -1, y: 0 },
  ArrowRight: { x: 1, y: 0 },
  w: { x: 0, y: -1 },
  s: { x: 0, y: 1 },
  a: { x: -1, y: 0 },
  d: { x: 1, y: 0 },
};

export function mount(boardEl, { onFinish, setStatus, levelIndex = 0 }) {
  const WIN_SCORE = winScoreForLevel(levelIndex);
  boardEl.className = "board-snake";
  boardEl.innerHTML = `<canvas id="snake-canvas" width="${GRID_SIZE * CELL_PX}" height="${
    GRID_SIZE * CELL_PX
  }"></canvas><p class="snake-score">Score: <span id="snake-score">0</span> / ${WIN_SCORE}</p>`;
  const canvas = boardEl.querySelector("#snake-canvas");
  const scoreEl = boardEl.querySelector("#snake-score");
  const ctx = canvas.getContext("2d");
  // Without this, a touch drag on the canvas is the browser's own
  // scroll/zoom gesture first and a `pointer*` event second (if it gets
  // one at all) -- this is what actually made "tapping/swiping" look
  // broken on mobile: the page scrolled instead of steering the snake.
  canvas.style.touchAction = "none";
  // Arrow keys/WASD are read off a `keydown` listener below, but keydown
  // only reaches whichever element (and, when this page is loaded inside
  // work.js's dashboard-tab `<iframe>`, whichever *frame*) currently has
  // focus -- neither the canvas nor the iframe gets that automatically
  // just by being on screen, which is what made the keys look dead
  // without first clicking into the board. Giving the canvas a tabIndex
  // and focusing it here (plus work.js/tabs/work.js focusing their side
  // of an iframe embedding) means a shift's snake game is steerable the
  // instant it mounts, no click required.
  canvas.tabIndex = -1;
  canvas.style.outline = "none";
  canvas.focus({ preventScroll: true });

  let snake = [
    { x: 10, y: 10 },
    { x: 9, y: 10 },
    { x: 8, y: 10 },
  ];
  let direction = { x: 1, y: 0 };
  let pendingDirection = direction;
  let food = placeFood();
  let score = 0;
  let gameOver = false;
  let timer = null;
  let started = false;

  function placeFood() {
    while (true) {
      const candidate = {
        x: Math.floor(Math.random() * GRID_SIZE),
        y: Math.floor(Math.random() * GRID_SIZE),
      };
      if (!snake.some((s) => s.x === candidate.x && s.y === candidate.y)) return candidate;
    }
  }

  function steer(next) {
    if (next.x === -direction.x && next.y === -direction.y) return;
    pendingDirection = next;
    if (!started) {
      started = true;
      timer = setInterval(tick, TICK_MS);
    }
  }

  function onKeyDown(event) {
    const next = DIRECTIONS[event.key];
    if (!next) return;
    event.preventDefault();
    steer(next);
  }

  // Touch/pointer fallback for Activities opened without a keyboard: a
  // swipe steers the same as an arrow key would, and a plain tap (too
  // short a drag to read as a swipe) just starts the game moving in its
  // current direction -- same as pressing the key it's already heading in.
  let pointerStart = null;

  function onPointerDown(event) {
    pointerStart = { x: event.clientX, y: event.clientY };
    // `preventDefault()` (needed below to stop the touch turning into a
    // page scroll) also suppresses the browser's default click-to-focus
    // behavior, so a tap wouldn't hand keyboard focus back to the canvas
    // without this explicit call.
    canvas.focus({ preventScroll: true });
    event.preventDefault();
  }

  function onPointerUp(event) {
    if (!pointerStart) return;
    const dx = event.clientX - pointerStart.x;
    const dy = event.clientY - pointerStart.y;
    pointerStart = null;
    if (Math.abs(dx) < SWIPE_THRESHOLD_PX && Math.abs(dy) < SWIPE_THRESHOLD_PX) {
      steer(direction);
      return;
    }
    const next =
      Math.abs(dx) > Math.abs(dy) ? { x: dx > 0 ? 1 : -1, y: 0 } : { x: 0, y: dy > 0 ? 1 : -1 };
    steer(next);
  }

  function onPointerCancel() {
    pointerStart = null;
  }

  function draw() {
    ctx.fillStyle = "#14161c";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#e0736b";
    ctx.fillRect(food.x * CELL_PX, food.y * CELL_PX, CELL_PX - 1, CELL_PX - 1);
    snake.forEach((segment, index) => {
      ctx.fillStyle = index === 0 ? "#e0a72e" : "#7cd992";
      ctx.fillRect(segment.x * CELL_PX, segment.y * CELL_PX, CELL_PX - 1, CELL_PX - 1);
    });
  }

  function finish(won) {
    gameOver = true;
    clearInterval(timer);
    document.removeEventListener("keydown", onKeyDown);
    canvas.removeEventListener("pointerdown", onPointerDown);
    canvas.removeEventListener("pointerup", onPointerUp);
    canvas.removeEventListener("pointercancel", onPointerCancel);
    onFinish(won);
  }

  function tick() {
    if (gameOver) return;
    direction = pendingDirection;
    const head = { x: snake[0].x + direction.x, y: snake[0].y + direction.y };

    if (head.x < 0 || head.x >= GRID_SIZE || head.y < 0 || head.y >= GRID_SIZE) {
      draw();
      if (setStatus) setStatus("You hit a wall.");
      finish(false);
      return;
    }
    if (snake.some((segment) => segment.x === head.x && segment.y === head.y)) {
      draw();
      if (setStatus) setStatus("You ran into yourself.");
      finish(false);
      return;
    }

    snake.unshift(head);
    if (head.x === food.x && head.y === food.y) {
      score += 1;
      scoreEl.textContent = String(score);
      if (score >= WIN_SCORE) {
        draw();
        finish(true);
        return;
      }
      food = placeFood();
    } else {
      snake.pop();
    }
    draw();
  }

  document.addEventListener("keydown", onKeyDown);
  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointerup", onPointerUp);
  canvas.addEventListener("pointercancel", onPointerCancel);
  draw();
}
