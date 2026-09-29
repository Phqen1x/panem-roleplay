// The "Staff" tab: only offered by `app.js` when `/identify` reports
// `is_staff` -- the dashboard equivalent of `/staff jail`, plus the admin
// panel for the Picrew-style character customizer's layer catalog
// (`panem_shared.layers`). Acts on any character by exact name (not the
// logged-in player's own characters -- `ctx.characters()` doesn't apply
// here), same as the bot command's own `character:` field. Every write is
// re-checked server-side (`build_staff_router`'s `_require_staff`)
// regardless of what `/identify` said, so this tab being reachable is
// convenience, not the actual gate.
//
// The layers panel is the actual upload/marking tool this feature exists
// for: staff create a category (a name + a stack position), then upload
// images into it one at a time, each with its own display name. There is
// no seed data -- the catalog starts empty and stays that way until staff
// use this panel, which is built to render sensibly on zero categories
// (just the "New category" form, nothing else) rather than assuming
// something is already there.
import { fetchJson, el, dropdown, setStatusText } from "./_shared.js?v=7";

// `panem_shared.enums.AfflictionStat`'s five values -- the only stats a
// staff-authored affliction type's cure/auto-apply condition can name.
const AFFLICTION_STAT_OPTIONS = [
  { value: "", label: "-- none --" },
  { value: "health", label: "Health" },
  { value: "hunger", label: "Hunger" },
  { value: "thirst", label: "Thirst" },
  { value: "fatigue", label: "Fatigue" },
  { value: "sanity", label: "Sanity" },
];

function jailPanel(ctx) {
  const resultLine = el("p", { class: "result-line" });
  const nameInput = el("input", { type: "text", placeholder: "Character name" });
  const ticksInput = el("input", { type: "number", value: "50", min: "1" });
  const reasonInput = el("input", { type: "text", placeholder: "Optional reason" });
  const jailBtn = el("button", { class: "btn", type: "button" }, "Jail");

  jailBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    const characterName = nameInput.value.trim();
    const ticks = Number(ticksInput.value);
    if (!characterName || !Number.isFinite(ticks) || ticks < 1) {
      resultLine.className = "result-line lose";
      resultLine.textContent = "Enter a character name and a positive tick count.";
      return;
    }
    try {
      const body = await fetchJson("/activity/dashboard/staff/jail", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          character_name: characterName,
          ticks,
          reason: reasonInput.value.trim() || null,
        }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent =
        body.prior_bonus_ticks > 0
          ? `${body.character_name} jailed for ${body.applied_ticks} ticks ` +
            `(${body.base_ticks} entered + ${body.prior_bonus_ticks} for repeat priors) ` +
            `(until tick ${body.jailed_until_tick}).`
          : `${body.character_name} jailed for ${body.applied_ticks} ticks ` +
            `(until tick ${body.jailed_until_tick}).`;
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Jail a character" }),
    el("div", { class: "field-row" }, el("label", { text: "Character" }), nameInput),
    el("div", { class: "field-row" }, el("label", { text: "Ticks" }), ticksInput),
    el(
      "p",
      { class: "tab-status" },
      "The actual sentence is this base, plus 6 ticks per prior jailing (repeat offenders serve longer)."
    ),
    el("div", { class: "field-row" }, el("label", { text: "Reason" }), reasonInput),
    el("div", { class: "field-row" }, jailBtn),
    resultLine
  );
}

function marketStockPanel(ctx) {
  const resultLine = el("p", { class: "result-line" });

  const districtOptions = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map((id) => {
    const name = ctx.districtName ? ctx.districtName(id) : (id === 0 ? "The Capitol" : `District ${id}`);
    return el("option", { value: String(id) }, `${id}: ${name}`);
  });
  const districtSelect = el("select", { class: "field-input" }, ...districtOptions);
  districtSelect.value = "1";

  const goodIdInput = el("input", { type: "text", placeholder: "e.g. grain, wild_game, coal" });
  const qtyInput = el("input", { type: "number", value: "10", min: "1" });
  const addBtn = el("button", { class: "btn primary", type: "button" }, "Add Stock");

  addBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    const goodId = goodIdInput.value.trim();
    const qty = Number(qtyInput.value);
    if (!goodId || !Number.isFinite(qty) || qty <= 0) {
      resultLine.className = "result-line lose";
      resultLine.textContent = "Enter a good id and a positive quantity.";
      return;
    }
    try {
      const body = await fetchJson("/activity/dashboard/staff/market/add-stock", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          district_id: Number(districtSelect.value),
          good_id: goodId,
          qty,
        }),
      });
      resultLine.className = "result-line win";
      const dName = ctx.districtName ? ctx.districtName(body.district_id) : `District ${body.district_id}`;
      resultLine.textContent =
        `Added ${body.qty_added} ${body.good_name} to ${dName}'s market -- ` +
        `now ${body.new_supply} in stock.`;
      goodIdInput.value = "";
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Add Market Stock" }),
    el(
      "p",
      { class: "tab-status" },
      "Tops up a district's current stock of a good directly, for legal or illicit goods alike " +
        "-- doesn't wait on the sim's own daily supply update."
    ),
    el("div", { class: "field-row" }, el("label", { text: "District" }), districtSelect),
    el("div", { class: "field-row" }, el("label", { text: "Good id" }), goodIdInput),
    el("div", { class: "field-row" }, el("label", { text: "Quantity" }), qtyInput),
    el("div", { class: "field-row" }, addBtn),
    resultLine
  );
}

