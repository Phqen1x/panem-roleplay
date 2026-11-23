// The "Character" tab: list/create/edit/retire, mirroring `/character
// list|create|avatar|tag|retire`. Character *creation* here writes the DB
// row directly and waits on panem_bot's `_announce_pending_characters`
// background poll to post the staff-approval embed (panem_api has no bot
// token to post it itself) -- see dashboard_routes.py's build_characters_
// router docstring. District is a plain select here rather than inferred
// from a Discord guild role, the one deliberate simplification from the
// Discord flow.
//
// The visual customizer is a Picrew-style layered-image picker: staff
// upload artwork into named "categories" (Staff tab's Layers panel), each
// with a stack position, and a player just cycles through each category's
// options with </> arrows. It's a separate concern from the free-text
// `appearance` field above -- the customizer drives a live-composited
// portrait (`avatar_creator.js`, stored as `appearance_layers` JSON:
// `{category_id: option_id}`), while `appearance` stays exactly what it
// always was -- the player's own written description. Both a new
// character and an existing one (via the "Customize appearance" toggle on
// its card) go through the same `appearanceEditor` component, so
// restyling later needs no separate UI.
//
// Unlike the old 3D renderer this replaced, `mountAvatar` here is plain
// DOM (a stack of <img>s) with no GPU resources to leak, but every mount
// still returns its own `dispose()` (removes those DOM nodes) and every
// function below that creates one calls it at the right lifecycle point,
// mostly so a stale preview never lingers in a detached card.
import { fetchJson, el, dropdown, setStatusText } from "./_shared.js?v=9";
import { mountAvatar } from "./avatar_creator.js?v=11";

const SHIFT_PHASES = ["morning", "afternoon", "evening", "night"];

// Simulation first so it's the dropdown's default selection, matching the
// backend's own default (`RpMode.SIMULATION`) -- `dropdown()` selects
// whichever option comes first unless told otherwise.
const RP_MODES = [
  { value: "simulation", label: "Simulation -- the full experience" },
  { value: "life", label: "Life -- full economy/crime/work, no housing/needs" },
  { value: "story", label: "Story -- freeform RP only, no economy/crime/work" },
];

let catalogPromise = null;
function loadLayerCatalog(ctx) {
  if (!catalogPromise) {
    catalogPromise = ctx.apiFetch("/activity/dashboard/layers").then((body) => body.categories);
  }
  return catalogPromise;
}

function createSvgIcon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.75");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");

  if (name === "user") {
    const path1 = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path1.setAttribute("d", "M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2");
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("cx", "12");
    circle.setAttribute("cy", "7");
    circle.setAttribute("r", "4");
    svg.append(path1, circle);
  } else if (name === "upload") {
    const path1 = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path1.setAttribute("d", "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4");
    const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    polyline.setAttribute("points", "17 8 12 3 7 8");
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", "12");
    line.setAttribute("y1", "3");
    line.setAttribute("x2", "12");
    line.setAttribute("y2", "15");
    svg.append(path1, polyline, line);
  }
  return svg;
}

// Shows the character's uploaded picture as their primary portrait in
// both viewing and creation until fully custom layered characters are
// deployed. Supports instant local file preview, URL input updates, and
// graceful fallback placeholders.
function characterPortrait(initialUrl, { small = false, placeholderText = "No picture uploaded" } = {}) {
  const container = el("div", { class: small ? "avatar-preview small" : "avatar-preview" });
  let currentUrl = initialUrl || null;
  let activeBlobUrl = null;

  function render(url) {
    container.innerHTML = "";
    if (url && typeof url === "string" && url.trim()) {
      const img = el("img", {
        class: "avatar-portrait-img",
        src: url.trim(),
        alt: "Character portrait",
      });
      img.addEventListener("error", () => {
        renderPlaceholder("Could not load image");
      });
      container.append(img);
    } else {
      renderPlaceholder(placeholderText);
    }
  }

  function renderPlaceholder(msg) {
    container.innerHTML = "";
    const placeholder = el(
      "div",
      { class: "avatar-portrait-placeholder" },
      createSvgIcon("user"),
      el("span", { text: msg })
    );
    container.append(placeholder);
  }

  render(currentUrl);

  return {
    el: container,
    setUrl(url, isBlob = false) {
      if (activeBlobUrl && activeBlobUrl !== url) {
        URL.revokeObjectURL(activeBlobUrl);
        activeBlobUrl = null;
      }
      if (isBlob) activeBlobUrl = url;
      currentUrl = url;
      render(url);
    },
    getUrl() {
      return currentUrl;
    },
    dispose() {
      if (activeBlobUrl) {
        URL.revokeObjectURL(activeBlobUrl);
        activeBlobUrl = null;
      }
    },
  };
}

