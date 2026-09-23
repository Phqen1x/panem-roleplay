// The dashboard's landing page (Milestone 11 of the RP Modes feature): a
// character's current RP mode, meters, crime toggle, active afflictions,
// and a mode-switch control. Reads `/activity/dashboard/mode/{id}/status`
// (Milestone 6), the same endpoint `/character mode`'s bot-side confirm
// view is built against, so the two surfaces never drift apart.
import { fetchJson, el, dropdown } from "./_shared.js?v=3";

const MODE_LABELS = { story: "Story", life: "Life", simulation: "Simulation" };

const MODE_DESCRIPTIONS = {
  story:
    "Freeform RP only -- no economy, crime, housing, work, or NPC interaction, and no travel cost or delay.",
  life: "The full economy/crime/market/work/travel loop, minus housing and the needs system.",
  simulation:
    "The complete experience: economy, crime, housing, work, and the full needs system (hunger, thirst, sanity, fatigue).",
};

// Shown in the mode-switch confirmation panel so the consequences of a
// switch are spelled out in plain language before the player commits to
// it -- see the plan's "abundantly clear what the repercussions of
// switching are" requirement.
const MODE_BULLETS = {
  story: [
    "Freeform RP only -- no economy, crime, housing, work, or NPC interaction.",
    "No travel cost or delay, and no need to be at a location to RP there.",
    "Cannot be stolen from, burgled, or otherwise targeted by crime, ever.",
  ],
  life: [
    "Full economy, crime, market, and work access.",
    "No housing, and the needs system (hunger/thirst/sanity/fatigue) stops applying.",
    "Cross-district travel always takes exactly 1 tick.",
    "Crime can be toggled on/off for yourself, once per real day.",
  ],
  simulation: [
    "The full experience: economy, crime, housing, work, and travel.",
    "The complete needs system -- hunger, thirst, sanity, and fatigue all apply.",
    "Afflictions and death can trigger automatically at staff-defined stat thresholds.",
    "Crime can never be disabled for yourself.",
  ],
};

const METER_FIELDS = [
  { key: "health", label: "Health" },
  { key: "hunger", label: "Hunger" },
  { key: "thirst", label: "Thirst" },
  { key: "fatigue", label: "Fatigue" },
  { key: "sanity", label: "Sanity" },
];

function formatRemaining(isoTimestamp) {
  const remainingMs = new Date(isoTimestamp).getTime() - Date.now();
  if (remainingMs <= 0) return null;
  const totalMinutes = Math.ceil(remainingMs / 60000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

function meterBar(label, value) {
  const pct = Math.max(0, Math.min(100, value));
  return el(
    "div",
    { class: "meter-row" },
    el("span", { class: "meter-label", text: `${label}  ${Math.round(value)}` }),
    el("div", { class: "meter-track" }, el("div", { class: "meter-fill", style: `width: ${pct}%` }))
  );
}

function modePanel(status) {
  const label = MODE_LABELS[status.mode] || status.mode;
  const children = [
    el("h2", { text: "Mode" }),
    el("p", { class: "tab-status" }, `${label} -- ${MODE_DESCRIPTIONS[status.mode] || ""}`),
  ];
  if (status.dead) {
    children.push(
      el(
        "p",
        { class: "result-line lose" },
        status.death_cause ? `Deceased -- ${status.death_cause}` : "Deceased."
      )
    );
  }
  return el("div", { class: "panel" }, ...children);
}

function metersPanel(status) {
  if (status.mode !== "simulation") {
    return el(
      "div",
      { class: "panel" },
      el("h2", { text: "Meters" }),
      el(
        "p",
        { class: "tab-status" },
        `Meters aren't tracked in ${MODE_LABELS[status.mode] || status.mode} mode.`
      )
    );
  }
  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Meters" }),
    ...METER_FIELDS.map((field) => meterBar(field.label, status[field.key]))
  );
}

function afflictionsPanel(status) {
  const items = status.afflictions.length
    ? status.afflictions.map((affliction) =>
        el(
          "li",
          {},
          `${affliction.name}${affliction.is_permanent ? " (permanent)" : ""}` +
            (affliction.cause ? ` -- ${affliction.cause}` : "")
        )
      )
    : [el("li", { class: "tab-status" }, "No active afflictions.")];
  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Afflictions" }),
    el("ul", { class: "affliction-list" }, ...items)
  );
}

