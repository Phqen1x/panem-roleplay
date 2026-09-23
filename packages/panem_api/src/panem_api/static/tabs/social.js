// The "Social" tab: Capitol Neoclassical layout with two panels:
// 1. Current Engagement (left): live scene, district plaza illustration,
//    participant metadata, Discord deep link CTA, and district motto.
// 2. Residents Directory (right): searchable, filterable citizen table with
//    monogram avatar badges, status indicators (Available/Working/Busy), and
//    an interactive dossier modal for resident backstories and stances.
import { fetchJson, el, renderIcon } from "./_shared.js?v=5";

function determineStatus(r) {
  if (r.status) return r.status;
  const loc = (r.location_name || "").toLowerCase();
  const job = (r.job_title || "").toLowerCase();
  if (loc.includes("workshop") || loc.includes("exchange") || loc.includes("academy") || loc.includes("station")) {
    return "working";
  }
  if (loc.includes("justice") || loc.includes("court") || loc.includes("jail") || loc.includes("office")) {
    return "busy";
  }
  return "available";
}

function engagementPanel(ctx) {
  const statusEl = el("p", { class: "tab-status" });
  const contentHost = el("div", {});
  const panel = el(
    "div",
    { class: "panel engagement-panel" },
    el(
      "div",
      { class: "card-header" },
      renderIcon("crest", 20),
      el("div", { class: "card-header-text" }, el("h2", { text: "Current Engagement" }))
    ),
    statusEl,
    contentHost
  );

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    contentHost.innerHTML = "";
    if (!characterId || !discordId) {
      statusEl.textContent = "Pick a character above first.";
      return;
    }
    try {
      const status = await ctx.apiFetch(
        `/activity/dashboard/social/${characterId}?discord_id=${encodeURIComponent(discordId)}`
      );

      const title = status.in_scene ? status.scene_title : "The Square — ambient";
      const kind = status.in_scene ? status.scene_kind : "ambient";
      const location = status.location_name || "The Square";
      const characters = status.participant_character_names && status.participant_character_names.length > 0
        ? status.participant_character_names.join(", ")
        : "You";

      statusEl.textContent = "";

      const titleEl = el("div", { class: "engagement-title", text: title });

      // District Scene Frame
      const sceneFrame = el(
        "div",
        { class: "scene-frame" },
        el("img", {
          src: "/d1_square.png",
          alt: location,
          class: "scene-img",
          onerror: (e) => { e.target.style.display = "none"; },
        })
      );

      // Meta list
      const metaList = el(
        "div",
        { class: "meta-list" },
        el(
          "div",
          { class: "meta-item" },
          renderIcon("wave", 15),
          el("span", {}, "Kind: ", el("strong", { text: kind }))
        ),
        el(
          "div",
          { class: "meta-item" },
          renderIcon("pin", 15),
          el("span", {}, "Location: ", el("strong", { text: location }))
        ),
        el(
          "div",
          { class: "meta-item" },
          renderIcon("user", 15),
          el("span", {}, "Characters: ", el("strong", { text: characters }))
        )
      );

      // CTA Button
      const threadUrl = status.discord_thread_url || "https://discord.com";
      const ctaBtn = el(
        "a",
        {
          class: "btn primary btn-discord-cta",
          href: threadUrl,
          target: "_blank",
          rel: "noopener",
          style: "text-decoration: none;",
        },
        renderIcon("discord", 16),
        "Continue in Discord →"
      );

      // District Motto Plaque
      const charList = typeof ctx.characters === "function" ? ctx.characters() : [];
      const character = charList.find((c) => c.id === characterId);
      const districtId = character ? (character.current_district_id ?? character.district_id ?? 1) : 1;
      const districtName =
        (character && (character.current_district_name || character.district_name)) ||
        (ctx.districtName ? ctx.districtName(districtId) : `District ${districtId}`);
      const motto = ctx.districtMotto ? ctx.districtMotto(districtId) : "Excellence Endures";

      const mottoBox = el(
        "div",
        { class: "motto-box" },
        el("div", { class: "motto-divider", text: "◇" }),
        el("div", { class: "district-badge-text", text: districtName }),
        el("div", { class: "district-slogan-text", text: motto })
      );

      contentHost.append(titleEl, sceneFrame, metaList, ctaBtn, mottoBox);
    } catch (err) {
      statusEl.textContent = `Could not load engagement status: ${err.message}`;
    }
  }

  refresh();
  panel.refresh = refresh;
  return panel;
}