// One </> row per category that actually has artwork uploaded yet -- a
// category with zero options has nothing to cycle through, so it's left
// out entirely rather than showing a picker that can only ever say
// "None". `initialOptionId` that no longer matches any current option
// (staff deleted it) falls back to "None" (index 0) the same way a
// missing selection does.
function categoryPicker(category, initialOptionId, onChange) {
  const choices = [null, ...category.options];
  let index = choices.findIndex((o) => o && o.id === initialOptionId);
  if (index < 0) index = 0;

  const thumb = el("img", { class: "layer-thumb", alt: "" });
  const nameLabel = el("span", { class: "tab-status" });

  function renderCurrent() {
    const current = choices[index];
    if (current) {
      thumb.src = current.image_url;
      thumb.hidden = false;
      nameLabel.textContent = current.name;
    } else {
      thumb.hidden = true;
      thumb.removeAttribute("src");
      nameLabel.textContent = "None";
    }
  }

  function step(delta) {
    index = (index + delta + choices.length) % choices.length;
    renderCurrent();
    const current = choices[index];
    onChange(current ? current.id : null);
  }

  renderCurrent();
  return el(
    "div",
    { class: "field-row layer-picker" },
    el("label", { text: category.name }),
    el("button", { type: "button", class: "btn secondary", onclick: () => step(-1) }, "<"),
    thumb,
    nameLabel,
    el("button", { type: "button", class: "btn secondary", onclick: () => step(1) }, ">")
  );
}

// The one customizer component shared by the create form and every
// existing character's "Customize appearance" panel. `initialSelection`
// may be a partial (or missing) `{category_id: option_id}` map -- a
// brand-new character just starts with every category unset ("None").
function appearanceEditor(catalog, initialSelection) {
  const selection = { ...(initialSelection || {}) };
  const previewContainer = el("div", { class: "avatar-preview" });
  const avatar = mountAvatar(previewContainer, catalog, selection);

  const pickers = catalog
    .filter((category) => category.options.length > 0)
    .map((category) =>
      categoryPicker(category, selection[String(category.id)], (optionId) => {
        if (optionId == null) delete selection[String(category.id)];
        else selection[String(category.id)] = optionId;
        avatar.update(selection);
      })
    );

  const root = el("div", { class: "avatar-editor" }, previewContainer);
  if (pickers.length > 0) {
    root.append(...pickers);
  } else {
    root.append(
      el("p", { class: "tab-status" }, "No customization options uploaded yet -- check back soon.")
    );
  }

  return {
    el: root,
    getSelection: () => ({ ...selection }),
    dispose: () => avatar.dispose(),
  };
}