function optionRow(ctx, category, option, { onChanged }) {
  const resultLine = el("p", { class: "result-line" });
  const deleteBtn = el("button", { class: "btn secondary", type: "button" }, "Delete");
  deleteBtn.addEventListener("click", async () => {
    if (!confirm(`Delete "${option.name}"? This can't be undone.`)) return;
    try {
      await fetchJson(`/activity/dashboard/staff/layers/options/${option.id}/delete`, {
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
    { class: "layer-option-row" },
    el("img", { class: "layer-thumb", src: option.image_url, alt: "" }),
    el("span", { class: "tab-status" }, option.name),
    deleteBtn,
    resultLine
  );
}

function categoryPanel(ctx, category, { onChanged }) {
  const resultLine = el("p", { class: "result-line" });

  const nameInput = el("input", { type: "text", value: category.name, maxlength: "64" });
  const zIndexInput = el("input", { type: "number", value: String(category.z_index) });
  const saveBtn = el("button", { class: "btn secondary", type: "button" }, "Save");
  saveBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      await fetchJson(`/activity/dashboard/staff/layers/categories/${category.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          name: nameInput.value,
          z_index: Number(zIndexInput.value),
        }),
      });
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  const deleteBtn = el("button", { class: "btn secondary", type: "button" }, "Delete category");
  deleteBtn.addEventListener("click", async () => {
    if (!confirm(`Delete "${category.name}" and every image in it? This can't be undone.`)) return;
    try {
      await fetchJson(`/activity/dashboard/staff/layers/categories/${category.id}/delete`, {
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

  const uploadName = el("input", { type: "text", placeholder: "Option name", maxlength: "64" });
  const uploadFile = el("input", { type: "file", accept: "image/png,image/webp,image/gif" });
  const uploadResult = el("p", { class: "result-line" });
  const uploadBtn = el("button", { class: "btn", type: "button" }, "Upload");
  uploadBtn.addEventListener("click", async () => {
    uploadResult.textContent = "";
    const file = uploadFile.files[0];
    const name = uploadName.value.trim();
    if (!file || !name) {
      uploadResult.className = "result-line lose";
      uploadResult.textContent = "Pick an image and give it a name.";
      return;
    }
    const form = new FormData();
    form.append("discord_id", String(ctx.discordId()));
    form.append("name", name);
    form.append("file", file);
    try {
      // No Content-Type header here -- the browser sets the multipart
      // boundary itself from the FormData body, and overriding it breaks
      // the upload.
      await fetchJson(`/activity/dashboard/staff/layers/categories/${category.id}/options`, {
        method: "POST",
        body: form,
      });
      uploadName.value = "";
      uploadFile.value = "";
      onChanged();
    } catch (err) {
      uploadResult.className = "result-line lose";
      uploadResult.textContent = err.message;
    }
  });

  const optionsList = el(
    "div",
    { class: "layer-option-list" },
    ...(category.options.length > 0
      ? category.options.map((option) => optionRow(ctx, category, option, { onChanged }))
      : [el("p", { class: "tab-status" }, "No images uploaded yet.")])
  );

  return el(
    "div",
    { class: "panel" },
    el("div", { class: "field-row" }, el("label", { text: "Name" }), nameInput),
    el("div", { class: "field-row" }, el("label", { text: "Stack order" }), zIndexInput),
    el("div", { class: "field-row" }, saveBtn, deleteBtn),
    resultLine,
    el("h3", { text: "Images" }),
    optionsList,
    el("div", { class: "field-row" }, el("label", { text: "New image" }), uploadName),
    el("div", { class: "field-row" }, el("label", { text: "File" }), uploadFile),
    el("div", { class: "field-row" }, uploadBtn),
    uploadResult
  );
}

function layersPanel(ctx) {
  const root = el("div", {});
  const listEl = el("div", {});
  const statusEl = el("p", { class: "tab-status" });

  const newNameInput = el("input", { type: "text", placeholder: "e.g. Hair, Eyes, Base", maxlength: "64" });
  const newZIndexInput = el("input", { type: "number", value: "0" });
  const newResult = el("p", { class: "result-line" });
  const newBtn = el("button", { class: "btn", type: "button" }, "Add category");
  newBtn.addEventListener("click", async () => {
    newResult.textContent = "";
    const name = newNameInput.value.trim();
    if (!name) {
      newResult.className = "result-line lose";
      newResult.textContent = "Give the category a name.";
      return;
    }
    try {
      await fetchJson("/activity/dashboard/staff/layers/categories", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          name,
          z_index: Number(newZIndexInput.value) || 0,
        }),
      });
      newNameInput.value = "";
      await refresh();
    } catch (err) {
      newResult.className = "result-line lose";
      newResult.textContent = err.message;
    }
  });

  async function refresh() {
    listEl.innerHTML = "";
    setStatusText(statusEl, "Loading…");
    try {
      const body = await fetchJson("/activity/dashboard/layers");
      setStatusText(statusEl, "");
      if (body.categories.length === 0) {
        listEl.append(
          el("p", { class: "tab-status" }, "No categories yet -- add one below to get started.")
        );
      }
      for (const category of body.categories) {
        listEl.append(
          el(
            "div",
            { class: "layer-category" },
            el("h2", { text: `${category.name} (order ${category.z_index})` }),
            categoryPanel(ctx, category, { onChanged: refresh })
          )
        );
      }
    } catch (err) {
      setStatusText(statusEl, `Could not load layer categories: ${err.message}`, { error: true });
    }
  }

  root.append(
    el("h2", { text: "Character customizer layers" }),
    statusEl,
    listEl,
    el(
      "div",
      { class: "panel" },
      el("h3", { text: "New category" }),
      el("div", { class: "field-row" }, el("label", { text: "Name" }), newNameInput),
      el(
        "div",
        { class: "field-row" },
        el("label", { text: "Stack order" }),
        newZIndexInput
      ),
      el("div", { class: "field-row" }, newBtn),
      newResult
    )
  );

  refresh();
  return root;
}

function mottosPanel(ctx) {
  const resultLine = el("p", { class: "result-line" });

  const districtOptions = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map((id) => {
    const name = ctx.districtName ? ctx.districtName(id) : (id === 0 ? "The Capitol" : `District ${id}`);
    return el("option", { value: String(id) }, `${id}: ${name}`);
  });

  const districtSelect = el("select", { class: "field-input" }, ...districtOptions);
  districtSelect.value = "1";

  const mottoInput = el("input", {
    type: "text",
    maxlength: "120",
    placeholder: "District motto / declaration",
    value: ctx.districtMotto ? ctx.districtMotto(1) : "Excellence Endures",
  });

  districtSelect.addEventListener("change", () => {
    resultLine.textContent = "";
    resultLine.className = "result-line";
    const did = Number(districtSelect.value);
    mottoInput.value = ctx.districtMotto ? ctx.districtMotto(did) : "";
  });

  const saveBtn = el("button", { class: "btn primary", type: "button" }, "Save Motto");
  saveBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    const districtId = Number(districtSelect.value);
    const motto = mottoInput.value.trim();
    if (!motto) {
      resultLine.className = "result-line lose";
      resultLine.textContent = "Please enter a motto (up to 120 characters).";
      return;
    }
    try {
      const body = await fetchJson("/activity/dashboard/staff/districts/motto", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          district_id: districtId,
          motto,
        }),
      });
      resultLine.className = "result-line win";
      const dName = ctx.districtName ? ctx.districtName(districtId) : `District ${districtId}`;
      resultLine.textContent = `Saved motto for ${dName}: "${body.motto}".`;
      window.dispatchEvent(
        new CustomEvent("panem:motto-updated", {
          detail: { district_id: districtId, motto: body.motto },
        })
      );
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "District Mottos & Declarations" }),
    el(
      "p",
      { class: "tab-status" },
      "Staff editable mottos displayed on footer telemetry and district plaques across Panem."
    ),
    el("div", { class: "field-row" }, el("label", { text: "District" }), districtSelect),
    el("div", { class: "field-row" }, el("label", { text: "Motto" }), mottoInput),
    el("div", { class: "field-row" }, saveBtn),
    resultLine
  );
}

