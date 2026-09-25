// The "Social" tab: Capitol Neoclassical layout with two panels, plus the
// player-to-player economy primitives (Pay, Trade) beneath them:
// 1. Current Engagement (left): live scene, district plaza illustration,
//    participant metadata, Discord deep link CTA, and district motto.
// 2. Residents Directory (right): searchable, filterable table of both NPC
//    residents and other players' characters currently in the district
//    (each labeled NPC/USER), monogram avatar badges, status indicators
//    (NPCs: Available/Working/Busy; other players: Sleeping/Talking to
//    Residents/Idle), and an interactive dossier modal -- NPC backstories
//    and stances for an NPC row, everything a player submitted about their
//    character at creation (age, gender, appearance text, backstory,
//    avatar, and their Picrew-style layered appearance) for a USER row.
//
// The Engagement panel is deliberately read-only + a "Continue in
// Discord" deep link, not a full chat UI -- per the user's own choice
// when this feature was scoped: making it fully interactive here would
// need a dashboard -> Redis -> panem_bot relay (only the bot process
// holds a token and can post/create Discord threads), which is out of
// scope for this pass.
//
// The Trade panel has no push channel of its own the way the bot's DM +
// Accept/Decline view does -- a recipient discovers a pending offer by
// reopening this tab (or switching characters, which remounts it), same
// "poll, don't push" posture the Engagement panel already has.
import { fetchJson, el, renderIcon, setStatusText } from "./_shared.js?v=7";
import { mountAvatar } from "./avatar_creator.js?v=11";

// Same one-shot cache pattern as tabs/character.js's own `loadLayerCatalog`
// -- the layer catalog is staff-authored and effectively static for the
// lifetime of this tab, so every dossier open reuses the first fetch
// instead of refetching it every time a USER row is clicked.
let layerCatalogPromise = null;
function loadLayerCatalog(ctx) {
  if (!layerCatalogPromise) {
    layerCatalogPromise = ctx.apiFetch("/activity/dashboard/layers").then((body) => body.categories);
  }
  return layerCatalogPromise;
}

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

// Server-computed statuses (`sleeping`/`engaged`/`idle`, for `kind ===
// "user"` rows -- dashboard_routes.py's `_character_status`) get a
// friendlier label than `determineStatus`'s bare capitalized fallback
// would give them; NPC statuses (`available`/`working`/`busy`) already
// read fine capitalized as-is.
const STATUS_LABELS = {
  engaged: "Talking to Residents",
};

