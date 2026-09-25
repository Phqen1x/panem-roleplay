// The "History" tab: only offered by `app.js` when `/identify` reports
// `is_staff`, same gate as `staff.js` (and, same as that tab, purely
// cosmetic -- `panem_api.dashboard_routes.build_district_lore_router`
// re-checks staff on every read and write regardless of what this client
// thinks). Staff author each district's context/history here; `panem_bot.
// services.dialogue` folds a short, capped summary of it into NPC replies
// (`panem_shared.district_lore.prompt_summary`) so it colors a
// conversation without ever becoming the whole of one.
//
// One district is edited at a time -- pick a district, its lore loads
// into the form, "Save district lore" replaces the whole row (matches how
// `district_lore_svc.upsert_lore` always replaces every field together,
// same shape as `affliction_types_svc.update_type`'s cure/auto-apply
// pair). Victors & mentors are a separate, always-visible list under the
// same district since they're per-entry CRUD, not a single row to resave.
//
// Below the per-district sections, two nation-wide ones (`panemHistoryPanel`/
// `worldLorePanel`) cover `panem_shared.lore`'s `PanemHistoryEntry`/
// `WorldLoreSettings` -- context every NPC in every district can draw on,
// not scoped to any one of them, previously only manageable via `/staff
// lore history-add|remove|list`/`au-set|au-show`.
//
// Every textarea on this tab (district notes and both nation-wide panels
// alike) auto-expands to fit its content via `resizeTextarea`/
// `setTextareaValue` below, rather than scrolling within a fixed-height box.
import { el, fetchJson, setStatusText } from "./_shared.js?v=7";

const DISTRICT_IDS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];

function districtLabel(ctx, id) {
  return ctx.districtName ? ctx.districtName(id) : id === 0 ? "The Capitol" : `District ${id}`;
}

function fieldRow(labelText, inputEl) {
  return el("div", { class: "field-row" }, el("label", { text: labelText }), inputEl);
}

// Grows `t` to fit its current content instead of leaving staff to scroll
// within a tiny fixed box to read/edit what they already wrote -- the
// classic auto-expanding-textarea trick (reset to `auto` first so a
// shrinking edit doesn't get stuck at its old, taller height, since
// `scrollHeight` never reports smaller than the box's current rendered
// height otherwise). Exported as its own function (rather than folded
// silently into an `input` listener alone) since setting `.value`
// programmatically -- `applyLore`, loading a fresh district -- never
// fires that event, so callers doing that must resize by hand too.
function resizeTextarea(t) {
  t.style.height = "auto";
  t.style.height = `${t.scrollHeight}px`;
}

function textarea(value, placeholder, rows = 3) {
  const t = el("textarea", { rows: String(rows), placeholder: placeholder || "", class: "auto-textarea" });
  t.value = value || "";
  t.addEventListener("input", () => resizeTextarea(t));
  return t;
}

function setTextareaValue(t, value) {
  t.value = value || "";
  resizeTextarea(t);
}

