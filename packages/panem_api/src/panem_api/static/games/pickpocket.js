// Pickpocket timing minigame for `/steal` -- strike (click, or press
// space) when the moving needle sits over the mark's pocket to lift it
// clean. Adapted from a canvas sketch supplied for this feature,
// restyled to this project's shared dark theme, difficulty-scaled
// instead of a fixed-size zone, and made single-shot (one strike
// settles the attempt, no "try again" reset loop) to match every other
// Activity minigame here reporting exactly one result per attempt.

export const label = "Pickpocket";
export function instructions() {
  return "Strike (click, or press space) when the needle sits over the pocket.";
}

export function mount(boardEl, { onFinish, setStatus, difficulty = 0.5 }) {
  const width = 360;
  const height = 50;
  let done = false;
  let started = false; // the needle stays frozen until the player's first input
  boardEl.className = "board-pickpocket";
  boardEl.innerHTML = `
    <canvas id="pickpocket-canvas" width="${width}" height="${height}"></canvas>
    <button type="button" id="pickpocket-strike">STRIKE</button>
  `;
  const canvas = boardEl.querySelector("#pickpocket-canvas");
  const ctx = canvas.getContext("2d");
  const strikeBtn = boardEl.querySelector("#pickpocket-strike");

  // A player mark reads a harder `difficulty` than an NPC one
  // (`panem_shared.stealing.steal_difficulty`), which shows up here as
  // a narrower pocket zone and a faster needle.
  const clamped = Math.min(0.95, Math.max(0, difficulty));
  const targetWidth = Math.max(20, Math.round(65 - clamped * 45));
  const targetX = Math.floor(Math.random() * (width - targetWidth - 30)) + 15;
  const speed = 3 + clamped * 3;

  let needleX = 0;
  let direction = 1;
  let rafId = null;

  function render() {
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#333";
    ctx.fillRect(0, height / 2 - 15, width, 30);
    ctx.fillStyle = "#2ed573";
    ctx.fillRect(targetX, height / 2 - 15, targetWidth, 30);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(needleX, height / 2 - 25, 4, 50);

    if (!started) {
      ctx.fillStyle = "rgba(0, 0, 0, 0.6)";
      ctx.fillRect(0, 0, width, height);
      ctx.fillStyle = "#ffffff";
      ctx.font = "bold 12px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("Click or press Space to begin", width / 2, height / 2 + 4);
    }
  }

  function tick() {
    if (done) return;
    needleX += speed * direction;
    if (needleX >= width - 4 || needleX <= 0) direction *= -1;
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

  // The needle keeps moving the instant `tick` starts, so starting it
  // right at mount would run the clock while the player is still reading
  // the instructions. Gated behind the first input instead: that input
  // only wakes the needle up (`beginIfNeeded`) rather than also acting as
  // an (almost certainly wasted, since the needle hasn't moved from its
  // resting position yet) strike -- the player's *next* input is their
  // real first strike attempt.
  function beginIfNeeded() {
    if (started || done) return;
    started = true;
    if (setStatus) setStatus("");
    tick();
  }

  function onStrikeClick() {
    if (!started) {
      beginIfNeeded();
      return;
    }
    strike();
  }

  function onKey(event) {
    if (event.code === "Space") {
      event.preventDefault();
      if (!started) {
        beginIfNeeded();
        return;
      }
      strike();
    }
  }

  strikeBtn.addEventListener("click", onStrikeClick);
  window.addEventListener("keydown", onKey);

  if (setStatus) setStatus("Ready when you are -- click or press Space to start.");
  render();
}
