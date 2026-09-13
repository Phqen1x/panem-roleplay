// `/work`'s minigame (Plan §9-adjacent): a small, classic Minesweeper
// board. No Discord SDK handshake here (unlike app.js) -- this page
// doesn't read any Discord-scoped data, only `shift_id` from its own
// query string, so it has nothing to authenticate for. Winning/losing
// posts to panem_api's work-result endpoint, which pays the shift the
// same way panem_bot's classic /work option-select flow does, just with
// a win/lose wage multiplier instead of a chosen option's
// (`panem_shared.shifts.resolve_shift_game`).

const GRID_SIZE = 8;
const MINE_COUNT = 10;

const statusEl = document.getElementById("status");
const boardEl = document.getElementById("board");
const resultEl = document.getElementById("result");

const shiftId = new URLSearchParams(location.search).get("shift_id");

let cells = []; // flat array of {mine, count, revealed, flagged}
let firstClick = true;
let gameOver = false;

function setStatus(text) {
  statusEl.textContent = text;
}

async function fetchJson(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || `${path} -> ${response.status}`);
  }
  return body;
}

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

async function finish(won) {
  gameOver = true;
  render();
  setStatus(won ? "Board cleared!" : "You hit a mine.");
  try {
    const body = await fetchJson(`/activity/work/${shiftId}/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ won }),
    });
    resultEl.hidden = false;
    resultEl.className = won ? "win" : "lose";
    resultEl.textContent = `${body.character_name} earns ${body.wage} money this shift.`;
  } catch (err) {
    resultEl.hidden = false;
    resultEl.className = "lose";
    resultEl.textContent = `Couldn't report the result: ${err.message}`;
  }
}

function onCellClick(index) {
  if (gameOver) return;
  if (firstClick) {
    placeMines(index);
    firstClick = false;
  }
  const cell = cells[index];
  if (cell.mine) {
    cells.forEach((c) => {
      if (c.mine) c.revealed = true;
    });
    render();
    finish(false);
    return;
  }
  reveal(index);
  render();
  if (isWon()) finish(true);
}

function onCellFlag(index) {
  if (gameOver || firstClick) return;
  const cell = cells[index];
  if (!cell.revealed) cell.flagged = !cell.flagged;
  render();
}

async function main() {
  if (!shiftId) {
    setStatus("No shift given -- use the link from /work in Discord.");
    return;
  }
  let info;
  try {
    info = await fetchJson(`/activity/work/${shiftId}`);
  } catch (err) {
    setStatus(`Can't load this shift: ${err.message}`);
    return;
  }
  if (info.already_resolved) {
    setStatus(`${info.character_name}'s ${info.job_title} shift is already done.`);
    return;
  }
  setStatus(`${info.character_name} works as ${info.job_title}. Clear the board (right-click to flag) --`);
  cells = Array.from({ length: GRID_SIZE * GRID_SIZE }, () => ({
    mine: false,
    count: 0,
    revealed: false,
    flagged: false,
  }));
  boardEl.hidden = false;
  render();
}

main();
