// Pick your poison -- three identical bottles, one of them poisoned.
// 2/3 odds, no skill component (same trust model as every other game
// here: the client alone decides `won` and reports it).

export const label = "Pick Your Poison";
export const instructions = "Three bottles, one is poisoned. Pick one to drink.";

export function mount(boardEl, { onFinish }) {
  let done = false;
  const poisonedIndex = Math.floor(Math.random() * 3);

  boardEl.className = "board-poison";
  boardEl.innerHTML = "";
  for (let i = 0; i < 3; i++) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "bottle-btn";
    button.dataset.index = String(i);
    button.innerHTML = '<span class="bottle-icon">&#127866;</span>';
    boardEl.appendChild(button);
  }

  boardEl.querySelectorAll(".bottle-btn").forEach((button) => {
    button.addEventListener("click", () => {
      if (done) return;
      done = true;
      const index = Number(button.dataset.index);
      const won = index !== poisonedIndex;
      boardEl.querySelectorAll(".bottle-btn").forEach((b, i) => {
        b.disabled = true;
        if (i === poisonedIndex) b.classList.add("poisoned");
        if (i === index) b.classList.add("chosen");
      });
      button.classList.add(won ? "win" : "lose");
      setTimeout(() => onFinish(won), 500);
    });
  });
}