function afflictionTypeRow(ctx, afflictionType, { onChanged }) {
  const resultLine = el("p", { class: "result-line" });

  const nameInput = el("input", { type: "text", value: afflictionType.name, maxlength: "64" });
  const descriptionInput = el(
    "input",
    { type: "text", value: afflictionType.description, maxlength: "400" }
  );
  const permanentInput = el("input", { type: "checkbox" });
  permanentInput.checked = afflictionType.is_permanent;

  const cureStatSelect = dropdown(AFFLICTION_STAT_OPTIONS);
  cureStatSelect.value = afflictionType.cure_stat || "";
  const cureRow = el(
    "div",
    { class: "field-row" },
    el("label", { text: "Cure stat" }),
    cureStatSelect
  );
  const cureThresholdInput = el("input", {
    type: "number",
    step: "0.1",
    value: afflictionType.cure_threshold ?? "",
  });

  const autoStatSelect = dropdown(AFFLICTION_STAT_OPTIONS);
  autoStatSelect.value = afflictionType.auto_apply_stat || "";
  const autoThresholdInput = el("input", {
    type: "number",
    step: "0.1",
    value: afflictionType.auto_apply_threshold ?? "",
  });

  function syncPermanentDisabled() {
    const disabled = permanentInput.checked;
    cureStatSelect.disabled = disabled;
    cureThresholdInput.disabled = disabled;
  }
  permanentInput.addEventListener("change", syncPermanentDisabled);
  syncPermanentDisabled();

  const saveBtn = el("button", { class: "btn secondary", type: "button" }, "Save");
  saveBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    const isPermanent = permanentInput.checked;
    const cureStat = isPermanent ? null : cureStatSelect.value || null;
    const cureThreshold = cureStat ? Number(cureThresholdInput.value) : null;
    const autoStat = autoStatSelect.value || null;
    const autoThreshold = autoStat ? Number(autoThresholdInput.value) : null;
    try {
      await fetchJson(`/activity/dashboard/staff/affliction-types/${afflictionType.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          name: nameInput.value,
          description: descriptionInput.value,
          is_permanent: isPermanent,
          cure_stat: cureStat,
          cure_threshold: cureThreshold,
          auto_apply_stat: autoStat,
          auto_apply_threshold: autoThreshold,
        }),
      });
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  const deleteBtn = el("button", { class: "btn secondary", type: "button" }, "Delete");
  deleteBtn.addEventListener("click", async () => {
    if (!confirm(`Delete "${afflictionType.name}"? This can't be undone.`)) return;
    try {
      await fetchJson(`/activity/dashboard/staff/affliction-types/${afflictionType.id}/delete`, {
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
    { class: "panel" },
    el("div", { class: "field-row" }, el("label", { text: "Name" }), nameInput),
    el("div", { class: "field-row" }, el("label", { text: "Description" }), descriptionInput),
    el(
      "div",
      { class: "field-row" },
      el("label", { text: "Permanent (incurable)" }),
      permanentInput
    ),
    cureRow,
    el(
      "div",
      { class: "field-row" },
      el("label", { text: "Cure threshold (rises above)" }),
      cureThresholdInput
    ),
    el(
      "div",
      { class: "field-row" },
      el("label", { text: "Auto-apply stat (Simulation mode only)" }),
      autoStatSelect
    ),
    el(
      "div",
      { class: "field-row" },
      el("label", { text: "Auto-apply threshold (falls beneath)" }),
      autoThresholdInput
    ),
    el("div", { class: "field-row" }, saveBtn, deleteBtn),
    resultLine
  );
}

function afflictionTypesPanel(ctx) {
  const root = el("div", {});
  const listEl = el("div", {});
  const statusEl = el("p", { class: "tab-status" });

  const newNameInput = el("input", { type: "text", placeholder: "e.g. Broken Leg", maxlength: "64" });
  const newDescriptionInput = el("input", {
    type: "text",
    placeholder: "What it is, how it's usually caused",
    maxlength: "400",
  });
  const newPermanentInput = el("input", { type: "checkbox" });
  const newCureStatSelect = dropdown(AFFLICTION_STAT_OPTIONS);
  const newCureThresholdInput = el("input", { type: "number", step: "0.1" });
  const newAutoStatSelect = dropdown(AFFLICTION_STAT_OPTIONS);
  const newAutoThresholdInput = el("input", { type: "number", step: "0.1" });
  const newResult = el("p", { class: "result-line" });
  const newBtn = el("button", { class: "btn", type: "button" }, "Add affliction type");

  function syncNewPermanentDisabled() {
    const disabled = newPermanentInput.checked;
    newCureStatSelect.disabled = disabled;
    newCureThresholdInput.disabled = disabled;
  }
  newPermanentInput.addEventListener("change", syncNewPermanentDisabled);
  syncNewPermanentDisabled();

  newBtn.addEventListener("click", async () => {
    newResult.textContent = "";
    const name = newNameInput.value.trim();
    if (!name) {
      newResult.className = "result-line lose";
      newResult.textContent = "Give the affliction a name.";
      return;
    }
    const isPermanent = newPermanentInput.checked;
    const cureStat = isPermanent ? null : newCureStatSelect.value || null;
    const cureThreshold = cureStat ? Number(newCureThresholdInput.value) : null;
    const autoStat = newAutoStatSelect.value || null;
    const autoThreshold = autoStat ? Number(newAutoThresholdInput.value) : null;
    try {
      await fetchJson("/activity/dashboard/staff/affliction-types", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          name,
          description: newDescriptionInput.value.trim(),
          is_permanent: isPermanent,
          cure_stat: cureStat,
          cure_threshold: cureThreshold,
          auto_apply_stat: autoStat,
          auto_apply_threshold: autoThreshold,
        }),
      });
      newNameInput.value = "";
      newDescriptionInput.value = "";
      newPermanentInput.checked = false;
      syncNewPermanentDisabled();
      await refresh();
    } catch (err) {
      newResult.className = "result-line lose";
      newResult.textContent = err.message;
    }
  });

  async function refresh() {
    listEl.innerHTML = "";
    setStatusText(statusEl, "Loading…");
    try {
      const body = await fetchJson("/activity/dashboard/affliction-types");
      setStatusText(statusEl, "");
      if (body.types.length === 0) {
        listEl.append(
          el(
            "p",
            { class: "tab-status" },
            "No affliction types yet -- add one below to get started."
          )
        );
      }
      for (const afflictionType of body.types) {
        listEl.append(
          el(
            "div",
            { class: "layer-category" },
            el("h2", {
              text: afflictionType.name + (afflictionType.is_permanent ? " (permanent)" : ""),
            }),
            afflictionTypeRow(ctx, afflictionType, { onChanged: refresh })
          )
        );
      }
    } catch (err) {
      setStatusText(statusEl, `Could not load affliction types: ${err.message}`, { error: true });
    }
  }

  root.append(
    el("h2", { text: "Injuries & afflictions" }),
    statusEl,
    listEl,
    el(
      "div",
      { class: "panel" },
      el("h3", { text: "New affliction type" }),
      el("div", { class: "field-row" }, el("label", { text: "Name" }), newNameInput),
      el(
        "div",
        { class: "field-row" },
        el("label", { text: "Description" }),
        newDescriptionInput
      ),
      el(
        "div",
        { class: "field-row" },
        el("label", { text: "Permanent (incurable)" }),
        newPermanentInput
      ),
      el("div", { class: "field-row" }, el("label", { text: "Cure stat" }), newCureStatSelect),
      el(
        "div",
        { class: "field-row" },
        el("label", { text: "Cure threshold (rises above)" }),
        newCureThresholdInput
      ),
      el(
        "div",
        { class: "field-row" },
        el("label", { text: "Auto-apply stat (Simulation mode only)" }),
        newAutoStatSelect
      ),
      el(
        "div",
        { class: "field-row" },
        el("label", { text: "Auto-apply threshold (falls beneath)" }),
        newAutoThresholdInput
      ),
      el("div", { class: "field-row" }, newBtn),
      newResult
    )
  );

  refresh();
  return root;
}

// ---- Everything else `/staff ...` could do, consolidated into this tab --
// mirrors `dashboard_routes.build_staff_router`'s own comment: every one of
// these panels is the web equivalent of one `panem_bot.cogs.staff.StaffCog`
// subcommand, targeting a character/player by exact name or Discord id the
// same way the bot command's own fields do (there's no ownership check to
// reuse here, same as jailPanel above). `/staff scene ...` and `/staff
// whois` stayed bot-only -- both need a specific Discord channel/thread or
// message link this dashboard has no equivalent of.

const POSITION_OPTIONS = [
  { value: "victor", label: "Victor" },
  { value: "gamemaker", label: "Gamemaker" },
  { value: "governor", label: "Governor" },
  { value: "president", label: "President" },
  { value: "vice_president", label: "Vice President" },
];
const SHIFT_PHASE_OPTIONS = [
  { value: "morning", label: "Morning" },
  { value: "afternoon", label: "Afternoon" },
  { value: "evening", label: "Evening" },
  { value: "night", label: "Night" },
];
const JOB_LEVEL_OPTIONS = [
  { value: "apprentice", label: "Apprentice" },
  { value: "novice", label: "Novice" },
  { value: "journeyman", label: "Journeyman" },
  { value: "master", label: "Master" },
  { value: "expert", label: "Expert" },
];
const GENDER_OPTIONS = [
  { value: "", label: "Random" },
  { value: "male", label: "Male" },
  { value: "female", label: "Female" },
  { value: "nonbinary", label: "Nonbinary" },
];

function districtDropdown(ctx) {
  const options = Array.from({ length: 13 }, (_, id) => ({
    value: String(id),
    label: `${id}: ${ctx.districtName ? ctx.districtName(id) : id === 0 ? "The Capitol" : `District ${id}`}`,
  }));
  return dropdown(options);
}

function givePanel(ctx) {
  const moneyResult = el("p", { class: "result-line" });
  const moneyName = el("input", { type: "text", placeholder: "Character name" });
  const moneyAmount = el("input", { type: "number", value: "0" });
  const moneyBtn = el("button", { class: "btn", type: "button" }, "Give Money");
  moneyBtn.addEventListener("click", async () => {
    moneyResult.textContent = "";
    const name = moneyName.value.trim();
    const amount = Number(moneyAmount.value);
    if (!name || !Number.isFinite(amount) || amount === 0) {
      moneyResult.className = "result-line lose";
      moneyResult.textContent = "Enter a character name and a nonzero amount.";
      return;
    }
    try {
      const body = await fetchJson("/activity/dashboard/staff/give/money", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), character_name: name, amount }),
      });
      moneyResult.className = "result-line win";
      moneyResult.textContent = `${body.character_name} now has ${body.new_balance} money.`;
    } catch (err) {
      moneyResult.className = "result-line lose";
      moneyResult.textContent = err.message;
    }
  });

  const itemResult = el("p", { class: "result-line" });
  const itemName = el("input", { type: "text", placeholder: "Character name" });
  const goodSelect = dropdown([]);
  fetchJson("/activity/dashboard/staff/goods")
    .then((goods) => goodSelect.setOptions(goods.map((g) => ({ value: g.id, label: `${g.name} (${g.id})` }))))
    .catch(() => {});
  const itemQty = el("input", { type: "number", value: "1" });
  const itemBtn = el("button", { class: "btn", type: "button" }, "Give Item");
  itemBtn.addEventListener("click", async () => {
    itemResult.textContent = "";
    const name = itemName.value.trim();
    const qty = Number(itemQty.value);
    if (!name || !goodSelect.value || !Number.isFinite(qty) || qty === 0) {
      itemResult.className = "result-line lose";
      itemResult.textContent = "Enter a character name, pick a good, and a nonzero quantity.";
      return;
    }
    try {
      const body = await fetchJson("/activity/dashboard/staff/give/item", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          character_name: name,
          good_id: goodSelect.value,
          qty,
        }),
      });
      itemResult.className = "result-line win";
      itemResult.textContent = `${body.character_name} now has ${body.new_qty}x ${body.good_name}.`;
    } catch (err) {
      itemResult.className = "result-line lose";
      itemResult.textContent = err.message;
    }
  });

  const positionResult = el("p", { class: "result-line" });
  const positionName = el("input", { type: "text", placeholder: "Character name" });
  const positionSelect = dropdown(POSITION_OPTIONS);
  const positionGrant = el("input", { type: "checkbox" });
  positionGrant.checked = true;
  const positionBtn = el("button", { class: "btn", type: "button" }, "Apply");
  positionBtn.addEventListener("click", async () => {
    positionResult.textContent = "";
    const name = positionName.value.trim();
    if (!name) {
      positionResult.className = "result-line lose";
      positionResult.textContent = "Enter a character name.";
      return;
    }
    try {
      const body = await fetchJson("/activity/dashboard/staff/give/position", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          character_name: name,
          position: positionSelect.value,
          grant: positionGrant.checked,
        }),
      });
      positionResult.className = "result-line win";
      const summary = body.positions.map((p) => p.replaceAll("_", " ")).join(", ") || "none";
      positionResult.textContent = `${body.character_name}'s positions: ${summary}.`;
    } catch (err) {
      positionResult.className = "result-line lose";
      positionResult.textContent = err.message;
    }
  });

  const jobResult = el("p", { class: "result-line" });
  const jobName = el("input", { type: "text", placeholder: "Character name" });
  const jobTitle = el("input", { type: "text", placeholder: "Job title", maxlength: "64" });
  const shiftSelect = dropdown(SHIFT_PHASE_OPTIONS);
  const jobIllicit = el("input", { type: "checkbox" });
  const jobBtn = el("button", { class: "btn", type: "button" }, "Set Job");
  jobBtn.addEventListener("click", async () => {
    jobResult.textContent = "";
    const name = jobName.value.trim();
    const title = jobTitle.value.trim();
    if (!name || !title) {
      jobResult.className = "result-line lose";
      jobResult.textContent = "Enter a character name and a job title.";
      return;
    }
    try {
      const body = await fetchJson("/activity/dashboard/staff/give/job", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          character_name: name,
          job_title: title,
          shift_phase: shiftSelect.value,
          illicit: jobIllicit.checked,
        }),
      });
      jobResult.className = "result-line win";
      jobResult.textContent = `${body.character_name} now works as ${body.job_title} (${body.shift_phase} shift).`;
    } catch (err) {
      jobResult.className = "result-line lose";
      jobResult.textContent = err.message;
    }
  });

  const masteryResult = el("p", { class: "result-line" });
  const masteryName = el("input", { type: "text", placeholder: "Character name" });
  const masteryShifts = el("input", { type: "number", placeholder: "Exact shift count" });
  const masteryLevel = dropdown([{ value: "", label: "-- or jump to a level --" }, ...JOB_LEVEL_OPTIONS]);
  const masteryBtn = el("button", { class: "btn", type: "button" }, "Set Mastery");
  masteryBtn.addEventListener("click", async () => {
    masteryResult.textContent = "";
    const name = masteryName.value.trim();
    const shiftsRaw = masteryShifts.value.trim();
    if (!name || (!shiftsRaw && !masteryLevel.value)) {
      masteryResult.className = "result-line lose";
      masteryResult.textContent = "Enter a character name, and either a shift count or a level.";
      return;
    }
    try {
      const body = await fetchJson("/activity/dashboard/staff/give/mastery", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          character_name: name,
          shifts_completed: shiftsRaw ? Number(shiftsRaw) : null,
          level: shiftsRaw ? null : masteryLevel.value || null,
        }),
      });
      masteryResult.className = "result-line win";
      masteryResult.textContent =
        `${body.character_name} now has ${body.shifts_completed} completed shifts -- ${body.level}.`;
    } catch (err) {
      masteryResult.className = "result-line lose";
      masteryResult.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Give / Modify a Character" }),
    el("h3", { text: "Money" }),
    el("div", { class: "field-row" }, el("label", { text: "Character" }), moneyName),
    el("div", { class: "field-row" }, el("label", { text: "Amount (+/-)" }), moneyAmount),
    el("div", { class: "field-row" }, moneyBtn),
    moneyResult,
    el("h3", { text: "Item" }),
    el("div", { class: "field-row" }, el("label", { text: "Character" }), itemName),
    el("div", { class: "field-row" }, el("label", { text: "Good" }), goodSelect),
    el("div", { class: "field-row" }, el("label", { text: "Quantity (+/-)" }), itemQty),
    el("div", { class: "field-row" }, itemBtn),
    itemResult,
    el("h3", { text: "Position" }),
    el("div", { class: "field-row" }, el("label", { text: "Character" }), positionName),
    el("div", { class: "field-row" }, el("label", { text: "Position" }), positionSelect),
    el("div", { class: "field-row" }, el("label", { text: "Grant (unchecked = revoke)" }), positionGrant),
    el("div", { class: "field-row" }, positionBtn),
    positionResult,
    el("h3", { text: "Job" }),
    el("div", { class: "field-row" }, el("label", { text: "Character" }), jobName),
    el("div", { class: "field-row" }, el("label", { text: "Job title" }), jobTitle),
    el("div", { class: "field-row" }, el("label", { text: "Shift" }), shiftSelect),
    el("div", { class: "field-row" }, el("label", { text: "Illicit" }), jobIllicit),
    el("div", { class: "field-row" }, jobBtn),
    jobResult,
    el("h3", { text: "Mastery" }),
    el("div", { class: "field-row" }, el("label", { text: "Character" }), masteryName),
    el("div", { class: "field-row" }, el("label", { text: "Exact shifts" }), masteryShifts),
    el("div", { class: "field-row" }, el("label", { text: "Or jump to level" }), masteryLevel),
    el("div", { class: "field-row" }, masteryBtn),
    masteryResult
  );
}

