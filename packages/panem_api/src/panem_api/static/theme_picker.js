// The donor-only dashboard theme popup: a little color-wheel button pinned
// to the header's top-right corner (`app.js` only unhides `#theme-picker`
// when `/activity/dashboard/identify` reports `is_donor`) that opens a
// Discord-role-color-picker-style panel -- a saturation/value square plus
// a hue strip, driving a live hex swatch/text field, for each of the two
// customizable colors (background, accent). See `panem_shared.theme`'s
// module docstring for the server-side gate and persistence this feeds.
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

const TARGETS = ["background", "accent"];
const TARGET_LABELS = { background: "Background", accent: "Accent" };

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
// getTheme(): () => {background_hex, accent_hex}, the last-*saved* theme --
// read when the popup opens (and by `refresh()`, e.g. after `/identify`
// loads a saved theme, or right after a save/reset resolves) to seed both
// targets' pickers and as the value an unsaved edit reverts to on close.
// onPreview(theme): called on every drag/type, for instant CSS-var preview.
// onSave(theme)/onReset(): return a Promise of the persisted `{background_
// hex, accent_hex}` (or throw) -- this shows a status line and calls
// `onPreview` with the result either way.
export function mountThemePicker(container, { getTheme, onPreview, onSave, onReset }) {
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
      <div class="theme-picker-actions">
        <button type="button" class="theme-save-btn">Save</button>
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
  const saveBtn = container.querySelector(".theme-save-btn");
  const resetBtn = container.querySelector(".theme-reset-btn");
  const statusEl = container.querySelector(".theme-picker-status");
  const targetButtons = [...container.querySelectorAll(".theme-target-btn")];
  const swatches = Object.fromEntries(
    TARGETS.map((t) => [t, container.querySelector(`[data-swatch="${t}"]`)])
  );

  // Two independent HSV states, one per target -- switching which color
  // you're editing (Background <-> Accent) must not discard whatever the
  // other one was mid-edited to, and both feed `onPreview` together on
  // every change so the live preview always reflects both at once.
  let hsv = { background: hexToHsv("#14161c"), accent: hexToHsv("#e0a72e") };
  let activeTarget = "background";
  let saving = false;

  function currentHex(target) {
    const { h, s, v } = hsv[target];
    return hsvToHex(h, s, v);
  }

  function currentTheme() {
    return { background_hex: currentHex("background"), accent_hex: currentHex("accent") };
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

  function syncFromSaved() {
    const theme = getTheme();
    hsv = { background: hexToHsv(theme.background_hex), accent: hexToHsv(theme.accent_hex) };
    render();
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

  async function withStatus(action, busyText) {
    if (saving) return;
    saving = true;
    saveBtn.disabled = true;
    resetBtn.disabled = true;
    setStatus(busyText);
    try {
      const saved = await action();
      setStatus("Saved.");
      hsv = { background: hexToHsv(saved.background_hex), accent: hexToHsv(saved.accent_hex) };
      render();
      onPreview(saved);
    } catch (err) {
      setStatus(`Couldn't save: ${err.message}`);
    } finally {
      saving = false;
      saveBtn.disabled = false;
      resetBtn.disabled = false;
    }
  }

  saveBtn.addEventListener("click", () => withStatus(() => onSave(currentTheme()), "Saving…"));
  resetBtn.addEventListener("click", () => withStatus(() => onReset(), "Resetting…"));

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

  return {
    // Called by app.js whenever the saved theme changes from outside this
    // widget (a fresh `/identify` response, or right after this widget's
    // own save/reset already applied it -- a redundant-but-harmless call
    // in that case, since `hsv` already matches).
    refresh() {
      if (popup.hidden) return; // no need to touch a closed popup's state
      syncFromSaved();
    },
  };
}
