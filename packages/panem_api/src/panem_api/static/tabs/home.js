// The dashboard's landing page: a live weather/event hero if there's
// anything active worth leading with, a short bulletin preview, this
// character's own "jump back in"/"recent"/every-tab app shortcuts (the
// one place every tab is reachable without the desktop sidebar -- see
// `dashboard.css`'s `#shell-body` comment), and -- predating all of that
// (Milestone 11 of the RP Modes feature) -- their current RP mode,
// meters, crime toggle, active afflictions, and a mode-switch control.
// The mode/meters section reads `/activity/dashboard/mode/{id}/status`
// (Milestone 6), the same endpoint `/character mode`'s bot-side confirm
// view is built against, so the two surfaces never drift apart.
import { fetchJson, el, dropdown, setStatusText, renderTabIcon } from "./_shared.js?v=13";

const MODE_LABELS = { story: "Story", life: "Life", simulation: "Simulation" };
const SHIFT_PHASES = ["morning", "afternoon", "evening", "night"];

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
  if (status.pending_mode) {
    // Staged via `rp_modes.stage_mode_switch` -- awaiting the same staff
    // approval `/character mode` sends a fresh job-info switch to. The
    // character keeps playing in `status.mode` until it's resolved.
    const pendingLabel = MODE_LABELS[status.pending_mode] || status.pending_mode;
    return el(
      "div",
      { class: "panel" },
      el("h2", { text: "Change Mode" }),
      el(
        "p",
        { class: "tab-status" },
        `A switch to ${pendingLabel} mode is awaiting staff approval -- you stay in ` +
          `${MODE_LABELS[status.mode] || status.mode} mode until then.`
      )
    );
  }

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

  // Never been Life/Simulation before (`!status.has_job_info`) -- switching
  // into either needs a job on file first, same job-title/shift-phase/
  // illicit prompts character creation uses, and it needs staff sign-off
  // before it takes effect (`rp_modes.mode_switch_needs_job_info`). A
  // character that already has `job_title` on file keeps it and switches
  // straight through, same as switching to Story always does.
  const jobInput = el("input", { type: "text", maxlength: "80", placeholder: "Miner, Baker, ..." });
  const phaseSelect = dropdown(SHIFT_PHASES.map((phase) => ({ value: phase, label: phase })));
  const illicitInput = el("input", { type: "checkbox" });
  const jobInfoNote = el(
    "p",
    { class: "tab-status" },
    "This character has never had a job on file -- switching needs one, plus staff " +
      "approval, before it takes effect."
  );
  const jobRow = el("div", { class: "field-row" }, el("label", { text: "Job title" }), jobInput);
  const shiftRow = el("div", { class: "field-row" }, el("label", { text: "Shift" }), phaseSelect);
  const illicitRow = el(
    "div",
    { class: "field-row" },
    el("label", { text: "Illicit job?" }),
    illicitInput
  );

  function needsJobInfo() {
    return modeSelect.value !== "story" && !status.has_job_info;
  }

  function syncJobFieldsVisibility() {
    const needed = needsJobInfo();
    jobInfoNote.hidden = !needed;
    jobRow.hidden = !needed;
    shiftRow.hidden = !needed;
    illicitRow.hidden = !needed;
  }
  modeSelect.addEventListener("change", () => {
    renderBullets();
    syncJobFieldsVisibility();
  });
  renderBullets();
  syncJobFieldsVisibility();

  const confirmBtn = el("button", { class: "btn", type: "button" }, "Confirm switch");
  const cancelBtn = el("button", { class: "btn secondary", type: "button" }, "Cancel");
  cancelBtn.addEventListener("click", () => {
    confirmHost.hidden = true;
  });
  confirmBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    const needed = needsJobInfo();
    if (needed && !jobInput.value.trim()) {
      resultLine.className = "result-line lose";
      resultLine.textContent = "Job title is required for this switch.";
      return;
    }
    try {
      await ctx.apiFetch(`/activity/dashboard/mode/${ctx.characterId()}/switch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          new_mode: modeSelect.value,
          job_title: needed ? jobInput.value : null,
          shift_phase: needed ? phaseSelect.value : null,
          job_is_illicit: needed ? illicitInput.checked : false,
        }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = needed
        ? `Switch to ${MODE_LABELS[modeSelect.value]} sent to staff for approval.`
        : `Switched to ${MODE_LABELS[modeSelect.value]}.`;
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
    jobInfoNote,
    jobRow,
    shiftRow,
    illicitRow,
    el("div", { class: "field-row" }, confirmBtn, cancelBtn)
  );

  return el("div", { class: "panel" }, el("h2", { text: "Change Mode" }), openBtn, confirmHost);
}

function eventLabel(kind) {
  return kind
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

// The one deliberately "live" element on this page -- shown only when
// there's actually something to lead with (an active or predicted
// weather event); omitted entirely otherwise rather than a flat "all
// clear" banner manufacturing excitement that isn't there.
function heroPanel(ctx, weatherStatus) {
  const event_ = weatherStatus.active_event || weatherStatus.upcoming_event;
  if (!event_) return null;
  const isActive = Boolean(weatherStatus.active_event);
  return el(
    "div",
    { class: "panel hero-panel" },
    el("h2", { text: isActive ? "Happening Now" : "Predicted" }),
    el(
      "p",
      { class: "tab-status" },
      `${eventLabel(event_.kind)} -- category ${event_.severity}.` +
        (isActive
          ? ` Expected to pass around ${ctx.formatTick(event_.actual_end_tick)}. Take cover if you're outside.`
          : ` Predicted to start around ${ctx.formatTick(event_.predicted_start_tick)}.`)
    )
  );
}

