// The block-character customizer: four independent body parts (head,
// torso, arms, legs), each with its own shape (18 outfit letters) and
// color (18 palette textures) -- see `character.js`'s module docstring
// for how a part from one outfit file grafts cleanly onto another (every
// outfit shares one rig). 18^4 shape combinations x 18^4 color
// combinations is the full space this opens up, a long way past picking
// one of 18 whole-body presets. Applies each change immediately to the
// live 3D model (via `onChange`, `city.js`'s `localPlayer.setAppearance`)
// and persists it right away too -- there's nothing to lose by committing
// a cosmetic pick the instant it's made.
import { el } from "../tabs/_shared.js?v=8";
import { ASSETS_BASE } from "./character.js";

const LETTERS = "abcdefghijklmnopqr".split("");
const PARTS = [
  { key: "head", label: "Head" },
  { key: "torso", label: "Torso" },
  { key: "arms", label: "Arms" },
  { key: "legs", label: "Legs" },
];

function shapeButton(letter, active, onClick) {
  return el(
    "button",
    {
      type: "button",
      class: `map3d-letter-swatch${active ? " active" : ""}`,
      onclick: onClick,
      title: `Shape ${letter.toUpperCase()}`,
    },
    letter.toUpperCase()
  );
}

function colorButton(letter, active, onClick) {
  return el(
    "button",
    {
      type: "button",
      class: `map3d-swatch map3d-swatch-palette${active ? " active" : ""}`,
      style: `background-image: url(${ASSETS_BASE}/characters/Textures/texture-${letter}.png)`,
      onclick: onClick,
      title: `Color ${letter.toUpperCase()}`,
    },
    ""
  );
}

export function openCustomizer(overlayHost, ctx, characterId, onChange) {
  overlayHost.innerHTML = "";
  const statusLine = el("p", { class: "tab-status" });
  const sections = {};
  const sectionEls = PARTS.map(({ key, label }) => {
    const shapeRow = el("div", { class: "map3d-swatch-grid" });
    const colorRow = el("div", { class: "map3d-swatch-grid" });
    sections[key] = { shapeRow, colorRow };
    return el(
      "div",
      { class: "map3d-customizer-section" },
      el("h3", { text: label }),
      el("div", { class: "map3d-customizer-subhead" }, "Shape"),
      shapeRow,
      el("div", { class: "map3d-customizer-subhead" }, "Color"),
      colorRow
    );
  });

  const panel = el(
    "div",
    { class: "panel map3d-popup map3d-customizer" },
    el("h2", { text: "Customize Character" }),
    statusLine,
    ...sectionEls,
    el(
      "button",
      { class: "btn secondary", type: "button", onclick: () => (overlayHost.innerHTML = "") },
      "Close"
    )
  );
  overlayHost.append(panel);

  let current = null;
  let saving = false;
  let queuedSave = false;

  async function save() {
    if (saving) {
      queuedSave = true;
      return;
    }
    saving = true;
    statusLine.textContent = "";
    try {
      const body = await ctx.apiFetch(`/activity/dashboard/map3d/character/${characterId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), parts: current }),
      });
      current = { ...body };
      onChange(current);
    } catch (err) {
      statusLine.className = "tab-status error";
      statusLine.textContent = err.message;
    } finally {
      saving = false;
      if (queuedSave) {
        queuedSave = false;
        await save();
      }
    }
  }

  function setPart(key, value) {
    current = { ...current, [key]: value };
    render();
    save();
  }

  function render() {
    for (const { key } of PARTS) {
      const { shapeRow, colorRow } = sections[key];
      shapeRow.innerHTML = "";
      for (const letter of LETTERS) {
        shapeRow.append(
          shapeButton(letter, letter === current[key], () => setPart(key, letter))
        );
      }
      colorRow.innerHTML = "";
      const colorKey = `${key}_color`;
      for (const letter of LETTERS) {
        colorRow.append(
          colorButton(letter, letter === current[colorKey], () => setPart(colorKey, letter))
        );
      }
    }
  }

  (async () => {
    try {
      current = await ctx.apiFetch(
        `/activity/dashboard/map3d/character/${characterId}?discord_id=${encodeURIComponent(ctx.discordId())}`
      );
    } catch (err) {
      statusLine.className = "tab-status error";
      statusLine.textContent = err.message;
      current = {
        head: "a",
        head_color: "a",
        torso: "a",
        torso_color: "a",
        arms: "a",
        arms_color: "a",
        legs: "a",
        legs_color: "a",
      };
    }
    render();
  })();
}
