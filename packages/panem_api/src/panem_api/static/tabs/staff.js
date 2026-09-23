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
import { fetchJson, el, dropdown } from "./_shared.js?v=3";

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
    statusEl.textContent = "Loading…";
    try {
      const body = await fetchJson("/activity/dashboard/layers");
      statusEl.textContent = "";
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
      statusEl.textContent = `Could not load layer categories: ${err.message}`;
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
    statusEl.textContent = "Loading…";
    try {
      const body = await fetchJson("/activity/dashboard/affliction-types");
      statusEl.textContent = "";
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
      statusEl.textContent = `Could not load affliction types: ${err.message}`;
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

export function mount(root, ctx) {
  root.append(jailPanel(ctx), layersPanel(ctx), afflictionTypesPanel(ctx));
}