function lorePanel(ctx) {
  const resultLine = el("p", { class: "result-line" });
  const statusEl = el("p", { class: "tab-status" }, "Loading…");

  const districtSelect = el(
    "select",
    { class: "field-input" },
    ...DISTRICT_IDS.map((id) => el("option", { value: String(id) }, `${id}: ${districtLabel(ctx, id)}`))
  );
  districtSelect.value = "1";

  const classificationSelect = el(
    "select",
    { class: "field-input" },
    el("option", { value: "" }, "-- unset --"),
    el("option", { value: "inner" }, "Inner"),
    el("option", { value: "outlier" }, "Outlier")
  );

  const adjectivesInput = el("input", {
    type: "text",
    placeholder: "Brash, Arrogant, Dreary, Excitable, ...",
  });
  const accentNotes = textarea(
    "",
    "Example lines, phonetic spellings, or a linguistic term (e.g. \"rhotic, clipped consonants\")."
  );
  const urbanRuralNotes = textarea("", "How urban vs. rural life shapes attitudes and speech here.");
  const academyNameInput = el("input", { type: "text", placeholder: "e.g. \"The Salvos Academy\"" });
  const academyNotes = textarea("", "How this district's career academy (if any) works.");
  const gamesHistory = textarea(
    "",
    "Free-text summary: how this district's tributes have done across the Games.",
    4
  );
  const regimeNotes = textarea(
    "",
    "How this district experiences gamemakers/the current regime. (To flag a specific character as " +
      "Gamemaker/President/Vice President, use /staff give position -- this is scene-setting notes only.)"
  );
  const miscNotes = textarea("", "Anything else that doesn't fit above.", 4);

  const opinionInputs = new Map();
  const opinionsWrap = el("div", { class: "district-opinions" });
  for (const otherId of DISTRICT_IDS) {
    const input = el("input", { type: "text", placeholder: "How this district sees them" });
    opinionInputs.set(otherId, input);
  }

  function rebuildOpinionsWrap(selfId) {
    opinionsWrap.innerHTML = "";
    for (const otherId of DISTRICT_IDS) {
      if (otherId === selfId) continue;
      opinionsWrap.append(
        el(
          "div",
          { class: "field-row" },
          el("label", { text: districtLabel(ctx, otherId) }),
          opinionInputs.get(otherId)
        )
      );
    }
  }

  function applyLore(districtId, lore) {
    classificationSelect.value = lore.classification || "";
    adjectivesInput.value = (lore.adjectives || []).join(", ");
    setTextareaValue(accentNotes, lore.accent_notes);
    setTextareaValue(urbanRuralNotes, lore.urban_rural_notes);
    academyNameInput.value = lore.academy_name || "";
    setTextareaValue(academyNotes, lore.academy_notes);
    setTextareaValue(gamesHistory, lore.games_history);
    setTextareaValue(regimeNotes, lore.regime_notes);
    setTextareaValue(miscNotes, lore.misc_notes);
    rebuildOpinionsWrap(districtId);
    for (const [otherId, input] of opinionInputs) {
      input.value = (lore.opinions && lore.opinions[String(otherId)]) || "";
    }
  }

  async function loadDistrict() {
    resultLine.textContent = "";
    setStatusText(statusEl, "Loading…");
    const districtId = Number(districtSelect.value);
    try {
      const lore = await fetchJson(
        `/activity/dashboard/history/districts/${districtId}?discord_id=${ctx.discordId()}`
      );
      applyLore(districtId, lore);
      setStatusText(statusEl, "");
      return lore;
    } catch (err) {
      setStatusText(statusEl, `Could not load district history: ${err.message}`, { error: true });
      rebuildOpinionsWrap(districtId);
      return null;
    }
  }

  districtSelect.addEventListener("change", () => {
    loadDistrict().then((lore) => peoplePanelRef.refresh(Number(districtSelect.value), lore));
  });

  const saveBtn = el("button", { class: "btn primary", type: "button" }, "Save district lore");
  saveBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    const districtId = Number(districtSelect.value);
    const opinions = {};
    for (const [otherId, input] of opinionInputs) {
      const value = input.value.trim();
      if (value) opinions[String(otherId)] = value;
    }
    try {
      const lore = await fetchJson(`/activity/dashboard/history/districts/${districtId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          classification: classificationSelect.value || null,
          adjectives: adjectivesInput.value
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
          accent_notes: accentNotes.value,
          urban_rural_notes: urbanRuralNotes.value,
          academy_name: academyNameInput.value.trim() || null,
          academy_notes: academyNotes.value,
          games_history: gamesHistory.value,
          regime_notes: regimeNotes.value,
          opinions,
          misc_notes: miscNotes.value,
        }),
      });
      applyLore(districtId, lore);
      resultLine.className = "result-line win";
      resultLine.textContent = `Saved ${districtLabel(ctx, districtId)}'s history.`;
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  const panel = el(
    "div",
    { class: "panel" },
    el("h2", { text: "District History" }),
    el(
      "p",
      { class: "tab-status" },
      "Context NPCs from this district can draw on -- a short summary rides in their dialogue " +
        "prompts, but this is reference material, not a script they'll recite."
    ),
    el("div", { class: "field-row" }, el("label", { text: "District" }), districtSelect),
    statusEl,
    fieldRow("Classification", classificationSelect),
    fieldRow("Adjectives (comma-separated)", adjectivesInput),
    fieldRow("Accent notes", accentNotes),
    fieldRow("Urban vs. rural attitudes/speech", urbanRuralNotes),
    fieldRow("Career academy name", academyNameInput),
    fieldRow("Career academy notes", academyNotes),
    fieldRow("Games performance & tribute history", gamesHistory),
    fieldRow("Gamemakers / current regime notes", regimeNotes),
    el("h3", { text: "Opinions of other districts" }),
    opinionsWrap,
    fieldRow("Miscellaneous", miscNotes),
    el("div", { class: "field-row" }, saveBtn),
    resultLine
  );

  // Filled in by mount() once both panels exist, so the district select's
  // change handler above can also refresh the people list below without
  // either panel needing to know how the other one is built.
  const peoplePanelRef = { refresh: () => {} };

  return {
    root: panel,
    setPeoplePanel: (p) => (peoplePanelRef.refresh = p.refresh),
    getDistrictId: () => Number(districtSelect.value),
    loadDistrict,
  };
}

function personRow(ctx, person, { onChanged }) {
  const resultLine = el("p", { class: "result-line" });

  const roleSelect = el(
    "select",
    { class: "field-input" },
    el("option", { value: "victor" }, "Victor"),
    el("option", { value: "mentor" }, "Mentor")
  );
  roleSelect.value = person.role;
  const nameInput = el("input", { type: "text", value: person.name, maxlength: "80" });
  const characterIdInput = el("input", {
    type: "number",
    placeholder: "Character ID (optional)",
    value: person.character_id != null ? String(person.character_id) : "",
  });
  const activeInput = el("input", { type: "checkbox" });
  activeInput.checked = person.is_active;
  const notesInput = el("input", { type: "text", value: person.notes, maxlength: "1000" });

  const saveBtn = el("button", { class: "btn", type: "button" }, "Save");
  const deleteBtn = el("button", { class: "btn secondary", type: "button" }, "Delete");

  saveBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    const rawCharacterId = characterIdInput.value.trim();
    try {
      await fetchJson(`/activity/dashboard/history/people/${person.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          role: roleSelect.value,
          name: nameInput.value.trim(),
          character_id: rawCharacterId ? Number(rawCharacterId) : null,
          character_id_set: true,
          is_active: activeInput.checked,
          notes: notesInput.value,
        }),
      });
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  deleteBtn.addEventListener("click", async () => {
    if (!confirm(`Remove ${person.name} from this district's victor/mentor list?`)) return;
    try {
      await fetchJson(`/activity/dashboard/history/people/${person.id}/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId() }),
      });
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "layer-category" },
    el(
      "div",
      { class: "field-row" },
      roleSelect,
      nameInput,
      characterIdInput,
      el("label", {}, activeInput, " Active"),
      saveBtn,
      deleteBtn
    ),
    el(
      "div",
      { class: "field-row" },
      el("label", { text: "Notes" }),
      notesInput
    ),
    person.character_name
      ? el("p", { class: "tab-status" }, `Linked to character: ${person.character_name}`)
      : null,
    resultLine
  );
}

function peoplePanel(ctx, getDistrictId) {
  const listEl = el("div", {});
  const statusEl = el("p", { class: "tab-status" }, "");

  const newRoleSelect = el(
    "select",
    { class: "field-input" },
    el("option", { value: "victor" }, "Victor"),
    el("option", { value: "mentor" }, "Mentor")
  );
  const newNameInput = el("input", { type: "text", placeholder: "Name", maxlength: "80" });
  const newCharacterIdInput = el("input", { type: "number", placeholder: "Character ID (optional)" });
  const newNotesInput = el("input", { type: "text", placeholder: "Notes" });
  const newBtn = el("button", { class: "btn primary", type: "button" }, "Add");
  const newResult = el("p", { class: "result-line" });

  async function refresh(districtId, lore) {
    listEl.innerHTML = "";
    const people = lore ? lore.people || [] : [];
    if (people.length === 0) {
      listEl.append(el("p", { class: "tab-status" }, "No victors or mentors on record yet."));
    }
    for (const person of people) {
      listEl.append(personRow(ctx, person, { onChanged: () => refresh(getDistrictId(), lore) }));
    }
  }

  newBtn.addEventListener("click", async () => {
    newResult.textContent = "";
    const name = newNameInput.value.trim();
    if (!name) {
      newResult.className = "result-line lose";
      newResult.textContent = "Enter a name.";
      return;
    }
    const districtId = getDistrictId();
    const rawCharacterId = newCharacterIdInput.value.trim();
    try {
      await fetchJson(`/activity/dashboard/history/districts/${districtId}/people`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          role: newRoleSelect.value,
          name,
          character_id: rawCharacterId ? Number(rawCharacterId) : null,
          is_active: true,
          notes: newNotesInput.value,
        }),
      });
      newNameInput.value = "";
      newCharacterIdInput.value = "";
      newNotesInput.value = "";
      const lore = await fetchJson(
        `/activity/dashboard/history/districts/${districtId}?discord_id=${ctx.discordId()}`
      );
      await refresh(districtId, lore);
    } catch (err) {
      newResult.className = "result-line lose";
      newResult.textContent = err.message;
    }
  });

  const root = el(
    "div",
    { class: "panel" },
    el("h2", { text: "Victors & Mentors" }),
    statusEl,
    listEl,
    el(
      "div",
      { class: "panel" },
      el("h3", { text: "Add victor / mentor" }),
      el("div", { class: "field-row" }, el("label", { text: "Role" }), newRoleSelect),
      el("div", { class: "field-row" }, el("label", { text: "Name" }), newNameInput),
      el(
        "div",
        { class: "field-row" },
        el("label", { text: "Character ID" }),
        newCharacterIdInput
      ),
      el("div", { class: "field-row" }, el("label", { text: "Notes" }), newNotesInput),
      el("div", { class: "field-row" }, newBtn),
      newResult
    )
  );

  return { root, refresh: (districtId, lore) => refresh(districtId, lore) };
}

// -------------------------------------------------- Panem-wide history

// `panem_shared.lore.PanemHistoryEntry` -- unlike everything in `lorePanel`
// above (one district's own context), these facts aren't scoped to any
// district at all: every NPC nationwide can draw on one once a proxied
// line matches one of its keywords (`panem_bot.services.dialogue`).
// Previously only manageable via `/staff lore history-add|remove|list`.
function panemHistoryEntryRow(ctx, entry, { onDeleted }) {
  const resultLine = el("p", { class: "result-line" });
  const deleteBtn = el("button", { class: "btn secondary", type: "button" }, "Delete");

  deleteBtn.addEventListener("click", async () => {
    if (!confirm("Remove this history entry for good?")) return;
    try {
      await fetchJson(`/activity/dashboard/history/panem-history/${entry.id}/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId() }),
      });
      onDeleted();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "layer-category" },
    el(
      "div",
      { class: "field-row" },
      el("span", {}, `[${entry.keywords.join(", ")}]`),
      deleteBtn
    ),
    el("p", {}, entry.text),
    resultLine
  );
}