function residentsPanel(ctx, openDossierFn) {
  const statusEl = el("p", { class: "tab-status" });
  const listHost = el("div", {});
  const headerTitleEl = el("h2", { text: "District Residents" });
  const headerSubtitleEl = el("p", { text: "Artisans, workers, and citizens of distinction." });
  const panel = el(
    "div",
    { class: "panel residents-panel" },
    el(
      "div",
      { class: "card-header" },
      renderIcon("users", 20),
      el(
        "div",
        { class: "card-header-text" },
        headerTitleEl,
        headerSubtitleEl
      )
    ),
    statusEl,
    listHost
  );

  let allResidents = [];
  let sortCol = "name";
  let sortAsc = true;

  function renderTable(filterText = "", jobFilter = "all", locationFilter = "all") {
    listHost.innerHTML = "";

    let filtered = allResidents.filter((r) => {
      const q = filterText.toLowerCase().trim();
      const matchSearch =
        !q ||
        r.name.toLowerCase().includes(q) ||
        r.job_title.toLowerCase().includes(q) ||
        (r.location_name || "").toLowerCase().includes(q);
      const matchJob = jobFilter === "all" || r.job_title === jobFilter;
      const matchLoc = locationFilter === "all" || r.location_name === locationFilter;
      return matchSearch && matchJob && matchLoc;
    });

    filtered.sort((a, b) => {
      let vA = (a[sortCol] || "").toLowerCase();
      let vB = (b[sortCol] || "").toLowerCase();
      if (vA < vB) return sortAsc ? -1 : 1;
      if (vA > vB) return sortAsc ? 1 : -1;
      return 0;
    });

    // Toolbar (Search + Filters)
    const searchInput = el("input", {
      type: "text",
      class: "field-row search-input",
      placeholder: "Search residents by name or job...",
      value: filterText,
    });
    const searchBox = el(
      "div",
      { class: "search-box", style: "position: relative; flex: 1; min-width: 200px;" },
      searchInput
    );

    // Job options
    const jobsSet = Array.from(new Set(allResidents.map((r) => r.job_title))).sort();
    const jobSelect = el(
      "select",
      { class: "filter-select", style: "min-width: 130px;" },
      el("option", { value: "all" }, "All Jobs"),
      ...jobsSet.map((j) =>
        el("option", { value: j, selected: j === jobFilter ? "selected" : undefined }, j)
      )
    );

    // Location options
    const locsSet = Array.from(
      new Set(allResidents.map((r) => r.location_name).filter(Boolean))
    ).sort();
    const locSelect = el(
      "select",
      { class: "filter-select", style: "min-width: 140px;" },
      el("option", { value: "all" }, "All Locations"),
      ...locsSet.map((l) =>
        el("option", { value: l, selected: l === locationFilter ? "selected" : undefined }, l)
      )
    );

    function onFilterChange() {
      renderTable(searchInput.value, jobSelect.value, locSelect.value);
    }

    searchInput.addEventListener("input", onFilterChange);
    jobSelect.addEventListener("change", onFilterChange);
    locSelect.addEventListener("change", onFilterChange);

    const toolbar = el(
      "div",
      {
        class: "toolbar",
        style: "display: flex; gap: 12px; align-items: center; margin-bottom: 16px; flex-wrap: wrap;",
      },
      searchBox,
      jobSelect,
      locSelect
    );

    if (filtered.length === 0) {
      listHost.append(
        toolbar,
        el(
          "p",
          { class: "tab-status", style: "text-align: center; padding: 24px;" },
          "No citizens found matching your filter."
        )
      );
      return;
    }

    const table = el(
      "table",
      { class: "data-table" },
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          el("th", {
            text: "Name ↕",
            style: "cursor: pointer;",
            onclick: () => {
              if (sortCol === "name") sortAsc = !sortAsc;
              else { sortCol = "name"; sortAsc = true; }
              renderTable(searchInput.value, jobSelect.value, locSelect.value);
            },
          }),
          el("th", {
            text: "Job ↕",
            style: "cursor: pointer;",
            onclick: () => {
              if (sortCol === "job_title") sortAsc = !sortAsc;
              else { sortCol = "job_title"; sortAsc = true; }
              renderTable(searchInput.value, jobSelect.value, locSelect.value);
            },
          }),
          el("th", {
            text: "Where ↕",
            style: "cursor: pointer;",
            onclick: () => {
              if (sortCol === "location_name") sortAsc = !sortAsc;
              else { sortCol = "location_name"; sortAsc = true; }
              renderTable(searchInput.value, jobSelect.value, locSelect.value);
            },
          }),
          el("th", { text: "Status" }),
          el("th", { style: "width: 24px;" })
        )
      )
    );

    const tbody = el("tbody", {});
    for (const r of filtered) {
      const initial = r.name.charAt(0);
      const st = determineStatus(r);
      const stLabel = st.charAt(0).toUpperCase() + st.slice(1);

      const statusBadge = el(
        "span",
        { class: `status-pill ${st}` },
        el("span", { class: "status-dot" }),
        stLabel
      );

      const avatar = el("div", { class: "avatar-badge", text: initial });
      const nameCell = el(
        "td",
        {},
        el(
          "div",
          { style: "display: flex; align-items: center; gap: 10px; font-weight: 500;" },
          avatar,
          el("span", { text: r.name })
        )
      );

      const row = el(
        "tr",
        {
          class: "clickable-row",
          onclick: () => openDossierFn(r.name),
        },
        nameCell,
        el("td", { text: r.job_title }),
        el("td", { text: r.location_name || "unknown" }),
        el("td", {}, statusBadge),
        el("td", { class: "row-arrow", text: "›", style: "color: var(--muted); font-size: 16px;" })
      );
      tbody.append(row);
    }
    table.append(tbody);
    listHost.append(toolbar, table);
  }

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    listHost.innerHTML = "";
    if (!characterId || !discordId) {
      statusEl.textContent = "Pick a character above first.";
      return;
    }
    const charList = typeof ctx.characters === "function" ? ctx.characters() : [];
    const character = charList.find((c) => c.id === characterId);
    const districtId = character ? (character.current_district_id ?? character.district_id ?? 1) : 1;
    const districtName =
      (character && (character.current_district_name || character.district_name)) ||
      (ctx.districtName ? ctx.districtName(districtId) : `District ${districtId}`);
    headerTitleEl.textContent = `Residents of ${districtName}`;
    headerSubtitleEl.textContent = `Residents of ${districtName}: artisans, workers, and citizens of distinction.`;

    try {
      const data = await ctx.apiFetch(
        `/activity/dashboard/residents/${characterId}?discord_id=${encodeURIComponent(discordId)}`
      );
      allResidents = data.residents || [];
      statusEl.textContent = "";
      renderTable();
    } catch (err) {
      statusEl.textContent = `Could not load residents: ${err.message}`;
    }
  }

  refresh();
  panel.refresh = refresh;
  return panel;
}

