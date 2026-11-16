// A "find the odd one out" minigame for Rumors investigation -- a grid
// of identically-colored tiles except one, whose color differs by a
// margin that shrinks as `difficulty` rises (easy: obvious; hard:
// subtle). Plain DOM buttons, the same "a grid of real clickable
// elements" choice games/gardening.js already makes for a find-and-click
// game rather than a continuous-motion canvas one -- and, like every
// other minigame in this codebase, procedurally drawn rather than real
// photographs (there are no image assets anywhere in this project to
// "spot the difference" between).
export const label = "Spot the Odd One Out";
export function instructions() {
  return (
    "One tile's color is slightly different from all the rest -- click it " +
    "before time runs out. Clicking the wrong tile ends the round early."
  );
}

const GRID_COLS = 5;
const GRID_ROWS = 4;
const BASE_HUE = 210;
const BASE_SATURATION = 55;
const BASE_LIGHTNESS = 45;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function mount(boardEl, { onFinish, setStatus, difficulty = 0.5 }) {
  const clamped = clamp(difficulty, 0, 0.95);
  const totalCells = GRID_COLS * GRID_ROWS;
  const lightnessDelta = 22 - clamped * 15; // 22 (easy, obvious) down to ~7.8 (hard, subtle)
  const timeLimitS = 12 - clamped * 5; // 12s (easy) down to 7s (hard)
  const oddIndex = Math.floor(Math.random() * totalCells);
  // Randomize whether the odd tile is lighter or darker so "always look
  // for the brighter one" isn't a free shortcut.
  const sign = Math.random() < 0.5 ? 1 : -1;

  let done = false;
  let timeLeft = timeLimitS;
  let timerId = null;

  boardEl.className = "board-oddoneout";
  boardEl.innerHTML = `
    <p class="oddoneout-meta"><span id="oddoneout-timer">${Math.ceil(timeLimitS)}s</span></p>
    <div class="oddoneout-grid"></div>
  `;
  const gridEl = boardEl.querySelector(".oddoneout-grid");
  const timerEl = boardEl.querySelector("#oddoneout-timer");

  const cells = [];
  for (let i = 0; i < totalCells; i += 1) {
    const lightness =
      i === oddIndex ? clamp(BASE_LIGHTNESS + sign * lightnessDelta, 5, 95) : BASE_LIGHTNESS;
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = "oddoneout-cell";
    cell.style.backgroundColor = `hsl(${BASE_HUE}, ${BASE_SATURATION}%, ${lightness}%)`;
    cell.addEventListener("click", () => onCellClick(i === oddIndex));
    gridEl.appendChild(cell);
    cells.push(cell);
  }

  function finish(won) {
    if (done) return;
    done = true;
    if (timerId) clearInterval(timerId);
    for (const cell of cells) cell.disabled = true;
    onFinish(won);
  }

  timerId = setInterval(() => {
    timeLeft -= 1;
    timerEl.textContent = `${Math.max(0, Math.ceil(timeLeft))}s`;
    if (timeLeft <= 0) {
      if (setStatus) setStatus("Time's up.");
      finish(false);
    }
  }, 1000);

  function onCellClick(isOdd) {
    if (done) return;
    if (isOdd) {
      if (setStatus) setStatus("Found it.");
      finish(true);
    } else {
      if (setStatus) setStatus("Not quite -- that one matched the rest.");
      finish(false);
    }
  }

  if (setStatus) setStatus("Find the one tile that doesn't match.");
}
