// The "Character" tab: list/create/edit/retire, mirroring `/character
// list|create|avatar|tag|retire`. Character *creation* here writes the DB
// row directly and waits on panem_bot's `_announce_pending_characters`
// background poll to post the staff-approval embed (panem_api has no bot
// token to post it itself) -- see dashboard_routes.py's build_characters_
// router docstring. District is a plain select here rather than inferred
// from a Discord guild role, the one deliberate simplification from the
// Discord flow.
//
// The visual customizer (skin tone, gender presentation, age look, face
// shape/expression, hair style/color, facial hair, build, height,
// clothing style/color, jewelry) is a separate concern from the free-text
// `appearance` field above: the customizer drives a live-rendered 3D
// portrait (`avatar_creator.js`, stored as `appearance_traits` JSON),
// while `appearance` stays exactly what it always was -- the player's own
// written description. Both a new character and an existing one (via the
// "Customize appearance" toggle on its card) go through the same
// `appearanceEditor` component, so restyling later needs no separate UI.
//
// `avatar_creator.js`'s 3D preview is a live WebGL scene, not a stateless
// canvas draw -- `mountAvatar(container, traits)` returns a handle with
// `update(traits)`/`dispose()`, and every mount here MUST be disposed once
// its container leaves the DOM (a card is replaced on refresh, the tab is
// unmounted) or its render loop and GL context leak. Every function below
// that creates one returns its own `dispose`, and `mount()`'s `refresh()`/
// `unmount()` are what actually call them.
import { fetchJson, el, dropdown } from "./_shared.js?v=3";
import { mountAvatar } from "./avatar_creator.js?v=10";

const SHIFT_PHASES = ["morning", "afternoon", "evening", "night"];

let optionsPromise = null;
function loadAppearanceOptions(ctx) {
  if (!optionsPromise) {
    optionsPromise = ctx.apiFetch("/activity/dashboard/characters/appearance-options");
  }
  return optionsPromise;
}

function humanizeLabel(value) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function fieldRow(label, control) {
  return el("div", { class: "field-row" }, el("label", { text: label }), control);
}

function choiceSelect(options, initial, onChange) {
  const select = dropdown(options.map((value) => ({ value, label: humanizeLabel(value) })));
  select.value = initial;
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

function colorSwatchPicker(colors, initial, onChange) {
  const wrap = el("div", { class: "swatch-row" });
  let current = colors.includes(initial) ? initial : colors[0];

  function render() {
    wrap.innerHTML = "";
    for (const color of colors) {
      wrap.append(
        el("button", {
          type: "button",
          class: `swatch${color === current ? " active" : ""}`,
          style: `background:${color}`,
          title: color,
          onclick: () => {
            current = color;
            onChange(current);
            render();
          },
        })
      );
    }
  }

  render();
  return wrap;
}

function jewelryCheckboxes(options, initial, onChange) {
  const wrap = el("div", { class: "jewelry-checks" });
  const state = new Set(initial);
  for (const item of options) {
    const checkbox = el("input", { type: "checkbox" });
    checkbox.checked = state.has(item);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.add(item);
      else state.delete(item);
      onChange([...state]);
    });
    wrap.append(el("label", { class: "jewelry-check" }, checkbox, ` ${humanizeLabel(item)}`));
  }
  return wrap;
}

function heightSlider(min, max, initial, onChange) {
  const wrap = el("div", { class: "field-row" });
  const input = el("input", { type: "range", min: String(min), max: String(max), value: String(initial) });
  const readout = el("span", { class: "tab-status" }, `${initial}cm`);
  input.addEventListener("input", () => {
    readout.textContent = `${input.value}cm`;
    onChange(Number(input.value));
  });
  wrap.append(input, readout);
  return wrap;
}