function dossierModal(ctx) {
  const overlay = el("div", { class: "modal-overlay" });
  const avatarEl = el("div", { class: "dossier-avatar", text: "A" });
  const nameEl = el("div", { class: "dossier-name", text: "Citizen" });
  const jobEl = el("div", { class: "dossier-job", text: "District Resident" });
  const closeBtn = el("button", { class: "btn-close-modal", text: "×", onclick: close });

  const locationEl = el("span", { text: "Unknown" });
  const traitsEl = el("div", { class: "traits-chips" });
  const speechEl = el("span", { text: "Polite and measured." });
  const stanceEl = el("span", { text: "Neutral." });
  const appearanceEl = el("span", { text: "" });
  const backstoryEl = el("span", { text: "" });

  const card = el(
    "div",
    { class: "dossier-card", onclick: (e) => e.stopPropagation() },
    el(
      "div",
      { class: "dossier-header" },
      el("div", { class: "dossier-header-info" }, avatarEl, el("div", {}, nameEl, jobEl)),
      closeBtn
    ),
    el(
      "div",
      { class: "dossier-body" },
      el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Current Location" }), locationEl),
      el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Traits" }), traitsEl),
      el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Speech Style" }), speechEl),
      el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Opinion of You" }), stanceEl),
      el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Appearance" }), appearanceEl),
      el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Backstory" }), backstoryEl)
    )
  );

  overlay.append(card);
  overlay.addEventListener("click", close);

  function close() {
    overlay.classList.remove("open");
  }

  async function open(residentName) {
    overlay.classList.add("open");
    avatarEl.textContent = residentName.charAt(0);
    nameEl.textContent = residentName;
    jobEl.textContent = "Loading citizen profile...";
    traitsEl.innerHTML = "";
    locationEl.textContent = "Locating...";
    speechEl.textContent = "—";
    stanceEl.textContent = "—";
    appearanceEl.textContent = "—";
    backstoryEl.textContent = "—";

    try {
      const profile = await ctx.apiFetch(
        `/activity/dashboard/residents/${ctx.characterId()}/${encodeURIComponent(residentName)}` +
          `?discord_id=${encodeURIComponent(ctx.discordId())}`
      );
      const charList = typeof ctx.characters === "function" ? ctx.characters() : [];
      const character = charList.find((c) => c.id === ctx.characterId());
      const districtId = character ? (character.current_district_id ?? character.district_id ?? 1) : 1;
      const districtName =
        (character && (character.current_district_name || character.district_name)) ||
        (ctx.districtName ? ctx.districtName(districtId) : `District ${districtId}`);
      jobEl.textContent = `${profile.job_title} • ${profile.district_name || districtName}`;
      locationEl.textContent = profile.location_name || "The Square";
      speechEl.textContent = profile.tone || "Polished and measured.";
      stanceEl.textContent = profile.stance || "Courteous and attentive.";
      appearanceEl.textContent = profile.appearance || "Elegant district attire.";
      backstoryEl.textContent = profile.backstory || `A citizen of ${districtName}.`;

      traitsEl.innerHTML = "";
      (profile.traits || []).forEach((t) => {
        traitsEl.append(el("span", { class: "trait-chip", text: t }));
      });
    } catch (err) {
      jobEl.textContent = `Error: ${err.message}`;
    }
  }

  return { element: overlay, open };
}

export function mount(root, ctx) {
  const modal = dossierModal(ctx);
  const layout = el("div", { class: "social-layout" });
  const eng = engagementPanel(ctx);
  const res = residentsPanel(ctx, (name) => modal.open(name));
  layout.append(eng, res);
  root.append(layout, modal.element);

  const onMottoUpdated = () => {
    if (typeof eng.refresh === "function") eng.refresh();
  };
  window.addEventListener("panem:motto-updated", onMottoUpdated);

  return {
    unmount: () => {
      window.removeEventListener("panem:motto-updated", onMottoUpdated);
    },
  };
}