function characterActionsPanel(ctx) {
  const killResult = el("p", { class: "result-line" });
  const killName = el("input", { type: "text", placeholder: "Character name" });
  const killReason = el("input", { type: "text", placeholder: "Cause of death (optional)" });
  const killBtn = el("button", { class: "btn secondary", type: "button" }, "Kill");
  killBtn.addEventListener("click", async () => {
    killResult.textContent = "";
    const name = killName.value.trim();
    if (!name) {
      killResult.className = "result-line lose";
      killResult.textContent = "Enter a character name.";
      return;
    }
    if (!confirm(`Kill "${name}"? This can't be undone from here.`)) return;
    try {
      const body = await fetchJson("/activity/dashboard/staff/character/kill", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          character_name: name,
          reason: killReason.value.trim() || null,
        }),
      });
      killResult.className = "result-line win";
      killResult.textContent = `${body.character_name} has died.`;
    } catch (err) {
      killResult.className = "result-line lose";
      killResult.textContent = err.message;
    }
  });

  const noteResult = el("p", { class: "result-line" });
  const noteName = el("input", { type: "text", placeholder: "Character name" });
  const noteText = el("input", { type: "text", placeholder: "Note text", maxlength: "500" });
  const noteBtn = el("button", { class: "btn secondary", type: "button" }, "Log Note");
  noteBtn.addEventListener("click", async () => {
    noteResult.textContent = "";
    const name = noteName.value.trim();
    const text = noteText.value.trim();
    if (!name || !text) {
      noteResult.className = "result-line lose";
      noteResult.textContent = "Enter a character name and note text.";
      return;
    }
    try {
      await fetchJson("/activity/dashboard/staff/character/note", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), character_name: name, text }),
      });
      noteResult.className = "result-line win";
      noteResult.textContent = "Note logged.";
      noteText.value = "";
    } catch (err) {
      noteResult.className = "result-line lose";
      noteResult.textContent = err.message;
    }
  });

  const deleteResult = el("p", { class: "result-line" });
  const deleteName = el("input", { type: "text", placeholder: "Character name (pending only)" });
  const deleteReason = el("input", { type: "text", placeholder: "Reason (optional, sent to applicant)" });
  const deletePendingBtn = el("button", { class: "btn secondary", type: "button" }, "Delete Pending");
  deletePendingBtn.addEventListener("click", async () => {
    deleteResult.textContent = "";
    const name = deleteName.value.trim();
    if (!name) {
      deleteResult.className = "result-line lose";
      deleteResult.textContent = "Enter a character name.";
      return;
    }
    if (!confirm(`Delete pending application "${name}"? This can't be undone.`)) return;
    try {
      const body = await fetchJson("/activity/dashboard/staff/character/delete-pending", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          character_name: name,
          reason: deleteReason.value.trim() || null,
        }),
      });
      deleteResult.className = "result-line win";
      deleteResult.textContent = `Deleted pending application "${body.character_name}".`;
    } catch (err) {
      deleteResult.className = "result-line lose";
      deleteResult.textContent = err.message;
    }
  });

  const limitResult = el("p", { class: "result-line" });
  const limitDiscordId = el("input", { type: "text", placeholder: "Player's Discord ID" });
  const limitValue = el("input", { type: "number", placeholder: "Max characters (blank = reset to default)" });
  const limitBtn = el("button", { class: "btn secondary", type: "button" }, "Set Limit");
  limitBtn.addEventListener("click", async () => {
    limitResult.textContent = "";
    const targetId = limitDiscordId.value.trim();
    if (!targetId) {
      limitResult.className = "result-line lose";
      limitResult.textContent = "Enter the player's Discord ID.";
      return;
    }
    try {
      const body = await fetchJson("/activity/dashboard/staff/character/limit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          target_discord_id: Number(targetId),
          limit: limitValue.value.trim() ? Number(limitValue.value) : null,
        }),
      });
      limitResult.className = "result-line win";
      limitResult.textContent = body.limit == null ? "Reset to the guild default." : `Limit set to ${body.limit}.`;
    } catch (err) {
      limitResult.className = "result-line lose";
      limitResult.textContent = err.message;
    }
  });

  const banResult = el("p", { class: "result-line" });
  const banDiscordId = el("input", { type: "text", placeholder: "Player's Discord ID" });
  const banBtn = el("button", { class: "btn secondary", type: "button" }, "Ban");
  banBtn.addEventListener("click", async () => {
    banResult.textContent = "";
    const targetId = banDiscordId.value.trim();
    if (!targetId) {
      banResult.className = "result-line lose";
      banResult.textContent = "Enter the player's Discord ID.";
      return;
    }
    if (!confirm(`Ban Discord user ${targetId} from the bot?`)) return;
    try {
      await fetchJson("/activity/dashboard/staff/ban", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), target_discord_id: Number(targetId) }),
      });
      banResult.className = "result-line win";
      banResult.textContent = "Banned.";
    } catch (err) {
      banResult.className = "result-line lose";
      banResult.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Character & Player Actions" }),
    el("h3", { text: "Kill a character" }),
    el("div", { class: "field-row" }, el("label", { text: "Character" }), killName),
    el("div", { class: "field-row" }, el("label", { text: "Reason" }), killReason),
    el("div", { class: "field-row" }, killBtn),
    killResult,
    el("h3", { text: "Staff note" }),
    el("div", { class: "field-row" }, el("label", { text: "Character" }), noteName),
    el("div", { class: "field-row" }, el("label", { text: "Note" }), noteText),
    el("div", { class: "field-row" }, noteBtn),
    noteResult,
    el("h3", { text: "Delete a pending application" }),
    el("div", { class: "field-row" }, el("label", { text: "Character" }), deleteName),
    el("div", { class: "field-row" }, el("label", { text: "Reason" }), deleteReason),
    el("div", { class: "field-row" }, deletePendingBtn),
    deleteResult,
    el("h3", { text: "Character limit override" }),
    el("div", { class: "field-row" }, el("label", { text: "Discord ID" }), limitDiscordId),
    el("div", { class: "field-row" }, el("label", { text: "Limit" }), limitValue),
    el("div", { class: "field-row" }, limitBtn),
    limitResult,
    el("h3", { text: "Ban from the bot" }),
    el("div", { class: "field-row" }, el("label", { text: "Discord ID" }), banDiscordId),
    el("div", { class: "field-row" }, banBtn),
    banResult
  );
}