function statusLabel(status) {
  return STATUS_LABELS[status] || status.charAt(0).toUpperCase() + status.slice(1);
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
      setStatusText(statusEl, "Pick a character above first.");
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

      setStatusText(statusEl, "");

      const titleEl = el("div", { class: "engagement-title", text: title });

      // District Scene Frame -- no location art yet, so this is a plain
      // placeholder reserved for future per-district illustrations.
      const sceneFrame = el(
        "div",
        { class: "scene-frame scene-placeholder" },
        renderIcon("crest", 28),
        el("span", { class: "scene-placeholder-text", text: "Location art coming soon" })
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

      // CTA Button -- a plain `<a target="_blank">` doesn't work here: the
      // Activity runs inside Discord's sandboxed iframe, which swallows
      // that kind of navigation rather than opening anything, so this
      // routes through `ctx.openExternalLink` (the embedded-app-sdk's
      // `openExternalLink` command outside the sandbox, `window.open` as
      // a plain-browser preview-mode fallback) instead.
      const threadUrl = status.discord_thread_url;
      const ctaBtn = el(
        "button",
        {
          class: "btn primary btn-discord-cta",
          type: "button",
          disabled: threadUrl ? undefined : "disabled",
          onclick: () => threadUrl && ctx.openExternalLink(threadUrl),
        },
        renderIcon("discord", 16),
        threadUrl ? "Continue in Discord →" : "No active thread"
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
      setStatusText(statusEl, `Could not load engagement status: ${err.message}`, { error: true });
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

  function renderTable(filterText = "", jobFilter = "all", locationFilter = "all", kindFilter = "all") {
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
      const matchKind = kindFilter === "all" || r.kind === kindFilter;
      return matchSearch && matchJob && matchLoc && matchKind;
    });

    filtered.sort((a, b) => {
      // `opinion_score` is numeric (an NPC's raw relationship affinity, or
      // `null` for a "user" row -- see dashboard_routes.py's
      // `ResidentSummary`) -- sorting it as lowercased text would put -3
      // after -20 and before 4, so it gets its own numeric compare instead
      // of the generic string one every other column uses. A `null` (a
      // player row, opinion not tracked) always sorts last regardless of
      // direction, same as it has nothing meaningful to compare.
      if (sortCol === "opinion_score") {
        const nA = a.opinion_score;
        const nB = b.opinion_score;
        if (nA == null && nB == null) return 0;
        if (nA == null) return 1;
        if (nB == null) return -1;
        return sortAsc ? nA - nB : nB - nA;
      }
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

    // Type filter -- NPC residents vs. other players' characters currently
    // in this district (`r.kind`, dashboard_routes.py's `ResidentSummary`).
    const kindSelect = el(
      "select",
      { class: "filter-select", style: "min-width: 110px;" },
      el("option", { value: "all", selected: kindFilter === "all" ? "selected" : undefined }, "Everyone"),
      el("option", { value: "npc", selected: kindFilter === "npc" ? "selected" : undefined }, "NPCs"),
      el("option", { value: "user", selected: kindFilter === "user" ? "selected" : undefined }, "Players")
    );

    function onFilterChange() {
      renderTable(searchInput.value, jobSelect.value, locSelect.value, kindSelect.value);
    }

    searchInput.addEventListener("input", onFilterChange);
    jobSelect.addEventListener("change", onFilterChange);
    locSelect.addEventListener("change", onFilterChange);
    kindSelect.addEventListener("change", onFilterChange);

    const toolbar = el(
      "div",
      {
        class: "toolbar",
        style: "display: flex; gap: 12px; align-items: center; margin-bottom: 16px; flex-wrap: wrap;",
      },
      searchBox,
      jobSelect,
      locSelect,
      kindSelect
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
              renderTable(searchInput.value, jobSelect.value, locSelect.value, kindSelect.value);
            },
          }),
          el("th", {
            text: "Job ↕",
            style: "cursor: pointer;",
            onclick: () => {
              if (sortCol === "job_title") sortAsc = !sortAsc;
              else { sortCol = "job_title"; sortAsc = true; }
              renderTable(searchInput.value, jobSelect.value, locSelect.value, kindSelect.value);
            },
          }),
          el("th", {
            text: "Where ↕",
            style: "cursor: pointer;",
            onclick: () => {
              if (sortCol === "location_name") sortAsc = !sortAsc;
              else { sortCol = "location_name"; sortAsc = true; }
              renderTable(searchInput.value, jobSelect.value, locSelect.value, kindSelect.value);
            },
          }),
          el("th", {
            text: "Opinion ↕",
            style: "cursor: pointer;",
            onclick: () => {
              if (sortCol === "opinion_score") sortAsc = !sortAsc;
              else { sortCol = "opinion_score"; sortAsc = false; }
              renderTable(searchInput.value, jobSelect.value, locSelect.value, kindSelect.value);
            },
          }),
          el("th", { text: "Type" }),
          el("th", { text: "Status" }),
          el("th", { style: "width: 24px;" })
        )
      )
    );

    const tbody = el("tbody", {});
    for (const r of filtered) {
      const initial = r.name.charAt(0);
      const st = determineStatus(r);
      const isNpc = r.kind !== "user";

      const statusBadge = el(
        "span",
        { class: `status-pill ${st}` },
        el("span", { class: "status-dot" }),
        statusLabel(st)
      );

      const kindBadge = el(
        "span",
        { class: `kind-badge ${isNpc ? "npc" : "user"}` },
        isNpc ? "NPC" : "USER"
      );

      // `opinion_label` is only ever set for an NPC row (see
      // dashboard_routes.py's `ResidentSummary`) -- another player's
      // character has no tracked relationship to show here.
      const opinionCell = r.opinion_label
        ? el(
            "span",
            { class: `opinion-pill ${r.opinion_label}` },
            r.opinion_label.charAt(0).toUpperCase() + r.opinion_label.slice(1)
          )
        : el("span", { text: "—" });

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

      // Every row opens the dossier modal now -- an NPC's own backstory/
      // traits/opinion (`/activity/dashboard/residents/{id}/{name}`) or,
      // for another player's character, everything they submitted at
      // creation (`/activity/dashboard/residents/{id}/character/{name}`).
      const row = el(
        "tr",
        { class: "clickable-row", onclick: () => openDossierFn(r.name, r.kind) },
        nameCell,
        el("td", { text: r.job_title }),
        el("td", { text: r.location_name || "unknown" }),
        el("td", {}, opinionCell),
        el("td", {}, kindBadge),
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
      setStatusText(statusEl, "Pick a character above first.");
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
      setStatusText(statusEl, "");
      renderTable();
    } catch (err) {
      setStatusText(statusEl, `Could not load residents: ${err.message}`, { error: true });
    }
  }

  refresh();
  panel.refresh = refresh;
  return panel;
}

