// Baking grain/dairy in the oven -- same Papa's-Burgeria-style multi-stage
// shape as ./cook.js (Prep, then the scored doneness-gauge stage, then
// Plate), just oven flavor instead of stove. Kept as its own file rather
// than sharing code with cook.js -- every other minigame module in this
// directory is self-contained too, and the two differ enough in flavor
// (ingredients, timing, zone labels) that a shared abstraction would cost
// more than it saves.

export const label = "Oven";
export function instructions() {
  return "Prep, then watch the gauge -- pull it from the oven right as it hits Perfect.";
}

const SWEEP_MS = 5000;
// Perfect is half the width it used to be -- see cook.js's own ZONES
// comment for the reasoning (same 40-60 -> 45-55 shrink, same reason).
const ZONES = [
  { key: "raw", label: "Doughy", from: 0, to: 20 },
  { key: "undercooked", label: "Underbaked", from: 20, to: 45 },
  { key: "perfect", label: "Perfect", from: 45, to: 55 },
  { key: "overcooked", label: "Overbaked", from: 55, to: 80 },
  { key: "burnt", label: "Burnt", from: 80, to: 100 },
];
const INGREDIENTS = ["\u{1F33E}", "\u{1F95B}", "\u{1F525}"]; // grain, dairy, flame

function zoneAt(pct) {
  return ZONES.find((z) => pct >= z.from && pct < z.to) || ZONES[ZONES.length - 1];
}

export function mount(boardEl, { onFinish, setStatus }) {
  boardEl.className = "board-cookbake";
  mountPrep();

  function mountPrep() {
    boardEl.innerHTML = `
      <p class="stage-label">Prep: mix it, in order.</p>
      <div class="prep-row"></div>
    `;
    const row = boardEl.querySelector(".prep-row");
    let next = 0;
    INGREDIENTS.forEach((icon, i) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "prep-btn";
      btn.textContent = icon;
      btn.dataset.index = String(i);
      btn.addEventListener("click", () => {
        if (i !== next) return;
        btn.disabled = true;
        btn.classList.add("done");
        next += 1;
        if (next === INGREDIENTS.length) {
          setTimeout(mountBake, 300);
        }
      });
      row.appendChild(btn);
    });
  }

  function mountBake() {
    if (setStatus) setStatus("In the oven now -- don't let it burn.");
    boardEl.innerHTML = `
      <p class="stage-label">Bake: pull it at the right moment.</p>
      <div class="gauge-wrap">
        <div class="gauge-track">
          ${ZONES.map((z) => `<div class="gauge-zone ${z.key}" style="width:${z.to - z.from}%"></div>`).join("")}
          <div class="gauge-needle" id="needle"></div>
        </div>
      </div>
      <p class="doneness-readout" id="readout">Doughy</p>
      <button type="button" class="take-off-btn" id="take-off">Pull it out</button>
    `;
    const needleEl = boardEl.querySelector("#needle");
    const readoutEl = boardEl.querySelector("#readout");
    const takeOffBtn = boardEl.querySelector("#take-off");

    const start = performance.now();
    let done = false;
    let rafId = null;

    function tick(now) {
      const elapsed = now - start;
      const pct = Math.min(100, (elapsed / SWEEP_MS) * 100);
      needleEl.style.left = `${pct}%`;
      const zone = zoneAt(pct);
      readoutEl.textContent = zone.label;
      readoutEl.className = `doneness-readout ${zone.key === "perfect" ? "bonus" : ""}`;
      if (pct >= 100) {
        finishBake(false, "burnt");
        return;
      }
      rafId = requestAnimationFrame(tick);
    }
    rafId = requestAnimationFrame(tick);

    function finishBake(bonus, zoneKey) {
      if (done) return;
      done = true;
      if (rafId) cancelAnimationFrame(rafId);
      takeOffBtn.disabled = true;
      setTimeout(() => mountPlate(bonus, zoneKey), 400);
    }

    takeOffBtn.addEventListener("click", () => {
      if (done) return;
      const now = performance.now();
      const pct = Math.min(100, ((now - start) / SWEEP_MS) * 100);
      const zone = zoneAt(pct);
      finishBake(zone.key === "perfect", zone.key);
    });
  }

  function mountPlate(bonus, zoneKey) {
    if (setStatus) setStatus(bonus ? "Baked just right." : "It's edible, at least.");
    boardEl.innerHTML = `
      <p class="stage-label">Plate: ${ZONES.find((z) => z.key === zoneKey)?.label || "Done"}.</p>
      <button type="button" class="plate-btn">Serve it</button>
    `;
    boardEl.querySelector(".plate-btn").addEventListener("click", () => onFinish(bonus), {
      once: true,
    });
  }
}