function crimeTogglePanel(ctx, status, resultLine, onChanged) {
  if (status.mode !== "life") return null;
  const remaining = status.next_crime_toggle_eligible_at
    ? formatRemaining(status.next_crime_toggle_eligible_at)
    : null;
  const nextEnabled = !status.crime_enabled;
  const toggleBtn = el(
    "button",
    { class: "btn", type: "button" },
    nextEnabled ? "Enable crime" : "Disable crime"
  );
  if (remaining) toggleBtn.disabled = true;
  toggleBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      await ctx.apiFetch(`/activity/dashboard/mode/${ctx.characterId()}/crime-toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), enabled: nextEnabled }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = nextEnabled ? "Crime enabled." : "Crime disabled.";
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });
  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Crime" }),
    el(
      "p",
      { class: "tab-status" },
      `Crime is currently ${status.crime_enabled ? "enabled" : "disabled"} for you.`
    ),
    remaining ? el("p", { class: "tab-status" }, `You can toggle again in ${remaining}.`) : null,
    toggleBtn
  );
}

function modeSwitchPanel(ctx, status, resultLine, onChanged) {
  const remaining = status.next_mode_switch_eligible_at
    ? formatRemaining(status.next_mode_switch_eligible_at)
    : null;

  if (remaining) {
    return el(
      "div",
      { class: "panel" },
      el("h2", { text: "Change Mode" }),
      el("p", { class: "tab-status" }, `You can switch modes again in ${remaining}.`)
    );
  }

  const confirmHost = el("div", {});
  confirmHost.hidden = true;
  const openBtn = el("button", { class: "btn", type: "button" }, "Change Mode");
  openBtn.addEventListener("click", () => {
    confirmHost.hidden = !confirmHost.hidden;
  });

  const otherModes = Object.keys(MODE_LABELS)
    .filter((mode) => mode !== status.mode)
    .map((mode) => ({ value: mode, label: MODE_LABELS[mode] }));
  const modeSelect = dropdown(otherModes);
  const bulletsHost = el("ul", { class: "mode-switch-bullets" });

  function renderBullets() {
    bulletsHost.innerHTML = "";
    for (const bullet of MODE_BULLETS[modeSelect.value] || []) {
      bulletsHost.append(el("li", {}, bullet));
    }
  }
  modeSelect.addEventListener("change", renderBullets);
  renderBullets();

  const confirmBtn = el("button", { class: "btn", type: "button" }, "Confirm switch");
  const cancelBtn = el("button", { class: "btn secondary", type: "button" }, "Cancel");
  cancelBtn.addEventListener("click", () => {
    confirmHost.hidden = true;
  });
  confirmBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      await ctx.apiFetch(`/activity/dashboard/mode/${ctx.characterId()}/switch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), new_mode: modeSelect.value }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = `Switched to ${MODE_LABELS[modeSelect.value]}.`;
      confirmHost.hidden = true;
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  confirmHost.append(
    el("p", { class: "tab-status" }, "What changes:"),
    bulletsHost,
    el("div", { class: "field-row" }, el("label", { text: "New mode" }), modeSelect),
    el("div", { class: "field-row" }, confirmBtn, cancelBtn)
  );

  return el("div", { class: "panel" }, el("h2", { text: "Change Mode" }), openBtn, confirmHost);
}

export function mount(root, ctx) {
  const statusLine = el("p", { class: "tab-status" });
  const resultLine = el("p", { class: "result-line" });
  const host = el("div", {});
  root.append(statusLine, host, resultLine);

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    host.innerHTML = "";
    if (!characterId || !discordId) {
      statusLine.textContent = "Pick a character above first.";
      return;
    }
    try {
      const status = await ctx.apiFetch(
        `/activity/dashboard/mode/${characterId}/status?discord_id=${encodeURIComponent(discordId)}`
      );
      statusLine.textContent = "";
      const panels = [
        modePanel(status),
        metersPanel(status),
        crimeTogglePanel(ctx, status, resultLine, refresh),
        afflictionsPanel(status),
        modeSwitchPanel(ctx, status, resultLine, refresh),
      ].filter(Boolean);
      host.append(...panels);
    } catch (err) {
      statusLine.textContent = `Could not load status: ${err.message}`;
    }
  }

  refresh();

  return {};
}