function panemHistoryPanel(ctx) {
  const statusEl = el("p", { class: "tab-status" }, "Loading…");
  const listEl = el("div", {});
  const keywordsInput = el("input", {
    type: "text",
    placeholder: "Comma-separated keywords, e.g. \"dark days, district 13\"",
  });
  const textInput = textarea("", "The fact itself, as an NPC should understand it.", 3);
  const addBtn = el("button", { class: "btn primary", type: "button" }, "Add");
  const addResult = el("p", { class: "result-line" });

  async function refresh() {
    setStatusText(statusEl, "Loading…");
    try {
      const body = await fetchJson(
        `/activity/dashboard/history/panem-history?discord_id=${ctx.discordId()}`
      );
      listEl.innerHTML = "";
      if (body.entries.length === 0) {
        listEl.append(el("p", { class: "tab-status" }, "No history entries yet."));
      }
      for (const entry of body.entries) {
        listEl.append(panemHistoryEntryRow(ctx, entry, { onDeleted: refresh }));
      }
      setStatusText(statusEl, "");
    } catch (err) {
      setStatusText(statusEl, `Could not load Panem-wide history: ${err.message}`, { error: true });
    }
  }

  addBtn.addEventListener("click", async () => {
    addResult.textContent = "";
    try {
      await fetchJson("/activity/dashboard/history/panem-history", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          keywords: keywordsInput.value.split(","),
          text: textInput.value,
        }),
      });
      keywordsInput.value = "";
      setTextareaValue(textInput, "");
      await refresh();
    } catch (err) {
      addResult.className = "result-line lose";
      addResult.textContent = err.message;
    }
  });

  const root = el(
    "div",
    { class: "panel" },
    el("h2", { text: "Panem-Wide History" }),
    el(
      "p",
      { class: "tab-status" },
      "Facts every NPC in every district can draw on -- not scoped to one district the way the " +
        "section above is. Brought up only when a keyword matches what's actually being said."
    ),
    statusEl,
    listEl,
    el(
      "div",
      { class: "panel" },
      el("h3", { text: "Add a history fact" }),
      fieldRow("Keywords", keywordsInput),
      fieldRow("Fact", textInput),
      el("div", { class: "field-row" }, addBtn),
      addResult
    )
  );

  refresh();
  return { root };
}