function housingEngagementPanel(ctx) {
  const priceResult = el("p", { class: "result-line" });
  const propertyIdInput = el("input", { type: "number", placeholder: "Property ID" });
  const priceInput = el("input", { type: "number", placeholder: "New price (blank = clear override)" });
  const priceBtn = el("button", { class: "btn", type: "button" }, "Set Price");
  priceBtn.addEventListener("click", async () => {
    priceResult.textContent = "";
    const propertyId = Number(propertyIdInput.value);
    if (!Number.isFinite(propertyId) || propertyId <= 0) {
      priceResult.className = "result-line lose";
      priceResult.textContent = "Enter a valid property ID.";
      return;
    }
    try {
      const body = await fetchJson("/activity/dashboard/staff/housing/set-price", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          property_id: propertyId,
          price: priceInput.value.trim() ? Number(priceInput.value) : null,
        }),
      });
      priceResult.className = "result-line win";
      priceResult.textContent =
        body.price == null
          ? `Property #${body.property_id} now uses the sim's suggested price.`
          : `Property #${body.property_id} now asks ${Math.round(body.price)} money.`;
    } catch (err) {
      priceResult.className = "result-line lose";
      priceResult.textContent = err.message;
    }
  });

  const timeoutResult = el("p", { class: "result-line" });
  const timeoutInput = el("input", { type: "number", value: "20", min: "1" });
  const timeoutBtn = el("button", { class: "btn", type: "button" }, "Set Timeout");
  timeoutBtn.addEventListener("click", async () => {
    timeoutResult.textContent = "";
    const minutes = Number(timeoutInput.value);
    if (!Number.isFinite(minutes) || minutes < 1) {
      timeoutResult.className = "result-line lose";
      timeoutResult.textContent = "Enter a positive number of minutes.";
      return;
    }
    try {
      const body = await fetchJson("/activity/dashboard/staff/engagement/timeout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), minutes }),
      });
      timeoutResult.className = "result-line win";
      timeoutResult.textContent = `Engagements now auto-close after ${body.minutes} minute(s) of no player message.`;
    } catch (err) {
      timeoutResult.className = "result-line lose";
      timeoutResult.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Housing & Engagements" }),
    el("h3", { text: "Override a listed price" }),
    el("div", { class: "field-row" }, el("label", { text: "Property ID" }), propertyIdInput),
    el("div", { class: "field-row" }, el("label", { text: "Price" }), priceInput),
    el("div", { class: "field-row" }, priceBtn),
    priceResult,
    el("h3", { text: "NPC engagement idle timeout" }),
    el("div", { class: "field-row" }, el("label", { text: "Minutes" }), timeoutInput),
    el("div", { class: "field-row" }, timeoutBtn),
    timeoutResult
  );
}

