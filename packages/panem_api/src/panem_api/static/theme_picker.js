// The donor-only dashboard theme popup: a little color-wheel button pinned
// to the header's top-right corner (`app.js` only unhides `#theme-picker`
// when `/activity/dashboard/identify` reports `is_donor`) that opens a
// Discord-role-color-picker-style panel -- a saturation/value square plus
// a hue strip, driving a live hex swatch/text field, for each of the four
// customizable colors (background, accent, panel, text) -- plus a small
// profile manager: named, savable presets a donor can set as their
// account's general default and/or assign to individual characters. See
// `panem_shared.theme`'s module docstring for the server-side gate and
// the profile system this feeds, and `dashboard_routes.build_theme_router`
// for the REST surface each button here calls through `app.js`'s ctx.
//
// Original canvas-free widget built from two layered CSS gradients (the
// classic SV-square trick: a black-to-transparent vertical gradient over
// a white-to-hue horizontal one) -- a well-known, generic color-picker
// shape, not anyone's particular implementation, and no external library
// needed (matches this codebase's no-build-step convention).
//
// `?v=` cache-busting: bump the literal below (and every importer's own
// `?v=` on its `import ... from "./theme_picker.js?v=N"` line) any time
// this file's exports change -- same convention `tabs/_shared.js` documents.

