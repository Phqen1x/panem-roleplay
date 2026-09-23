// Cooking meat/seafood on the stove -- the Vitals tab's Papa's-Burgeria-
// style multi-stage minigame (Milestone 6). Three stages: a cosmetic Prep
// QTE, the real Cook stage (a doneness gauge sweeps through five zones --
// only landing "Take it off" inside the Perfect zone earns the bonus, too
// early or too late both miss it), and a cosmetic Plate stage. `onFinish
// (bonus)` reports only whether Perfect landed -- win/lose framing doesn't
// apply here, you always end up with food either way.

export const label = "Stove";
export function instructions() {
  return "Prep, then watch the gauge -- take it off the heat right as it hits Perfect.";
}

const SWEEP_MS = 4200;
const ZONES = [
  { key: "raw", label: "Raw", from: 0, to: 20 },
  { key: "undercooked", label: "Undercooked", from: 20, to: 40 },
  { key: "perfect", label: "Perfect", from: 40, to: 60 },
  { key: "overcooked", label: "Overcooked", from: 60, to: 80 },
  { key: "burnt", label: "Burnt", from: 80, to: 100 },
];
const INGREDIENTS = ["\u{1F9C2}", "\u{1F9C4}", "\u{1F525}"]; // salt, garlic, flame

function zoneAt(pct) {
  return ZONES.find((z) => pct >= z.from && pct < z.to) || ZONES[ZONES.length - 1];
}

export function mount(boardEl, { onFinish, setStatus }) {
  boardEl.className = "board-cookbake";
  mountPrep();

  function mountPrep() {
    boardEl.innerHTML = `
      <p class="stage-label">Prep: season it, in order.</p>
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
          setTimeout(mountCook, 300);
        }
      });
      row.appendChild(btn);
    });
  }

  function mountCook() {
    if (setStatus) setStatus("On the stove now -- don't let it burn.");
    boardEl.innerHTML = `
      <p class="stage-label">Cook: take it off at the right moment.</p>
      <div class="gauge-wrap">
        <div class="gauge-track">
          ${ZONES.map((z) => `<div class="gauge-zone ${z.key}" style="width:${z.to - z.from}%"></div>`).join("")}
          <div class="gauge-needle" id="needle"></div>
        </div>
      </div>
      <p class="doneness-readout" id="readout">Raw</p>
      <button type="button" class="take-off-btn" id="take-off">Take it off</button>
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
        finishCook(false, "burnt");
        return;
      }
      rafId = requestAnimationFrame(tick);
    }
    rafId = requestAnimationFrame(tick);

    function finishCook(bonus, zoneKey) {
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
      finishCook(zone.key === "perfect", zone.key);
    });
  }

  function mountPlate(bonus, zoneKey) {
    if (setStatus) setStatus(bonus ? "Cooked just right." : "It's edible, at least.");
    boardEl.innerHTML = `
      <p class="stage-label">Plate: ${ZONES.find((z) => z.key === zoneKey)?.label || "Done"}.</p>
      <button type="button" class="plate-btn">Serve it</button>
    `;
    boardEl.querySelector(".plate-btn").addEventListener("click", () => onFinish(bonus), {
      once: true,
    });
  }
}