function districtAdminPanel(ctx) {
  const districtSelect = districtDropdown(ctx);
  const stateStatus = el("p", { class: "tab-status" });
  const refreshBtn = el("button", { class: "btn secondary", type: "button" }, "Refresh");

  async function refreshState() {
    setStatusText(stateStatus, "Loading…");
    try {
      const body = await fetchJson(
        `/activity/dashboard/staff/district/${districtSelect.value}?discord_id=${ctx.discordId()}`
      );
      const crackdownNote = body.crackdown_until_tick
        ? ` -- Crackdown until tick ${body.crackdown_until_tick}`
        : "";
      setStatusText(
        stateStatus,
        `Crisis: ${body.crisis_level} (${body.crisis_kind || "calm"}) -- Unrest ${body.unrest.toFixed(2)} -- ` +
          `Peacekeeper pressure ${body.peacekeeper_pressure.toFixed(2)} -- Morale ${body.morale.toFixed(0)} -- ` +
          `Capitol favor ${body.capitol_favor >= 0 ? "+" : ""}${body.capitol_favor.toFixed(1)} -- ` +
          `Quota ${body.quota_progress.toFixed(0)}/${body.quota_target.toFixed(0)} -- ` +
          `Treasury ${body.treasury.toFixed(0)}${crackdownNote}`
      );
    } catch (err) {
      setStatusText(stateStatus, `Could not load: ${err.message}`, { error: true });
    }
  }
  districtSelect.addEventListener("change", refreshState);
  refreshBtn.addEventListener("click", refreshState);
  refreshState();

  const crackdownResult = el("p", { class: "result-line" });
  const durationInput = el("input", { type: "number", placeholder: "Ticks (optional)" });
  const crackdownBtn = el("button", { class: "btn", type: "button" }, "Trigger Crackdown");
  crackdownBtn.addEventListener("click", async () => {
    crackdownResult.textContent = "";
    try {
      const body = await fetchJson("/activity/dashboard/staff/district/crackdown", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          district_id: Number(districtSelect.value),
          duration_ticks: durationInput.value.trim() ? Number(durationInput.value) : null,
        }),
      });
      crackdownResult.className = "result-line win";
      const dName = ctx.districtName ? ctx.districtName(body.district_id) : `District ${body.district_id}`;
      crackdownResult.textContent =
        `Peacekeepers crack down on ${dName} for ${body.duration_ticks} ticks ` +
        `(until tick ${body.until_tick}).`;
      refreshState();
    } catch (err) {
      crackdownResult.className = "result-line lose";
      crackdownResult.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "District Administration" }),
    el("div", { class: "field-row" }, el("label", { text: "District" }), districtSelect, refreshBtn),
    stateStatus,
    el("h3", { text: "Trigger a peacekeeper crackdown" }),
    el("div", { class: "field-row" }, el("label", { text: "Duration (ticks)" }), durationInput),
    el("div", { class: "field-row" }, crackdownBtn),
    crackdownResult
  );
}