const TARGETS = ["background", "accent", "panel", "text"];
const TARGET_LABELS = { background: "Background", accent: "Accent", panel: "Panel", text: "Text" };
// Mirrors `panem_shared.theme`'s `DEFAULT_*_HEX` constants, which
// themselves mirror `style.css`'s `:root` values -- only used to seed the
// picker before the first real theme (`getTheme()`) is available.
const DEFAULTS = { background: "#14161c", accent: "#e0a72e", panel: "#1b1f27", text: "#d7dbe4" };

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function hexToRgb(hex) {
  const n = Number.parseInt(hex.slice(1), 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

function rgbToHex(r, g, b) {
  const clampByte = (v) => clamp(Math.round(v), 0, 255);
  return (
    "#" +
    [clampByte(r), clampByte(g), clampByte(b)]
      .map((v) => v.toString(16).padStart(2, "0"))
      .join("")
  );
}

function rgbToHsv(r, g, b) {
  r /= 255;
  g /= 255;
  b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  let h = 0;
  if (d !== 0) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  const s = max === 0 ? 0 : d / max;
  const v = max;
  return { h, s, v };
}

function hsvToRgb(h, s, v) {
  const c = v * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - c;
  let r1 = 0;
  let g1 = 0;
  let b1 = 0;
  if (h < 60) [r1, g1, b1] = [c, x, 0];
  else if (h < 120) [r1, g1, b1] = [x, c, 0];
  else if (h < 180) [r1, g1, b1] = [0, c, x];
  else if (h < 240) [r1, g1, b1] = [0, x, c];
  else if (h < 300) [r1, g1, b1] = [x, 0, c];
  else [r1, g1, b1] = [c, 0, x];
  return { r: (r1 + m) * 255, g: (g1 + m) * 255, b: (b1 + m) * 255 };
}

function hexToHsv(hex) {
  const { r, g, b } = hexToRgb(hex);
  return rgbToHsv(r, g, b);
}

function hsvToHex(h, s, v) {
  const { r, g, b } = hsvToRgb(h, s, v);
  return rgbToHex(r, g, b);
}

function isValidHex(value) {
  return /^#[0-9a-fA-F]{6}$/.test(value);
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Drags via pointer capture rather than window-level mousemove/mouseup
// listeners: capturing the pointer on the element itself means `pointermove`
// keeps firing on it (with real coordinates, even past its edges) for the
// whole drag, and there's nothing to remember to remove afterward -- the
// capture releases itself on pointerup automatically.
function attachDrag(el, onMove) {
  function handle(event) {
    onMove(event);
    event.preventDefault();
  }
  el.addEventListener("pointerdown", (event) => {
    el.setPointerCapture(event.pointerId);
    handle(event);
  });
  el.addEventListener("pointermove", (event) => {
    if (event.buttons === 0) return;
    handle(event);
  });
}

// container: the always-present `#theme-picker` wrapper (`position:
// relative` via CSS) -- hidden/shown by app.js based on `is_donor`, never
// unmounted, so this only needs to be called once.
//
// getTheme(): () => {background_hex, accent_hex, panel_hex, text_hex,
// profile_id}, the theme currently resolved+applied for whatever context
// app.js is showing (the account's general default, or the selected
// character's own override) -- read whenever the popup opens/refreshes,
// and as the value an unsaved edit reverts to on close.
// getProfiles(): () => [{id, name, background_hex, accent_hex, panel_hex,
// text_hex}], every profile this donor has saved.
// getActiveProfileId(): () => number|null, the account's general default
// profile id (for the "Default ✓" label).
// getCharacter(): () => {id, name, themeProfileId}|null, the dashboard's
// currently selected character, if any -- controls whether the "assign to
// this character" row shows at all.
// onPreview(theme): called on every drag/type/profile-pick, for instant
// CSS-var preview -- no network call.
// onSaveNew({name, ...theme}) -> Promise<profile>: creates a new profile.
// onUpdate(id, {name, ...theme}) -> Promise<profile>: overwrites one.
// onDelete(id) -> Promise<void>.
// onActivate(id) -> Promise<void>: sets the account's general default.
// onAssign(id, characterId) -> Promise<void>: assigns to one character.
// onUnassign(characterId) -> Promise<void>: clears a character's override.
// onReset() -> Promise<void>: resets the *current context* (character if
// one is selected, else the account's general default) to the plain
// default style.
export function mountThemePicker(
  container,
  {
    getTheme,
    getProfiles,
    getActiveProfileId,
    getCharacter,
    onPreview,
    onSaveNew,
    onUpdate,
    onDelete,
    onActivate,
    onAssign,
    onUnassign,
    onReset,
  }
) {
  container.innerHTML = `
    <button type="button" class="theme-picker-toggle" aria-label="Customize dashboard colors"
      title="Customize dashboard colors" aria-expanded="false"></button>
    <div class="theme-picker-popup" hidden>
      <div class="theme-picker-targets">
        ${TARGETS.map(
          (t, i) =>
            `<button type="button" class="theme-target-btn${i === 0 ? " active" : ""}" data-target="${t}">
              <span class="theme-target-swatch" data-swatch="${t}"></span>${TARGET_LABELS[t]}
            </button>`
        ).join("")}
      </div>
      <div class="theme-picker-body">
        <div class="theme-picker-sv"><div class="theme-picker-sv-handle"></div></div>
        <div class="theme-picker-hue"><div class="theme-picker-hue-handle"></div></div>
      </div>
      <label class="theme-picker-hex-row">
        <span>#</span>
        <input type="text" class="theme-picker-hex-input" maxlength="6" spellcheck="false" />
      </label>

      <div class="theme-picker-profiles">
        <label class="theme-picker-profile-row">
          <span>Profile</span>
          <select class="theme-profile-select"></select>
        </label>
        <input type="text" class="theme-profile-name-input" placeholder="Profile name" maxlength="40" />
        <div class="theme-picker-actions">
          <button type="button" class="theme-btn theme-profile-save-btn">Save as New</button>
          <button type="button" class="theme-btn theme-profile-update-btn">Update</button>
        </div>
        <div class="theme-picker-actions">
          <button type="button" class="theme-btn theme-profile-activate-btn">Set as Default</button>
          <button type="button" class="theme-btn theme-profile-delete-btn">Delete</button>
        </div>
        <div class="theme-picker-actions theme-picker-assign-row" hidden>
          <button type="button" class="theme-btn theme-profile-assign-btn"></button>
        </div>
      </div>

      <div class="theme-picker-actions">
        <button type="button" class="theme-reset-btn">Reset to default</button>
      </div>
      <p class="theme-picker-status" hidden></p>
    </div>
  `;
  const toggleBtn = container.querySelector(".theme-picker-toggle");
  const popup = container.querySelector(".theme-picker-popup");
  const svEl = container.querySelector(".theme-picker-sv");
  const svHandle = container.querySelector(".theme-picker-sv-handle");
  const hueEl = container.querySelector(".theme-picker-hue");
  const hueHandle = container.querySelector(".theme-picker-hue-handle");
  const hexInput = container.querySelector(".theme-picker-hex-input");
  const resetBtn = container.querySelector(".theme-reset-btn");
  const statusEl = container.querySelector(".theme-picker-status");
  const targetButtons = [...container.querySelectorAll(".theme-target-btn")];
  const swatches = Object.fromEntries(
    TARGETS.map((t) => [t, container.querySelector(`[data-swatch="${t}"]`)])
  );

  const profileSelect = container.querySelector(".theme-profile-select");
  const nameInput = container.querySelector(".theme-profile-name-input");
  const saveNewBtn = container.querySelector(".theme-profile-save-btn");
  const updateBtn = container.querySelector(".theme-profile-update-btn");
  const activateBtn = container.querySelector(".theme-profile-activate-btn");
  const deleteBtn = container.querySelector(".theme-profile-delete-btn");
  const assignRow = container.querySelector(".theme-picker-assign-row");
  const assignBtn = container.querySelector(".theme-profile-assign-btn");
  const actionButtons = [saveNewBtn, updateBtn, activateBtn, deleteBtn, assignBtn, resetBtn];

  // Four independent HSV states, one per target -- switching which color
  // you're editing must not discard whatever another one was mid-edited
  // to, and all four feed `onPreview` together on every change so the
  // live preview always reflects all of them at once.
  let hsv = Object.fromEntries(TARGETS.map((t) => [t, hexToHsv(DEFAULTS[t])]));
  let activeTarget = "background";
  // The profile (if any) whose colors are currently loaded into `hsv` --
  // null means "editing without an associated saved profile". Sticky
  // across slider/hex edits (so tweaking a loaded profile's color and
  // hitting Update still targets it), cleared on delete or on explicitly
  // picking "Unsaved colors" from the dropdown.
  let loadedProfileId = null;
  let busy = false;

  function currentHex(target) {
    const { h, s, v } = hsv[target];
    return hsvToHex(h, s, v);
  }

  function currentTheme() {
    return {
      background_hex: currentHex("background"),
      accent_hex: currentHex("accent"),
      panel_hex: currentHex("panel"),
      text_hex: currentHex("text"),
    };
  }

  function findProfile(id) {
    if (id == null) return null;
    return getProfiles().find((p) => p.id === id) || null;
  }

  function setStatus(text) {
    statusEl.textContent = text;
    statusEl.hidden = !text;
  }

  function renderSwatches() {
    for (const target of TARGETS) {
      swatches[target].style.background = currentHex(target);
    }
  }

  function renderActivePicker() {
    const { h, s, v } = hsv[activeTarget];
    svEl.style.setProperty("--picker-hue", h);
    svHandle.style.left = `${s * 100}%`;
    svHandle.style.top = `${(1 - v) * 100}%`;
    hueHandle.style.top = `${(h / 360) * 100}%`;
    hexInput.value = currentHex(activeTarget).slice(1);
  }

  function render() {
    renderSwatches();
    renderActivePicker();
  }

  function renderProfileSelect() {
    const profiles = getProfiles();
    profileSelect.innerHTML =
      `<option value="">Unsaved colors</option>` +
      profiles.map((p) => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("");
    profileSelect.value = loadedProfileId != null ? String(loadedProfileId) : "";
  }

  function renderProfileControls() {
    renderProfileSelect();
    const hasLoaded = loadedProfileId != null;
    updateBtn.disabled = !hasLoaded;
    deleteBtn.disabled = !hasLoaded;
    activateBtn.disabled = !hasLoaded;
    activateBtn.textContent =
      hasLoaded && getActiveProfileId() === loadedProfileId ? "Default ✓" : "Set as Default";

    const character = getCharacter();
    if (!character) {
      assignRow.hidden = true;
      return;
    }
    assignRow.hidden = false;
    const isAssigned = hasLoaded && character.themeProfileId === loadedProfileId;
    assignBtn.disabled = !hasLoaded;
    assignBtn.textContent = isAssigned
      ? `Stop using for ${character.name}`
      : `Use for ${character.name}`;
  }

  function emitPreview() {
    render();
    onPreview(currentTheme());
  }

  attachDrag(svEl, (event) => {
    const rect = svEl.getBoundingClientRect();
    const s = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    const v = 1 - clamp((event.clientY - rect.top) / rect.height, 0, 1);
    hsv[activeTarget] = { ...hsv[activeTarget], s, v };
    emitPreview();
  });

  attachDrag(hueEl, (event) => {
    const rect = hueEl.getBoundingClientRect();
    const h = clamp((event.clientY - rect.top) / rect.height, 0, 1) * 360;
    hsv[activeTarget] = { ...hsv[activeTarget], h };
    emitPreview();
  });

  hexInput.addEventListener("change", () => {
    const value = `#${hexInput.value.trim()}`;
    if (!isValidHex(value)) {
      render(); // revert the field to the last-good value
      return;
    }
    hsv[activeTarget] = hexToHsv(value);
    emitPreview();
  });

  for (const button of targetButtons) {
    button.addEventListener("click", () => {
      activeTarget = button.dataset.target;
      for (const b of targetButtons) b.classList.toggle("active", b === button);
      renderActivePicker();
    });
  }

  function loadProfileIntoPicker(profile) {
    loadedProfileId = profile.id;
    hsv = {
      background: hexToHsv(profile.background_hex),
      accent: hexToHsv(profile.accent_hex),
      panel: hexToHsv(profile.panel_hex),
      text: hexToHsv(profile.text_hex),
    };
    nameInput.value = profile.name;
    render();
    renderProfileControls();
  }

  profileSelect.addEventListener("change", () => {
    if (profileSelect.value === "") {
      loadedProfileId = null;
      nameInput.value = "";
      renderProfileControls();
      return;
    }
    const profile = findProfile(Number(profileSelect.value));
    if (!profile) return;
    loadProfileIntoPicker(profile);
    // Preview this saved profile live without activating/assigning it --
    // "so you can see what it will look like if you choose it."
    onPreview(currentTheme());
  });

  function syncFromSaved() {
    const theme = getTheme();
    hsv = {
      background: hexToHsv(theme.background_hex),
      accent: hexToHsv(theme.accent_hex),
      panel: hexToHsv(theme.panel_hex),
      text: hexToHsv(theme.text_hex),
    };
    loadedProfileId = theme.profile_id ?? null;
    const profile = findProfile(loadedProfileId);
    nameInput.value = profile ? profile.name : "";
    render();
    renderProfileControls();
  }

  function closePopup({ revert = true } = {}) {
    if (popup.hidden) return;
    popup.hidden = true;
    toggleBtn.setAttribute("aria-expanded", "false");
    if (revert) {
      syncFromSaved();
      onPreview(getTheme());
    }
  }

  function setBusy(isBusy) {
    busy = isBusy;
    for (const btn of actionButtons) btn.disabled = isBusy;
    if (!isBusy) renderProfileControls(); // restores correct per-state enabled/disabled
  }

  async function runBusy(action, busyText) {
    if (busy) return;
    setBusy(true);
    setStatus(busyText);
    try {
      await action();
      setStatus("Saved.");
    } catch (err) {
      setStatus(`Couldn't save: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  saveNewBtn.addEventListener("click", () =>
    runBusy(async () => {
      const profile = await onSaveNew({ name: nameInput.value, ...currentTheme() });
      loadedProfileId = profile.id;
      nameInput.value = profile.name;
      renderProfileControls();
    }, "Saving…")
  );

  updateBtn.addEventListener("click", () => {
    if (loadedProfileId == null) return;
    const id = loadedProfileId;
    return runBusy(async () => {
      const profile = await onUpdate(id, { name: nameInput.value, ...currentTheme() });
      nameInput.value = profile.name;
      renderProfileControls();
    }, "Saving…");
  });

  deleteBtn.addEventListener("click", () => {
    if (loadedProfileId == null) return;
    const id = loadedProfileId;
    return runBusy(async () => {
      await onDelete(id);
      loadedProfileId = null;
      syncFromSaved();
      onPreview(getTheme());
    }, "Deleting…");
  });

  activateBtn.addEventListener("click", () => {
    if (loadedProfileId == null) return;
    const id = loadedProfileId;
    return runBusy(async () => {
      await onActivate(id);
      syncFromSaved();
      onPreview(getTheme());
    }, "Setting default…");
  });

  assignBtn.addEventListener("click", () => {
    const character = getCharacter();
    if (!character || loadedProfileId == null) return;
    const isAssigned = character.themeProfileId === loadedProfileId;
    return runBusy(async () => {
      if (isAssigned) await onUnassign(character.id);
      else await onAssign(loadedProfileId, character.id);
      syncFromSaved();
      onPreview(getTheme());
    }, isAssigned ? "Removing…" : "Assigning…");
  });

  resetBtn.addEventListener("click", () =>
    runBusy(async () => {
      await onReset();
      syncFromSaved();
      onPreview(getTheme());
    }, "Resetting…")
  );

  toggleBtn.addEventListener("click", () => {
    if (popup.hidden) {
      syncFromSaved();
      setStatus("");
      popup.hidden = false;
      toggleBtn.setAttribute("aria-expanded", "true");
    } else {
      closePopup();
    }
  });
  document.addEventListener("click", (event) => {
    if (!popup.hidden && !event.composedPath().includes(container)) closePopup();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closePopup();
  });

  render();
  renderProfileControls();

  return {
    // Called by app.js whenever the saved theme, profile list, or selected
    // character changes from outside this widget (a fresh `/identify` or
    // `/theme/resolve` response, or right after this widget's own actions
    // already applied one -- a redundant-but-harmless call in that case).
    refresh() {
      if (popup.hidden) return; // no need to touch a closed popup's state
      syncFromSaved();
    },
  };
}