// The one customizer component shared by the create form and every
// existing character's "Customize appearance" panel. `initialTraits` may
// be a partial object (or missing fields entirely, for a brand-new
// character) -- always merged onto `options.defaults` first so every
// picker always has a defined starting value.
function appearanceEditor(options, initialTraits) {
  const traits = {
    ...options.defaults,
    ...(initialTraits || {}),
    jewelry: [...((initialTraits && initialTraits.jewelry) || options.defaults.jewelry)],
  };
  const previewContainer = el("div", { class: "avatar-preview" });
  const avatar = mountAvatar(previewContainer, traits);

  function set(field, value) {
    traits[field] = value;
    avatar.update(traits);
  }

  const root = el(
    "div",
    { class: "avatar-editor" },
    previewContainer,
    fieldRow("Presentation", choiceSelect(options.gender_presentations, traits.gender_presentation, (v) => set("gender_presentation", v))),
    fieldRow("Looks", choiceSelect(options.age_looks, traits.age_look, (v) => set("age_look", v))),
    fieldRow("Face shape", choiceSelect(options.face_shapes, traits.face_shape, (v) => set("face_shape", v))),
    fieldRow("Expression", choiceSelect(options.expressions, traits.expression, (v) => set("expression", v))),
    fieldRow("Skin tone", colorSwatchPicker(options.skin_tones, traits.skin_tone, (v) => set("skin_tone", v))),
    fieldRow("Eye color", colorSwatchPicker(options.eye_colors, traits.eye_color, (v) => set("eye_color", v))),
    fieldRow("Hair style", choiceSelect(options.hair_styles, traits.hair_style, (v) => set("hair_style", v))),
    fieldRow("Hair color", colorSwatchPicker(options.hair_colors, traits.hair_color, (v) => set("hair_color", v))),
    fieldRow("Facial hair", choiceSelect(options.facial_hair, traits.facial_hair, (v) => set("facial_hair", v))),
    fieldRow("Build", choiceSelect(options.builds, traits.build, (v) => set("build", v))),
    fieldRow("Height", heightSlider(options.height_cm_min, options.height_cm_max, traits.height_cm, (v) => set("height_cm", v))),
    fieldRow("Clothing style", choiceSelect(options.clothing_styles, traits.clothing_style, (v) => set("clothing_style", v))),
    fieldRow("Clothing color", colorSwatchPicker(options.clothing_colors, traits.clothing_color, (v) => set("clothing_color", v))),
    fieldRow("Jewelry", jewelryCheckboxes(options.jewelry, traits.jewelry, (v) => set("jewelry", v)))
  );

  return {
    el: root,
    getTraits: () => ({ ...traits, jewelry: [...traits.jewelry] }),
    dispose: () => avatar.dispose(),
  };
}