// ---------------------------------------------- Alternate Universe notes

// `panem_shared.lore.WorldLoreSettings` -- unconditional background every
// NPC in the nation keeps in mind (unlike the keyword-gated facts above,
// this is always included in a dialogue request). Previously only
// manageable via `/staff lore au-set|au-show`.
function worldLorePanel(ctx) {
  const statusEl = el("p", { class: "tab-status" }, "Loading…");
  const notesInput = textarea("", "How this version of Panem's canon diverges, ongoing world-shaping facts, etc.", 4);
  const saveBtn = el("button", { class: "btn primary", type: "button" }, "Save");
  const resultLine = el("p", { class: "result-line" });

  async function load() {
    setStatusText(statusEl, "Loading…");
    try {
      const body = await fetchJson(
        `/activity/dashboard/history/world-lore?discord_id=${ctx.discordId()}`
      );
      setTextareaValue(notesInput, body.alternate_universe_notes);
      setStatusText(statusEl, "");
    } catch (err) {
      setStatusText(statusEl, `Could not load Alternate Universe notes: ${err.message}`, {
        error: true,
      });
    }
  }

  saveBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const body = await fetchJson("/activity/dashboard/history/world-lore", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          alternate_universe_notes: notesInput.value,
        }),
      });
      setTextareaValue(notesInput, body.alternate_universe_notes);
      resultLine.className = "result-line win";
      resultLine.textContent = "Saved.";
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  const root = el(
    "div",
    { class: "panel" },
    el("h2", { text: "Alternate Universe Notes" }),
    el(
      "p",
      { class: "tab-status" },
      "Free-form notes every NPC in the nation keeps in mind, always -- not something recalled " +
        "on cue like the history facts above."
    ),
    statusEl,
    fieldRow("Notes", notesInput),
    el("div", { class: "field-row" }, saveBtn),
    resultLine
  );

  load();
  return { root };
}

export function mount(root, ctx) {
  const lore = lorePanel(ctx);
  const people = peoplePanel(ctx, lore.getDistrictId);
  lore.setPeoplePanel(people);
  const panemHistory = panemHistoryPanel(ctx);
  const worldLore = worldLorePanel(ctx);
  root.append(lore.root, people.root, panemHistory.root, worldLore.root);
  lore.loadDistrict().then((loreData) => people.refresh(lore.getDistrictId(), loreData));
}