function characterCard(ctx, character, catalog, { onChanged }) {
  const statusLine = el(
    "p",
    { class: "tab-status" },
    `${character.status}${character.jailed ? " -- jailed" : ""}` +
      `${character.passed_out ? " -- passed out from exhaustion" : ""}`
  );
  const deathCauseLine =
    character.status === "dead" && character.death_cause
      ? el("p", { class: "tab-status" }, `Cause of death: ${character.death_cause}`)
      : null;

  // The primary character image shown at the top of the card is the uploaded portrait.
  const portrait = characterPortrait(ctx.mediaUrl(character.avatar_url), {
    small: true,
    placeholderText: "No portrait uploaded",
  });

  const avatarInput = el("input", {
    type: "text",
    value: character.avatar_url || "",
    placeholder: "https://...",
  });
  avatarInput.addEventListener("input", () => {
    portrait.setUrl(ctx.mediaUrl(avatarInput.value));
  });

  const tagInput = el("input", { type: "text", value: character.proxy_tag || "", placeholder: "tag::" });
  const resultLine = el("p", { class: "result-line" });

  const avatarFileInput = el("input", {
    type: "file",
    class: "avatar-file-hidden",
    accept: "image/png,image/jpeg,image/webp,image/gif",
  });
  const avatarFileLabel = el(
    "label",
    { class: "btn secondary avatar-file-label" },
    createSvgIcon("upload"),
    el("span", { text: "Choose picture" }),
    avatarFileInput
  );
  const avatarFileName = el("span", {
    class: character.avatar_url ? "avatar-file-name is-uploaded" : "avatar-file-name",
    text: character.avatar_url ? "Active picture uploaded" : "No file chosen",
  });
  const avatarUploadBtn = el("button", { class: "btn secondary", type: "button" }, "Upload image");
  avatarUploadBtn.disabled = true;
  const avatarUploadResult = el("span", { class: "result-line" });

  let chosenFile = null;
  avatarFileInput.addEventListener("change", () => {
    const file = avatarFileInput.files && avatarFileInput.files[0];
    if (!file) return;
    chosenFile = file;
    const blobUrl = URL.createObjectURL(file);
    portrait.setUrl(blobUrl, true);
    avatarFileName.className = "avatar-file-name has-file";
    avatarFileName.textContent = `Selected: ${file.name}`;
    avatarUploadBtn.disabled = false;
    avatarUploadResult.textContent = "";
  });

  avatarUploadBtn.addEventListener("click", async () => {
    const file = chosenFile || (avatarFileInput.files && avatarFileInput.files[0]);
    if (!file) {
      avatarUploadResult.className = "result-line lose";
      avatarUploadResult.textContent = "Choose a file first.";
      return;
    }
    avatarUploadBtn.disabled = true;
    avatarUploadResult.className = "result-line";
    avatarUploadResult.textContent = "Uploading…";
    const formData = new FormData();
    formData.append("discord_id", String(ctx.discordId()));
    formData.append("file", file);
    try {
      const updated = await ctx.apiFetch(
        `/activity/dashboard/characters/${character.id}/avatar-upload`,
        { method: "POST", body: formData }
      );
      character.avatar_url = updated.avatar_url;
      avatarInput.value = updated.avatar_url || "";
      portrait.setUrl(ctx.mediaUrl(updated.avatar_url));
      avatarFileName.className = "avatar-file-name is-uploaded";
      avatarFileName.textContent = `✓ Current: ${file.name}`;
      avatarUploadResult.className = "result-line win";
      avatarUploadResult.textContent = "Uploaded successfully!";
      avatarUploadBtn.disabled = true;
    } catch (err) {
      avatarUploadBtn.disabled = false;
      avatarUploadResult.className = "result-line lose";
      avatarUploadResult.textContent = err.message;
    }
  });

  const saveBtn = el("button", { class: "btn secondary", type: "button" }, "Save");
  saveBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      await ctx.apiFetch(`/activity/dashboard/characters/${character.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          avatar_url: avatarInput.value || null,
          proxy_tag: tagInput.value || null,
        }),
      });
      character.avatar_url = avatarInput.value || null;
      character.proxy_tag = tagInput.value || null;
      portrait.setUrl(ctx.mediaUrl(character.avatar_url));
      resultLine.className = "result-line win";
      resultLine.textContent = "Saved.";
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  const retireBtn = el("button", { class: "btn secondary", type: "button" }, "Retire");
  retireBtn.hidden = character.status !== "approved";
  retireBtn.addEventListener("click", async () => {
    if (!confirm(`Retire ${character.name}? This can't be undone.`)) return;
    try {
      await ctx.apiFetch(`/activity/dashboard/characters/${character.id}/retire`, {
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

  const customizeContainer = el("div", {});
  customizeContainer.hidden = true;
  let editor = null;
  const customizeBtn = el("button", { class: "btn secondary", type: "button" }, "Customize appearance");
  customizeBtn.addEventListener("click", () => {
    customizeContainer.hidden = !customizeContainer.hidden;
    if (customizeContainer.hidden || editor) return;
    editor = appearanceEditor(catalog, character.appearance_layers);
    const appearanceResult = el("p", { class: "result-line" });
    const saveAppearanceBtn = el("button", { class: "btn", type: "button" }, "Save appearance");
    saveAppearanceBtn.addEventListener("click", async () => {
      appearanceResult.textContent = "";
      try {
        await ctx.apiFetch(`/activity/dashboard/characters/${character.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: ctx.discordId(), appearance_layers: editor.getSelection() }),
        });
        appearanceResult.className = "result-line win";
        appearanceResult.textContent = "Saved.";
      } catch (err) {
        appearanceResult.className = "result-line lose";
        appearanceResult.textContent = err.message;
      }
    });
    customizeContainer.append(editor.el, saveAppearanceBtn, appearanceResult);
  });

  const uploadBox = el(
    "div",
    { class: "avatar-upload-box" },
    el("div", { class: "avatar-upload-row" }, avatarFileLabel, avatarUploadBtn, avatarFileName),
    avatarUploadResult
  );

  const cardEl = el(
    "div",
    { class: "panel" },
    el("h2", { text: `${character.name} (${character.district_name})` }),
    portrait.el,
    statusLine,
    deathCauseLine,
    el("p", { class: "tab-status" }, `${character.job_title || "no job"} -- ${character.money} money`),
    el("div", { class: "field-row" }, el("label", { text: "Avatar URL" }), avatarInput),
    el("div", { class: "field-row" }, el("label", { text: "Upload image" }), uploadBox),
    el("div", { class: "field-row" }, el("label", { text: "Proxy tag" }), tagInput),
    el("div", { class: "field-row" }, saveBtn, retireBtn, customizeBtn),
    resultLine,
    customizeContainer
  );

  return {
    el: cardEl,
    dispose: () => {
      portrait.dispose();
      if (editor) editor.dispose();
    },
  };
}

function createForm(ctx, catalog, { onCreated }) {
  const nameInput = el("input", { type: "text", maxlength: "32" });
  const ageInput = el("input", { type: "number", value: "16", min: "12", max: "89" });
  const districtInput = el("input", { type: "number", value: "1", min: "0", max: "12" });
  const modeSelect = dropdown(RP_MODES);
  const jobInput = el("input", { type: "text", maxlength: "80", placeholder: "Miner, Baker, ..." });
  const phaseSelect = dropdown(SHIFT_PHASES.map((phase) => ({ value: phase, label: phase })));
  const illicitInput = el("input", { type: "checkbox" });
  const appearanceInput = el("textarea", { rows: "2", maxlength: "400" });
  const backstoryInput = el("textarea", { rows: "3", maxlength: "1500" });
  const resultLine = el("p", { class: "result-line" });

  // For the time being, until fully custom characters are added, make the
  // character image in character creation show the uploaded picture that is
  // used for profile picture where the custom character would be.
  const portrait = characterPortrait(null, {
    small: false,
    placeholderText: "Upload or choose character picture",
  });

  const avatarUrlInput = el("input", {
    type: "text",
    placeholder: "https://... or choose a file below",
  });
  avatarUrlInput.addEventListener("input", () => {
    portrait.setUrl(ctx.mediaUrl(avatarUrlInput.value));
  });

  const avatarFileInput = el("input", {
    type: "file",
    class: "avatar-file-hidden",
    accept: "image/png,image/jpeg,image/webp,image/gif",
  });
  const avatarFileLabel = el(
    "label",
    { class: "btn secondary avatar-file-label" },
    createSvgIcon("upload"),
    el("span", { text: "Choose picture" }),
    avatarFileInput
  );
  const avatarFileName = el("span", { class: "avatar-file-name", text: "No file chosen" });
  const avatarUploadBtn = el("button", { class: "btn secondary", type: "button" }, "Upload picture");
  avatarUploadBtn.disabled = true;
  const avatarUploadResult = el("span", { class: "result-line" });

  let chosenFile = null;
  avatarFileInput.addEventListener("change", () => {
    const file = avatarFileInput.files && avatarFileInput.files[0];
    if (!file) return;
    chosenFile = file;
    const blobUrl = URL.createObjectURL(file);
    portrait.setUrl(blobUrl, true);
    avatarFileName.className = "avatar-file-name has-file";
    avatarFileName.textContent = `Selected: ${file.name}`;
    avatarUploadBtn.disabled = false;
    avatarUploadResult.textContent = "";
  });

  async function uploadSelectedImage() {
    const file = chosenFile || (avatarFileInput.files && avatarFileInput.files[0]);
    if (!file) return null;
    avatarUploadBtn.disabled = true;
    avatarUploadResult.className = "result-line";
    avatarUploadResult.textContent = "Uploading…";
    const formData = new FormData();
    formData.append("discord_id", String(ctx.discordId()));
    formData.append("file", file);
    try {
      const res = await ctx.apiFetch("/activity/dashboard/characters/avatar-upload", {
        method: "POST",
        body: formData,
      });
      avatarUrlInput.value = res.avatar_url || "";
      portrait.setUrl(ctx.mediaUrl(res.avatar_url));
      avatarFileName.className = "avatar-file-name is-uploaded";
      avatarFileName.textContent = `✓ Current: ${file.name}`;
      avatarUploadResult.className = "result-line win";
      avatarUploadResult.textContent = "Uploaded and ready!";
      avatarUploadBtn.disabled = true;
      return res.avatar_url;
    } catch (err) {
      avatarUploadBtn.disabled = false;
      avatarUploadResult.className = "result-line lose";
      avatarUploadResult.textContent = err.message;
      throw err;
    }
  }

  avatarUploadBtn.addEventListener("click", () => {
    uploadSelectedImage().catch(() => {});
  });

  const uploadBox = el(
    "div",
    { class: "avatar-upload-box" },
    el("div", { class: "avatar-upload-row" }, avatarFileLabel, avatarUploadBtn, avatarFileName),
    avatarUploadResult
  );

  const hasLayerOptions = catalog && catalog.some((cat) => cat.options && cat.options.length > 0);
  let editor = null;
  if (hasLayerOptions) {
    editor = appearanceEditor(catalog, {});
  }

  const jobRow = el("div", { class: "field-row" }, el("label", { text: "Job title" }), jobInput);
  const shiftRow = el("div", { class: "field-row" }, el("label", { text: "Shift" }), phaseSelect);
  const illicitRow = el(
    "div",
    { class: "field-row" },
    el("label", { text: "Illicit job?" }),
    illicitInput
  );

  function syncJobFieldsVisibility() {
    const isStory = modeSelect.value === "story";
    jobRow.hidden = isStory;
    shiftRow.hidden = isStory;
    illicitRow.hidden = isStory;
  }
  modeSelect.addEventListener("change", syncJobFieldsVisibility);
  syncJobFieldsVisibility();

  const submitBtn = el("button", { class: "btn", type: "button" }, "Create character");
  submitBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    const isStory = modeSelect.value === "story";
    submitBtn.disabled = true;
    try {
      let finalAvatarUrl = avatarUrlInput.value || null;
      if (chosenFile && !finalAvatarUrl) {
        try {
          finalAvatarUrl = await uploadSelectedImage();
        } catch {
          submitBtn.disabled = false;
          return;
        }
      }

      await ctx.apiFetch("/activity/dashboard/characters", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          district_id: Number(districtInput.value),
          name: nameInput.value,
          age: Number(ageInput.value),
          appearance: appearanceInput.value,
          backstory: backstoryInput.value,
          rp_mode: modeSelect.value,
          avatar_url: finalAvatarUrl,
          job_title: isStory ? null : jobInput.value,
          shift_phase: isStory ? null : phaseSelect.value,
          job_is_illicit: isStory ? false : illicitInput.checked,
          appearance_layers: editor ? editor.getSelection() : null,
        }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = "Application submitted -- awaiting staff approval.";
      nameInput.value = "";
      appearanceInput.value = "";
      backstoryInput.value = "";
      avatarUrlInput.value = "";
      avatarFileInput.value = "";
      chosenFile = null;
      portrait.setUrl(null);
      avatarFileName.className = "avatar-file-name";
      avatarFileName.textContent = "No file chosen";
      avatarUploadBtn.disabled = true;
      avatarUploadResult.textContent = "";
      onCreated();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    } finally {
      submitBtn.disabled = false;
    }
  });

  const lookChildren = [
    portrait.el,
    el("div", { class: "field-row" }, el("label", { text: "Avatar picture" }), uploadBox),
    el("div", { class: "field-row" }, el("label", { text: "Or image URL" }), avatarUrlInput),
  ];
  if (editor) {
    lookChildren.push(editor.el);
  }

  const formEl = el(
    "div",
    { class: "panel" },
    el("h2", { text: "New character" }),
    el("div", { class: "field-row" }, el("label", { text: "Name" }), nameInput),
    el("div", { class: "field-row" }, el("label", { text: "Age" }), ageInput),
    el("div", { class: "field-row" }, el("label", { text: "District" }), districtInput),
    el("div", { class: "field-row" }, el("label", { text: "RP Mode" }), modeSelect),
    jobRow,
    shiftRow,
    illicitRow,
    el("div", { class: "field-row" }, el("label", { text: "Appearance" }), appearanceInput),
    el("div", { class: "field-row" }, el("label", { text: "Backstory" }), backstoryInput),
    el("h2", { text: "Look" }),
    ...lookChildren,
    submitBtn,
    resultLine
  );

  return {
    el: formEl,
    dispose: () => {
      portrait.dispose();
      if (editor) editor.dispose();
    },
  };
}

export function mount(root, ctx) {
  const listEl = el("div", {});
  const statusEl = el("p", { class: "tab-status" });
  const formContainer = el("div", {});
  let catalog = null;
  let cardDisposers = [];
  let formHandle = null;

  function disposeCards() {
    for (const dispose of cardDisposers) dispose();
    cardDisposers = [];
  }

  async function refresh() {
    disposeCards();
    listEl.innerHTML = "";
    const discordId = ctx.discordId();
    if (!discordId) {
      setStatusText(statusEl, "Enter a Discord ID above to see your characters.");
      return;
    }
    setStatusText(statusEl, "Loading…");
    try {
      const body = await ctx.apiFetch(
        `/activity/dashboard/characters?discord_id=${encodeURIComponent(discordId)}`
      );
      setStatusText(statusEl, "");
      for (const character of body.characters) {
        const card = characterCard(ctx, character, catalog, { onChanged: refresh });
        cardDisposers.push(card.dispose);
        listEl.append(card.el);
      }
      if (body.characters.length === 0) {
        listEl.append(el("p", { class: "tab-status" }, "No characters yet."));
      }
    } catch (err) {
      setStatusText(statusEl, `Could not load characters: ${err.message}`, { error: true });
    }
  }

  root.append(statusEl, listEl, formContainer);

  (async () => {
    try {
      catalog = await loadLayerCatalog(ctx);
    } catch (err) {
      setStatusText(statusEl, `Could not load the appearance customizer: ${err.message}`, {
        error: true,
      });
      return;
    }
    formHandle = createForm(ctx, catalog, { onCreated: refresh });
    formContainer.append(formHandle.el);
    await refresh();
  })();

  return {
    unmount() {
      disposeCards();
      if (formHandle) formHandle.dispose();
    },
  };
}