function dossierModal(ctx) {
  const overlay = el("div", { class: "modal-overlay" });
  const avatarEl = el("div", { class: "dossier-avatar", text: "A" });
  const avatarImgEl = el("img", { class: "dossier-avatar-img", alt: "" });
  avatarImgEl.hidden = true;
  const nameEl = el("div", { class: "dossier-name", text: "Citizen" });
  const jobEl = el("div", { class: "dossier-job", text: "District Resident" });
  const closeBtn = el("button", { class: "btn-close-modal", text: "×", onclick: close });

  const locationEl = el("span", { text: "Unknown" });
  const traitsEl = el("div", { class: "traits-chips" });
  const speechEl = el("span", { text: "Polite and measured." });
  const stanceEl = el("span", { text: "Neutral." });
  const appearanceEl = el("span", { text: "" });
  const backstoryEl = el("span", { text: "" });
  const ageGenderEl = el("span", { text: "" });
  const appearancePreviewHost = el("div", { class: "avatar-preview small" });
  let appearancePreview = null; // mountAvatar's handle -- disposed/remade per open()

  // NPC-only rows (traits/speech-style/opinion-of-you) vs. USER-only rows
  // (age & gender, the layered appearance preview) -- one dossier layout
  // serves both kinds, toggling which half shows via `open`'s `kind`.
  const npcRow1 = el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Traits" }), traitsEl);
  const npcRow2 = el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Speech Style" }), speechEl);
  const npcRow3 = el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Opinion of You" }), stanceEl);
  const userRow1 = el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Age & Gender" }), ageGenderEl);
  const userRow2 = el(
    "div",
    { class: "dossier-prop" },
    el("span", { class: "dossier-prop-label", text: "Character Appearance" }),
    appearancePreviewHost
  );

  const card = el(
    "div",
    { class: "dossier-card", onclick: (e) => e.stopPropagation() },
    el(
      "div",
      { class: "dossier-header" },
      el(
        "div",
        { class: "dossier-header-info" },
        avatarEl,
        avatarImgEl,
        el("div", {}, nameEl, jobEl)
      ),
      closeBtn
    ),
    el(
      "div",
      { class: "dossier-body" },
      el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Current Location" }), locationEl),
      userRow1,
      npcRow1,
      npcRow2,
      npcRow3,
      el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Appearance" }), appearanceEl),
      userRow2,
      el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Backstory" }), backstoryEl)
    )
  );

  overlay.append(card);
  overlay.addEventListener("click", close);

  function close() {
    overlay.classList.remove("open");
  }

  function showNpcRows(show) {
    npcRow1.hidden = !show;
    npcRow2.hidden = !show;
    npcRow3.hidden = !show;
  }

  function showUserRows(show) {
    userRow1.hidden = !show;
    userRow2.hidden = !show;
  }

  async function renderAppearancePreview(selection) {
    if (appearancePreview) {
      appearancePreview.dispose();
      appearancePreview = null;
    }
    try {
      const catalog = await loadLayerCatalog(ctx);
      appearancePreview = mountAvatar(appearancePreviewHost, catalog, selection || {});
    } catch {
      // No layer catalog to render (not configured, or offline) -- the
      // rest of the dossier is still useful without this one preview.
    }
  }

  async function open(residentName, kind = "npc") {
    overlay.classList.add("open");
    avatarEl.textContent = residentName.charAt(0);
    avatarEl.hidden = false;
    avatarImgEl.hidden = true;
    nameEl.textContent = residentName;
    jobEl.textContent = "Loading citizen profile...";
    traitsEl.innerHTML = "";
    locationEl.textContent = "Locating...";
    speechEl.textContent = "—";
    stanceEl.textContent = "—";
    appearanceEl.textContent = "—";
    backstoryEl.textContent = "—";
    ageGenderEl.textContent = "—";
    showNpcRows(kind === "npc");
    showUserRows(kind === "user");

    const charList = typeof ctx.characters === "function" ? ctx.characters() : [];
    const viewer = charList.find((c) => c.id === ctx.characterId());
    const districtId = viewer ? (viewer.current_district_id ?? viewer.district_id ?? 1) : 1;
    const districtName =
      (viewer && (viewer.current_district_name || viewer.district_name)) ||
      (ctx.districtName ? ctx.districtName(districtId) : `District ${districtId}`);

    if (kind === "user") {
      try {
        const profile = await ctx.apiFetch(
          `/activity/dashboard/residents/${ctx.characterId()}/character/${encodeURIComponent(residentName)}` +
            `?discord_id=${encodeURIComponent(ctx.discordId())}`
        );
        jobEl.textContent = `${profile.job_title || "Unemployed"} • ${profile.district_name || districtName}`;
        locationEl.textContent = profile.location_name || "unknown";
        ageGenderEl.textContent = [profile.age, profile.gender].filter(Boolean).join(", ") || "—";
        appearanceEl.textContent = profile.appearance || "No appearance description given.";
        backstoryEl.textContent = profile.backstory || "No backstory given.";
        if (profile.avatar_url) {
          avatarImgEl.src = profile.avatar_url;
          avatarImgEl.hidden = false;
          avatarEl.hidden = true;
        }
        await renderAppearancePreview(profile.appearance_layers);
      } catch (err) {
        jobEl.textContent = `Error: ${err.message}`;
      }
      return;
    }

    try {
      const profile = await ctx.apiFetch(
        `/activity/dashboard/residents/${ctx.characterId()}/${encodeURIComponent(residentName)}` +
          `?discord_id=${encodeURIComponent(ctx.discordId())}`
      );
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

function payPanel(ctx) {
  const targetInput = el("input", { type: "text", placeholder: "Character name" });
  const amountInput = el("input", { type: "number", placeholder: "Amount", min: "1" });
  const resultLine = el("p", { class: "result-line" });
  const payBtn = el("button", { class: "btn", type: "button" }, "Pay");

  payBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    const target = targetInput.value.trim();
    const amount = Number(amountInput.value);
    if (!target || !Number.isFinite(amount) || amount <= 0) {
      resultLine.className = "result-line lose";
      resultLine.textContent = "Name who to pay and a positive amount.";
      return;
    }
    try {
      const body = await ctx.apiFetch(`/activity/dashboard/pay/${ctx.characterId()}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), target, amount }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = `Paid ${amount} to ${body.recipient_name} (${body.sender_money} left).`;
      targetInput.value = "";
      amountInput.value = "";
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Pay" }),
    el("div", { class: "field-row" }, el("label", { text: "To" }), targetInput),
    el("div", { class: "field-row" }, el("label", { text: "Amount" }), amountInput),
    el("div", { class: "field-row" }, payBtn),
    resultLine
  );
}

function tradeOfferSummary(trade) {
  const describe = (goodId, qty, money) => {
    const parts = [];
    if (goodId && qty) parts.push(`${qty} ${goodId}`);
    if (money) parts.push(`${money} money`);
    return parts.length > 0 ? parts.join(" + ") : "nothing";
  };
  const give = describe(trade.give_good_id, trade.give_qty, trade.give_money);
  const want = describe(trade.want_good_id, trade.want_qty, trade.want_money);
  if (trade.direction === "incoming") {
    return `${trade.initiator_name} offers you ${give} for ${want}.`;
  }
  return `You offered ${trade.recipient_name} ${give} for ${want}.`;
}

function tradeOfferRow(ctx, trade, { onChanged }) {
  const resultLine = el("p", { class: "result-line" });

  async function act(action) {
    resultLine.textContent = "";
    try {
      await ctx.apiFetch(
        `/activity/dashboard/trade/${ctx.characterId()}/${trade.id}/${action}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: ctx.discordId() }),
        }
      );
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  }

  const buttons = [];
  if (trade.direction === "incoming") {
    const acceptBtn = el("button", { class: "btn", type: "button" }, "Accept");
    acceptBtn.addEventListener("click", () => act("accept"));
    const declineBtn = el("button", { class: "btn secondary", type: "button" }, "Decline");
    declineBtn.addEventListener("click", () => act("decline"));
    buttons.push(acceptBtn, declineBtn);
  } else {
    const cancelBtn = el("button", { class: "btn secondary", type: "button" }, "Cancel");
    cancelBtn.addEventListener("click", () => act("cancel"));
    buttons.push(cancelBtn);
  }

  return el(
    "div",
    { class: "panel" },
    el("p", {}, tradeOfferSummary(trade)),
    el("div", { class: "field-row" }, ...buttons),
    resultLine
  );
}