function characterCard(ctx, character, options, { onChanged }) {
  const statusLine = el(
    "p",
    { class: "tab-status" },
    `${character.status}${character.jailed ? " -- jailed" : ""}`
  );
  const avatarInput = el("input", { type: "text", value: character.avatar_url || "", placeholder: "https://..." });
  const tagInput = el("input", { type: "text", value: character.proxy_tag || "", placeholder: "tag::" });
  const resultLine = el("p", { class: "result-line" });

  const previewContainer = el("div", { class: "avatar-preview small" });
  const previewAvatar = mountAvatar(previewContainer, { ...options.defaults, ...character.appearance_traits });

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
      resultLine.className = "result-line win";
      resultLine.textContent = "Saved.";
      onChanged();
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
    editor = appearanceEditor(options, character.appearance_traits);
    const appearanceResult = el("p", { class: "result-line" });
    const saveAppearanceBtn = el("button", { class: "btn", type: "button" }, "Save appearance");
    saveAppearanceBtn.addEventListener("click", async () => {
      appearanceResult.textContent = "";
      try {
        await ctx.apiFetch(`/activity/dashboard/characters/${character.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: ctx.discordId(), appearance_traits: editor.getTraits() }),
        });
        appearanceResult.className = "result-line win";
        appearanceResult.textContent = "Saved.";
        previewAvatar.update(editor.getTraits());
      } catch (err) {
        appearanceResult.className = "result-line lose";
        appearanceResult.textContent = err.message;
      }
    });
    customizeContainer.append(editor.el, saveAppearanceBtn, appearanceResult);
  });

  const cardEl = el(
    "div",
    { class: "panel" },
    el("h2", { text: `${character.name} (${character.district_name})` }),
    previewContainer,
    statusLine,
    el("p", { class: "tab-status" }, `${character.job_title || "no job"} -- ${character.money} money`),
    el("div", { class: "field-row" }, el("label", { text: "Avatar URL" }), avatarInput),
    el("div", { class: "field-row" }, el("label", { text: "Proxy tag" }), tagInput),
    el("div", { class: "field-row" }, saveBtn, retireBtn, customizeBtn),
    resultLine,
    customizeContainer
  );

  return {
    el: cardEl,
    dispose: () => {
      previewAvatar.dispose();
      if (editor) editor.dispose();
    },
  };
}

function createForm(ctx, options, { onCreated }) {
  const nameInput = el("input", { type: "text", maxlength: "32" });
  const ageInput = el("input", { type: "number", value: "16", min: "12", max: "99" });
  const districtInput = el("input", { type: "number", value: "1", min: "0", max: "12" });
  const jobInput = el("input", { type: "text", maxlength: "80", placeholder: "Miner, Baker, ..." });
  const phaseSelect = dropdown(SHIFT_PHASES.map((phase) => ({ value: phase, label: phase })));
  const illicitInput = el("input", { type: "checkbox" });
  const appearanceInput = el("textarea", { rows: "2", maxlength: "400" });
  const backstoryInput = el("textarea", { rows: "3", maxlength: "1500" });
  const resultLine = el("p", { class: "result-line" });
  const editor = appearanceEditor(options, options.defaults);

  const submitBtn = el("button", { class: "btn", type: "button" }, "Create character");
  submitBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
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
          job_title: jobInput.value,
          shift_phase: phaseSelect.value,
          job_is_illicit: illicitInput.checked,
          appearance_traits: editor.getTraits(),
        }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = "Application submitted -- awaiting staff approval.";
      nameInput.value = "";
      appearanceInput.value = "";
      backstoryInput.value = "";
      onCreated();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  const formEl = el(
    "div",
    { class: "panel" },
    el("h2", { text: "New character" }),
    el("div", { class: "field-row" }, el("label", { text: "Name" }), nameInput),
    el("div", { class: "field-row" }, el("label", { text: "Age" }), ageInput),
    el("div", { class: "field-row" }, el("label", { text: "District" }), districtInput),
    el("div", { class: "field-row" }, el("label", { text: "Job title" }), jobInput),
    el("div", { class: "field-row" }, el("label", { text: "Shift" }), phaseSelect),
    el("div", { class: "field-row" }, el("label", { text: "Illicit job?" }), illicitInput),
    el("div", { class: "field-row" }, el("label", { text: "Appearance" }), appearanceInput),
    el("div", { class: "field-row" }, el("label", { text: "Backstory" }), backstoryInput),
    el("h2", { text: "Look" }),
    editor.el,
    submitBtn,
    resultLine
  );

  return { el: formEl, dispose: () => editor.dispose() };
}

export function mount(root, ctx) {
  const listEl = el("div", {});
  const statusEl = el("p", { class: "tab-status" });
  const formContainer = el("div", {});
  let options = null;
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
      statusEl.textContent = "Enter a Discord ID above to see your characters.";
      return;
    }
    statusEl.textContent = "Loading…";
    try {
      const body = await ctx.apiFetch(
        `/activity/dashboard/characters?discord_id=${encodeURIComponent(discordId)}`
      );
      statusEl.textContent = "";
      for (const character of body.characters) {
        const card = characterCard(ctx, character, options, { onChanged: refresh });
        cardDisposers.push(card.dispose);
        listEl.append(card.el);
      }
      if (body.characters.length === 0) {
        listEl.append(el("p", { class: "tab-status" }, "No characters yet."));
      }
    } catch (err) {
      statusEl.textContent = `Could not load characters: ${err.message}`;
    }
  }

  root.append(statusEl, listEl, formContainer);

  (async () => {
    try {
      options = await loadAppearanceOptions(ctx);
    } catch (err) {
      statusEl.textContent = `Could not load the appearance customizer: ${err.message}`;
      return;
    }
    formHandle = createForm(ctx, options, { onCreated: refresh });
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
