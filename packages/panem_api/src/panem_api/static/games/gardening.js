// A weed-pulling minigame for the Hobbies tab's Gardening session --
// a grid of plant sprites, some of them weeds, mixed in among decoy
// plants the player must leave alone. Plain DOM buttons rather than a
// canvas sketch (unlike lockpick.js/fishing.js's physics-driven boards)
// since this is a find-and-click game, not a continuous-motion one --
// a grid of real clickable elements is simpler and more accessible than
// hit-testing canvas coordinates for the same effect.
export const label = "Gardening";
export function instructions() {
  return (
    "Weeds are hiding among the good plants -- click every weed (\u{1F33F}) " +
    "before time runs out. Clicking a good plant by mistake costs you one " +
    "of your 3 strikes; run out of strikes or time and the weeds win."
  );
}

const WEED_ICON = "\u{1F33F}"; // herb/sprout -- the weed
const PLANT_ICONS = ["\u{1F337}", "\u{1F33B}", "\u{1F345}", "\u{1F955}", "\u{1F33A}"]; // decoys
const GRID_COLS = 6;
const GRID_ROWS = 4;
const MAX_STRIKES = 3;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function mount(boardEl, { onFinish, setStatus, difficulty = 0.5 }) {
  const clamped = clamp(difficulty, 0, 0.95);
  const totalCells = GRID_COLS * GRID_ROWS;
  const weedCount = Math.round(5 + clamped * 3); // 5 (easy) to ~8 (hard) weeds among 24 cells
  const timeLimitS = 20 - clamped * 8; // 20s (easy) down to 12s (hard)

  const cellKinds = Array.from({ length: totalCells }, (_, i) => (i < weedCount ? "weed" : "plant"));
  // Fisher-Yates so the weeds land in random cells rather than always the
  // first `weedCount` slots.
  for (let i = cellKinds.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [cellKinds[i], cellKinds[j]] = [cellKinds[j], cellKinds[i]];
  }

  let weedsLeft = weedCount;
  let strikes = 0;
  let done = false;
  let started = false;
  let timeLeft = timeLimitS;
  let timerId = null;

  boardEl.className = "board-gardening";
  boardEl.innerHTML = `
    <p class="garden-meta">
      <span id="garden-weeds">Weeds left: ${weedsLeft}</span>
      <span id="garden-strikes">Strikes: 0/${MAX_STRIKES}</span>
      <span id="garden-timer">${Math.ceil(timeLimitS)}s</span>
    </p>
    <div class="garden-grid"></div>
  `;
  const gridEl = boardEl.querySelector(".garden-grid");
  const weedsEl = boardEl.querySelector("#garden-weeds");
  const strikesEl = boardEl.querySelector("#garden-strikes");
  const timerEl = boardEl.querySelector("#garden-timer");

  const cells = cellKinds.map((kind) => {
    const icon =
      kind === "weed" ? WEED_ICON : PLANT_ICONS[Math.floor(Math.random() * PLANT_ICONS.length)];
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = "garden-cell";
    cell.textContent = icon;
    cell.dataset.kind = kind;
    cell.addEventListener("click", () => onCellClick(cell, kind));
    gridEl.appendChild(cell);
    return cell;
  });

  function finish(won) {
    if (done) return;
    done = true;
    if (timerId) clearInterval(timerId);
    for (const cell of cells) cell.disabled = true;
    onFinish(won);
  }

  function beginIfNeeded() {
    if (started || done) return;
    started = true;
    if (setStatus) setStatus("");
    timerId = setInterval(() => {
      timeLeft -= 1;
      timerEl.textContent = `${Math.max(0, Math.ceil(timeLeft))}s`;
      if (timeLeft <= 0) {
        if (setStatus) setStatus("Time's up -- the weeds took over.");
        finish(false);
      }
    }, 1000);
  }

  function onCellClick(cell, kind) {
    if (done || cell.disabled) return;
    beginIfNeeded();
    cell.disabled = true;
    if (kind === "weed") {
      cell.classList.add("pulled");
      weedsLeft -= 1;
      weedsEl.textContent = `Weeds left: ${weedsLeft}`;
      if (weedsLeft <= 0) {
        if (setStatus) setStatus("Every weed pulled -- the garden's clear.");
        finish(true);
      }
    } else {
      cell.classList.add("wrong");
      strikes += 1;
      strikesEl.textContent = `Strikes: ${strikes}/${MAX_STRIKES}`;
      if (strikes >= MAX_STRIKES) {
        if (setStatus) setStatus("Too many good plants pulled up by mistake.");
        finish(false);
      }
    }
  }

  if (setStatus) setStatus("Click every weed before the clock (or your strikes) run out.");
}