function bulletinPreviewPanel(ctx, entries) {
  if (!entries.length) return null;
  const rows = entries
    .slice(0, 3)
    .map((entry) =>
      el(
        "div",
        { class: "field-row" },
        el("label", { text: entry.tag }),
        el("span", { class: "tab-status", text: entry.title })
      )
    );
  const viewBtn = el("button", { class: "btn secondary", type: "button" }, "View board");
  viewBtn.addEventListener("click", () => ctx.goToTab("bulletin"));
  return el("div", { class: "panel" }, el("h2", { text: "Bulletin Board" }), ...rows, viewBtn);
}

const APP_METADATA = {
  character: {
    title: "Character",
    subtitle: "Profile, inventory, and belongings",
    category: "daily",
    categoryName: "Daily Life",
    color: "#8a3243",
  },
  vitals: {
    title: "Vitals",
    subtitle: "Health, hunger, thirst, and needs",
    category: "daily",
    categoryName: "Daily Life",
    color: "#1e5338",
  },
  work: {
    title: "Work",
    subtitle: "Jobs, labor shifts, and daily earnings",
    category: "daily",
    categoryName: "Daily Life",
    color: "#a06f1d",
  },
  housing: {
    title: "Housing",
    subtitle: "Your home, residence, and interior",
    category: "daily",
    categoryName: "Daily Life",
    color: "#2c4c70",
  },
  pets: {
    title: "Pets",
    subtitle: "Your companions and pet care",
    category: "daily",
    categoryName: "Daily Life",
    color: "#945324",
  },
  hobbies: {
    title: "Hobbies",
    subtitle: "Crafting, skills, and recreation",
    category: "daily",
    categoryName: "Daily Life",
    color: "#366345",
  },
  map: {
    title: "Map",
    subtitle: "Interactive 3D district navigator",
    category: "explore",
    categoryName: "Explore",
    color: "#27548a",
  },
  travel: {
    title: "Travel",
    subtitle: "Transit, checkpoints, and journeys",
    category: "explore",
    categoryName: "Explore",
    color: "#253b59",
  },
  market: {
    title: "Market",
    subtitle: "Buy, sell, trade, and merchants",
    category: "explore",
    categoryName: "Explore",
    color: "#1a5347",
  },
  museum: {
    title: "Museum",
    subtitle: "District artifacts and historical heritage",
    category: "explore",
    categoryName: "Explore",
    color: "#6e5219",
  },
  weather: {
    title: "Weather",
    subtitle: "Current conditions and forecasts",
    category: "explore",
    categoryName: "Explore",
    color: "#255b70",
  },
  social: {
    title: "Social",
    subtitle: "Citizens, relationships, and contacts",
    category: "community",
    categoryName: "Community",
    color: "#802b3c",
  },
  bulletin: {
    title: "Bulletin",
    subtitle: "Public notices and official announcements",
    category: "community",
    categoryName: "Community",
    color: "#245738",
  },
  rumors: {
    title: "Rumors",
    subtitle: "Whispers, hearsay, and investigations",
    category: "community",
    categoryName: "Community",
    color: "#523970",
  },
  events: {
    title: "Activities & Events",
    subtitle: "Mystery chains, festivals, and gatherings",
    category: "community",
    categoryName: "Community",
    color: "#8c601c",
  },
  reactions: {
    title: "Reactions",
    subtitle: "District sentiments and citizen morale",
    category: "community",
    categoryName: "Community",
    color: "#7d344d",
  },
  crime: {
    title: "Crime",
    subtitle: "Underworld rackets, theft, and illicit deeds",
    category: "more",
    categoryName: "More",
    color: "#542626",
  },
  jail: {
    title: "Jail",
    subtitle: "Detention records, bail, and sentences",
    category: "more",
    categoryName: "More",
    color: "#52252c",
  },
  games: {
    title: "Games",
    subtitle: "Panem party pack & recreational matches",
    category: "more",
    categoryName: "More",
    color: "#783042",
  },
  history: {
    title: "History",
    subtitle: "District archives and historical records",
    category: "more",
    categoryName: "More",
    color: "#4a4e58",
  },
  staff: {
    title: "Staff",
    subtitle: "Administrative controls and moderation",
    category: "more",
    categoryName: "More",
    color: "#3f444e",
  },
};