function tradePanel(ctx) {
  const statusEl = el("p", { class: "tab-status" });
  const listEl = el("div", {});

  const targetInput = el("input", { type: "text", placeholder: "Character name" });
  const giveGoodInput = el("input", { type: "text", placeholder: "Good id" });
  const giveQtyInput = el("input", { type: "number", placeholder: "Qty", min: "1" });
  const giveMoneyInput = el("input", { type: "number", placeholder: "0", value: "0" });
  const wantGoodInput = el("input", { type: "text", placeholder: "Good id" });
  const wantQtyInput = el("input", { type: "number", placeholder: "Qty", min: "1" });
  const wantMoneyInput = el("input", { type: "number", placeholder: "0", value: "0" });
  const offerResult = el("p", { class: "result-line" });
  const offerBtn = el("button", { class: "btn", type: "button" }, "Send offer");

  offerBtn.addEventListener("click", async () => {
    offerResult.textContent = "";
    const target = targetInput.value.trim();
    if (!target) {
      offerResult.className = "result-line lose";
      offerResult.textContent = "Name who you're offering to.";
      return;
    }
    const giveGood = giveGoodInput.value.trim() || null;
    const wantGood = wantGoodInput.value.trim() || null;
    try {
      await ctx.apiFetch(`/activity/dashboard/trade/${ctx.characterId()}/offer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          target,
          give_good_id: giveGood,
          give_qty: giveGood ? Number(giveQtyInput.value) || null : null,
          give_money: Number(giveMoneyInput.value) || 0,
          want_good_id: wantGood,
          want_qty: wantGood ? Number(wantQtyInput.value) || null : null,
          want_money: Number(wantMoneyInput.value) || 0,
        }),
      });
      offerResult.className = "result-line win";
      offerResult.textContent = "Offer sent.";
      targetInput.value = "";
      giveGoodInput.value = "";
      giveQtyInput.value = "";
      giveMoneyInput.value = "0";
      wantGoodInput.value = "";
      wantQtyInput.value = "";
      wantMoneyInput.value = "0";
      await refresh();
    } catch (err) {
      offerResult.className = "result-line lose";
      offerResult.textContent = err.message;
    }
  });

  async function refresh() {
    listEl.innerHTML = "";
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    if (!characterId || !discordId) {
      setStatusText(statusEl, "Pick a character above first.");
      return;
    }
    try {
      const body = await ctx.apiFetch(
        `/activity/dashboard/trade/${characterId}/list?discord_id=${encodeURIComponent(discordId)}`
      );
      setStatusText(statusEl, "");
      if (body.trades.length === 0) {
        listEl.append(el("p", { class: "tab-status" }, "No pending trade offers."));
        return;
      }
      for (const trade of body.trades) {
        listEl.append(tradeOfferRow(ctx, trade, { onChanged: refresh }));
      }
    } catch (err) {
      setStatusText(statusEl, `Could not load trades: ${err.message}`, { error: true });
    }
  }

  refresh();

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Trade" }),
    statusEl,
    listEl,
    el("h3", { text: "Offer a trade" }),
    el("div", { class: "field-row" }, el("label", { text: "To" }), targetInput),
    el("div", { class: "field-row" }, el("label", { text: "You give: good" }), giveGoodInput),
    el("div", { class: "field-row" }, el("label", { text: "Qty" }), giveQtyInput),
    el("div", { class: "field-row" }, el("label", { text: "+ money" }), giveMoneyInput),
    el("div", { class: "field-row" }, el("label", { text: "You want: good" }), wantGoodInput),
    el("div", { class: "field-row" }, el("label", { text: "Qty" }), wantQtyInput),
    el("div", { class: "field-row" }, el("label", { text: "+ money" }), wantMoneyInput),
    el("div", { class: "field-row" }, offerBtn),
    offerResult
  );
}

export function mount(root, ctx) {
  const modal = dossierModal(ctx);
  const layout = el("div", { class: "social-layout" });
  const eng = engagementPanel(ctx);
  const res = residentsPanel(ctx, (name, kind) => modal.open(name, kind));
  layout.append(eng, res);
  root.append(layout, modal.element, payPanel(ctx), tradePanel(ctx));

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