function npcAdminPanel(ctx) {
  const districtSelect = districtDropdown(ctx);
  const npcSelect = dropdown([]);
  const npcStatus = el("p", { class: "tab-status" });
  const locationSelect = dropdown([]);
  const jobSelect = dropdown([{ value: "", label: "-- none --" }]);

  async function refreshDistrictData() {
    npcSelect.setOptions([]);
    setStatusText(npcStatus, "Loading residents…");
    const districtId = districtSelect.value;
    try {
      const [npcs, locations, jobs] = await Promise.all([
        fetchJson(`/activity/dashboard/staff/npcs?district_id=${districtId}&discord_id=${ctx.discordId()}`),
        fetchJson(
          `/activity/dashboard/staff/npcs/locations?district_id=${districtId}&discord_id=${ctx.discordId()}`
        ),
        fetchJson(`/activity/dashboard/staff/npcs/jobs?district_id=${districtId}&discord_id=${ctx.discordId()}`),
      ]);
      npcSelect.setOptions(npcs.map((n) => ({ value: n.id, label: n.name })));
      locationSelect.setOptions(locations.map((l) => ({ value: l.id, label: l.name })));
      jobSelect.setOptions([
        { value: "", label: "-- none --" },
        ...jobs.map((j) => ({ value: j.id, label: j.title })),
      ]);
      setStatusText(npcStatus, npcs.length ? "" : "No NPCs in this district yet.");
    } catch (err) {
      setStatusText(npcStatus, `Could not load: ${err.message}`, { error: true });
    }
  }
  districtSelect.addEventListener("change", refreshDistrictData);
  refreshDistrictData();

  function editAction(path, buildBody, resultEl, onSuccess) {
    return async () => {
      resultEl.textContent = "";
      if (!npcSelect.value) {
        resultEl.className = "result-line lose";
        resultEl.textContent = "Pick a resident first.";
        return;
      }
      try {
        const body = await fetchJson(path, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildBody()),
        });
        resultEl.className = "result-line win";
        onSuccess(body);
      } catch (err) {
        resultEl.className = "result-line lose";
        resultEl.textContent = err.message;
      }
    };
  }

  const renameInput = el("input", { type: "text", placeholder: "New name", maxlength: "64" });
  const renameResult = el("p", { class: "result-line" });
  const renameBtn = el("button", { class: "btn secondary", type: "button" }, "Rename");
  renameBtn.addEventListener(
    "click",
    editAction(
      "/activity/dashboard/staff/npcs/rename",
      () => ({ discord_id: ctx.discordId(), npc_id: npcSelect.value, new_name: renameInput.value.trim() }),
      renameResult,
      (body) => {
        renameResult.textContent = `Renamed to ${body.name}.`;
        refreshDistrictData();
      }
    )
  );

  const backgroundInput = el("input", { type: "text", placeholder: "Backstory", maxlength: "1500" });
  const backgroundResult = el("p", { class: "result-line" });
  const backgroundBtn = el("button", { class: "btn secondary", type: "button" }, "Save Backstory");
  backgroundBtn.addEventListener(
    "click",
    editAction(
      "/activity/dashboard/staff/npcs/background",
      () => ({ discord_id: ctx.discordId(), npc_id: npcSelect.value, backstory: backgroundInput.value.trim() }),
      backgroundResult,
      () => {
        backgroundResult.textContent = "Backstory updated.";
      }
    )
  );

  const appearanceInput = el("input", { type: "text", placeholder: "Appearance", maxlength: "400" });
  const appearanceResult = el("p", { class: "result-line" });
  const appearanceBtn = el("button", { class: "btn secondary", type: "button" }, "Save Appearance");
  appearanceBtn.addEventListener(
    "click",
    editAction(
      "/activity/dashboard/staff/npcs/appearance",
      () => ({ discord_id: ctx.discordId(), npc_id: npcSelect.value, appearance: appearanceInput.value.trim() }),
      appearanceResult,
      () => {
        appearanceResult.textContent = "Appearance updated.";
      }
    )
  );

  const traitsInput = el("input", { type: "text", placeholder: "Comma-separated traits" });
  const traitsResult = el("p", { class: "result-line" });
  const traitsBtn = el("button", { class: "btn secondary", type: "button" }, "Save Traits");
  traitsBtn.addEventListener(
    "click",
    editAction(
      "/activity/dashboard/staff/npcs/traits",
      () => ({ discord_id: ctx.discordId(), npc_id: npcSelect.value, traits: traitsInput.value.trim() }),
      traitsResult,
      () => {
        traitsResult.textContent = "Traits updated.";
      }
    )
  );

  const speechInput = el("input", { type: "text", placeholder: "e.g. warm, blunt, reserved" });
  const speechResult = el("p", { class: "result-line" });
  const speechBtn = el("button", { class: "btn secondary", type: "button" }, "Save Speech Tone");
  speechBtn.addEventListener(
    "click",
    editAction(
      "/activity/dashboard/staff/npcs/speech",
      () => ({ discord_id: ctx.discordId(), npc_id: npcSelect.value, tone: speechInput.value.trim() }),
      speechResult,
      () => {
        speechResult.textContent = "Speech tone updated.";
      }
    )
  );

  const addName = el("input", { type: "text", placeholder: "Name", maxlength: "64" });
  const addAge = el("input", { type: "number", value: "30", min: "1", max: "120" });
  const addGender = dropdown(GENDER_OPTIONS);
  const addTraits = el("input", { type: "text", placeholder: "Comma-separated traits" });
  const addBackstory = el("input", { type: "text", placeholder: "Backstory (optional)", maxlength: "1500" });
  const addAppearance = el("input", { type: "text", placeholder: "Appearance (optional)", maxlength: "400" });
  const addResult = el("p", { class: "result-line" });
  const addBtn = el("button", { class: "btn", type: "button" }, "Add NPC");
  addBtn.addEventListener("click", async () => {
    addResult.textContent = "";
    const name = addName.value.trim();
    const traits = addTraits.value.trim();
    if (!name || !traits || !locationSelect.value) {
      addResult.className = "result-line lose";
      addResult.textContent = "Enter a name, at least one trait, and a home location.";
      return;
    }
    try {
      const body = await fetchJson("/activity/dashboard/staff/npcs/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          name,
          district_id: Number(districtSelect.value),
          age: Number(addAge.value) || 30,
          home_location_id: locationSelect.value,
          traits,
          gender: addGender.value || null,
          job_id: jobSelect.value || null,
          backstory: addBackstory.value.trim() || null,
          appearance: addAppearance.value.trim() || null,
        }),
      });
      addResult.className = "result-line win";
      addResult.textContent = `${body.name} has joined the district.`;
      addName.value = "";
      addTraits.value = "";
      addBackstory.value = "";
      addAppearance.value = "";
      refreshDistrictData();
    } catch (err) {
      addResult.className = "result-line lose";
      addResult.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "NPC Management" }),
    el("div", { class: "field-row" }, el("label", { text: "District" }), districtSelect),
    el("div", { class: "field-row" }, el("label", { text: "Resident" }), npcSelect),
    npcStatus,
    el("h3", { text: "Rename" }),
    el("div", { class: "field-row" }, el("label", { text: "New name" }), renameInput),
    el("div", { class: "field-row" }, renameBtn),
    renameResult,
    el("h3", { text: "Backstory" }),
    el("div", { class: "field-row" }, el("label", { text: "Text" }), backgroundInput),
    el("div", { class: "field-row" }, backgroundBtn),
    backgroundResult,
    el("h3", { text: "Appearance" }),
    el("div", { class: "field-row" }, el("label", { text: "Text" }), appearanceInput),
    el("div", { class: "field-row" }, appearanceBtn),
    appearanceResult,
    el("h3", { text: "Traits" }),
    el("div", { class: "field-row" }, el("label", { text: "Traits" }), traitsInput),
    el("div", { class: "field-row" }, traitsBtn),
    traitsResult,
    el("h3", { text: "Speech tone" }),
    el("div", { class: "field-row" }, el("label", { text: "Tone" }), speechInput),
    el("div", { class: "field-row" }, speechBtn),
    speechResult,
    el("h3", { text: "Add a new NPC to this district" }),
    el("div", { class: "field-row" }, el("label", { text: "Name" }), addName),
    el("div", { class: "field-row" }, el("label", { text: "Age" }), addAge),
    el("div", { class: "field-row" }, el("label", { text: "Gender" }), addGender),
    el("div", { class: "field-row" }, el("label", { text: "Home location" }), locationSelect),
    el("div", { class: "field-row" }, el("label", { text: "Job (optional)" }), jobSelect),
    el("div", { class: "field-row" }, el("label", { text: "Traits" }), addTraits),
    el("div", { class: "field-row" }, el("label", { text: "Backstory" }), addBackstory),
    el("div", { class: "field-row" }, el("label", { text: "Appearance" }), addAppearance),
    el("div", { class: "field-row" }, addBtn),
    addResult
  );
}

