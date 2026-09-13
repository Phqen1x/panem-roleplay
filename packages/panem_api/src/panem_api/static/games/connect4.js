// Connect 4 against a robot foreman. Player is red, robot is yellow.
// The robot takes an immediate win if one is available, otherwise blocks
// the player's immediate win, otherwise plays a center-weighted random
// column -- simple, but not a pushover.

const COLS = 7;
const ROWS = 6;
const EMPTY = 0;
const PLAYER = 1;
const ROBOT = 2;

export const label = "Connect 4";
export const instructions = "Connect four in a row before the robot foreman does.";

function emptyBoard() {
  return Array.from({ length: ROWS }, () => Array(COLS).fill(EMPTY));
}

function lowestOpenRow(board, col) {
  for (let row = ROWS - 1; row >= 0; row--) {
    if (board[row][col] === EMPTY) return row;
  }
  return -1;
}

function isWinningMove(board, row, col, piece) {
  const directions = [
    [0, 1],
    [1, 0],
    [1, 1],
    [1, -1],
  ];
  for (const [dr, dc] of directions) {
    let count = 1;
    for (const sign of [1, -1]) {
      let r = row + dr * sign;
      let c = col + dc * sign;
      while (r >= 0 && r < ROWS && c >= 0 && c < COLS && board[r][c] === piece) {
        count += 1;
        r += dr * sign;
        c += dc * sign;
      }
    }
    if (count >= 4) return true;
  }
  return false;
}

function validColumns(board) {
  const out = [];
  for (let col = 0; col < COLS; col++) {
    if (lowestOpenRow(board, col) !== -1) out.push(col);
  }
  return out;
}

function chooseRobotColumn(board) {
  const columns = validColumns(board);
  for (const col of columns) {
    const row = lowestOpenRow(board, col);
    board[row][col] = ROBOT;
    const wins = isWinningMove(board, row, col, ROBOT);
    board[row][col] = EMPTY;
    if (wins) return col;
  }
  for (const col of columns) {
    const row = lowestOpenRow(board, col);
    board[row][col] = PLAYER;
    const blocks = isWinningMove(board, row, col, PLAYER);
    board[row][col] = EMPTY;
    if (blocks) return col;
  }
  const weighted = columns.flatMap((col) => {
    const distanceFromCenter = Math.abs(col - Math.floor(COLS / 2));
    const weight = Math.max(1, 4 - distanceFromCenter);
    return Array(weight).fill(col);
  });
  return weighted[Math.floor(Math.random() * weighted.length)];
}

export function mount(boardEl, { onFinish, setStatus }) {
  const board = emptyBoard();
  let gameOver = false;

  function render() {
    boardEl.innerHTML = "";
    for (let row = 0; row < ROWS; row++) {
      for (let col = 0; col < COLS; col++) {
        const cell = document.createElement("button");
        cell.type = "button";
        cell.className = "c4-cell";
        const piece = board[row][col];
        if (piece === PLAYER) cell.classList.add("c4-player");
        if (piece === ROBOT) cell.classList.add("c4-robot");
        cell.disabled = gameOver || piece !== EMPTY;
        cell.dataset.col = String(col);
        cell.addEventListener("click", () => onColumnClick(col));
        boardEl.appendChild(cell);
      }
    }
  }

  function onColumnClick(col) {
    if (gameOver) return;
    const row = lowestOpenRow(board, col);
    if (row === -1) return;
    board[row][col] = PLAYER;
    if (isWinningMove(board, row, col, PLAYER)) {
      gameOver = true;
      render();
      onFinish(true);
      return;
    }
    if (validColumns(board).length === 0) {
      gameOver = true;
      render();
      if (setStatus) setStatus("The board fills up before anyone connects four.");
      onFinish(false);
      return;
    }
    render();
    setTimeout(robotMove, 400);
  }

  function robotMove() {
    if (gameOver) return;
    const col = chooseRobotColumn(board);
    const row = lowestOpenRow(board, col);
    board[row][col] = ROBOT;
    if (isWinningMove(board, row, col, ROBOT)) {
      gameOver = true;
      render();
      onFinish(false);
      return;
    }
    render();
  }

  boardEl.className = "board-connect4";
  render();
}