const CATEGORIES = [
  { id: "all", label: "All" },
  { id: "daily", label: "Daily life" },
  { id: "explore", label: "Explore" },
  { id: "community", label: "Community" },
  { id: "more", label: "More" },
];

function homeLauncherPanel(ctx) {
  const container = el("div", { class: "home-launcher-container" });
  const allVisible = ctx.visibleTabs();

  // 1. Welcoming Header
  const char = typeof ctx.currentCharacter === "function" ? ctx.currentCharacter() : null;
  const charName = char && char.name ? char.name : "Citizen";
  const districtId = char ? char.district_id : 1;
  const districtName = char && char.district_name ? char.district_name : ctx.districtName(districtId);
  const motto = ctx.districtMotto(districtId);

  const welcomeHeader = el(
    "div",
    { class: "home-welcome-header" },
    el("h1", { class: "home-welcome-title", text: `Welcome back, ${charName}` }),
    el("p", { class: "home-welcome-sub", text: `${districtName} • ${motto}` })
  );

  // 2. Search Bar
  let searchQuery = "";
  const searchInput = el("input", {
    type: "text",
    class: "home-search-input",
    placeholder: "Find an app or activity...",
  });
  const searchClear = el("button", {
    type: "button",
    class: "home-search-clear",
    text: "✕",
  });
  searchClear.hidden = true;

  const searchIconSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  searchIconSvg.setAttribute("viewBox", "0 0 24 24");
  searchIconSvg.setAttribute("fill", "none");
  searchIconSvg.setAttribute("stroke", "currentColor");
  searchIconSvg.setAttribute("stroke-width", "2");
  searchIconSvg.classList.add("home-search-icon");
  searchIconSvg.innerHTML = '<circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line>';

  const searchBar = el("div", { class: "home-search-bar" }, searchIconSvg, searchInput, searchClear);

  // 3. Recently Opened Shelf (Icon-only on desktop per spec)
  const usage = ctx.tabUsage();
  const recentTabs = (usage.recent || [])
    .filter((name) => allVisible.includes(name))
    .slice(0, 6);

  const recentHost = el("div", { class: "home-recent-section" });
  if (recentTabs.length > 0) {
    const recentGrid = el("div", { class: "home-recent-shelf" });
    for (const name of recentTabs) {
      const meta = APP_METADATA[name] || {
        title: ctx.tabLabel(name),
        subtitle: "",
        color: "#3d6ea8",
      };
      const iconWrap = el("div", { class: "home-recent-icon-wrap" }, renderTabIcon(name));
      iconWrap.style.backgroundColor = meta.color;

      const tile = el(
        "button",
        {
          type: "button",
          class: "home-recent-tile",
          title: `${meta.title}${meta.subtitle ? " — " + meta.subtitle : ""}`,
        },
        iconWrap,
        el("span", { class: "home-recent-label", text: meta.title })
      );
      tile.addEventListener("click", () => ctx.goToTab(name));
      recentGrid.append(tile);
    }

    recentHost.append(
      el("div", { class: "home-section-header" }, el("h2", { text: "Recently opened" })),
      recentGrid
    );
  }

  // 4. Categories Tabs (Browse)
  let activeCategory = "all";
  const catBar = el("div", { class: "home-category-bar" });
  const catButtons = {};

  const rowsHost = el("div", { class: "home-rows-container" });

  function renderAppRow(name) {
    const meta = APP_METADATA[name] || {
      title: ctx.tabLabel(name),
      subtitle: "",
      color: "#3d6ea8",
    };

    const iconWrap = el("div", { class: "home-app-icon" }, renderTabIcon(name));
    iconWrap.style.backgroundColor = meta.color;

    const infoWrap = el(
      "div",
      { class: "home-app-info" },
      el("span", { class: "home-app-title", text: meta.title }),
      el("span", { class: "home-app-subtitle", text: meta.subtitle })
    );

    const chevron = el("span", { class: "home-app-chevron", text: "›" });

    const row = el(
      "button",
      {
        type: "button",
        class: "home-app-row",
      },
      iconWrap,
      infoWrap,
      chevron
    );
    row.addEventListener("click", () => ctx.goToTab(name));
    return row;
  }

  function renderRows() {
    rowsHost.innerHTML = "";
    const query = searchQuery.trim().toLowerCase();

    if (activeCategory === "all" && !query) {
      const grid = el("div", { class: "home-category-grid" });
      const groups = [
        { id: "daily", name: "Daily Life" },
        { id: "explore", name: "Explore" },
        { id: "community", name: "Community" },
        { id: "more", name: "More" },
      ];

      for (const group of groups) {
        const groupTabs = allVisible.filter((name) => {
          const meta = APP_METADATA[name] || { category: "more" };
          return meta.category === group.id;
        });

        if (groupTabs.length === 0) continue;

        const card = el(
          "div",
          { class: "home-category-card" },
          el(
            "div",
            { class: "home-category-card-header" },
            el("span", { class: "home-category-card-title", text: group.name }),
            el("span", { class: "home-category-card-count", text: `${groupTabs.length} apps` })
          )
        );

        for (const name of groupTabs) {
          card.append(renderAppRow(name));
        }
        grid.append(card);
      }
      rowsHost.append(grid);
      return;
    }

    // Filter apps based on category, search, and strict staff visibility
    const filtered = allVisible.filter((name) => {
      const meta = APP_METADATA[name] || {
        title: ctx.tabLabel(name),
        subtitle: "",
        category: "more",
        categoryName: "More",
      };
      if (activeCategory !== "all" && meta.category !== activeCategory) {
        return false;
      }
      if (query) {
        const titleMatch = (meta.title || "").toLowerCase().includes(query);
        const subMatch = (meta.subtitle || "").toLowerCase().includes(query);
        const catMatch = (meta.categoryName || "").toLowerCase().includes(query);
        return titleMatch || subMatch || catMatch;
      }
      return true;
    });

    if (filtered.length === 0) {
      rowsHost.append(
        el("div", { class: "home-empty-search" }, `No applications found matching "${searchQuery}".`)
      );
      return;
    }

    // Group rows into a single card when filtering
    const card = el("div", { class: "home-category-card" });
    for (const name of filtered) {
      card.append(renderAppRow(name));
    }
    rowsHost.append(card);
  }

  for (const cat of CATEGORIES) {
    const btn = el("button", {
      type: "button",
      class: cat.id === activeCategory ? "home-category-tab active" : "home-category-tab",
      text: cat.label,
    });
    btn.addEventListener("click", () => {
      activeCategory = cat.id;
      for (const [id, b] of Object.entries(catButtons)) {
        b.classList.toggle("active", id === activeCategory);
      }
      renderRows();
    });
    catButtons[cat.id] = btn;
    catBar.append(btn);
  }

  // Search input listeners
  searchInput.addEventListener("input", () => {
    searchQuery = searchInput.value;
    searchClear.hidden = !searchQuery;
    renderRows();
  });
  searchClear.addEventListener("click", () => {
    searchInput.value = "";
    searchQuery = "";
    searchClear.hidden = true;
    renderRows();
  });

  const browseHeader = el(
    "div",
    { class: "home-section-header browse-header" },
    el("h2", { text: "Browse" })
  );

  container.append(
    welcomeHeader,
    searchBar,
    recentHost,
    browseHeader,
    catBar,
    rowsHost
  );

  renderRows();
  return container;
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
      setStatusText(statusLine, "Pick a character above first.");
      return;
    }
    let weatherStatus = null;
    try {
      weatherStatus = await ctx.apiFetch(
        `/activity/dashboard/weather/${characterId}/status?discord_id=${encodeURIComponent(discordId)}`
      );
    } catch {
      // Best-effort -- the hero panel just doesn't show if this fails.
    }
    let bulletinEntries = [];
    try {
      const board = await ctx.apiFetch(
        `/activity/dashboard/bulletin/${characterId}/board?discord_id=${encodeURIComponent(discordId)}`
      );
      bulletinEntries = board.entries || [];
    } catch {
      // Best-effort -- the preview panel just doesn't show if this fails.
    }

    try {
      const status = await ctx.apiFetch(
        `/activity/dashboard/mode/${characterId}/status?discord_id=${encodeURIComponent(discordId)}`
      );
      setStatusText(statusLine, "");
      const panels = [
        homeLauncherPanel(ctx),
        weatherStatus ? heroPanel(ctx, weatherStatus) : null,
        bulletinPreviewPanel(ctx, bulletinEntries),
        modePanel(status),
        metersPanel(status),
        crimeTogglePanel(ctx, status, resultLine, refresh),
        afflictionsPanel(status),
        modeSwitchPanel(ctx, status, resultLine, refresh),
      ].filter(Boolean);
      host.append(...panels);
    } catch (err) {
      setStatusText(statusLine, `Could not load status: ${err.message}`, { error: true });
    }
  }

  refresh();

  return {};
}
