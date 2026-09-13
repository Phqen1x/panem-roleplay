// Classic Minesweeper -- one of several minigames `work.js` picks at
// random for a shift. Every game module in this directory exports a
// `mount(boardEl, { onFinish })` that renders itself into `boardEl` and
// calls `onFinish(won)` exactly once when the shift's outcome is decided.
//
// Grid grows by 2 squares per job level (`levelIndex`, 0 = Apprentice..
// 4 = Expert) -- mine count scales with it to hold mine density roughly
// constant, so a higher-level board isn't just bigger, it's actually
// harder to read at a glance too.

const BASE_GRID_SIZE = 8;
const GRID_GROWTH_PER_LEVEL = 2;
const BASE_MINE_DENSITY = 10 / (8 * 8);

function gridSizeForLevel(levelIndex) {
  return BASE_GRID_SIZE + GRID_GROWTH_PER_LEVEL * Math.max(0, levelIndex);
}

function mineCountForLevel(levelIndex) {
  const size = gridSizeForLevel(levelIndex);
  return Math.round(size * size * BASE_MINE_DENSITY);
}

export const label = "Minesweeper";
export function instructions(levelIndex = 0) {
  const size = gridSizeForLevel(levelIndex);
  return `Clear the ${size}x${size} board (right-click to flag) --`;
}

export function mount(boardEl, { onFinish, levelIndex = 0 }) {
  const GRID_SIZE = gridSizeForLevel(levelIndex);
  const MINE_COUNT = mineCountForLevel(levelIndex);
  let cells = [];
  let firstClick = true;
  let gameOver = false;

  function neighbors(index) {
    const row = Math.floor(index / GRID_SIZE);
    const col = index % GRID_SIZE;
    const out = [];
    for (let dr = -1; dr <= 1; dr++) {
      for (let dc = -1; dc <= 1; dc++) {
        if (dr === 0 && dc === 0) continue;
        const r = row + dr;
        const c = col + dc;
        if (r >= 0 && r < GRID_SIZE && c >= 0 && c < GRID_SIZE) {
          out.push(r * GRID_SIZE + c);
        }
      }
    }
    return out;
  }

  function placeMines(safeIndex) {
    const total = GRID_SIZE * GRID_SIZE;
    cells = Array.from({ length: total }, () => ({
      mine: false,
      count: 0,
      revealed: false,
      flagged: false,
    }));
    const safeZone = new Set([safeIndex, ...neighbors(safeIndex)]);
    const candidates = [...Array(total).keys()].filter((i) => !safeZone.has(i));
    for (let i = candidates.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [candidates[i], candidates[j]] = [candidates[j], candidates[i]];
    }
    for (const index of candidates.slice(0, MINE_COUNT)) {
      cells[index].mine = true;
    }
    for (let index = 0; index < total; index++) {
      if (!cells[index].mine) {
        cells[index].count = neighbors(index).filter((n) => cells[n].mine).length;
      }
    }
  }

  function reveal(index) {
    const cell = cells[index];
    if (cell.revealed || cell.flagged) return;
    cell.revealed = true;
    if (cell.count === 0 && !cell.mine) {
      for (const n of neighbors(index)) reveal(n);
    }
  }

  function isWon() {
    return cells.every((cell) => cell.mine || cell.revealed);
  }

  function render() {
    boardEl.style.gridTemplateColumns = `repeat(${GRID_SIZE}, 34px)`;
    boardEl.innerHTML = "";
    cells.forEach((cell, index) => {
      const button = document.createElement("button");
      button.className = "cell";
      button.type = "button";
      if (cell.revealed) {
        button.classList.add("revealed");
        if (cell.mine) {
          button.classList.add("mine");
          button.textContent = "*";
        } else if (cell.count > 0) {
          button.dataset.count = String(cell.count);
          button.textContent = String(cell.count);
        }
      } else if (cell.flagged) {
        button.classList.add("flagged");
        button.textContent = "!";
      }
      button.disabled = gameOver;
      button.addEventListener("click", () => onCellClick(index));
      button.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        onCellFlag(index);
      });
      boardEl.appendChild(button);
    });
  }

  function onCellClick(index) {
    if (gameOver) return;
    if (firstClick) {
      placeMines(index);
      firstClick = false;
    }
    const cell = cells[index];
    if (cell.mine) {
      gameOver = true;
      cells.forEach((c) => {
        if (c.mine) c.revealed = true;
      });
      render();
      onFinish(false);
      return;
    }
    reveal(index);
    render();
    if (isWon()) {
      gameOver = true;
      render();
      onFinish(true);
    }
  }

  function onCellFlag(index) {
    if (gameOver || firstClick) return;
    const cell = cells[index];
    if (!cell.revealed) cell.flagged = !cell.flagged;
    render();
  }

  cells = Array.from({ length: GRID_SIZE * GRID_SIZE }, () => ({
    mine: false,
    count: 0,
    revealed: false,
    flagged: false,
  }));
  boardEl.className = "board-minesweeper";
  render();
}
