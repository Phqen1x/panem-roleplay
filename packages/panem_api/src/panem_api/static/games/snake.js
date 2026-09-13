// Snake. Arrow keys or WASD steer; eating `WIN_SCORE` food clears the
// shift, crashing into a wall or yourself before then loses it.

const GRID_SIZE = 20;
const CELL_PX = 20;
const TICK_MS = 130;
const WIN_SCORE = 8;

export const label = "Snake";
export const instructions = `Eat ${WIN_SCORE} to clear the shift -- arrow keys or WASD, don't hit a wall or yourself.`;

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

export function mount(boardEl, { onFinish, setStatus }) {
  boardEl.className = "board-snake";
  boardEl.innerHTML = `<canvas id="snake-canvas" width="${GRID_SIZE * CELL_PX}" height="${
    GRID_SIZE * CELL_PX
  }"></canvas><p class="snake-score">Score: <span id="snake-score">0</span> / ${WIN_SCORE}</p>`;
  const canvas = boardEl.querySelector("#snake-canvas");
  const scoreEl = boardEl.querySelector("#snake-score");
  const ctx = canvas.getContext("2d");

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

  function placeFood() {
    while (true) {
      const candidate = {
        x: Math.floor(Math.random() * GRID_SIZE),
        y: Math.floor(Math.random() * GRID_SIZE),
      };
      if (!snake.some((s) => s.x === candidate.x && s.y === candidate.y)) return candidate;
    }
  }

  function onKeyDown(event) {
    const next = DIRECTIONS[event.key];
    if (!next) return;
    event.preventDefault();
    if (next.x === -direction.x && next.y === -direction.y) return;
    pendingDirection = next;
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
  draw();
  timer = setInterval(tick, TICK_MS);
}