function ambientMusicPanel(ctx) {
  const root = el("div", {});
  const listEl = el("div", {});
  const statusEl = el("p", { class: "tab-status" });

  const titleInput = el("input", { type: "text", placeholder: "Track Title (e.g., District 12 Night)", maxlength: "128" });
  const scopeSelect = dropdown([
    { value: "global", label: "Global (Plays Everywhere)" },
    { value: "district", label: "District Scoped" },
    { value: "location", label: "Location Scoped" },
    { value: "channel", label: "Channel Scoped" },
  ]);
  const districtSelect = districtDropdown(ctx);
  const locationInput = el("input", { type: "text", placeholder: "Location ID (e.g. d12_square)", maxlength: "64" });
  const channelInput = el("input", { type: "text", placeholder: "Channel ID (e.g. 123456789)", maxlength: "64" });
  const fileInput = el("input", { type: "file", accept: "audio/mp3,audio/mpeg,audio/wav,audio/ogg,audio/flac,audio/aac,audio/m4a" });
  const uploadResult = el("p", { class: "result-line" });
  const uploadBtn = el("button", { class: "btn primary", type: "button" }, "Upload Track");

  let currentPreviewAudio = null;

  async function refreshTracks() {
    listEl.innerHTML = "";
    setStatusText(statusEl, "Loading ambient tracks…");
    try {
      const tracks = await fetchJson(`/activity/dashboard/staff/ambient?discord_id=${ctx.discordId()}`);
      if (tracks.length === 0) {
        setStatusText(statusEl, "No ambient tracks uploaded yet.");
        return;
      }
      setStatusText(statusEl, `Total tracks: ${tracks.length}`);

      const tableRows = tracks.map((track) => {
        let targetText = "Global";
        if (track.scope === "district" && track.district_id != null) {
          const dName = ctx.districtName ? ctx.districtName(track.district_id) : `District ${track.district_id}`;
          targetText = `District: ${dName}`;
        } else if (track.scope === "location" && track.location_id) {
          targetText = `Location: ${track.location_id}`;
        } else if (track.scope === "channel" && track.channel_id) {
          targetText = `Channel: ${track.channel_id}`;
        }

        const previewBtn = el("button", { class: "btn secondary", type: "button" }, "Preview");
        previewBtn.addEventListener("click", () => {
          if (currentPreviewAudio) {
            currentPreviewAudio.pause();
            currentPreviewAudio = null;
          }
          currentPreviewAudio = new Audio(track.file_url);
          currentPreviewAudio.play().catch((e) => console.warn("Preview play error:", e));
        });

        const deleteBtn = el("button", { class: "btn lose", type: "button" }, "Delete");
        deleteBtn.addEventListener("click", async () => {
          if (!confirm(`Delete track "${track.title}"?`)) return;
          try {
            await fetchJson(`/activity/dashboard/staff/ambient/${track.id}/delete`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ discord_id: ctx.discordId() }),
            });
            if (currentPreviewAudio) {
              currentPreviewAudio.pause();
              currentPreviewAudio = null;
            }
            refreshTracks();
          } catch (err) {
            alert(`Failed to delete track: ${err.message}`);
          }
        });

        return el(
          "div",
          { class: "field-row", style: "justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color, rgba(197, 160, 89, 0.2)); padding: 6px 0;" },
          el("div", {}, el("strong", { text: track.title }), el("span", { class: "tab-status", style: "margin-left: 10px;" }, `[${track.scope.toUpperCase()}] ${targetText}`)),
          el("div", { class: "btn-group" }, previewBtn, deleteBtn)
        );
      });

      listEl.append(...tableRows);
    } catch (err) {
      setStatusText(statusEl, `Failed to load tracks: ${err.message}`, { error: true });
    }
  }

  uploadBtn.addEventListener("click", async () => {
    uploadResult.textContent = "";
    const title = titleInput.value.trim();
    const scope = scopeSelect.value;
    const file = fileInput.files[0];

    if (!title) {
      uploadResult.className = "result-line lose";
      uploadResult.textContent = "Please provide a track title.";
      return;
    }
    if (!file) {
      uploadResult.className = "result-line lose";
      uploadResult.textContent = "Please select an audio file to upload.";
      return;
    }

    const form = new FormData();
    form.append("discord_id", String(ctx.discordId()));
    form.append("title", title);
    form.append("scope", scope);

    if (scope === "district") {
      form.append("district_id", String(districtSelect.value));
    } else if (scope === "location" && locationInput.value.trim()) {
      form.append("location_id", locationInput.value.trim());
    } else if (scope === "channel" && channelInput.value.trim()) {
      form.append("channel_id", channelInput.value.trim());
    }

    form.append("file", file);

    try {
      uploadResult.className = "result-line";
      uploadResult.textContent = "Uploading track...";
      await fetchJson("/activity/dashboard/staff/ambient/upload", {
        method: "POST",
        body: form,
      });
      uploadResult.className = "result-line win";
      uploadResult.textContent = `Successfully uploaded track "${title}"!`;
      titleInput.value = "";
      fileInput.value = "";
      locationInput.value = "";
      channelInput.value = "";
      refreshTracks();
    } catch (err) {
      uploadResult.className = "result-line lose";
      uploadResult.textContent = err.message;
    }
  });

  refreshTracks();

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Ambient Music Options" }),
    el("p", { class: "tab-status" }, "Upload and manage ambient music tracks. Tracks can be global or scoped to a specific district, location, or channel."),
    statusEl,
    listEl,
    el("h3", { text: "Upload New Ambient Track" }),
    el("div", { class: "field-row" }, el("label", { text: "Track Title" }), titleInput),
    el("div", { class: "field-row" }, el("label", { text: "Scope" }), scopeSelect),
    el("div", { class: "field-row" }, el("label", { text: "District" }), districtSelect),
    el("div", { class: "field-row" }, el("label", { text: "Location ID" }), locationInput),
    el("div", { class: "field-row" }, el("label", { text: "Channel ID" }), channelInput),
    el("div", { class: "field-row" }, el("label", { text: "Audio File" }), fileInput),
    el("div", { class: "field-row" }, uploadBtn),
    uploadResult
  );
}

export function mount(root, ctx) {
  root.append(
    jailPanel(ctx),
    givePanel(ctx),
    characterActionsPanel(ctx),
    districtAdminPanel(ctx),
    npcAdminPanel(ctx),
    ambientMusicPanel(ctx),
    housingEngagementPanel(ctx),
    marketStockPanel(ctx),
    mottosPanel(ctx),
    layersPanel(ctx),
    afflictionTypesPanel(ctx)
  );
}

