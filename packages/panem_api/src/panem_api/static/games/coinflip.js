// Coin flip -- the simplest of the /work minigames. Straight 50/50, no
// skill component, matching the "coin flip game" the district's foremen
// apparently also play on slow shifts.

export const label = "Coin Flip";
export function instructions() {
  return "Call it -- heads keeps the shift, tails ends it.";
}

export function mount(boardEl, { onFinish }) {
  let done = false;
  boardEl.className = "board-coinflip";
  boardEl.innerHTML = `
    <div class="coin" id="coin"><span id="coin-face">?</span></div>
    <div class="coinflip-buttons">
      <button type="button" class="choice-btn" data-call="heads">Heads</button>
      <button type="button" class="choice-btn" data-call="tails">Tails</button>
    </div>
  `;
  const coinEl = boardEl.querySelector("#coin");
  const faceEl = boardEl.querySelector("#coin-face");

  boardEl.querySelectorAll(".choice-btn").forEach((button) => {
    button.addEventListener("click", () => {
      if (done) return;
      done = true;
      boardEl.querySelectorAll(".choice-btn").forEach((b) => (b.disabled = true));
      const call = button.dataset.call;
      const landed = Math.random() < 0.5 ? "heads" : "tails";
      coinEl.classList.add("flipping");
      setTimeout(() => {
        coinEl.classList.remove("flipping");
        faceEl.textContent = landed === "heads" ? "H" : "T";
        coinEl.classList.add(landed === call ? "win" : "lose");
        onFinish(landed === call);
      }, 700);
    });
  });
}
