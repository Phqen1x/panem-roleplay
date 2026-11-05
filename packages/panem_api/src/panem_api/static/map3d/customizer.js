// The block-character customizer: an 18-outfit x 18-palette grid picker,
// opened from `city.js`'s "Customize Character" button. Every outfit mesh
// shares the same UV layout (confirmed against the vendored pack before
// this feature was built), so any palette texture applies cleanly to any
// outfit -- this is the entire "customization" surface a Blocky
// Characters-based system can honestly offer (see `panem_shared.
// block_models`'s docstring for the same point about NPCs). Applies each
// change immediately to the live 3D model (via `onChange`, `city.js`'s
// `localPlayer.setAppearance`) and persists it right away too, rather
// than a separate "Save" step -- there's nothing to lose by committing a
// cosmetic pick the instant it's made.
import { el } from "../tabs/_shared.js?v=7";
import { ASSETS_BASE } from "./character.js";

const LETTERS = "abcdefghijklmnopqr".split("");

export function openCustomizer(overlayHost, ctx, characterId, onChange) {
  overlayHost.innerHTML = "";
  const statusLine = el("p", { class: "tab-status" });
  const outfitGrid = el("div", { class: "map3d-swatch-grid" });
  const paletteGrid = el("div", { class: "map3d-swatch-grid" });
  const panel = el(
    "div",
    { class: "panel map3d-popup map3d-customizer" },
    el("h2", { text: "Customize Character" }),
    statusLine,
    el("h3", { text: "Outfit" }),
    outfitGrid,
    el("h3", { text: "Color" }),
    paletteGrid,
    el(
      "button",
      { class: "btn secondary", type: "button", onclick: () => (overlayHost.innerHTML = "") },
      "Close"
    )
  );
  overlayHost.append(panel);

  let current = { outfit: "a", palette: "a" };

  async function save() {
    statusLine.textContent = "";
    try {
      const body = await ctx.apiFetch(`/activity/dashboard/map3d/character/${characterId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          block_outfit: current.outfit,
          block_palette: current.palette,
        }),
      });
      current = { outfit: body.block_outfit, palette: body.block_palette };
      render();
      onChange(current.outfit, current.palette);
    } catch (err) {
      statusLine.className = "tab-status error";
      statusLine.textContent = err.message;
    }
  }

  function render() {
    outfitGrid.innerHTML = "";
    for (const letter of LETTERS) {
      outfitGrid.append(
        el(
          "button",
          {
            type: "button",
            class: `map3d-swatch${letter === current.outfit ? " active" : ""}`,
            style: `background-image: url(${ASSETS_BASE}/characters/Previews/character-${letter}.png)`,
            onclick: () => {
              current = { ...current, outfit: letter };
              save();
            },
          },
          ""
        )
      );
    }
    paletteGrid.innerHTML = "";
    for (const letter of LETTERS) {
      paletteGrid.append(
        el(
          "button",
          {
            type: "button",
            class: `map3d-swatch map3d-swatch-palette${letter === current.palette ? " active" : ""}`,
            style: `background-image: url(${ASSETS_BASE}/characters/Textures/texture-${letter}.png)`,
            onclick: () => {
              current = { ...current, palette: letter };
              save();
            },
          },
          ""
        )
      );
    }
  }

  (async () => {
    try {
      current = await ctx.apiFetch(
        `/activity/dashboard/map3d/character/${characterId}?discord_id=${encodeURIComponent(ctx.discordId())}`
      );
      current = { outfit: current.block_outfit, palette: current.block_palette };
    } catch (err) {
      statusLine.className = "tab-status error";
      statusLine.textContent = err.message;
    }
    render();
  })();
}
