// An original lockpicking minigame -- a needle sweeps back and forth
// across a dial, and the player strikes (click, or press space) when
// it's inside the pin's lit "give" zone. Shared by `/lockpick` (escaping
// jail) and `/burgle` (breaking into a house): picking a lock is picking
// a lock either way, just against a cell door or a house door. Not a
// port of any third-party project -- see this session's own notes on
// why (a Unity/C# GPLv3 codebase can't be embedded in a browser iframe,
// and porting its code in would pull GPLv3 into this repo).

export const label = "Lockpick";
export function instructions() {
  return "Strike (click, or press space) when the needle sits in the lit zone.";
}

export function mount(boardEl, { onFinish, difficulty = 0.5 }) {
  const width = 360;
  const height = 50;
  let done = false;
  boardEl.className = "board-lockpick";
  boardEl.innerHTML = `
    <canvas id="lockpick-canvas" width="${width}" height="${height}"></canvas>
    <button type="button" id="lockpick-strike">STRIKE</button>
  `;
  const canvas = boardEl.querySelector("#lockpick-canvas");
  const ctx = canvas.getContext("2d");
  const strikeBtn = boardEl.querySelector("#lockpick-strike");

  // Harder difficulty (a longer sentence, or a house instead of a
  // person) shows up as a narrower zone and a faster needle -- the same
  // difficulty float `panem_shared.jail.lockpick_difficulty`/`stealing.
  // burgle_difficulty` already compute server-side for the RNG-fallback
  // path, just read as a minigame knob instead of a success probability.
  const clamped = Math.min(0.95, Math.max(0, difficulty));
  const targetWidth = Math.max(22, Math.round(70 - clamped * 50));
  const targetX = Math.floor(Math.random() * (width - targetWidth - 30)) + 15;
  const speed = 2.2 + clamped * 2.2;

  let needleX = 0;
  let direction = 1;
  let rafId = null;

  function render() {
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#333";
    ctx.fillRect(0, height / 2 - 8, width, 16);
    ctx.fillStyle = "#e0a72e";
    ctx.fillRect(targetX, height / 2 - 8, targetWidth, 16);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(needleX, height / 2 - 18, 3, 36);
  }

  function tick() {
    if (done) return;
    needleX += speed * direction;
    if (needleX >= width - 3 || needleX <= 0) direction *= -1;
    render();
    rafId = requestAnimationFrame(tick);
  }

  function strike() {
    if (done) return;
    done = true;
    cancelAnimationFrame(rafId);
    strikeBtn.disabled = true;
    window.removeEventListener("keydown", onKey);
    const won = needleX >= targetX && needleX <= targetX + targetWidth;
    onFinish(won);
  }

  function onKey(event) {
    if (event.code === "Space") {
      event.preventDefault();
      strike();
    }
  }

  strikeBtn.addEventListener("click", strike);
  window.addEventListener("keydown", onKey);

  render();
  tick();
}
